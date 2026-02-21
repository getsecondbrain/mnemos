"""Connection service — auto-generate neural links between memories."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from sqlmodel import Session, select, or_

from app.models.connection import Connection, RELATIONSHIP_TYPES
from app.models.memory import Memory
from app.services.embedding import EmbeddingService, ScoredChunk
from app.services.encryption import EncryptedEnvelope, EncryptionService
from app.services.llm import LLMService, LLMError

logger = logging.getLogger(__name__)

SIMILARITY_THRESHOLD = 0.75  # Minimum cosine similarity to trigger LLM explanation
MAX_CONNECTIONS_PER_MEMORY = 10  # Search for top-N similar chunks


@dataclass(frozen=True, slots=True)
class ConnectionResult:
    """Result of connection discovery for a memory."""
    memory_id: str
    connections_created: int
    connections_skipped: int  # already existed


class ConnectionService:
    """Auto-discover and create neural links between memories."""

    __slots__ = ("embedding_service", "llm_service", "encryption_service", "owner_name")

    def __init__(
        self,
        embedding_service: EmbeddingService,
        llm_service: LLMService,
        encryption_service: EncryptionService,
        owner_name: str = "",
    ) -> None:
        self.embedding_service = embedding_service
        self.llm_service = llm_service
        self.encryption_service = encryption_service
        self.owner_name = owner_name

    async def find_connections(
        self,
        memory_id: str,
        plaintext: str,
        session: Session,
    ) -> ConnectionResult:
        """Find and create connections for a newly ingested memory.

        Steps:
        1. Search Qdrant for similar chunks (excluding self).
        2. Deduplicate by memory_id (a memory may have multiple matching chunks).
        3. For matches above threshold, check if connection already exists.
        4. Ask LLM to explain the relationship.
        5. Encrypt the explanation and create the Connection record.
        """
        # 1. Find similar memory chunks
        similar_chunks = await self.embedding_service.search_similar(
            query_text=plaintext,
            top_k=MAX_CONNECTIONS_PER_MEMORY,
            exclude_memory_id=memory_id,
        )

        # 2. Deduplicate: keep best score per unique memory_id
        best_per_memory: dict[str, ScoredChunk] = {}
        for chunk in similar_chunks:
            existing = best_per_memory.get(chunk.memory_id)
            if existing is None or chunk.score > existing.score:
                best_per_memory[chunk.memory_id] = chunk

        created = 0
        skipped = 0

        for other_memory_id, chunk in best_per_memory.items():
            if chunk.score < SIMILARITY_THRESHOLD:
                continue

            # 3. Check for existing connection (either direction)
            if self._connection_exists(session, memory_id, other_memory_id):
                skipped += 1
                continue

            # 4. Decrypt the other chunk to get plaintext for LLM context
            other_text = self._decrypt_chunk(chunk)

            # 5. Ask LLM to explain the relationship
            try:
                explanation, rel_type = await self._explain_relationship(
                    plaintext[:500], other_text[:500]
                )
            except LLMError:
                logger.warning(
                    "LLM unavailable, creating embedding_similarity connection "
                    "for %s <-> %s",
                    memory_id, other_memory_id,
                )
                explanation = "Connected by embedding similarity."
                rel_type = "related"

            # 6. Encrypt explanation
            envelope = self.encryption_service.encrypt(
                explanation.encode("utf-8")
            )

            # 7. Create Connection record
            connection = Connection(
                source_memory_id=memory_id,
                target_memory_id=other_memory_id,
                relationship_type=rel_type,
                strength=chunk.score,
                explanation_encrypted=envelope.ciphertext.hex(),
                explanation_dek=envelope.encrypted_dek.hex(),
                encryption_algo=envelope.algo,
                encryption_version=envelope.version,
                generated_by=f"llm:ollama/{self.llm_service.model}",
                is_primary=False,
            )
            session.add(connection)
            created += 1

        if created > 0:
            session.commit()

        return ConnectionResult(
            memory_id=memory_id,
            connections_created=created,
            connections_skipped=skipped,
        )

    def get_connections_for_memory(
        self, memory_id: str, session: Session
    ) -> list[Connection]:
        """Return all connections where memory_id is source or target,
        excluding connections whose *other* endpoint is soft-deleted."""
        connections = list(session.exec(
            select(Connection).where(
                or_(
                    Connection.source_memory_id == memory_id,
                    Connection.target_memory_id == memory_id,
                )
            )
        ).all())

        if not connections:
            return connections

        # Determine the "other" memory IDs and batch-check which are deleted
        other_ids = {
            c.target_memory_id if c.source_memory_id == memory_id else c.source_memory_id
            for c in connections
        }
        deleted_ids: set[str] = set()
        for mid in other_ids:
            mem = session.get(Memory, mid)
            if mem and mem.deleted_at is not None:
                deleted_ids.add(mid)

        if not deleted_ids:
            return connections

        return [
            c for c in connections
            if (c.target_memory_id if c.source_memory_id == memory_id else c.source_memory_id)
            not in deleted_ids
        ]

    def delete_connections_for_memory(
        self, memory_id: str, session: Session
    ) -> int:
        """Delete all connections involving a memory. Returns count deleted."""
        connections = self.get_connections_for_memory(memory_id, session)
        count = len(connections)
        for conn in connections:
            session.delete(conn)
        if count > 0:
            session.commit()
        return count

    def _connection_exists(
        self, session: Session, memory_a: str, memory_b: str
    ) -> bool:
        """Check if a connection exists in either direction between two memories."""
        statement = select(Connection).where(
            or_(
                (Connection.source_memory_id == memory_a)
                & (Connection.target_memory_id == memory_b),
                (Connection.source_memory_id == memory_b)
                & (Connection.target_memory_id == memory_a),
            )
        )
        return session.exec(statement).first() is not None

    def _decrypt_chunk(self, chunk: ScoredChunk) -> str:
        """Decrypt a ScoredChunk's encrypted payload to plaintext string."""
        envelope = EncryptedEnvelope(
            ciphertext=bytes.fromhex(chunk.chunk_encrypted),
            encrypted_dek=bytes.fromhex(chunk.chunk_dek),
            algo=chunk.chunk_algo,
            version=chunk.chunk_version,
        )
        return self.encryption_service.decrypt(envelope).decode("utf-8")

    async def _explain_relationship(
        self, text_a: str, text_b: str
    ) -> tuple[str, str]:
        """Ask LLM to explain the relationship between two memories.

        Returns (explanation_text, relationship_type).
        """
        prompt = (
            "Explain the connection between these two pieces of information. "
            "Be concise (2-3 sentences max).\n\n"
            f"Memory A:\n{text_a}\n\n"
            f"Memory B:\n{text_b}\n\n"
            "Also classify the relationship as exactly one of: "
            "related, caused_by, contradicts, supports, references, extends, summarizes.\n\n"
            "Format your response as:\n"
            "TYPE: <relationship_type>\n"
            "EXPLANATION: <your explanation>"
        )
        conn_system = "You are a knowledge graph assistant that identifies relationships between pieces of information."
        if self.owner_name:
            conn_system = f"You are {self.owner_name}'s memory assistant. " + conn_system
        response = await self.llm_service.generate(
            prompt=prompt,
            system=conn_system,
            temperature=0.3,
        )
        return self._parse_llm_response(response.text)

    @staticmethod
    def _parse_llm_response(text: str) -> tuple[str, str]:
        """Parse TYPE and EXPLANATION from the LLM response.

        Falls back to 'related' if parsing fails.
        """
        rel_type = "related"
        explanation = text.strip()

        for line in text.strip().splitlines():
            line_stripped = line.strip()
            if line_stripped.upper().startswith("TYPE:"):
                candidate = line_stripped[5:].strip().lower()
                if candidate in RELATIONSHIP_TYPES:
                    rel_type = candidate
            elif line_stripped.upper().startswith("EXPLANATION:"):
                explanation = line_stripped[12:].strip()

        return explanation, rel_type
