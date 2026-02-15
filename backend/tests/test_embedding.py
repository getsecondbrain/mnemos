"""Tests for the embedding service.

Covers text chunking, Ollama embedding requests, Qdrant collection management,
memory embedding with encrypted payloads, similarity search, and vector deletion.
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.embedding import (
    CHUNK_MAX_WORDS,
    CHUNK_OVERLAP_WORDS,
    EmbeddingError,
    EmbeddingResult,
    EmbeddingService,
    ScoredChunk,
)
from app.services.encryption import EncryptionService


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(name="mock_qdrant")
def mock_qdrant_fixture() -> MagicMock:
    """A mock QdrantClient."""
    return MagicMock()


@pytest.fixture(name="embedding_service")
def embedding_service_fixture(mock_qdrant: MagicMock) -> EmbeddingService:
    """EmbeddingService with a fake Ollama URL and mock Qdrant."""
    return EmbeddingService(
        ollama_url="http://fake-ollama:11434",
        qdrant_client=mock_qdrant,
        model="nomic-embed-text",
    )


@pytest.fixture(name="encryption_service")
def encryption_service_fixture() -> EncryptionService:
    """EncryptionService with a random master key."""
    return EncryptionService(os.urandom(32))


@pytest.fixture(name="fake_embedding")
def fake_embedding_fixture() -> list[float]:
    """A fake 768-dimensional embedding vector."""
    return [0.1] * 768


# ── TestChunkText ─────────────────────────────────────────────────────


class TestChunkText:
    """Tests for EmbeddingService._chunk_text."""

    def test_short_text_no_chunking(self) -> None:
        """Text with fewer words than max_words returns a single chunk."""
        text = "This is a short text"
        chunks = EmbeddingService._chunk_text(text)
        assert len(chunks) == 1
        assert chunks[0] == text

    def test_exact_max_words(self) -> None:
        """Text with exactly max_words returns a single chunk."""
        words = ["word"] * CHUNK_MAX_WORDS
        text = " ".join(words)
        chunks = EmbeddingService._chunk_text(text)
        assert len(chunks) == 1

    def test_long_text_chunks(self) -> None:
        """Text with 1500 words returns multiple chunks, each <= max_words."""
        words = [f"word{i}" for i in range(1500)]
        text = " ".join(words)
        chunks = EmbeddingService._chunk_text(text)
        assert len(chunks) > 1
        for chunk in chunks:
            assert len(chunk.split()) <= CHUNK_MAX_WORDS

    def test_overlap(self) -> None:
        """Last N words of chunk[i] == first N words of chunk[i+1]."""
        words = [f"w{i}" for i in range(1200)]
        text = " ".join(words)
        chunks = EmbeddingService._chunk_text(text)
        assert len(chunks) >= 2
        for i in range(len(chunks) - 1):
            tail = chunks[i].split()[-CHUNK_OVERLAP_WORDS:]
            head = chunks[i + 1].split()[:CHUNK_OVERLAP_WORDS]
            assert tail == head

    def test_empty_text(self) -> None:
        """Empty string returns single empty-ish chunk."""
        chunks = EmbeddingService._chunk_text("")
        assert len(chunks) == 1
        assert chunks[0] == ""

    def test_overlap_greater_than_max_words(self) -> None:
        """Overlap >= max_words doesn't infinite loop, falls back to no overlap."""
        words = [f"w{i}" for i in range(100)]
        text = " ".join(words)
        # overlap >= max_words should be reset to 0
        chunks = EmbeddingService._chunk_text(text, max_words=10, overlap=15)
        assert len(chunks) > 1
        # With overlap=0, chunks should be non-overlapping
        all_words = []
        for chunk in chunks:
            all_words.extend(chunk.split())
        assert len(all_words) == 100


# ── TestGetEmbedding ──────────────────────────────────────────────────


class TestGetEmbedding:
    """Tests for EmbeddingService._get_embedding."""

    @pytest.mark.asyncio
    async def test_returns_768_dim_vector(
        self, embedding_service: EmbeddingService, fake_embedding: list[float]
    ) -> None:
        """Mock Ollama returns a 768-dim vector."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"embeddings": [fake_embedding]}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await embedding_service._get_embedding("test text")
            assert len(result) == 768
            assert result == fake_embedding

    @pytest.mark.asyncio
    async def test_ollama_unreachable(
        self, embedding_service: EmbeddingService
    ) -> None:
        """Ollama connection error raises EmbeddingError."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(EmbeddingError, match="Cannot connect"):
                await embedding_service._get_embedding("test")

    @pytest.mark.asyncio
    async def test_ollama_error_response(
        self, embedding_service: EmbeddingService
    ) -> None:
        """Ollama HTTP 500 raises EmbeddingError."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=mock_response,
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(EmbeddingError, match="Ollama returned HTTP"):
                await embedding_service._get_embedding("test")


# ── TestEnsureCollection ──────────────────────────────────────────────


class TestEnsureCollection:
    """Tests for EmbeddingService.ensure_collection."""

    def test_creates_collection_if_not_exists(
        self, embedding_service: EmbeddingService, mock_qdrant: MagicMock
    ) -> None:
        """When collection_exists() returns False, create_collection() is called."""
        mock_qdrant.collection_exists.return_value = False
        embedding_service.ensure_collection()
        mock_qdrant.create_collection.assert_called_once()
        call_kwargs = mock_qdrant.create_collection.call_args
        assert call_kwargs[1]["collection_name"] == "memories"

    def test_skips_if_exists(
        self, embedding_service: EmbeddingService, mock_qdrant: MagicMock
    ) -> None:
        """When collection_exists() returns True, create_collection() is NOT called."""
        mock_qdrant.collection_exists.return_value = True
        embedding_service.ensure_collection()
        mock_qdrant.create_collection.assert_not_called()


# ── TestEmbedMemory ───────────────────────────────────────────────────


class TestEmbedMemory:
    """Tests for EmbeddingService.embed_memory."""

    @pytest.mark.asyncio
    async def test_embed_single_chunk(
        self,
        embedding_service: EmbeddingService,
        encryption_service: EncryptionService,
        mock_qdrant: MagicMock,
        fake_embedding: list[float],
    ) -> None:
        """Short text produces 1 point upserted with correct payload shape."""
        with patch.object(
            EmbeddingService, "_get_embedding", new=AsyncMock(return_value=fake_embedding)
        ):
            result = await embedding_service.embed_memory(
                memory_id="mem-1",
                plaintext="A short memory",
                encryption_service=encryption_service,
            )

        assert result.chunks_stored == 1
        assert len(result.vector_ids) == 1
        assert result.memory_id == "mem-1"
        mock_qdrant.upsert.assert_called_once()
        points = mock_qdrant.upsert.call_args[1]["points"]
        assert len(points) == 1
        payload = points[0].payload
        assert payload["memory_id"] == "mem-1"
        assert payload["chunk_index"] == 0
        assert "chunk_encrypted" in payload
        assert "chunk_dek" in payload
        assert payload["chunk_algo"] == "aes-256-gcm"
        assert payload["chunk_version"] == 1

    @pytest.mark.asyncio
    async def test_embed_multiple_chunks(
        self,
        embedding_service: EmbeddingService,
        encryption_service: EncryptionService,
        mock_qdrant: MagicMock,
        fake_embedding: list[float],
    ) -> None:
        """Long text produces multiple points upserted."""
        long_text = " ".join([f"word{i}" for i in range(1500)])

        with patch.object(
            EmbeddingService, "_get_embedding", new=AsyncMock(return_value=fake_embedding)
        ):
            result = await embedding_service.embed_memory(
                memory_id="mem-2",
                plaintext=long_text,
                encryption_service=encryption_service,
            )

        assert result.chunks_stored > 1
        assert len(result.vector_ids) == result.chunks_stored
        points = mock_qdrant.upsert.call_args[1]["points"]
        assert len(points) == result.chunks_stored

    @pytest.mark.asyncio
    async def test_encrypted_chunks_in_payload(
        self,
        embedding_service: EmbeddingService,
        encryption_service: EncryptionService,
        mock_qdrant: MagicMock,
        fake_embedding: list[float],
    ) -> None:
        """Payload contains hex-encoded ciphertext and DEK that can be decrypted."""
        original_text = "This text should be encrypted in the payload"

        with patch.object(
            EmbeddingService, "_get_embedding", new=AsyncMock(return_value=fake_embedding)
        ):
            await embedding_service.embed_memory(
                memory_id="mem-3",
                plaintext=original_text,
                encryption_service=encryption_service,
            )

        points = mock_qdrant.upsert.call_args[1]["points"]
        payload = points[0].payload

        # Verify we can decrypt the stored chunk back to the original text
        from app.services.encryption import EncryptedEnvelope

        envelope = EncryptedEnvelope(
            ciphertext=bytes.fromhex(payload["chunk_encrypted"]),
            encrypted_dek=bytes.fromhex(payload["chunk_dek"]),
            algo=payload["chunk_algo"],
            version=payload["chunk_version"],
        )
        decrypted = encryption_service.decrypt(envelope)
        assert decrypted.decode("utf-8") == original_text

    @pytest.mark.asyncio
    async def test_empty_text_skips(
        self,
        embedding_service: EmbeddingService,
        encryption_service: EncryptionService,
        mock_qdrant: MagicMock,
    ) -> None:
        """Empty/whitespace text returns chunks_stored=0, no upsert."""
        result = await embedding_service.embed_memory(
            memory_id="mem-4",
            plaintext="   ",
            encryption_service=encryption_service,
        )
        assert result.chunks_stored == 0
        assert result.vector_ids == []
        mock_qdrant.upsert.assert_not_called()


# ── TestSearchSimilar ─────────────────────────────────────────────────


class TestSearchSimilar:
    """Tests for EmbeddingService.search_similar."""

    @pytest.mark.asyncio
    async def test_returns_scored_chunks(
        self,
        embedding_service: EmbeddingService,
        mock_qdrant: MagicMock,
        fake_embedding: list[float],
    ) -> None:
        """Mock Qdrant search results are converted to ScoredChunk list."""
        mock_result = MagicMock()
        mock_result.payload = {
            "memory_id": "mem-1",
            "chunk_index": 0,
            "chunk_encrypted": "aabb",
            "chunk_dek": "ccdd",
            "chunk_algo": "aes-256-gcm",
            "chunk_version": 1,
        }
        mock_result.score = 0.95
        mock_qdrant.search.return_value = [mock_result]

        with patch.object(
            EmbeddingService, "_get_embedding", new=AsyncMock(return_value=fake_embedding)
        ):
            results = await embedding_service.search_similar("query text")

        assert len(results) == 1
        assert isinstance(results[0], ScoredChunk)
        assert results[0].memory_id == "mem-1"
        assert results[0].score == 0.95
        assert results[0].chunk_encrypted == "aabb"

    @pytest.mark.asyncio
    async def test_excludes_memory_id(
        self,
        embedding_service: EmbeddingService,
        mock_qdrant: MagicMock,
        fake_embedding: list[float],
    ) -> None:
        """Verify filter is passed when exclude_memory_id is set."""
        mock_qdrant.search.return_value = []

        with patch.object(
            EmbeddingService, "_get_embedding", new=AsyncMock(return_value=fake_embedding)
        ):
            await embedding_service.search_similar(
                "query text", exclude_memory_id="mem-exclude"
            )

        call_kwargs = mock_qdrant.search.call_args[1]
        assert call_kwargs["query_filter"] is not None


# ── TestDeleteMemoryVectors ───────────────────────────────────────────


class TestDeleteMemoryVectors:
    """Tests for EmbeddingService.delete_memory_vectors."""

    @pytest.mark.asyncio
    async def test_deletes_by_memory_id(
        self, embedding_service: EmbeddingService, mock_qdrant: MagicMock
    ) -> None:
        """Verify correct filter is passed to Qdrant delete."""
        await embedding_service.delete_memory_vectors("mem-del")
        mock_qdrant.delete.assert_called_once()
        call_kwargs = mock_qdrant.delete.call_args[1]
        assert call_kwargs["collection_name"] == "memories"
