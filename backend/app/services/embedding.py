"""Embedding service for Mnemos — text chunking, vector generation, and storage.

Chunks text with overlap, generates embeddings via Ollama nomic-embed-text,
and stores vectors in Qdrant with encrypted chunk payloads.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from uuid import uuid4

import httpx
from qdrant_client import QdrantClient
from qdrant_client.models import Distance, VectorParams, PointStruct, models

from app.services.encryption import EncryptionService

logger = logging.getLogger(__name__)

COLLECTION_NAME = "memories"
VECTOR_SIZE = 768  # nomic-embed-text outputs 768-dimensional vectors
CHUNK_MAX_WORDS = 512
CHUNK_OVERLAP_WORDS = 64


class EmbeddingError(Exception):
    """Raised when embedding generation fails."""


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Result of embedding a memory's text."""

    memory_id: str
    chunks_stored: int
    vector_ids: list[str]


@dataclass(frozen=True, slots=True)
class ScoredChunk:
    """A search result from Qdrant with score and encrypted payload."""

    memory_id: str
    chunk_index: int
    score: float
    chunk_encrypted: str  # hex-encoded ciphertext
    chunk_dek: str  # hex-encoded encrypted DEK
    chunk_algo: str
    chunk_version: int


class EmbeddingService:
    """Generate and store vector embeddings for memories."""

    __slots__ = (
        "ollama_url", "qdrant", "model", "collection",
        "_fallback_url", "_fallback_api_key", "_fallback_model",
    )

    def __init__(
        self,
        ollama_url: str,
        qdrant_client: QdrantClient,
        model: str = "nomic-embed-text",
        fallback_url: str = "",
        fallback_api_key: str = "",
        fallback_embedding_model: str = "",
    ) -> None:
        self.ollama_url = ollama_url
        self.qdrant = qdrant_client
        self.model = model
        self.collection = COLLECTION_NAME
        self._fallback_url = fallback_url.rstrip("/") if fallback_url else ""
        self._fallback_api_key = fallback_api_key
        self._fallback_model = fallback_embedding_model or model

    def ensure_collection(self) -> None:
        """Create the Qdrant collection if it does not already exist."""
        if self.qdrant.collection_exists(self.collection):
            logger.info("Qdrant collection '%s' already exists", self.collection)
            return
        self.qdrant.create_collection(
            collection_name=self.collection,
            vectors_config=VectorParams(size=VECTOR_SIZE, distance=Distance.COSINE),
        )
        logger.info("Created Qdrant collection '%s'", self.collection)

    async def embed_memory(
        self,
        memory_id: str,
        plaintext: str,
        encryption_service: EncryptionService,
    ) -> EmbeddingResult:
        """Chunk, embed, encrypt, and store vectors for a memory."""
        if not plaintext or not plaintext.strip():
            return EmbeddingResult(
                memory_id=memory_id, chunks_stored=0, vector_ids=[]
            )

        chunks = self._chunk_text(plaintext)
        points: list[PointStruct] = []
        vector_ids: list[str] = []

        for i, chunk in enumerate(chunks):
            embedding = await self._get_embedding(chunk)
            envelope = encryption_service.encrypt(chunk.encode("utf-8"))
            point_id = str(uuid4())
            vector_ids.append(point_id)
            points.append(
                PointStruct(
                    id=point_id,
                    vector=embedding,
                    payload={
                        "memory_id": memory_id,
                        "chunk_index": i,
                        "chunk_encrypted": envelope.ciphertext.hex(),
                        "chunk_dek": envelope.encrypted_dek.hex(),
                        "chunk_algo": envelope.algo,
                        "chunk_version": envelope.version,
                    },
                )
            )

        self.qdrant.upsert(collection_name=self.collection, points=points)

        return EmbeddingResult(
            memory_id=memory_id,
            chunks_stored=len(chunks),
            vector_ids=vector_ids,
        )

    async def delete_memory_vectors(self, memory_id: str) -> int:
        """Delete all vectors associated with a memory."""
        self.qdrant.delete(
            collection_name=self.collection,
            points_selector=models.FilterSelector(
                filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="memory_id",
                            match=models.MatchValue(value=memory_id),
                        )
                    ]
                )
            ),
        )
        logger.info("Deleted vectors for memory_id=%s", memory_id)
        return 0

    async def search_similar(
        self,
        query_text: str,
        top_k: int = 5,
        exclude_memory_id: str | None = None,
    ) -> list[ScoredChunk]:
        """Search for similar chunks by embedding the query text."""
        embedding = await self._get_embedding(query_text)

        query_filter: models.Filter | None = None
        if exclude_memory_id is not None:
            query_filter = models.Filter(
                must_not=[
                    models.FieldCondition(
                        key="memory_id",
                        match=models.MatchValue(value=exclude_memory_id),
                    )
                ]
            )

        response = self.qdrant.query_points(
            collection_name=self.collection,
            query=embedding,
            limit=top_k,
            query_filter=query_filter,
        )

        return [
            ScoredChunk(
                memory_id=r.payload["memory_id"],
                chunk_index=r.payload["chunk_index"],
                score=r.score,
                chunk_encrypted=r.payload["chunk_encrypted"],
                chunk_dek=r.payload["chunk_dek"],
                chunk_algo=r.payload["chunk_algo"],
                chunk_version=r.payload["chunk_version"],
            )
            for r in response.points
        ]

    async def _get_embedding(self, text: str) -> list[float]:
        """Get embedding vector, trying Ollama then fallback."""
        try:
            return await self._get_embedding_ollama(text)
        except EmbeddingError:
            if not self._fallback_url:
                raise
            logger.info("Falling back to cloud API for embedding")
            return await self._get_embedding_openai(text)

    async def _get_embedding_ollama(self, text: str) -> list[float]:
        """Get embedding vector from Ollama."""
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/embed",
                    json={"model": self.model, "input": text},
                )
                response.raise_for_status()
                data = response.json()
                # Newer Ollama /api/embed returns {"embeddings": [[...]]}
                if "embeddings" in data:
                    return data["embeddings"][0]
                # Fallback to legacy format
                return data["embedding"]
        except httpx.ConnectError as exc:
            raise EmbeddingError(
                f"Cannot connect to Ollama at {self.ollama_url}: {exc}"
            ) from exc
        except httpx.HTTPStatusError as exc:
            raise EmbeddingError(
                f"Ollama returned HTTP {exc.response.status_code}: {exc}"
            ) from exc
        except (KeyError, IndexError) as exc:
            raise EmbeddingError(
                f"Unexpected response format from Ollama: {exc}"
            ) from exc

    async def _get_embedding_openai(self, text: str) -> list[float]:
        """Get embedding vector from OpenAI-compatible /embeddings endpoint."""
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._fallback_api_key}",
        }
        payload = {
            "model": self._fallback_model,
            "input": text,
        }
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                response = await client.post(
                    f"{self._fallback_url}/embeddings",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                return data["data"][0]["embedding"]
        except httpx.ConnectError as exc:
            raise EmbeddingError(f"Cannot connect to fallback at {self._fallback_url}: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise EmbeddingError(f"Fallback returned HTTP {exc.response.status_code}: {exc}") from exc
        except (KeyError, IndexError) as exc:
            raise EmbeddingError(f"Unexpected response from fallback: {exc}") from exc

    @staticmethod
    def _chunk_text(
        text: str,
        max_words: int = CHUNK_MAX_WORDS,
        overlap: int = CHUNK_OVERLAP_WORDS,
    ) -> list[str]:
        """Split text into overlapping chunks by word count."""
        words = text.split()
        if len(words) <= max_words:
            return [text]

        # Guard against infinite loop
        if overlap >= max_words:
            overlap = 0

        chunks: list[str] = []
        start = 0
        while start < len(words):
            end = start + max_words
            chunks.append(" ".join(words[start:end]))
            start = end - overlap

        return chunks
