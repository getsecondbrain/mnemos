from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlmodel import Session, func, select

from app.config import get_settings
from app.db import get_session
from app.dependencies import require_auth
from app.models.memory import Memory, MemoryCreate, MemoryRead, MemoryUpdate
from app.models.tag import MemoryTag
from app.services.git_ops import GitOpsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memories", tags=["memories"])


@router.post("", response_model=MemoryRead, status_code=201)
async def create_memory(
    body: MemoryCreate,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Memory:
    memory = Memory(
        title=body.title,
        content=body.content,
        content_type=body.content_type,
        source_type=body.source_type,
        captured_at=body.captured_at or datetime.now(timezone.utc),
        metadata_json=body.metadata_json,
        parent_id=body.parent_id,
        source_id=body.source_id,
        title_dek=body.title_dek,
        content_dek=body.content_dek,
        encryption_algo=body.encryption_algo or "aes-256-gcm",
        encryption_version=body.encryption_version or 1,
    )
    session.add(memory)
    session.commit()
    session.refresh(memory)

    # Git version tracking
    try:
        settings = get_settings()
        git_svc = GitOpsService(settings.data_dir / "git")
        commit_sha = git_svc.commit_memory(
            memory.id,
            memory.content,
            message=f"Create memory {memory.id}",
        )
        if commit_sha:
            memory.git_commit = commit_sha
            session.add(memory)
            session.commit()
            session.refresh(memory)
    except Exception:
        logger.warning("Git commit failed for memory %s", memory.id, exc_info=True)

    return memory


@router.get("", response_model=list[MemoryRead])
async def list_memories(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    content_type: str | None = None,
    tag_ids: list[str] | None = Query(None, description="Filter by tag IDs (AND logic)"),
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[Memory]:
    statement = select(Memory).order_by(Memory.created_at.desc())  # type: ignore[union-attr]
    if content_type:
        statement = statement.where(Memory.content_type == content_type)  # type: ignore[arg-type]
    if tag_ids:
        statement = (
            statement
            .join(MemoryTag, Memory.id == MemoryTag.memory_id)  # type: ignore[arg-type]
            .where(MemoryTag.tag_id.in_(tag_ids))  # type: ignore[union-attr]
            .group_by(Memory.id)  # type: ignore[arg-type]
            .having(func.count(func.distinct(MemoryTag.tag_id)) == len(tag_ids))
        )
    statement = statement.offset(skip).limit(limit)
    return list(session.exec(statement).all())


@router.get("/{memory_id}", response_model=MemoryRead)
async def get_memory(
    memory_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Memory:
    memory = session.get(Memory, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")
    return memory


@router.put("/{memory_id}", response_model=MemoryRead)
async def update_memory(
    memory_id: str,
    body: MemoryUpdate,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Memory:
    memory = session.get(Memory, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    update_data = body.model_dump(exclude_unset=True)
    for key, value in update_data.items():
        setattr(memory, key, value)
    memory.updated_at = datetime.now(timezone.utc)

    session.add(memory)
    session.commit()
    session.refresh(memory)

    # Git version tracking
    try:
        settings = get_settings()
        git_svc = GitOpsService(settings.data_dir / "git")
        commit_sha = git_svc.commit_memory(
            memory.id,
            memory.content,
            message=f"Update memory {memory.id}",
        )
        if commit_sha:
            memory.git_commit = commit_sha
            session.add(memory)
            session.commit()
            session.refresh(memory)
    except Exception:
        logger.warning("Git commit failed for memory %s", memory.id, exc_info=True)

    return memory


@router.delete("/{memory_id}", status_code=204)
async def delete_memory(
    memory_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> None:
    memory = session.get(Memory, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    # Git: remove memory file
    try:
        settings = get_settings()
        git_svc = GitOpsService(settings.data_dir / "git")
        git_svc.delete_memory_file(
            memory.id,
            message=f"Delete memory {memory.id}",
        )
    except Exception:
        logger.warning("Git delete failed for memory %s", memory.id, exc_info=True)

    session.delete(memory)
    session.commit()
