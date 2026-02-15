"""Search service — blind index HMAC search + vector search fusion."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from uuid import uuid4

from sqlmodel import Session, text

from app.services.embedding import EmbeddingService
from app.services.encryption import EncryptionService

logger = logging.getLogger(__name__)

DEFAULT_TOP_K = 20


class SearchMode(str, Enum):
    HYBRID = "hybrid"    # blind index + vector (default)
    KEYWORD = "keyword"  # blind index only
    SEMANTIC = "semantic" # vector only


@dataclass(frozen=True, slots=True)
class SearchHit:
    """A single search result with score and source info."""
    memory_id: str
    score: float         # 0.0-1.0 fused relevance score
    keyword_score: float # contribution from blind index match (0 or 1 per token)
    vector_score: float  # cosine similarity from Qdrant
    matched_tokens: int  # how many query tokens matched


@dataclass(frozen=True, slots=True)
class SearchResult:
    """Container for search results."""
    hits: list[SearchHit]
    total: int
    query_tokens_generated: int
    mode: str


class SearchService:
    """Fused search over encrypted memories using blind index + vector similarity."""

    __slots__ = ("embedding_service", "encryption_service")

    KEYWORD_WEIGHT = 0.4  # Weight for blind index score in fusion
    VECTOR_WEIGHT = 0.6   # Weight for vector similarity in fusion

    def __init__(
        self,
        embedding_service: EmbeddingService,
        encryption_service: EncryptionService,
    ) -> None:
        self.embedding_service = embedding_service
        self.encryption_service = encryption_service

    async def search(
        self,
        query: str,
        session: Session,
        mode: SearchMode = SearchMode.HYBRID,
        top_k: int = DEFAULT_TOP_K,
        content_type: str | None = None,
        tag_ids: list[str] | None = None,
    ) -> SearchResult:
        """Execute a fused search query.

        1. Generate HMAC tokens for query keywords.
        2. Match tokens against search_tokens table (blind index).
        3. Embed query and search Qdrant (vector).
        4. Fuse scores with weighted combination.
        5. Deduplicate by memory_id, rank, return top-K.
        """
        keyword_hits: dict[str, float] = {}
        vector_hits: dict[str, float] = {}
        query_token_count = 0

        # --- Blind index search ---
        if mode in (SearchMode.HYBRID, SearchMode.KEYWORD):
            tokens = self.encryption_service.generate_search_tokens(query)
            query_token_count = len(tokens)

            if tokens:
                keyword_hits = self._blind_index_search(
                    tokens, session, content_type, tag_ids
                )

        # --- Vector search ---
        if mode in (SearchMode.HYBRID, SearchMode.SEMANTIC):
            vector_hits = await self._vector_search(
                query, top_k=top_k * 2  # over-fetch for fusion
            )

        # --- Filter vector results by tags ---
        if tag_ids and vector_hits:
            vector_hits = {
                mid: score
                for mid, score in vector_hits.items()
                if mid in self._filter_by_tags(set(vector_hits.keys()), tag_ids, session)
            }

        # --- Fuse results ---
        fused = self._fuse_scores(keyword_hits, vector_hits, query_token_count)

        # Sort by fused score descending, take top_k
        sorted_hits = sorted(fused.values(), key=lambda h: h.score, reverse=True)
        top_hits = sorted_hits[:top_k]

        return SearchResult(
            hits=top_hits,
            total=len(fused),
            query_tokens_generated=query_token_count,
            mode=mode.value,
        )

    def _blind_index_search(
        self,
        token_hmacs: list[str],
        session: Session,
        content_type: str | None = None,
        tag_ids: list[str] | None = None,
    ) -> dict[str, float]:
        """Search the search_tokens table for matching HMACs.

        Returns {memory_id: score} where score = matched_tokens / total_query_tokens.
        """
        if not token_hmacs:
            return {}

        # Build parameterized query for search_tokens table
        placeholders = ", ".join(f":t{i}" for i in range(len(token_hmacs)))
        params: dict[str, str | int] = {f"t{i}": t for i, t in enumerate(token_hmacs)}

        needs_join = content_type or tag_ids

        if needs_join:
            tag_join = ""
            tag_where = ""
            tag_having = ""
            if tag_ids:
                tag_join = "JOIN memory_tags mt ON st.memory_id = mt.memory_id"
                tag_placeholders = ", ".join(f":tag_{i}" for i in range(len(tag_ids)))
                for i, tid in enumerate(tag_ids):
                    params[f"tag_{i}"] = tid
                tag_where = f"AND mt.tag_id IN ({tag_placeholders})"
                tag_having = f"HAVING COUNT(DISTINCT mt.tag_id) = :tag_count"
                params["tag_count"] = len(tag_ids)

            content_join = ""
            content_where = ""
            if content_type:
                content_join = "JOIN memories m ON st.memory_id = m.id"
                content_where = "AND m.content_type = :content_type"
                params["content_type"] = content_type

            sql = f"""
                SELECT st.memory_id, COUNT(DISTINCT st.token_hmac) as match_count
                FROM search_tokens st
                {content_join}
                {tag_join}
                WHERE st.token_hmac IN ({placeholders})
                {content_where}
                {tag_where}
                GROUP BY st.memory_id
                {tag_having}
                ORDER BY match_count DESC
            """
        else:
            sql = f"""
                SELECT memory_id, COUNT(DISTINCT token_hmac) as match_count
                FROM search_tokens
                WHERE token_hmac IN ({placeholders})
                GROUP BY memory_id
                ORDER BY match_count DESC
            """

        result = session.exec(text(sql).bindparams(**params))
        total_tokens = len(token_hmacs)

        hits: dict[str, float] = {}
        for row in result:
            memory_id = row[0]
            match_count = row[1]
            # Normalize: fraction of query tokens that matched
            hits[memory_id] = match_count / total_tokens

        return hits

    def _filter_by_tags(
        self, memory_ids: set[str], tag_ids: list[str], session: Session
    ) -> set[str]:
        """Filter a set of memory IDs to only those having ALL specified tags."""
        if not memory_ids or not tag_ids:
            return memory_ids

        mid_placeholders = ", ".join(f":mid_{i}" for i in range(len(memory_ids)))
        tag_placeholders = ", ".join(f":tag_{i}" for i in range(len(tag_ids)))

        params: dict[str, str | int] = {}
        for i, mid in enumerate(memory_ids):
            params[f"mid_{i}"] = mid
        for i, tid in enumerate(tag_ids):
            params[f"tag_{i}"] = tid
        params["tag_count"] = len(tag_ids)

        sql = f"""
            SELECT memory_id
            FROM memory_tags
            WHERE memory_id IN ({mid_placeholders})
            AND tag_id IN ({tag_placeholders})
            GROUP BY memory_id
            HAVING COUNT(DISTINCT tag_id) = :tag_count
        """

        result = session.exec(text(sql).bindparams(**params))
        return {row[0] for row in result}

    async def _vector_search(
        self,
        query: str,
        top_k: int = 40,
    ) -> dict[str, float]:
        """Search Qdrant for semantically similar memory chunks.

        Returns {memory_id: best_cosine_score} (deduplicated by memory_id,
        keeping highest score).
        """
        scored_chunks = await self.embedding_service.search_similar(
            query_text=query,
            top_k=top_k,
        )

        hits: dict[str, float] = {}
        for chunk in scored_chunks:
            existing = hits.get(chunk.memory_id, 0.0)
            if chunk.score > existing:
                hits[chunk.memory_id] = chunk.score

        return hits

    def _fuse_scores(
        self,
        keyword_hits: dict[str, float],
        vector_hits: dict[str, float],
        query_token_count: int,
    ) -> dict[str, SearchHit]:
        """Combine keyword and vector scores with weighted fusion.

        Formula: fused = KEYWORD_WEIGHT * keyword_score + VECTOR_WEIGHT * vector_score

        Memory IDs that appear in only one source get 0 for the missing source.
        """
        all_memory_ids = set(keyword_hits.keys()) | set(vector_hits.keys())
        fused: dict[str, SearchHit] = {}

        for memory_id in all_memory_ids:
            kw_score = keyword_hits.get(memory_id, 0.0)
            vec_score = vector_hits.get(memory_id, 0.0)

            # Weighted fusion
            combined = (
                self.KEYWORD_WEIGHT * kw_score
                + self.VECTOR_WEIGHT * vec_score
            )

            # Calculate matched token count from keyword_score
            matched_tokens = round(kw_score * query_token_count) if query_token_count > 0 else 0

            fused[memory_id] = SearchHit(
                memory_id=memory_id,
                score=combined,
                keyword_score=kw_score,
                vector_score=vec_score,
                matched_tokens=matched_tokens,
            )

        return fused

    async def index_memory_tokens(
        self,
        memory_id: str,
        plaintext: str,
        session: Session,
        token_type: str = "body",
    ) -> int:
        """Generate and store blind index search tokens for a memory.

        Extracts keywords from plaintext, HMACs each, stores in search_tokens table.
        Returns the number of tokens stored.
        """
        tokens = self.encryption_service.generate_search_tokens(plaintext)
        if not tokens:
            return 0

        # Insert tokens into search_tokens table
        now = datetime.now(timezone.utc).isoformat()
        for token_hmac in tokens:
            session.exec(
                text(
                    "INSERT OR IGNORE INTO search_tokens (id, memory_id, token_hmac, token_type, created_at) "
                    "VALUES (:id, :memory_id, :token_hmac, :token_type, :created_at)"
                ).bindparams(
                    id=str(uuid4()),
                    memory_id=memory_id,
                    token_hmac=token_hmac,
                    token_type=token_type,
                    created_at=now,
                )
            )

        session.commit()
        return len(tokens)

    def delete_memory_tokens(
        self, memory_id: str, session: Session
    ) -> None:
        """Delete all search tokens for a memory."""
        session.exec(
            text("DELETE FROM search_tokens WHERE memory_id = :memory_id").bindparams(
                memory_id=memory_id,
            )
        )
        session.commit()
