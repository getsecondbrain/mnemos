"""Tests for the RAG service.

Covers the query pipeline (retrieve, decrypt, prompt, generate) and
the streaming variant, using mocked embedding/llm services and a
real encryption service for realistic encrypted chunk payloads.
"""

from __future__ import annotations

import os
from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from app.services.embedding import EmbeddingService, ScoredChunk
from app.services.encryption import EncryptionService
from app.services.llm import LLMResponse, LLMService
from app.services.rag import RAGResult, RAGService


# ── Helpers ───────────────────────────────────────────────────────────


def make_scored_chunk(
    encryption_service: EncryptionService,
    memory_id: str,
    chunk_index: int,
    text: str,
    score: float = 0.9,
) -> ScoredChunk:
    """Create a ScoredChunk with real encrypted data."""
    envelope = encryption_service.encrypt(text.encode("utf-8"))
    return ScoredChunk(
        memory_id=memory_id,
        chunk_index=chunk_index,
        score=score,
        chunk_encrypted=envelope.ciphertext.hex(),
        chunk_dek=envelope.encrypted_dek.hex(),
        chunk_algo=envelope.algo,
        chunk_version=envelope.version,
    )


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(name="encryption_service")
def encryption_service_fixture() -> EncryptionService:
    return EncryptionService(os.urandom(32))


@pytest.fixture(name="mock_embedding_service")
def mock_embedding_service_fixture() -> MagicMock:
    return MagicMock(spec=EmbeddingService)


@pytest.fixture(name="mock_llm_service")
def mock_llm_service_fixture() -> MagicMock:
    svc = MagicMock(spec=LLMService)
    svc.model = "llama3.2:8b"
    return svc


@pytest.fixture(name="rag_service")
def rag_service_fixture(
    mock_embedding_service: MagicMock,
    mock_llm_service: MagicMock,
    encryption_service: EncryptionService,
) -> RAGService:
    return RAGService(
        embedding_service=mock_embedding_service,
        llm_service=mock_llm_service,
        encryption_service=encryption_service,
    )


# ── TestQuery ─────────────────────────────────────────────────────────


class TestQuery:
    """Tests for RAGService.query."""

    @pytest.mark.asyncio
    async def test_successful_rag_query(
        self,
        rag_service: RAGService,
        mock_embedding_service: MagicMock,
        mock_llm_service: MagicMock,
        encryption_service: EncryptionService,
    ) -> None:
        """Full RAG pipeline: 2 chunks retrieved, decrypted, answer generated."""
        chunks = [
            make_scored_chunk(encryption_service, "mem-1", 0, "I went to Paris in 2019."),
            make_scored_chunk(encryption_service, "mem-2", 0, "The Eiffel Tower was beautiful."),
        ]
        mock_embedding_service.search_similar = AsyncMock(return_value=chunks)
        mock_llm_service.generate = AsyncMock(
            return_value=LLMResponse(
                text="You visited Paris in 2019 and saw the Eiffel Tower.",
                model="llama3.2:8b",
                total_duration_ms=3000,
                backend="ollama",
            )
        )

        result = await rag_service.query("Tell me about my trip to Paris")

        assert isinstance(result, RAGResult)
        assert result.answer == "You visited Paris in 2019 and saw the Eiffel Tower."
        assert result.chunks_used == 2
        assert set(result.sources) == {"mem-1", "mem-2"}
        assert result.model == "llama3.2:8b"

    @pytest.mark.asyncio
    async def test_no_relevant_chunks(
        self,
        rag_service: RAGService,
        mock_embedding_service: MagicMock,
        mock_llm_service: MagicMock,
    ) -> None:
        """Empty search results return a default answer without calling the LLM."""
        mock_embedding_service.search_similar = AsyncMock(return_value=[])

        result = await rag_service.query("What is quantum physics?")

        assert "don't have any relevant memories" in result.answer
        assert result.sources == []
        assert result.chunks_used == 0
        mock_llm_service.generate.assert_not_called()

    @pytest.mark.asyncio
    async def test_context_passed_to_llm(
        self,
        rag_service: RAGService,
        mock_embedding_service: MagicMock,
        mock_llm_service: MagicMock,
        encryption_service: EncryptionService,
    ) -> None:
        """Verify the system prompt passed to LLM contains the decrypted chunk text."""
        chunk_text = "My dog's name is Max."
        chunks = [make_scored_chunk(encryption_service, "mem-1", 0, chunk_text)]
        mock_embedding_service.search_similar = AsyncMock(return_value=chunks)
        mock_llm_service.generate = AsyncMock(
            return_value=LLMResponse(text="Your dog is Max.", model="llama3.2:8b", total_duration_ms=1000, backend="ollama")
        )

        await rag_service.query("What is my dog's name?")

        call_kwargs = mock_llm_service.generate.call_args[1]
        assert chunk_text in call_kwargs["system"]

    @pytest.mark.asyncio
    async def test_deduplicates_source_ids(
        self,
        rag_service: RAGService,
        mock_embedding_service: MagicMock,
        mock_llm_service: MagicMock,
        encryption_service: EncryptionService,
    ) -> None:
        """3 chunks where 2 share a memory_id produce only 2 unique source IDs."""
        chunks = [
            make_scored_chunk(encryption_service, "mem-1", 0, "Chunk A part 1"),
            make_scored_chunk(encryption_service, "mem-1", 1, "Chunk A part 2"),
            make_scored_chunk(encryption_service, "mem-2", 0, "Chunk B"),
        ]
        mock_embedding_service.search_similar = AsyncMock(return_value=chunks)
        mock_llm_service.generate = AsyncMock(
            return_value=LLMResponse(text="Answer.", model="llama3.2:8b", total_duration_ms=500, backend="ollama")
        )

        result = await rag_service.query("Tell me everything")

        assert result.chunks_used == 3
        assert len(result.sources) == 2
        assert set(result.sources) == {"mem-1", "mem-2"}


# ── TestStreamQuery ───────────────────────────────────────────────────


class TestStreamQuery:
    """Tests for RAGService.stream_query."""

    @pytest.mark.asyncio
    async def test_returns_iterator_and_sources(
        self,
        rag_service: RAGService,
        mock_embedding_service: MagicMock,
        mock_llm_service: MagicMock,
        encryption_service: EncryptionService,
    ) -> None:
        """stream_query returns a (AsyncIterator, source_ids) tuple."""
        chunks = [
            make_scored_chunk(encryption_service, "mem-1", 0, "Some memory text."),
        ]
        mock_embedding_service.search_similar = AsyncMock(return_value=chunks)

        async def fake_stream(**kwargs):
            yield "Hello "
            yield "world"

        mock_llm_service.stream = MagicMock(return_value=fake_stream())

        token_iter, source_ids = await rag_service.stream_query("Hello?")

        assert source_ids == ["mem-1"]

        tokens = []
        async for token in token_iter:
            tokens.append(token)
        assert "".join(tokens) == "Hello world"

    @pytest.mark.asyncio
    async def test_stream_no_chunks(
        self,
        rag_service: RAGService,
        mock_embedding_service: MagicMock,
    ) -> None:
        """Empty search results stream a default message."""
        mock_embedding_service.search_similar = AsyncMock(return_value=[])

        token_iter, source_ids = await rag_service.stream_query("Unknown topic")

        assert source_ids == []
        tokens = []
        async for token in token_iter:
            tokens.append(token)
        assert "don't have any relevant memories" in "".join(tokens)


# ── TestBuildSystemPrompt ────────────────────────────────────────────


class TestBuildSystemPrompt:
    """Tests for RAGService._build_system_prompt."""

    def _make_rag_service(self, owner_name: str = "", family_context: str = "") -> RAGService:
        """Create a RAGService with mock dependencies for prompt testing."""
        return RAGService(
            embedding_service=MagicMock(),
            llm_service=MagicMock(),
            encryption_service=MagicMock(),
            db_session=None,
            owner_name=owner_name,
            family_context=family_context,
        )

    @patch("app.services.rag.datetime")
    def test_build_system_prompt_with_owner(self, mock_dt: MagicMock) -> None:
        """Owner name and date appear in the system prompt."""
        mock_dt.now.return_value = datetime(2026, 2, 21, 12, 0, 0)
        svc = self._make_rag_service(owner_name="Alice")

        prompt = svc._build_system_prompt("some retrieved context")

        assert "Alice's second brain" in prompt
        assert "Saturday, February 21, 2026" in prompt
        assert "Alice's" in prompt
        assert "some retrieved context" in prompt
        assert "a personal second brain" not in prompt
        assert "their" not in prompt

    def test_build_system_prompt_with_family(self) -> None:
        """Family context block is included when family_context is set."""
        svc = self._make_rag_service(owner_name="Alice", family_context="Bob (spouse); Charlie (child)")

        prompt = svc._build_system_prompt("context here")

        assert "Family context: Bob (spouse); Charlie (child)." in prompt
        assert "Alice's second brain" in prompt

    def test_build_system_prompt_without_owner(self) -> None:
        """Generic fallback when no owner name is set."""
        svc = self._make_rag_service(owner_name="", family_context="")

        prompt = svc._build_system_prompt("context here")

        assert "a personal second brain" in prompt
        assert "their" in prompt
        assert "Family context:" not in prompt
        assert "context here" in prompt
