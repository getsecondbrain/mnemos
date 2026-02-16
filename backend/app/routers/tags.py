"""Tag router — CRUD for tags and memory-tag associations."""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from uuid import uuid4

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select, text

from app.db import get_session
from app.dependencies import get_encryption_service, require_auth
from app.models.memory import Memory
from app.models.tag import (
    MemoryTag,
    MemoryTagRead,
    Tag,
    TagCreate,
    TagRead,
    TagUpdate,
)
from app.services.encryption import EncryptionService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/tags", tags=["tags"])
memory_tags_router = APIRouter(prefix="/api/memories", tags=["tags"])


# --- Tag CRUD ---


@router.post("", response_model=TagRead, status_code=201)
async def create_tag(
    body: TagCreate,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> TagRead:
    name = body.name.strip().lower()
    if not name:
        raise HTTPException(status_code=422, detail="Tag name cannot be empty")

    existing = session.exec(select(Tag).where(Tag.name == name)).first()
    if existing:
        raise HTTPException(status_code=409, detail=f"Tag '{name}' already exists")

    tag = Tag(name=name, color=body.color)
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return TagRead.model_validate(tag)


@router.get("", response_model=list[TagRead])
async def list_tags(
    q: str | None = Query(None, description="Filter tags by name substring"),
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[TagRead]:
    statement = select(Tag).order_by(Tag.name)  # type: ignore[arg-type]
    if q:
        statement = statement.where(Tag.name.contains(q.strip().lower()))  # type: ignore[union-attr]
    tags = session.exec(statement).all()
    return [TagRead.model_validate(t) for t in tags]


@router.get("/{tag_id}", response_model=TagRead)
async def get_tag(
    tag_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> TagRead:
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")
    return TagRead.model_validate(tag)


@router.put("/{tag_id}", response_model=TagRead)
async def update_tag(
    tag_id: str,
    body: TagUpdate,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> TagRead:
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    if body.name is not None:
        new_name = body.name.strip().lower()
        if not new_name:
            raise HTTPException(status_code=422, detail="Tag name cannot be empty")
        if new_name != tag.name:
            existing = session.exec(select(Tag).where(Tag.name == new_name)).first()
            if existing:
                raise HTTPException(
                    status_code=409, detail=f"Tag '{new_name}' already exists"
                )
            tag.name = new_name

    if body.color is not None:
        tag.color = body.color

    tag.updated_at = datetime.now(timezone.utc)
    session.add(tag)
    session.commit()
    session.refresh(tag)
    return TagRead.model_validate(tag)


@router.delete("/{tag_id}", status_code=204)
async def delete_tag(
    tag_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> None:
    tag = session.get(Tag, tag_id)
    if not tag:
        raise HTTPException(status_code=404, detail="Tag not found")

    # Delete all memory-tag associations first
    associations = session.exec(
        select(MemoryTag).where(MemoryTag.tag_id == tag_id)
    ).all()
    for assoc in associations:
        session.delete(assoc)

    session.delete(tag)
    session.commit()


# --- Memory-Tag association endpoints ---


class AddTagsRequest(BaseModel):
    tag_ids: list[str]


@memory_tags_router.post(
    "/{memory_id}/tags", response_model=list[MemoryTagRead]
)
async def add_tags_to_memory(
    memory_id: str,
    body: AddTagsRequest,
    _session_id: str = Depends(require_auth),
    enc: EncryptionService = Depends(get_encryption_service),
    session: Session = Depends(get_session),
) -> list[MemoryTagRead]:
    memory = session.get(Memory, memory_id)
    if not memory or memory.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")

    for tid in body.tag_ids:
        tag = session.get(Tag, tid)
        if not tag:
            raise HTTPException(
                status_code=404, detail=f"Tag '{tid}' not found"
            )
        # Skip if association already exists
        existing = session.exec(
            select(MemoryTag).where(
                MemoryTag.memory_id == memory_id,
                MemoryTag.tag_id == tid,
            )
        ).first()
        if not existing:
            session.add(MemoryTag(memory_id=memory_id, tag_id=tid))
            # Index tag name as search tokens so chat/search can find this memory by tag
            _index_tag_tokens(memory_id, tag.name, enc, session)

    session.commit()
    return _get_memory_tags(memory_id, session)


@memory_tags_router.delete("/{memory_id}/tags/{tag_id}", status_code=204)
async def remove_tag_from_memory(
    memory_id: str,
    tag_id: str,
    _session_id: str = Depends(require_auth),
    enc: EncryptionService = Depends(get_encryption_service),
    session: Session = Depends(get_session),
) -> None:
    memory = session.get(Memory, memory_id)
    if not memory or memory.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")

    assoc = session.exec(
        select(MemoryTag).where(
            MemoryTag.memory_id == memory_id,
            MemoryTag.tag_id == tag_id,
        )
    ).first()
    if not assoc:
        raise HTTPException(
            status_code=404, detail="Tag association not found"
        )

    # Remove tag search tokens for this memory
    tag = session.get(Tag, tag_id)
    if tag:
        _remove_tag_tokens(memory_id, tag.name, enc, session)

    session.delete(assoc)
    session.commit()


@memory_tags_router.get(
    "/{memory_id}/tags", response_model=list[MemoryTagRead]
)
async def list_memory_tags(
    memory_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[MemoryTagRead]:
    memory = session.get(Memory, memory_id)
    if not memory or memory.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _get_memory_tags(memory_id, session)


def _get_memory_tags(memory_id: str, session: Session) -> list[MemoryTagRead]:
    """Fetch all tags for a memory, joining MemoryTag with Tag."""
    results = session.exec(
        select(MemoryTag, Tag)
        .where(MemoryTag.memory_id == memory_id)
        .where(MemoryTag.tag_id == Tag.id)
        .order_by(Tag.name)  # type: ignore[arg-type]
    ).all()
    return [
        MemoryTagRead(
            tag_id=tag.id,
            tag_name=tag.name,
            tag_color=tag.color,
            created_at=mt.created_at,
        )
        for mt, tag in results
    ]


@router.post("/reindex", status_code=200)
async def reindex_all_tag_tokens(
    _session_id: str = Depends(require_auth),
    enc: EncryptionService = Depends(get_encryption_service),
    session: Session = Depends(get_session),
) -> dict:
    """One-time backfill: index search tokens for all existing memory-tag associations."""
    results = session.exec(
        select(MemoryTag, Tag)
        .where(MemoryTag.tag_id == Tag.id)
    ).all()

    count = 0
    for mt, tag in results:
        _index_tag_tokens(mt.memory_id, tag.name, enc, session)
        count += 1

    session.commit()
    return {"reindexed": count}


def _index_tag_tokens(
    memory_id: str,
    tag_name: str,
    enc: EncryptionService,
    session: Session,
) -> None:
    """Index a tag's name as search tokens (type='tag') for a memory."""
    tokens = enc.generate_search_tokens(tag_name)
    now = datetime.now(timezone.utc).isoformat()
    for token_hmac in tokens:
        session.execute(
            text(
                "INSERT OR IGNORE INTO search_tokens (id, memory_id, token_hmac, token_type, created_at) "
                "VALUES (:id, :memory_id, :token_hmac, :token_type, :created_at)"
            ).bindparams(
                id=str(uuid4()),
                memory_id=memory_id,
                token_hmac=token_hmac,
                token_type="tag",
                created_at=now,
            )
        )


def _remove_tag_tokens(
    memory_id: str,
    tag_name: str,
    enc: EncryptionService,
    session: Session,
) -> None:
    """Remove search tokens for a specific tag from a memory."""
    tokens = enc.generate_search_tokens(tag_name)
    for token_hmac in tokens:
        session.execute(
            text(
                "DELETE FROM search_tokens "
                "WHERE memory_id = :memory_id AND token_hmac = :token_hmac AND token_type = 'tag'"
            ).bindparams(
                memory_id=memory_id,
                token_hmac=token_hmac,
            )
        )
