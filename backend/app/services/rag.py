"""RAG (Retrieval Augmented Generation) service for Mnemos.

Embeds a user query, retrieves top-K relevant chunks from Qdrant,
decrypts them, builds a context-augmented prompt, and generates an
answer via the LLM service.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from dataclasses import dataclass

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

    __slots__ = ("embedding_service", "llm_service", "encryption_service")

    def __init__(
        self,
        embedding_service: EmbeddingService,
        llm_service: LLMService,
        encryption_service: EncryptionService,
    ) -> None:
        self.embedding_service = embedding_service
        self.llm_service = llm_service
        self.encryption_service = encryption_service

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

        Performs retrieval and decryption synchronously, then returns
        a streaming iterator and the list of source memory IDs so
        the caller can stream tokens to the client while knowing
        which sources were used.
        """
        scored_chunks = await self.embedding_service.search_similar(
            question, top_k=top_k
        )

        if not scored_chunks:

            async def _empty_stream() -> AsyncIterator[str]:
                yield "I don't have any relevant memories to answer that question."

            return _empty_stream(), []

        context_chunks, source_ids = self._decrypt_chunks(scored_chunks)

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

        for chunk in scored_chunks:
            envelope = EncryptedEnvelope(
                ciphertext=bytes.fromhex(chunk.chunk_encrypted),
                encrypted_dek=bytes.fromhex(chunk.chunk_dek),
                algo=chunk.chunk_algo,
                version=chunk.chunk_version,
            )
            plaintext = self.encryption_service.decrypt(envelope)
            context_chunks.append(plaintext.decode("utf-8"))
            source_ids.add(chunk.memory_id)

        return context_chunks, source_ids
