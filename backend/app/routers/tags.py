"""Tag router — CRUD for tags and memory-tag associations."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlmodel import Session, select

from app.db import get_session
from app.dependencies import require_auth
from app.models.memory import Memory
from app.models.tag import (
    MemoryTag,
    MemoryTagRead,
    Tag,
    TagCreate,
    TagRead,
    TagUpdate,
)

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
    session: Session = Depends(get_session),
) -> list[MemoryTagRead]:
    memory = session.get(Memory, memory_id)
    if not memory:
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

    session.commit()
    return _get_memory_tags(memory_id, session)


@memory_tags_router.delete("/{memory_id}/tags/{tag_id}", status_code=204)
async def remove_tag_from_memory(
    memory_id: str,
    tag_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> None:
    memory = session.get(Memory, memory_id)
    if not memory:
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
    if not memory:
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
