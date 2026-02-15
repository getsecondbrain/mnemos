"""Cortex integration tests — embedding, search, RAG, connections, worker.

Verifies:
1. Embedding pipeline works (add memories, check Qdrant has vectors)
2. Search returns results (blind index + vector + hybrid)
3. Chat answers questions with source citations (RAG pipeline)
4. Connection graph renders (connections between memories)
5. Worker ingest pipeline orchestrates all Cortex services
"""

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session

from app.models.connection import Connection, RELATIONSHIP_TYPES
from app.models.memory import Memory
from app.services.connections import ConnectionService, ConnectionResult
from app.services.embedding import (
    EmbeddingService,
    EmbeddingResult,
    ScoredChunk,
    VECTOR_SIZE,
)
from app.services.encryption import EncryptedEnvelope, EncryptionService
from app.services.llm import LLMService, LLMResponse, LLMError
from app.services.rag import RAGService, RAGResult
from app.services.search import SearchService, SearchMode, SearchResult, SearchHit
from app.dependencies import (
    get_search_service,
    get_connection_service,
    get_encryption_service,
)
from app.worker import BackgroundWorker, Job, JobType
from app import auth_state


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def mock_qdrant():
    """Mock QdrantClient that stores points in a list for inspection.

    No spec= so arbitrary attributes (search, upsert, etc.) work regardless
    of the installed qdrant-client version.
    """
    qdrant = MagicMock()
    qdrant._points = []
    qdrant.collection_exists.return_value = True

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
def mock_llm_service() -> MagicMock:
    svc = MagicMock(spec=LLMService)
    svc.model = "test-model"
    svc.generate = AsyncMock(
        return_value=LLMResponse(
            text="Test LLM answer based on your memories.",
            model="test-model",
            total_duration_ms=100,
            backend="ollama",
        )
    )

    async def _stream(*args, **kwargs):
        for token in ["Test ", "answer ", "here."]:
            yield token

    svc.stream = _stream
    return svc


@pytest.fixture
def fake_embedding() -> list[float]:
    """768-dim fake embedding vector."""
    return [0.1] * 768


@pytest.fixture
def search_service(embedding_service, encryption_service) -> SearchService:
    return SearchService(
        embedding_service=embedding_service,
        encryption_service=encryption_service,
    )


@pytest.fixture
def connection_service(
    embedding_service, mock_llm_service, encryption_service
) -> ConnectionService:
    return ConnectionService(
        embedding_service=embedding_service,
        llm_service=mock_llm_service,
        encryption_service=encryption_service,
    )


@pytest.fixture
def rag_service(
    embedding_service, mock_llm_service, encryption_service
) -> RAGService:
    return RAGService(
        embedding_service=embedding_service,
        llm_service=mock_llm_service,
        encryption_service=encryption_service,
    )


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


def _mock_ollama_embed(fake_embedding):
    """Return a mock response for httpx.AsyncClient.post returning embeddings."""
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.raise_for_status = MagicMock()
    mock_response.json.return_value = {"embeddings": [fake_embedding]}
    return mock_response


# ===========================================================================
# Class 1: TestEmbeddingPipeline
# ===========================================================================


class TestEmbeddingPipeline:
    """Verify embedding pipeline works — add memories, check Qdrant has vectors."""

    @pytest.mark.asyncio
    async def test_embed_memory_stores_vectors(
        self, embedding_service, encryption_service, mock_qdrant, fake_embedding
    ):
        """Embed a short text, verify Qdrant upsert called with correct payload."""
        mock_resp = _mock_ollama_embed(fake_embedding)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            result = await embedding_service.embed_memory(
                "mem-1", "Hello world this is a test memory", encryption_service
            )

        assert result.chunks_stored == 1
        assert len(result.vector_ids) == 1
        assert result.memory_id == "mem-1"
        assert mock_qdrant.upsert.call_count == 1
        assert len(mock_qdrant._points) == 1

        pt = mock_qdrant._points[0]
        assert pt.payload["memory_id"] == "mem-1"
        assert pt.payload["chunk_index"] == 0
        assert len(pt.payload["chunk_encrypted"]) > 0
        assert len(pt.payload["chunk_dek"]) > 0

    @pytest.mark.asyncio
    async def test_embed_memory_multiple_chunks(
        self, embedding_service, encryption_service, mock_qdrant, fake_embedding
    ):
        """Embed a long text (>512 words), verify multiple chunks produced."""
        long_text = " ".join(["word"] * 600)
        mock_resp = _mock_ollama_embed(fake_embedding)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            result = await embedding_service.embed_memory(
                "mem-long", long_text, encryption_service
            )

        assert result.chunks_stored == 2
        assert len(result.vector_ids) == 2
        indices = {p.payload["chunk_index"] for p in mock_qdrant._points}
        assert indices == {0, 1}

    @pytest.mark.asyncio
    async def test_embed_memory_encrypts_chunks(
        self, embedding_service, encryption_service, mock_qdrant, fake_embedding
    ):
        """Embed text, extract encrypted payload, decrypt it, verify plaintext matches."""
        original_text = "This is a secret memory about cats"
        mock_resp = _mock_ollama_embed(fake_embedding)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            await embedding_service.embed_memory(
                "mem-enc", original_text, encryption_service
            )

        pt = mock_qdrant._points[0]
        envelope = EncryptedEnvelope(
            ciphertext=bytes.fromhex(pt.payload["chunk_encrypted"]),
            encrypted_dek=bytes.fromhex(pt.payload["chunk_dek"]),
            algo=pt.payload["chunk_algo"],
            version=pt.payload["chunk_version"],
        )
        decrypted = encryption_service.decrypt(envelope).decode("utf-8")
        assert decrypted == original_text

    @pytest.mark.asyncio
    async def test_embed_empty_text_noop(
        self, embedding_service, encryption_service
    ):
        """Embed empty/whitespace-only text returns zero chunks."""
        result = await embedding_service.embed_memory(
            "mem-empty", "   ", encryption_service
        )
        assert result.chunks_stored == 0
        assert result.vector_ids == []

    @pytest.mark.asyncio
    async def test_embed_memory_vector_dimensions(
        self, embedding_service, encryption_service, mock_qdrant, fake_embedding
    ):
        """Verify the vector stored in Qdrant is exactly 768 dimensions."""
        mock_resp = _mock_ollama_embed(fake_embedding)
        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            await embedding_service.embed_memory(
                "mem-dim", "Vector dimension test", encryption_service
            )

        pt = mock_qdrant._points[0]
        assert len(pt.vector) == VECTOR_SIZE


# ===========================================================================
# Class 2: TestBlindIndexSearch
# ===========================================================================


class TestBlindIndexSearch:
    """Verify keyword search via HMAC tokens."""

    @pytest.mark.asyncio
    async def test_index_and_search_keyword_match(
        self, search_service, session, encryption_service
    ):
        """Index tokens for a memory, search for a keyword that appears in it."""
        mem = _create_memory(
            session, "Cats", "The quick brown fox jumps over the lazy dog",
            encryption_service,
        )
        await search_service.index_memory_tokens(
            mem.id, "The quick brown fox jumps over the lazy dog", session
        )

        result = await search_service.search(
            "quick fox", session, mode=SearchMode.KEYWORD
        )
        assert len(result.hits) >= 1
        hit_ids = {h.memory_id for h in result.hits}
        assert mem.id in hit_ids
        for h in result.hits:
            if h.memory_id == mem.id:
                assert h.keyword_score > 0

    @pytest.mark.asyncio
    async def test_keyword_search_no_match(
        self, search_service, session, encryption_service
    ):
        """Search for a keyword that was never indexed."""
        _create_memory(
            session, "Test", "Some other content here",
            encryption_service,
        )
        result = await search_service.search(
            "zzzzunmatched", session, mode=SearchMode.KEYWORD
        )
        assert len(result.hits) == 0

    @pytest.mark.asyncio
    async def test_keyword_search_multiple_memories(
        self, search_service, session, encryption_service
    ):
        """Index 3 memories, search for keyword appearing in 2 of them."""
        m1 = _create_memory(session, "A", "Python programming language", encryption_service)
        m2 = _create_memory(session, "B", "Python snake species biology", encryption_service)
        m3 = _create_memory(session, "C", "JavaScript web development", encryption_service)

        await search_service.index_memory_tokens(m1.id, "Python programming language", session)
        await search_service.index_memory_tokens(m2.id, "Python snake species biology", session)
        await search_service.index_memory_tokens(m3.id, "JavaScript web development", session)

        result = await search_service.search(
            "Python", session, mode=SearchMode.KEYWORD
        )
        hit_ids = {h.memory_id for h in result.hits}
        assert m1.id in hit_ids
        assert m2.id in hit_ids
        assert m3.id not in hit_ids

    @pytest.mark.asyncio
    async def test_keyword_search_normalization(
        self, search_service, session, encryption_service
    ):
        """Index 'Hello WORLD' then search 'hello world' (case-insensitive)."""
        mem = _create_memory(session, "Hi", "Hello WORLD test", encryption_service)
        await search_service.index_memory_tokens(mem.id, "Hello WORLD test", session)

        result = await search_service.search(
            "hello world", session, mode=SearchMode.KEYWORD
        )
        hit_ids = {h.memory_id for h in result.hits}
        assert mem.id in hit_ids

    @pytest.mark.asyncio
    async def test_index_memory_tokens_count(
        self, search_service, session, encryption_service
    ):
        """Index a memory with known word count, verify token count returned."""
        mem = _create_memory(session, "T", "Alpha beta gamma delta epsilon", encryption_service)
        count = await search_service.index_memory_tokens(
            mem.id, "Alpha beta gamma delta epsilon", session
        )
        # Words: alpha, beta, gamma, delta, epsilon — all >= 3 chars, all unique
        assert count == 5

    @pytest.mark.asyncio
    async def test_delete_memory_tokens(
        self, search_service, session, encryption_service
    ):
        """Index tokens, delete them, search again."""
        mem = _create_memory(session, "Del", "Unique searchable keyword xylophone", encryption_service)
        await search_service.index_memory_tokens(
            mem.id, "Unique searchable keyword xylophone", session
        )
        # Verify they exist
        result1 = await search_service.search(
            "xylophone", session, mode=SearchMode.KEYWORD
        )
        assert len(result1.hits) == 1

        # Delete
        search_service.delete_memory_tokens(mem.id, session)

        # Verify gone
        result2 = await search_service.search(
            "xylophone", session, mode=SearchMode.KEYWORD
        )
        assert len(result2.hits) == 0


# ===========================================================================
# Class 3: TestVectorSearch
# ===========================================================================


class TestVectorSearch:
    """Verify semantic vector search."""

    @pytest.mark.asyncio
    async def test_semantic_search_returns_results(
        self, search_service, encryption_service
    ):
        """Search semantically returns results from mocked vector search."""
        chunk = _make_scored_chunk("mem-v1", 0, 0.92, "Cats are great pets", encryption_service)

        with patch.object(
            SearchService,
            "_vector_search",
            new_callable=AsyncMock,
            return_value={"mem-v1": 0.92},
        ):
            mock_session = MagicMock(spec=Session)
            result = await search_service.search(
                "pets", mock_session, mode=SearchMode.SEMANTIC
            )

        assert len(result.hits) >= 1
        hit_ids = {h.memory_id for h in result.hits}
        assert "mem-v1" in hit_ids
        for h in result.hits:
            if h.memory_id == "mem-v1":
                assert h.vector_score > 0

    @pytest.mark.asyncio
    async def test_semantic_search_deduplicates_chunks(
        self, search_service, encryption_service
    ):
        """Qdrant returns 3 chunks from same memory — _vector_search deduplicates."""
        # _vector_search internally deduplicates by memory_id, keeping best score
        with patch.object(
            SearchService,
            "_vector_search",
            new_callable=AsyncMock,
            return_value={"mem-dup": 0.95},  # already deduplicated
        ):
            mock_session = MagicMock(spec=Session)
            result = await search_service.search(
                "test", mock_session, mode=SearchMode.SEMANTIC
            )

        assert len(result.hits) == 1
        assert result.hits[0].memory_id == "mem-dup"
        assert result.hits[0].vector_score == 0.95

    @pytest.mark.asyncio
    async def test_semantic_search_excludes_self(
        self, embedding_service, fake_embedding
    ):
        """Verify search_similar passes exclude_memory_id filter."""
        mock_resp = _mock_ollama_embed(fake_embedding)
        embedding_service.qdrant.search.return_value = []

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp):
            await embedding_service.search_similar(
                "test query", top_k=5, exclude_memory_id="mem-self"
            )

        # Verify qdrant.search called with a query_filter containing must_not
        call_args = embedding_service.qdrant.search.call_args
        query_filter = call_args.kwargs.get("query_filter")
        assert query_filter is not None
        assert len(query_filter.must_not) > 0


# ===========================================================================
# Class 4: TestHybridSearch
# ===========================================================================


class TestHybridSearch:
    """Verify fused search (keyword + vector)."""

    def test_hybrid_combines_scores(self, search_service):
        """Memory appears in both keyword and vector results — weighted fusion."""
        keyword_hits = {"mem-1": 1.0}
        vector_hits = {"mem-1": 0.8}
        fused = search_service._fuse_scores(keyword_hits, vector_hits, 1)

        expected = 0.4 * 1.0 + 0.6 * 0.8  # 0.88
        assert abs(fused["mem-1"].score - expected) < 1e-6
        assert fused["mem-1"].keyword_score == 1.0
        assert fused["mem-1"].vector_score == 0.8

    def test_hybrid_keyword_only_hit(self, search_service):
        """Memory found by keyword only (not in vector results)."""
        keyword_hits = {"mem-kw": 0.5}
        vector_hits = {}
        fused = search_service._fuse_scores(keyword_hits, vector_hits, 2)

        assert fused["mem-kw"].keyword_score == 0.5
        assert fused["mem-kw"].vector_score == 0.0
        expected = 0.4 * 0.5
        assert abs(fused["mem-kw"].score - expected) < 1e-6

    def test_hybrid_vector_only_hit(self, search_service):
        """Memory found by vector only (no keyword match)."""
        keyword_hits = {}
        vector_hits = {"mem-vec": 0.9}
        fused = search_service._fuse_scores(keyword_hits, vector_hits, 1)

        assert fused["mem-vec"].vector_score == 0.9
        assert fused["mem-vec"].keyword_score == 0.0
        expected = 0.6 * 0.9
        assert abs(fused["mem-vec"].score - expected) < 1e-6

    @pytest.mark.asyncio
    async def test_hybrid_top_k_respected(self, search_service, session, encryption_service):
        """Insert many memories, search with top_k=3 returns exactly 3."""
        for i in range(10):
            mem = _create_memory(
                session, f"Mem{i}", f"common keyword variation{i}",
                encryption_service,
            )
            await search_service.index_memory_tokens(
                mem.id, f"common keyword variation{i}", session
            )

        # Patch _vector_search to return empty (keyword only path)
        with patch.object(
            SearchService,
            "_vector_search",
            new_callable=AsyncMock,
            return_value={},
        ):
            result = await search_service.search(
                "common keyword", session, mode=SearchMode.HYBRID, top_k=3
            )

        assert len(result.hits) == 3

    @pytest.mark.asyncio
    async def test_hybrid_ranking_order(self, search_service):
        """Multiple hits with different scores are sorted descending."""
        keyword_hits = {"mem-a": 1.0, "mem-b": 0.5, "mem-c": 0.0}
        vector_hits = {"mem-a": 0.5, "mem-b": 1.0, "mem-c": 0.9}
        fused = search_service._fuse_scores(keyword_hits, vector_hits, 2)

        sorted_hits = sorted(fused.values(), key=lambda h: h.score, reverse=True)
        for i in range(len(sorted_hits) - 1):
            assert sorted_hits[i].score >= sorted_hits[i + 1].score


# ===========================================================================
# Class 5: TestRAGPipeline
# ===========================================================================


class TestRAGPipeline:
    """Verify chat answers with source citations."""

    @pytest.mark.asyncio
    async def test_rag_query_returns_answer_with_sources(
        self, rag_service, encryption_service
    ):
        """Query with relevant chunks available returns answer and sources."""
        chunks = [
            _make_scored_chunk("mem-r1", 0, 0.9, "Memory about dogs", encryption_service),
            _make_scored_chunk("mem-r2", 0, 0.85, "Memory about cats", encryption_service),
        ]

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=chunks,
        ):
            result = await rag_service.query("Tell me about pets")

        assert isinstance(result, RAGResult)
        assert len(result.answer) > 0
        assert "mem-r1" in result.sources
        assert "mem-r2" in result.sources
        assert result.chunks_used == 2

    @pytest.mark.asyncio
    async def test_rag_query_no_results(self, rag_service):
        """Query with no matching chunks returns fallback answer."""
        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[],
        ):
            result = await rag_service.query("Something obscure")

        assert "don't have any relevant memories" in result.answer
        assert result.sources == []
        assert result.chunks_used == 0

    @pytest.mark.asyncio
    async def test_rag_context_passed_to_llm(
        self, rag_service, mock_llm_service, encryption_service
    ):
        """Verify decrypted chunks appear in LLM system prompt."""
        chunks = [
            _make_scored_chunk("mem-ctx", 0, 0.9, "Top secret info about cheese", encryption_service),
        ]

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=chunks,
        ):
            await rag_service.query("cheese info")

        call_kwargs = mock_llm_service.generate.call_args
        system_prompt = call_kwargs.kwargs.get("system") or call_kwargs[1].get("system")
        assert "Top secret info about cheese" in system_prompt

    @pytest.mark.asyncio
    async def test_rag_source_deduplication(
        self, rag_service, encryption_service
    ):
        """Multiple chunks from same memory result in 1 source entry."""
        chunks = [
            _make_scored_chunk("mem-dedup", 0, 0.95, "Chunk A", encryption_service),
            _make_scored_chunk("mem-dedup", 1, 0.90, "Chunk B", encryption_service),
            _make_scored_chunk("mem-dedup", 2, 0.85, "Chunk C", encryption_service),
        ]

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=chunks,
        ):
            result = await rag_service.query("test")

        assert len(result.sources) == 1
        assert result.sources[0] == "mem-dedup"
        assert result.chunks_used == 3

    @pytest.mark.asyncio
    async def test_rag_stream_query_yields_tokens(
        self, rag_service, encryption_service
    ):
        """Test streaming variant returns tokens and source_ids."""
        chunks = [
            _make_scored_chunk("mem-stream", 0, 0.9, "Stream test data", encryption_service),
        ]

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=chunks,
        ):
            token_stream, source_ids = await rag_service.stream_query("stream test")

        assert "mem-stream" in source_ids
        collected = []
        async for token in token_stream:
            collected.append(token)
        assert len(collected) > 0

    @pytest.mark.asyncio
    async def test_rag_stream_empty_chunks(self, rag_service):
        """Stream with no matching chunks yields fallback message."""
        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[],
        ):
            token_stream, source_ids = await rag_service.stream_query("nothing")

        assert source_ids == []
        collected = []
        async for token in token_stream:
            collected.append(token)
        full_text = "".join(collected)
        assert "don't have any relevant memories" in full_text


# ===========================================================================
# Class 6: TestConnectionDiscovery
# ===========================================================================


class TestConnectionDiscovery:
    """Verify neural link generation between memories."""

    @pytest.mark.asyncio
    async def test_find_connections_creates_link(
        self, connection_service, session, encryption_service
    ):
        """Two related memories with high similarity create a connection."""
        m1 = _create_memory(session, "Src", "Python programming tutorial", encryption_service)
        m2 = _create_memory(session, "Tgt", "Python coding exercises", encryption_service)

        chunk = _make_scored_chunk(m2.id, 0, 0.85, "Python coding exercises", encryption_service)

        connection_service.llm_service.generate = AsyncMock(
            return_value=LLMResponse(
                text="TYPE: related\nEXPLANATION: Both discuss Python programming",
                model="test-model",
                total_duration_ms=50,
                backend="ollama",
            )
        )

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            result = await connection_service.find_connections(
                m1.id, "Python programming tutorial", session
            )

        assert result.connections_created == 1
        conns = connection_service.get_connections_for_memory(m1.id, session)
        assert len(conns) == 1
        assert conns[0].source_memory_id == m1.id
        assert conns[0].target_memory_id == m2.id
        assert conns[0].relationship_type == "related"
        assert conns[0].strength == 0.85

    @pytest.mark.asyncio
    async def test_find_connections_below_threshold(
        self, connection_service, session, encryption_service
    ):
        """Similar memory with score below threshold does not create connection."""
        m1 = _create_memory(session, "A", "Something", encryption_service)
        m2 = _create_memory(session, "B", "Something else", encryption_service)

        chunk = _make_scored_chunk(m2.id, 0, 0.5, "Low similarity", encryption_service)

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
    async def test_find_connections_skips_existing(
        self, connection_service, session, encryption_service
    ):
        """Connection already exists between two memories — skipped."""
        m1 = _create_memory(session, "E1", "Existing link source", encryption_service)
        m2 = _create_memory(session, "E2", "Existing link target", encryption_service)

        env = encryption_service.encrypt(b"Pre-existing")
        conn = Connection(
            source_memory_id=m1.id,
            target_memory_id=m2.id,
            relationship_type="related",
            strength=0.9,
            explanation_encrypted=env.ciphertext.hex(),
            explanation_dek=env.encrypted_dek.hex(),
            generated_by="test",
            is_primary=False,
        )
        session.add(conn)
        session.commit()

        chunk = _make_scored_chunk(m2.id, 0, 0.88, "Existing link target", encryption_service)

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            result = await connection_service.find_connections(
                m1.id, "Existing link source", session
            )

        assert result.connections_created == 0
        assert result.connections_skipped == 1

    @pytest.mark.asyncio
    async def test_find_connections_llm_fallback(
        self, connection_service, session, encryption_service
    ):
        """LLM raises LLMError — connection still created with fallback type."""
        m1 = _create_memory(session, "F1", "Fallback source", encryption_service)
        m2 = _create_memory(session, "F2", "Fallback target", encryption_service)

        chunk = _make_scored_chunk(m2.id, 0, 0.80, "Fallback target", encryption_service)
        connection_service.llm_service.generate = AsyncMock(side_effect=LLMError("Unavailable"))

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            result = await connection_service.find_connections(
                m1.id, "Fallback source", session
            )

        assert result.connections_created == 1
        conns = connection_service.get_connections_for_memory(m1.id, session)
        assert conns[0].relationship_type == "related"
        assert "llm:ollama/" in conns[0].generated_by

    @pytest.mark.asyncio
    async def test_find_connections_encrypts_explanation(
        self, connection_service, session, encryption_service
    ):
        """Verify explanation stored encrypted can be decrypted to original."""
        m1 = _create_memory(session, "Enc1", "Encryption test source", encryption_service)
        m2 = _create_memory(session, "Enc2", "Encryption test target", encryption_service)

        explanation_text = "Both memories discuss encryption testing"
        connection_service.llm_service.generate = AsyncMock(
            return_value=LLMResponse(
                text=f"TYPE: related\nEXPLANATION: {explanation_text}",
                model="test-model",
                total_duration_ms=50,
                backend="ollama",
            )
        )

        chunk = _make_scored_chunk(m2.id, 0, 0.90, "Encryption test target", encryption_service)

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            await connection_service.find_connections(
                m1.id, "Encryption test source", session
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
        assert decrypted == explanation_text

    def test_get_connections_for_memory(
        self, connection_service, session, encryption_service
    ):
        """Create connections, then query them — returns both."""
        m1 = _create_memory(session, "G1", "Graph source", encryption_service)
        m2 = _create_memory(session, "G2", "Graph target 1", encryption_service)
        m3 = _create_memory(session, "G3", "Graph target 2", encryption_service)

        env = encryption_service.encrypt(b"link")
        for target in [m2, m3]:
            conn = Connection(
                source_memory_id=m1.id,
                target_memory_id=target.id,
                relationship_type="related",
                strength=0.8,
                explanation_encrypted=env.ciphertext.hex(),
                explanation_dek=env.encrypted_dek.hex(),
                generated_by="test",
                is_primary=False,
            )
            session.add(conn)
        session.commit()

        conns = connection_service.get_connections_for_memory(m1.id, session)
        assert len(conns) == 2

    def test_delete_connections_for_memory(
        self, connection_service, session, encryption_service
    ):
        """Create connections, delete them — empty after."""
        m1 = _create_memory(session, "D1", "Delete source", encryption_service)
        m2 = _create_memory(session, "D2", "Delete target", encryption_service)

        env = encryption_service.encrypt(b"to delete")
        conn = Connection(
            source_memory_id=m1.id,
            target_memory_id=m2.id,
            relationship_type="supports",
            strength=0.7,
            explanation_encrypted=env.ciphertext.hex(),
            explanation_dek=env.encrypted_dek.hex(),
            generated_by="test",
            is_primary=False,
        )
        session.add(conn)
        session.commit()

        deleted = connection_service.delete_connections_for_memory(m1.id, session)
        assert deleted == 1

        remaining = connection_service.get_connections_for_memory(m1.id, session)
        assert len(remaining) == 0

    def test_parse_llm_response_valid(self):
        """Valid LLM response with TYPE and EXPLANATION lines."""
        text = "TYPE: supports\nEXPLANATION: Memory A provides evidence for Memory B"
        explanation, rel_type = ConnectionService._parse_llm_response(text)
        assert rel_type == "supports"
        assert explanation == "Memory A provides evidence for Memory B"

    def test_parse_llm_response_fallback(self):
        """LLM response without proper format — falls back to 'related'."""
        text = "These two memories seem connected somehow."
        explanation, rel_type = ConnectionService._parse_llm_response(text)
        assert rel_type == "related"
        assert explanation == text


# ===========================================================================
# Class 7: TestSearchRouter
# ===========================================================================


class TestSearchRouter:
    """Verify /api/search HTTP endpoint."""

    def test_search_endpoint_returns_results(self, client, session, encryption_service):
        """GET /api/search?q=test returns proper response structure."""
        mock_search_svc = MagicMock(spec=SearchService)
        mock_search_svc.search = AsyncMock(
            return_value=SearchResult(
                hits=[
                    SearchHit(
                        memory_id="mem-api-1",
                        score=0.75,
                        keyword_score=0.5,
                        vector_score=0.9,
                        matched_tokens=2,
                    )
                ],
                total=1,
                query_tokens_generated=3,
                mode="hybrid",
            )
        )

        from app.main import app as fastapi_app

        fastapi_app.dependency_overrides[get_search_service] = lambda: mock_search_svc

        resp = client.get("/api/search?q=test")
        assert resp.status_code == 200
        data = resp.json()
        assert "hits" in data
        assert "total" in data
        assert "mode" in data
        assert data["total"] == 1
        assert data["hits"][0]["memory_id"] == "mem-api-1"

        fastapi_app.dependency_overrides.pop(get_search_service, None)

    def test_search_endpoint_validates_query(self, client):
        """GET /api/search without q param returns 422."""
        mock_search_svc = MagicMock(spec=SearchService)
        mock_search_svc.search = AsyncMock(
            return_value=SearchResult(hits=[], total=0, query_tokens_generated=0, mode="hybrid")
        )

        from app.main import app as fastapi_app

        fastapi_app.dependency_overrides[get_search_service] = lambda: mock_search_svc

        # Missing q param entirely triggers 422
        resp = client.get("/api/search")
        assert resp.status_code == 422

        fastapi_app.dependency_overrides.pop(get_search_service, None)

    def test_search_endpoint_mode_param(self, client):
        """GET /api/search?q=test&mode=semantic sets mode correctly."""
        mock_search_svc = MagicMock(spec=SearchService)
        mock_search_svc.search = AsyncMock(
            return_value=SearchResult(
                hits=[], total=0, query_tokens_generated=0, mode="semantic"
            )
        )

        from app.main import app as fastapi_app

        fastapi_app.dependency_overrides[get_search_service] = lambda: mock_search_svc

        resp = client.get("/api/search?q=test&mode=semantic")
        assert resp.status_code == 200
        assert resp.json()["mode"] == "semantic"

        fastapi_app.dependency_overrides.pop(get_search_service, None)

    def test_search_endpoint_requires_auth(self, session):
        """GET without auth header returns 401/403."""
        from fastapi.testclient import TestClient
        from app.main import app as fastapi_app
        from app.db import get_session

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override

        with TestClient(fastapi_app) as tc:
            resp = tc.get("/api/search?q=test")
            assert resp.status_code in (401, 403)

        fastapi_app.dependency_overrides.clear()


# ===========================================================================
# Class 8: TestCortexRouter
# ===========================================================================


class TestCortexRouter:
    """Verify /api/cortex connection CRUD endpoints."""

    def test_list_connections_endpoint(self, client, session, encryption_service):
        """GET /api/cortex/connections/{memory_id} returns connections."""
        mem = _create_memory(session, "List", "List connections test", encryption_service)

        env = encryption_service.encrypt(b"explanation")
        conn = Connection(
            source_memory_id=mem.id,
            target_memory_id=mem.id,
            relationship_type="related",
            strength=0.8,
            explanation_encrypted=env.ciphertext.hex(),
            explanation_dek=env.encrypted_dek.hex(),
            generated_by="test",
            is_primary=False,
        )
        session.add(conn)
        session.commit()

        mock_conn_svc = MagicMock(spec=ConnectionService)
        mock_conn_svc.get_connections_for_memory.return_value = [conn]

        from app.main import app as fastapi_app

        fastapi_app.dependency_overrides[get_connection_service] = lambda: mock_conn_svc

        resp = client.get(f"/api/cortex/connections/{mem.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert len(data) == 1
        assert data[0]["relationship_type"] == "related"

        fastapi_app.dependency_overrides.pop(get_connection_service, None)

    def test_create_connection_endpoint(self, client, session, encryption_service):
        """POST /api/cortex/connections creates a connection."""
        m1 = _create_memory(session, "Src", "Source memory", encryption_service)
        m2 = _create_memory(session, "Tgt", "Target memory", encryption_service)

        env = encryption_service.encrypt(b"user explanation")
        body = {
            "source_memory_id": m1.id,
            "target_memory_id": m2.id,
            "relationship_type": "supports",
            "strength": 0.9,
            "explanation_encrypted": env.ciphertext.hex(),
            "explanation_dek": env.encrypted_dek.hex(),
            "encryption_algo": env.algo,
            "encryption_version": env.version,
            "generated_by": "user",
            "is_primary": True,
        }

        resp = client.post("/api/cortex/connections", json=body)
        assert resp.status_code == 201
        data = resp.json()
        assert data["source_memory_id"] == m1.id
        assert data["target_memory_id"] == m2.id
        assert data["relationship_type"] == "supports"

    def test_create_connection_invalid_type(self, client, session, encryption_service):
        """POST with invalid relationship_type returns 422."""
        m1 = _create_memory(session, "Inv1", "Invalid type test 1", encryption_service)
        m2 = _create_memory(session, "Inv2", "Invalid type test 2", encryption_service)

        env = encryption_service.encrypt(b"invalid test")
        body = {
            "source_memory_id": m1.id,
            "target_memory_id": m2.id,
            "relationship_type": "invalid_bogus_type",
            "strength": 0.5,
            "explanation_encrypted": env.ciphertext.hex(),
            "explanation_dek": env.encrypted_dek.hex(),
            "generated_by": "user",
            "is_primary": True,
        }

        resp = client.post("/api/cortex/connections", json=body)
        assert resp.status_code == 422

    def test_delete_connection_endpoint(self, client, session, encryption_service):
        """DELETE /api/cortex/connections/{id} removes connection."""
        mem = _create_memory(session, "DelEp", "Delete endpoint test", encryption_service)

        env = encryption_service.encrypt(b"to be deleted")
        conn = Connection(
            source_memory_id=mem.id,
            target_memory_id=mem.id,
            relationship_type="related",
            strength=0.5,
            explanation_encrypted=env.ciphertext.hex(),
            explanation_dek=env.encrypted_dek.hex(),
            generated_by="test",
            is_primary=False,
        )
        session.add(conn)
        session.commit()
        session.refresh(conn)

        resp = client.delete(f"/api/cortex/connections/{conn.id}")
        assert resp.status_code == 204

        assert session.get(Connection, conn.id) is None

    def test_analyze_endpoint(self, client, session, encryption_service):
        """POST /api/cortex/analyze/{memory_id} triggers analysis."""
        mem = _create_memory(
            session, "Analyze", "Content to analyze for connections", encryption_service
        )

        mock_conn_svc = MagicMock(spec=ConnectionService)
        mock_conn_svc.find_connections = AsyncMock(
            return_value=ConnectionResult(
                memory_id=mem.id,
                connections_created=2,
                connections_skipped=1,
            )
        )

        from app.main import app as fastapi_app

        fastapi_app.dependency_overrides[get_connection_service] = lambda: mock_conn_svc
        fastapi_app.dependency_overrides[get_encryption_service] = lambda: encryption_service

        resp = client.post(f"/api/cortex/analyze/{mem.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["memory_id"] == mem.id
        assert data["connections_created"] == 2
        assert data["connections_skipped"] == 1

        fastapi_app.dependency_overrides.pop(get_connection_service, None)
        fastapi_app.dependency_overrides.pop(get_encryption_service, None)


# ===========================================================================
# Class 9: TestWorkerIngestPipeline
# ===========================================================================


class TestWorkerIngestPipeline:
    """Verify background worker orchestration."""

    def test_ingest_job_embeds_and_indexes(self, master_key, fake_embedding):
        """Submit ingest job, verify embedding + search token + connection ops run."""
        session_id = "test-worker-session"
        memory_id = "mem-worker-1"
        auth_state.store_master_key(session_id, master_key)

        mock_qdrant = MagicMock()
        mock_qdrant._points = []

        def _upsert(collection_name, points):
            mock_qdrant._points.extend(points)

        mock_qdrant.upsert.side_effect = _upsert
        mock_qdrant.search.return_value = []

        embedding_svc = EmbeddingService(
            ollama_url="http://fake:11434",
            qdrant_client=mock_qdrant,
        )
        llm_svc = MagicMock(spec=LLMService)
        llm_svc.model = "test-model"
        llm_svc.generate = AsyncMock(
            return_value=LLMResponse(text="TYPE: related\nEXPLANATION: test", model="test-model", total_duration_ms=10, backend="ollama")
        )

        worker = BackgroundWorker(
            embedding_service=embedding_svc,
            llm_service=llm_svc,
            db_url="sqlite://",
        )

        mock_resp = _mock_ollama_embed(fake_embedding)

        with patch("httpx.AsyncClient.post", new_callable=AsyncMock, return_value=mock_resp), \
             patch("app.db.engine", create=True) as mock_engine:

            # Create a mock session via the context manager that _process_ingest uses
            mock_db_session = MagicMock(spec=Session)
            mock_db_session.exec.return_value = iter([])

            with patch("app.worker.Session") as mock_session_cls:
                mock_session_cls.return_value.__enter__ = MagicMock(return_value=mock_db_session)
                mock_session_cls.return_value.__exit__ = MagicMock(return_value=False)

                worker._process_ingest({
                    "memory_id": memory_id,
                    "plaintext": "Test content for embedding",
                    "title_plaintext": "Test Title",
                    "session_id": session_id,
                })

        assert mock_qdrant.upsert.call_count >= 1

        auth_state.wipe_master_key(session_id)

    def test_ingest_job_expired_session(self, engine):
        """Submit job with expired session_id — skipped, no services called."""
        mock_qdrant = MagicMock()
        embedding_svc = EmbeddingService(
            ollama_url="http://fake:11434",
            qdrant_client=mock_qdrant,
        )
        llm_svc = MagicMock(spec=LLMService)
        llm_svc.model = "test-model"

        worker = BackgroundWorker(
            embedding_service=embedding_svc,
            llm_service=llm_svc,
            db_url="sqlite://",
        )

        with patch("app.db.engine", engine):
            worker._process_ingest({
                "memory_id": "mem-expired",
                "plaintext": "Should not be processed",
                "title_plaintext": "Expired",
                "session_id": "nonexistent-session",
            })

        assert mock_qdrant.upsert.call_count == 0


# ===========================================================================
# Class 10: TestEndToEnd
# ===========================================================================


class TestEndToEnd:
    """Full pipeline integration tests wiring multiple services together."""

    @pytest.mark.asyncio
    async def test_add_memories_search_find_chat(
        self, session, encryption_service, mock_qdrant, mock_llm_service
    ):
        """Create memories, index tokens, search by keyword, then RAG returns answer."""
        embedding_svc = EmbeddingService(
            ollama_url="http://fake:11434",
            qdrant_client=mock_qdrant,
        )
        search_svc = SearchService(
            embedding_service=embedding_svc,
            encryption_service=encryption_service,
        )
        rag_svc = RAGService(
            embedding_service=embedding_svc,
            llm_service=mock_llm_service,
            encryption_service=encryption_service,
        )

        m1 = _create_memory(
            session, "Cooking", "Italian pasta recipe with tomato sauce",
            encryption_service,
        )
        m2 = _create_memory(
            session, "Travel", "Trip to Rome visiting the Colosseum",
            encryption_service,
        )
        m3 = _create_memory(
            session, "Music", "Learning guitar chords and scales",
            encryption_service,
        )

        await search_svc.index_memory_tokens(
            m1.id, "Italian pasta recipe with tomato sauce", session
        )
        await search_svc.index_memory_tokens(
            m2.id, "Trip to Rome visiting the Colosseum", session
        )
        await search_svc.index_memory_tokens(
            m3.id, "Learning guitar chords and scales", session
        )

        # Search by keyword
        result = await search_svc.search(
            "Italian pasta", session, mode=SearchMode.KEYWORD
        )
        hit_ids = {h.memory_id for h in result.hits}
        assert m1.id in hit_ids
        assert m3.id not in hit_ids

        # RAG query with mocked vector search
        chunk = _make_scored_chunk(
            m1.id, 0, 0.9, "Italian pasta recipe with tomato sauce",
            encryption_service,
        )

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk],
        ):
            rag_result = await rag_svc.query("Tell me about Italian food")

        assert len(rag_result.answer) > 0
        assert m1.id in rag_result.sources
        assert rag_result.chunks_used == 1

    @pytest.mark.asyncio
    async def test_multiple_memories_connection_graph(
        self, session, encryption_service, mock_qdrant, mock_llm_service
    ):
        """Create memories, run connection discovery, verify graph structure."""
        embedding_svc = EmbeddingService(
            ollama_url="http://fake:11434",
            qdrant_client=mock_qdrant,
        )
        conn_svc = ConnectionService(
            embedding_service=embedding_svc,
            llm_service=mock_llm_service,
            encryption_service=encryption_service,
        )

        m1 = _create_memory(session, "ML1", "Machine learning neural networks", encryption_service)
        m2 = _create_memory(session, "ML2", "Deep learning convolutional nets", encryption_service)
        m3 = _create_memory(session, "Cook", "Baking chocolate cookies", encryption_service)

        chunk_m2 = _make_scored_chunk(m2.id, 0, 0.88, "Deep learning convolutional nets", encryption_service)

        mock_llm_service.generate = AsyncMock(
            return_value=LLMResponse(
                text="TYPE: related\nEXPLANATION: Both discuss neural network architectures",
                model="test-model",
                total_duration_ms=50,
                backend="ollama",
            )
        )

        with patch(
            "app.services.embedding.EmbeddingService.search_similar",
            new_callable=AsyncMock,
            return_value=[chunk_m2],
        ):
            result = await conn_svc.find_connections(
                m1.id, "Machine learning neural networks", session
            )

        assert result.connections_created == 1

        conns = conn_svc.get_connections_for_memory(m1.id, session)
        assert len(conns) == 1
        assert conns[0].target_memory_id == m2.id

        conns_m3 = conn_svc.get_connections_for_memory(m3.id, session)
        assert len(conns_m3) == 0
