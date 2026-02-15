"""Dedicated connection service tests — discovery, existence, parsing, CRUD.

Covers:
- find_connections: high similarity, below threshold, skip existing,
  reverse direction, deduplication, LLM fallback, explanation encryption,
  no-results, generated_by, strength
- get_connections: source, target (bidirectional), empty
- delete_connections: all, count, zero
- _parse_llm_response: all 7 relationship types, fallback, empty, invalid, case
- _connection_exists: forward, reverse, not-exists
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session

from app.models.connection import Connection, RELATIONSHIP_TYPES
from app.models.memory import Memory
from app.services.connections import (
    ConnectionService,
    ConnectionResult,
    SIMILARITY_THRESHOLD,
    MAX_CONNECTIONS_PER_MEMORY,
)
from app.services.embedding import EmbeddingService, ScoredChunk
from app.services.encryption import EncryptedEnvelope, EncryptionService
from app.services.llm import LLMService, LLMResponse, LLMError


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _create_memory(
    session: Session,
    title: str,
    content: str,
    encryption_service: EncryptionService,
    content_type: str = "text",
) -> Memory:
    """Create a Memory with encrypted fields in the test DB."""
    title_env = encryption_service.encrypt(title.encode())
    content_env = encryption_service.encrypt(content.encode())
    memory = Memory(
        title=title_env.ciphertext.hex(),
        content=content_env.ciphertext.hex(),
        title_dek=title_env.encrypted_dek.hex(),
        content_dek=content_env.encrypted_dek.hex(),
        content_type=content_type,
        source_type="manual",
        content_hash=encryption_service.content_hash(content.encode()),
    )
    session.add(memory)
    session.commit()
    session.refresh(memory)
    return memory


def _make_scored_chunk(
    memory_id: str,
    chunk_index: int,
    score: float,
    plaintext: str,
    encryption_service: EncryptionService,
) -> ScoredChunk:
    """Create a ScoredChunk with real encrypted payload."""
    envelope = encryption_service.encrypt(plaintext.encode())
    return ScoredChunk(
        memory_id=memory_id,
        chunk_index=chunk_index,
        score=score,
        chunk_encrypted=envelope.ciphertext.hex(),
        chunk_dek=envelope.encrypted_dek.hex(),
        chunk_algo=envelope.algo,
        chunk_version=envelope.version,
    )


def _add_connection(
    session: Session,
    source_id: str,
    target_id: str,
    encryption_service: EncryptionService,
    rel_type: str = "related",
    strength: float = 0.8,
) -> Connection:
    """Insert a Connection record into the DB."""
    env = encryption_service.encrypt(b"test explanation")
    conn = Connection(
        source_memory_id=source_id,
        target_memory_id=target_id,
        relationship_type=rel_type,
        strength=strength,
        explanation_encrypted=env.ciphertext.hex(),
        explanation_dek=env.encrypted_dek.hex(),
        generated_by="test",
        is_primary=False,
    )
    session.add(conn)
    session.commit()
    session.refresh(conn)
    return conn


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_qdrant():
    qdrant = MagicMock()
    qdrant.collection_exists.return_value = True
    qdrant._points = []

    def _upsert(collection_name, points):
        qdrant._points.extend(points)

    qdrant.upsert.side_effect = _upsert
    return qdrant


@pytest.fixture
def embedding_service(mock_qdrant) -> EmbeddingService:
    return EmbeddingService(
        ollama_url="http://fake-ollama:11434",
        qdrant_client=mock_qdrant,
    )


@pytest.fixture
def mock_llm() -> MagicMock:
    svc = MagicMock(spec=LLMService)
    svc.model = "test-model"
    svc.generate = AsyncMock(
        return_value=LLMResponse(
            text="TYPE: related\nEXPLANATION: Both discuss the same topic",
            model="test-model",
            total_duration_ms=50,
            backend="ollama",
        )
    )
    return svc


@pytest.fixture
def connection_service(
    embedding_service, mock_llm, encryption_service
) -> ConnectionService:
    return ConnectionService(
        embedding_service=embedding_service,
        llm_service=mock_llm,
        encryption_service=encryption_service,
    )


# ===========================================================================
# TestFindConnections
# ===========================================================================


class TestFindConnections:
    @pytest.mark.asyncio
    async def test_creates_connection_for_high_similarity(
        self, connection_service, session, encryption_service
    ) -> None:
        """Two related memories with high similarity create a connection."""
        m1 = _create_memory(session, "Src", "Python tutorial", encryption_service)
        m2 = _create_memory(session, "Tgt", "Python exercises", encryption_service)
        chunk = _make_scored_chunk(m2.id, 0, 0.85, "Python exercises", encryption_service)

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            result = await connection_service.find_connections(
                m1.id, "Python tutorial", session
            )

        assert result.connections_created == 1
        conns = connection_service.get_connections_for_memory(m1.id, session)
        assert len(conns) == 1
        assert conns[0].source_memory_id == m1.id
        assert conns[0].target_memory_id == m2.id

    @pytest.mark.asyncio
    async def test_skips_below_threshold(
        self, connection_service, session, encryption_service
    ) -> None:
        """Score below SIMILARITY_THRESHOLD does not create connection."""
        m1 = _create_memory(session, "A", "Something", encryption_service)
        m2 = _create_memory(session, "B", "Else", encryption_service)
        chunk = _make_scored_chunk(
            m2.id, 0, SIMILARITY_THRESHOLD - 0.01, "Low sim", encryption_service
        )

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            result = await connection_service.find_connections(
                m1.id, "Something", session
            )

        assert result.connections_created == 0

    @pytest.mark.asyncio
    async def test_skips_existing_connection(
        self, connection_service, session, encryption_service
    ) -> None:
        """Existing forward connection A→B → skipped."""
        m1 = _create_memory(session, "E1", "Source", encryption_service)
        m2 = _create_memory(session, "E2", "Target", encryption_service)
        _add_connection(session, m1.id, m2.id, encryption_service)

        chunk = _make_scored_chunk(m2.id, 0, 0.88, "Target", encryption_service)

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            result = await connection_service.find_connections(
                m1.id, "Source", session
            )

        assert result.connections_created == 0
        assert result.connections_skipped == 1

    @pytest.mark.asyncio
    async def test_skips_existing_reverse_direction(
        self, connection_service, session, encryption_service
    ) -> None:
        """Connection exists B→A, new search finds B for A → skipped."""
        m1 = _create_memory(session, "R1", "MemA", encryption_service)
        m2 = _create_memory(session, "R2", "MemB", encryption_service)
        # Connection from m2 → m1 (reverse direction)
        _add_connection(session, m2.id, m1.id, encryption_service)

        chunk = _make_scored_chunk(m2.id, 0, 0.85, "MemB", encryption_service)

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            result = await connection_service.find_connections(
                m1.id, "MemA", session
            )

        assert result.connections_created == 0
        assert result.connections_skipped == 1

    @pytest.mark.asyncio
    async def test_deduplicates_multiple_chunks_same_memory(
        self, connection_service, session, encryption_service
    ) -> None:
        """Two chunks from same memory_id → only one connection created (best score)."""
        m1 = _create_memory(session, "D1", "Source", encryption_service)
        m2 = _create_memory(session, "D2", "Target", encryption_service)
        chunk1 = _make_scored_chunk(m2.id, 0, 0.80, "Chunk A", encryption_service)
        chunk2 = _make_scored_chunk(m2.id, 1, 0.90, "Chunk B", encryption_service)

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk1, chunk2],
        ):
            result = await connection_service.find_connections(
                m1.id, "Source", session
            )

        assert result.connections_created == 1
        conns = connection_service.get_connections_for_memory(m1.id, session)
        assert len(conns) == 1
        # Best score (0.90) should be used
        assert conns[0].strength == 0.90

    @pytest.mark.asyncio
    async def test_llm_error_falls_back_to_related(
        self, connection_service, session, encryption_service
    ) -> None:
        """LLM raises LLMError → connection created with fallback type 'related'."""
        m1 = _create_memory(session, "F1", "Fallback src", encryption_service)
        m2 = _create_memory(session, "F2", "Fallback tgt", encryption_service)
        chunk = _make_scored_chunk(m2.id, 0, 0.82, "Fallback tgt", encryption_service)
        connection_service.llm_service.generate = AsyncMock(
            side_effect=LLMError("Unavailable")
        )

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            result = await connection_service.find_connections(
                m1.id, "Fallback src", session
            )

        assert result.connections_created == 1
        conns = connection_service.get_connections_for_memory(m1.id, session)
        assert conns[0].relationship_type == "related"

    @pytest.mark.asyncio
    async def test_encrypts_explanation(
        self, connection_service, session, encryption_service
    ) -> None:
        """Explanation stored encrypted can be decrypted to original text."""
        m1 = _create_memory(session, "Enc1", "Enc source", encryption_service)
        m2 = _create_memory(session, "Enc2", "Enc target", encryption_service)
        explanation = "Both memories discuss encryption"
        connection_service.llm_service.generate = AsyncMock(
            return_value=LLMResponse(
                text=f"TYPE: supports\nEXPLANATION: {explanation}",
                model="test-model",
                total_duration_ms=50,
                backend="ollama",
            )
        )
        chunk = _make_scored_chunk(m2.id, 0, 0.88, "Enc target", encryption_service)

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            await connection_service.find_connections(
                m1.id, "Enc source", session
            )

        conns = connection_service.get_connections_for_memory(m1.id, session)
        conn = conns[0]
        envelope = EncryptedEnvelope(
            ciphertext=bytes.fromhex(conn.explanation_encrypted),
            encrypted_dek=bytes.fromhex(conn.explanation_dek),
            algo=conn.encryption_algo,
            version=conn.encryption_version,
        )
        decrypted = encryption_service.decrypt(envelope).decode("utf-8")
        assert decrypted == explanation

    @pytest.mark.asyncio
    async def test_no_similar_memories_creates_nothing(
        self, connection_service, session, encryption_service
    ) -> None:
        """search_similar returns [] → 0 created."""
        m1 = _create_memory(session, "Lone", "Lonely memory", encryption_service)

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await connection_service.find_connections(
                m1.id, "Lonely memory", session
            )

        assert result.connections_created == 0
        assert result.connections_skipped == 0

    @pytest.mark.asyncio
    async def test_connection_stores_generated_by(
        self, connection_service, session, encryption_service
    ) -> None:
        """generated_by matches 'llm:ollama/{model}'."""
        m1 = _create_memory(session, "G1", "Gen src", encryption_service)
        m2 = _create_memory(session, "G2", "Gen tgt", encryption_service)
        chunk = _make_scored_chunk(m2.id, 0, 0.85, "Gen tgt", encryption_service)

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            await connection_service.find_connections(
                m1.id, "Gen src", session
            )

        conns = connection_service.get_connections_for_memory(m1.id, session)
        assert conns[0].generated_by == "llm:ollama/test-model"

    @pytest.mark.asyncio
    async def test_connection_strength_matches_score(
        self, connection_service, session, encryption_service
    ) -> None:
        """Connection strength equals the similarity score."""
        m1 = _create_memory(session, "S1", "Strength src", encryption_service)
        m2 = _create_memory(session, "S2", "Strength tgt", encryption_service)
        chunk = _make_scored_chunk(m2.id, 0, 0.91, "Strength tgt", encryption_service)

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            await connection_service.find_connections(
                m1.id, "Strength src", session
            )

        conns = connection_service.get_connections_for_memory(m1.id, session)
        assert conns[0].strength == 0.91


# ===========================================================================
# TestGetConnections
# ===========================================================================


class TestGetConnections:
    def test_returns_connections_as_source(
        self, connection_service, session, encryption_service
    ) -> None:
        """Query by source memory_id returns connection."""
        m1 = _create_memory(session, "Src", "Source", encryption_service)
        m2 = _create_memory(session, "Tgt", "Target", encryption_service)
        _add_connection(session, m1.id, m2.id, encryption_service)

        conns = connection_service.get_connections_for_memory(m1.id, session)
        assert len(conns) == 1
        assert conns[0].source_memory_id == m1.id

    def test_returns_connections_as_target(
        self, connection_service, session, encryption_service
    ) -> None:
        """Query by target memory_id also returns it (bidirectional query)."""
        m1 = _create_memory(session, "Src", "Source", encryption_service)
        m2 = _create_memory(session, "Tgt", "Target", encryption_service)
        _add_connection(session, m1.id, m2.id, encryption_service)

        # Query by target
        conns = connection_service.get_connections_for_memory(m2.id, session)
        assert len(conns) == 1
        assert conns[0].target_memory_id == m2.id

    def test_returns_empty_for_unconnected_memory(
        self, connection_service, session, encryption_service
    ) -> None:
        """Memory with no connections returns empty list."""
        m = _create_memory(session, "Alone", "No connections", encryption_service)
        conns = connection_service.get_connections_for_memory(m.id, session)
        assert len(conns) == 0


# ===========================================================================
# TestDeleteConnections
# ===========================================================================


class TestDeleteConnections:
    def test_deletes_all_connections_for_memory(
        self, connection_service, session, encryption_service
    ) -> None:
        """Delete all connections involving a memory."""
        m1 = _create_memory(session, "D1", "Del src", encryption_service)
        m2 = _create_memory(session, "D2", "Del tgt1", encryption_service)
        m3 = _create_memory(session, "D3", "Del tgt2", encryption_service)
        _add_connection(session, m1.id, m2.id, encryption_service)
        _add_connection(session, m1.id, m3.id, encryption_service)

        connection_service.delete_connections_for_memory(m1.id, session)

        remaining = connection_service.get_connections_for_memory(m1.id, session)
        assert len(remaining) == 0

    def test_delete_returns_count(
        self, connection_service, session, encryption_service
    ) -> None:
        """delete_connections_for_memory returns correct count."""
        m1 = _create_memory(session, "C1", "Count src", encryption_service)
        m2 = _create_memory(session, "C2", "Count tgt1", encryption_service)
        m3 = _create_memory(session, "C3", "Count tgt2", encryption_service)
        _add_connection(session, m1.id, m2.id, encryption_service)
        _add_connection(session, m1.id, m3.id, encryption_service)

        deleted = connection_service.delete_connections_for_memory(m1.id, session)
        assert deleted == 2

    def test_delete_no_connections_returns_zero(
        self, connection_service, session, encryption_service
    ) -> None:
        """No connections to delete → returns 0."""
        m = _create_memory(session, "None", "No conns", encryption_service)
        deleted = connection_service.delete_connections_for_memory(m.id, session)
        assert deleted == 0


# ===========================================================================
# TestParseResponse
# ===========================================================================


class TestParseResponse:
    def test_parses_valid_response_supports(self) -> None:
        text = "TYPE: supports\nEXPLANATION: A provides evidence for B"
        explanation, rel_type = ConnectionService._parse_llm_response(text)
        assert rel_type == "supports"
        assert explanation == "A provides evidence for B"

    def test_parses_valid_response_caused_by(self) -> None:
        text = "TYPE: caused_by\nEXPLANATION: A was caused by B"
        explanation, rel_type = ConnectionService._parse_llm_response(text)
        assert rel_type == "caused_by"
        assert explanation == "A was caused by B"

    def test_parses_valid_response_contradicts(self) -> None:
        text = "TYPE: contradicts\nEXPLANATION: A contradicts B"
        explanation, rel_type = ConnectionService._parse_llm_response(text)
        assert rel_type == "contradicts"

    def test_parses_valid_response_references(self) -> None:
        text = "TYPE: references\nEXPLANATION: A references B"
        explanation, rel_type = ConnectionService._parse_llm_response(text)
        assert rel_type == "references"

    def test_parses_valid_response_extends(self) -> None:
        text = "TYPE: extends\nEXPLANATION: A extends B"
        explanation, rel_type = ConnectionService._parse_llm_response(text)
        assert rel_type == "extends"

    def test_parses_valid_response_summarizes(self) -> None:
        text = "TYPE: summarizes\nEXPLANATION: A summarizes B"
        explanation, rel_type = ConnectionService._parse_llm_response(text)
        assert rel_type == "summarizes"

    def test_fallback_on_malformed_response(self) -> None:
        """No TYPE/EXPLANATION format → fallback to 'related'."""
        text = "These two memories seem connected somehow."
        explanation, rel_type = ConnectionService._parse_llm_response(text)
        assert rel_type == "related"
        assert explanation == text

    def test_fallback_on_empty_response(self) -> None:
        """Empty string → fallback to 'related'."""
        explanation, rel_type = ConnectionService._parse_llm_response("")
        assert rel_type == "related"

    def test_invalid_type_falls_back_to_related(self) -> None:
        """TYPE: something_invalid → related."""
        text = "TYPE: something_invalid\nEXPLANATION: test"
        explanation, rel_type = ConnectionService._parse_llm_response(text)
        assert rel_type == "related"

    def test_case_insensitive_type_parsing(self) -> None:
        """TYPE: SUPPORTS → supports (case-insensitive)."""
        text = "TYPE: SUPPORTS\nEXPLANATION: A supports B"
        explanation, rel_type = ConnectionService._parse_llm_response(text)
        assert rel_type == "supports"


# ===========================================================================
# TestConnectionExistence
# ===========================================================================


class TestConnectionExistence:
    def test_connection_exists_forward(
        self, connection_service, session, encryption_service
    ) -> None:
        """Connection A→B: exists returns True."""
        m1 = _create_memory(session, "A", "Mem A", encryption_service)
        m2 = _create_memory(session, "B", "Mem B", encryption_service)
        _add_connection(session, m1.id, m2.id, encryption_service)

        assert connection_service._connection_exists(session, m1.id, m2.id) is True

    def test_connection_exists_reverse(
        self, connection_service, session, encryption_service
    ) -> None:
        """Connection B→A: checking A,B still returns True."""
        m1 = _create_memory(session, "A", "Mem A", encryption_service)
        m2 = _create_memory(session, "B", "Mem B", encryption_service)
        _add_connection(session, m2.id, m1.id, encryption_service)

        assert connection_service._connection_exists(session, m1.id, m2.id) is True

    def test_connection_not_exists(
        self, connection_service, session, encryption_service
    ) -> None:
        """No connection between A and B → False."""
        m1 = _create_memory(session, "A", "Mem A", encryption_service)
        m2 = _create_memory(session, "B", "Mem B", encryption_service)

        assert connection_service._connection_exists(session, m1.id, m2.id) is False
