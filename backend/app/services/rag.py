"""RAG (Retrieval Augmented Generation) service for Mnemos.

Embeds a user query, retrieves top-K relevant chunks from Qdrant,
decrypts them, builds a context-augmented prompt, and generates an
answer via the LLM service.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

from sqlmodel import Session, select, text

from app.services.embedding import EmbeddingService, ScoredChunk
from app.services.encryption import EncryptedEnvelope, EncryptionService
from app.services.llm import LLMService

logger = logging.getLogger(__name__)

SYSTEM_PROMPT = """You are the digital memory of a person. You have access to their
memories, notes, documents, and life experiences. Answer questions based on the retrieved
context below. If you don't have relevant memories, say so honestly.

Always cite which memories you're drawing from. Distinguish between:
- ORIGINAL SOURCE: Direct quotes or information from the person's own memories
- CONNECTION: Your inference about how memories relate to each other

Pay close attention to [Tags: ...] annotations on memories — these are user-assigned labels
that describe people, pets, topics, or categories. When asked about a tag name (e.g. a
nickname or label), use the tagged memory as context for your answer.

Retrieved memories:
{context}"""

DEFAULT_TOP_K = 5


@dataclass(frozen=True, slots=True)
class RAGResult:
    """Result of a RAG query."""

    answer: str
    sources: list[str]  # unique memory_ids that contributed context
    chunks_used: int
    model: str  # which LLM model produced the answer


class RAGService:
    """Retrieval Augmented Generation pipeline over encrypted memories."""

    __slots__ = ("embedding_service", "llm_service", "encryption_service", "db_session")

    def __init__(
        self,
        embedding_service: EmbeddingService,
        llm_service: LLMService,
        encryption_service: EncryptionService,
        db_session: Session | None = None,
    ) -> None:
        self.embedding_service = embedding_service
        self.llm_service = llm_service
        self.encryption_service = encryption_service
        self.db_session = db_session

    async def query(
        self,
        question: str,
        top_k: int = DEFAULT_TOP_K,
    ) -> RAGResult:
        """Answer a question using RAG over the brain's memories.

        1. Embed the question and retrieve top-K similar chunks.
        2. Decrypt each chunk.
        3. Build a context-augmented system prompt.
        4. Generate an answer via the LLM.
        """
        scored_chunks = await self.embedding_service.search_similar(
            question, top_k=top_k
        )

        if not scored_chunks:
            return RAGResult(
                answer="I don't have any relevant memories to answer that question.",
                sources=[],
                chunks_used=0,
                model=self.llm_service.model,
            )

        context_chunks, source_ids = self._decrypt_chunks(scored_chunks)

        context = "\n\n---\n\n".join(context_chunks)
        system_prompt = SYSTEM_PROMPT.format(context=context)

        response = await self.llm_service.generate(
            prompt=question,
            system=system_prompt,
            temperature=0.7,
        )

        return RAGResult(
            answer=response.text,
            sources=list(source_ids),
            chunks_used=len(context_chunks),
            model=response.model,
        )

    async def stream_query(
        self,
        question: str,
        top_k: int = DEFAULT_TOP_K,
    ) -> tuple[AsyncIterator[str], list[str]]:
        """Stream an answer while returning source IDs upfront.

        Uses both vector search (embeddings) and keyword search (blind index)
        to find relevant memories, then streams an LLM answer.
        """
        scored_chunks = await self.embedding_service.search_similar(
            question, top_k=top_k
        )

        context_chunks, source_ids = self._decrypt_chunks(scored_chunks)

        # Also find memories via blind index (keyword/tag tokens)
        # This catches memories found by tag name or keyword that vector search missed
        keyword_extras = self._keyword_matched_contexts(question, source_ids)
        if keyword_extras:
            for mid, ctx_text in keyword_extras:
                context_chunks.append(ctx_text)
                source_ids.add(mid)

        if not context_chunks:

            async def _empty_stream() -> AsyncIterator[str]:
                yield "I don't have any relevant memories to answer that question."

            return _empty_stream(), []

        context = "\n\n---\n\n".join(context_chunks)
        system_prompt = SYSTEM_PROMPT.format(context=context)

        token_stream = self.llm_service.stream(
            prompt=question,
            system=system_prompt,
            temperature=0.7,
        )

        return token_stream, list(source_ids)

    def _decrypt_chunks(
        self,
        scored_chunks: list[ScoredChunk],
    ) -> tuple[list[str], set[str]]:
        """Decrypt scored chunks and collect unique source memory IDs."""
        context_chunks: list[str] = []
        source_ids: set[str] = set()

        # Pre-fetch tags for all retrieved memory IDs
        memory_tags: dict[str, list[str]] = {}
        if self.db_session:
            unique_mids = {c.memory_id for c in scored_chunks}
            memory_tags = self._fetch_tags_for_memories(unique_mids)

        for chunk in scored_chunks:
            envelope = EncryptedEnvelope(
                ciphertext=bytes.fromhex(chunk.chunk_encrypted),
                encrypted_dek=bytes.fromhex(chunk.chunk_dek),
                algo=chunk.chunk_algo,
                version=chunk.chunk_version,
            )
            plaintext = self.encryption_service.decrypt(envelope).decode("utf-8")

            # Annotate chunk with tag names so the LLM sees them
            tags = memory_tags.get(chunk.memory_id, [])
            if tags:
                plaintext = f"[Tags: {', '.join(tags)}]\n{plaintext}"

            context_chunks.append(plaintext)
            source_ids.add(chunk.memory_id)

        return context_chunks, source_ids

    def _keyword_matched_contexts(
        self,
        question: str,
        already_found: set[str],
    ) -> list[tuple[str, str]]:
        """Find memories via blind index keyword search and return decrypted context.

        Returns a list of (memory_id, context_text) for memories matched by
        keyword/tag that were NOT already found by vector search.
        """
        if not self.db_session:
            return []

        tokens = self.encryption_service.generate_search_tokens(question)
        if not tokens:
            return []

        placeholders = ", ".join(f":t{i}" for i in range(len(tokens)))
        params: dict[str, str] = {f"t{i}": t for i, t in enumerate(tokens)}

        sql = f"""
            SELECT DISTINCT st.memory_id
            FROM search_tokens st
            WHERE st.token_hmac IN ({placeholders})
        """
        try:
            result = self.db_session.execute(text(sql).bindparams(**params))
            matched_mids = {row[0] for row in result}
        except Exception:
            logger.warning("Keyword search in RAG failed", exc_info=True)
            return []

        new_mids = matched_mids - already_found
        if not new_mids:
            return []

        # Decrypt title + content for these memories directly from DB
        extras: list[tuple[str, str]] = []
        tags_map = self._fetch_tags_for_memories(new_mids)

        for mid in list(new_mids)[:5]:  # cap at 5 extra memories
            try:
                row = self.db_session.execute(
                    text(
                        "SELECT title, content, title_dek, content_dek, "
                        "encryption_algo, encryption_version, content_type "
                        "FROM memories WHERE id = :mid"
                    ).bindparams(mid=mid)
                ).first()
                if not row:
                    continue

                title_enc, content_enc, title_dek, content_dek, algo, version, ctype = row

                if title_dek and content_dek:
                    title_plain = self.encryption_service.decrypt(
                        EncryptedEnvelope(
                            ciphertext=bytes.fromhex(title_enc),
                            encrypted_dek=bytes.fromhex(title_dek),
                            algo=algo or "aes-256-gcm",
                            version=version or 1,
                        )
                    ).decode("utf-8")
                    content_plain = self.encryption_service.decrypt(
                        EncryptedEnvelope(
                            ciphertext=bytes.fromhex(content_enc),
                            encrypted_dek=bytes.fromhex(content_dek),
                            algo=algo or "aes-256-gcm",
                            version=version or 1,
                        )
                    ).decode("utf-8")
                else:
                    title_plain = title_enc
                    content_plain = content_enc

                ctx = f"[Memory: {title_plain}] ({ctype})\n{content_plain}"
                tags = tags_map.get(mid, [])
                if tags:
                    ctx = f"[Tags: {', '.join(tags)}]\n{ctx}"
                extras.append((mid, ctx))
            except Exception:
                logger.warning("Failed to decrypt memory %s for RAG", mid, exc_info=True)

        return extras

    def _fetch_tags_for_memories(
        self, memory_ids: set[str]
    ) -> dict[str, list[str]]:
        """Fetch tag names for a set of memory IDs from the database."""
        if not self.db_session or not memory_ids:
            return {}

        placeholders = ", ".join(f":mid_{i}" for i in range(len(memory_ids)))
        params = {f"mid_{i}": mid for i, mid in enumerate(memory_ids)}

        sql = f"""
            SELECT mt.memory_id, t.name
            FROM memory_tags mt
            JOIN tags t ON mt.tag_id = t.id
            WHERE mt.memory_id IN ({placeholders})
            ORDER BY t.name
        """
        try:
            result = self.db_session.execute(text(sql).bindparams(**params))
            tags_map: dict[str, list[str]] = {}
            for row in result:
                tags_map.setdefault(row[0], []).append(row[1])
            return tags_map
        except Exception:
            logger.warning("Failed to fetch tags for RAG context", exc_info=True)
            return {}
