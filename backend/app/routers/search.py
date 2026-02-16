"""Search router — blind index + semantic search over encrypted memories."""
from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, Depends, Query
from sqlmodel import Session

from app.db import get_session
from app.dependencies import get_search_service, require_auth
from app.models.person import MemoryPerson
from app.services.search import SearchMode, SearchService

router = APIRouter(prefix="/api/search", tags=["search"])


class SearchHitResponse(BaseModel):
    """Single search result."""
    memory_id: str
    score: float
    keyword_score: float
    vector_score: float
    matched_tokens: int


class SearchResponse(BaseModel):
    """Search response with results and metadata."""
    hits: list[SearchHitResponse]
    total: int
    query_tokens_generated: int
    mode: str


@router.get("", response_model=SearchResponse)
async def search_memories(
    q: str = Query(..., min_length=1, max_length=500, description="Search query"),
    mode: SearchMode = Query(SearchMode.HYBRID, description="Search mode"),
    top_k: int = Query(20, ge=1, le=100, description="Max results to return"),
    content_type: str | None = Query(None, description="Filter by content type"),
    tag_ids: list[str] | None = Query(None, description="Filter by tag IDs (AND logic)"),
    person_ids: list[str] | None = Query(None, description="Filter by person IDs (AND logic)"),
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
    search_service: SearchService = Depends(get_search_service),
) -> SearchResponse:
    """Search memories using blind index (keyword) and/or vector (semantic) search.

    Modes:
    - hybrid: combines keyword and semantic scores (default)
    - keyword: blind index HMAC matching only
    - semantic: vector similarity only
    """
    result = await search_service.search(
        query=q,
        session=session,
        mode=mode,
        top_k=top_k,
        content_type=content_type,
        tag_ids=tag_ids,
        person_ids=person_ids,
    )

    return SearchResponse(
        hits=[
            SearchHitResponse(
                memory_id=hit.memory_id,
                score=hit.score,
                keyword_score=hit.keyword_score,
                vector_score=hit.vector_score,
                matched_tokens=hit.matched_tokens,
            )
            for hit in result.hits
        ],
        total=result.total,
        query_tokens_generated=result.query_tokens_generated,
        mode=result.mode,
    )
