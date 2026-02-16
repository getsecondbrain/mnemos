"""Suggestions router — list, accept, and dismiss AI-generated suggestions."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, select

from app.db import get_session
from app.dependencies import get_encryption_service, require_auth
from app.models.memory import Memory
from app.models.suggestion import Suggestion, SuggestionRead, SuggestionStatus, SuggestionType
from app.models.tag import MemoryTag, Tag
from app.routers.tags import _index_tag_tokens
from app.services.encryption import EncryptedEnvelope, EncryptionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/suggestions", tags=["suggestions"])


@router.get("", response_model=list[SuggestionRead])
async def list_suggestions(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[SuggestionRead]:
    """List pending suggestions, most recent first. Excludes suggestions for soft-deleted memories."""
    results = session.exec(
        select(Suggestion)
        .join(Memory, Suggestion.memory_id == Memory.id)  # type: ignore[arg-type]
        .where(Suggestion.status == SuggestionStatus.PENDING.value)
        .where(Memory.deleted_at == None)  # noqa: E711
        .order_by(Suggestion.created_at.desc())  # type: ignore[union-attr]
        .offset(skip)
        .limit(limit)
    ).all()
    return [SuggestionRead.model_validate(s) for s in results]


@router.post("/{suggestion_id}/accept", response_model=SuggestionRead)
async def accept_suggestion(
    suggestion_id: str,
    _session_id: str = Depends(require_auth),
    enc: EncryptionService = Depends(get_encryption_service),
    session: Session = Depends(get_session),
) -> SuggestionRead:
    """Accept a suggestion and apply its side effects."""
    suggestion = session.get(Suggestion, suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if suggestion.status != SuggestionStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="Suggestion already processed")

    # Apply side effects based on suggestion type
    if suggestion.suggestion_type == SuggestionType.TAG_SUGGEST.value:
        try:
            # Verify the referenced memory still exists and is not deleted
            memory = session.get(Memory, suggestion.memory_id)
            if not memory or memory.deleted_at is not None:
                raise HTTPException(
                    status_code=404,
                    detail="Referenced memory no longer exists",
                )

            # Decrypt suggestion content to get the tag name
            envelope = EncryptedEnvelope(
                ciphertext=bytes.fromhex(suggestion.content_encrypted),
                encrypted_dek=bytes.fromhex(suggestion.content_dek),
                algo=suggestion.encryption_algo,
                version=suggestion.encryption_version,
            )
            tag_name = enc.decrypt(envelope).decode("utf-8").strip().lower()
            if not tag_name:
                raise HTTPException(status_code=422, detail="Suggested tag name is empty")

            # Find or create the tag
            tag = session.exec(select(Tag).where(Tag.name == tag_name)).first()
            if not tag:
                tag = Tag(name=tag_name)
                session.add(tag)
                session.flush()

            # Create the memory-tag association if it doesn't exist
            existing = session.exec(
                select(MemoryTag).where(
                    MemoryTag.memory_id == suggestion.memory_id,
                    MemoryTag.tag_id == tag.id,
                )
            ).first()
            if not existing:
                session.add(MemoryTag(memory_id=suggestion.memory_id, tag_id=tag.id))
                _index_tag_tokens(suggestion.memory_id, tag.name, enc, session)
        except HTTPException:
            raise
        except Exception:
            logger.exception("Failed to apply tag suggestion %s", suggestion_id)
            raise HTTPException(status_code=500, detail="Failed to apply suggestion")

    # For enrich_prompt, digest, pattern: no automatic action, just mark accepted

    suggestion.status = SuggestionStatus.ACCEPTED.value
    suggestion.updated_at = datetime.now(timezone.utc)
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return SuggestionRead.model_validate(suggestion)


@router.post("/{suggestion_id}/dismiss", response_model=SuggestionRead)
async def dismiss_suggestion(
    suggestion_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> SuggestionRead:
    """Dismiss a suggestion."""
    suggestion = session.get(Suggestion, suggestion_id)
    if not suggestion:
        raise HTTPException(status_code=404, detail="Suggestion not found")

    if suggestion.status != SuggestionStatus.PENDING.value:
        raise HTTPException(status_code=409, detail="Suggestion already processed")

    suggestion.status = SuggestionStatus.DISMISSED.value
    suggestion.updated_at = datetime.now(timezone.utc)
    session.add(suggestion)
    session.commit()
    session.refresh(suggestion)
    return SuggestionRead.model_validate(suggestion)
