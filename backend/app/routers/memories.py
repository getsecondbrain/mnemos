from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, func, select

from app.config import get_settings
from app.db import get_session
from app.dependencies import get_encryption_service, require_auth
from app.models.memory import Memory, MemoryCreate, MemoryRead, MemoryUpdate
from app.models.tag import MemoryTag
from app.services.encryption import EncryptedEnvelope, EncryptionService
from app.services.git_ops import GitOpsService

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memories", tags=["memories"])


@router.post("", response_model=MemoryRead, status_code=201)
async def create_memory(
    request: Request,
    body: MemoryCreate,
    _session_id: str = Depends(require_auth),
    enc: EncryptionService = Depends(get_encryption_service),
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

    # Submit background indexing job (search tokens + embeddings)
    if body.content_dek and body.title_dek:
        try:
            content_plain = enc.decrypt(EncryptedEnvelope(
                ciphertext=bytes.fromhex(body.content),
                encrypted_dek=bytes.fromhex(body.content_dek),
                algo=body.encryption_algo or "aes-256-gcm",
                version=body.encryption_version or 1,
            )).decode("utf-8")
            title_plain = enc.decrypt(EncryptedEnvelope(
                ciphertext=bytes.fromhex(body.title),
                encrypted_dek=bytes.fromhex(body.title_dek),
                algo=body.encryption_algo or "aes-256-gcm",
                version=body.encryption_version or 1,
            )).decode("utf-8")

            worker = getattr(request.app.state, "worker", None)
            if worker is not None:
                from app.worker import Job, JobType
                worker.submit_job(
                    Job(
                        job_type=JobType.INGEST,
                        payload={
                            "memory_id": memory.id,
                            "plaintext": content_plain,
                            "title_plaintext": title_plain,
                            "session_id": _session_id,
                        },
                    )
                )
        except Exception:
            logger.warning("Background indexing submit failed for memory %s", memory.id, exc_info=True)

    return memory


@router.get("/stats/timeline")
async def timeline_stats(
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict:
    """Return memory counts grouped by year for the timeline bar."""
    from sqlalchemy import text as sa_text

    # Must use session.execute() (not session.exec()) because exec() calls
    # .scalars() which returns only the first column, dropping the count.
    rows = session.execute(
        sa_text(
            "SELECT strftime('%Y', captured_at) AS year, COUNT(*) AS count "
            "FROM memories GROUP BY 1 ORDER BY 1"
        )
    ).all()

    years = [{"year": int(r[0]), "count": r[1]} for r in rows if r[0]]  # type: ignore[index]
    total = sum(y["count"] for y in years)
    earliest_year = years[0]["year"] if years else None
    latest_year = years[-1]["year"] if years else None

    return {
        "years": years,
        "total": total,
        "earliest_year": earliest_year,
        "latest_year": latest_year,
    }


@router.get("", response_model=list[MemoryRead])
async def list_memories(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    content_type: str | None = None,
    tag_ids: list[str] | None = Query(None, description="Filter by tag IDs (AND logic)"),
    year: int | None = Query(None, description="Filter by captured_at year"),
    order_by: str = Query("captured_at", description="Sort field: captured_at or created_at"),
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[Memory]:
    order_col = Memory.captured_at if order_by == "captured_at" else Memory.created_at
    statement = select(Memory).order_by(order_col.desc())  # type: ignore[union-attr]
    if content_type:
        statement = statement.where(Memory.content_type == content_type)  # type: ignore[arg-type]
    if year is not None:
        start_dt = datetime(year, 1, 1, tzinfo=timezone.utc)
        end_dt = datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        statement = statement.where(Memory.captured_at >= start_dt).where(Memory.captured_at <= end_dt)  # type: ignore[arg-type]
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
    request: Request,
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

    # Delete dependent rows to avoid FK constraint violations
    from sqlalchemy import text as sa_text

    for table in ("search_tokens", "memory_tags", "sources"):
        session.execute(
            sa_text(f"DELETE FROM {table} WHERE memory_id = :mid"),  # noqa: S608
            {"mid": memory_id},
        )
    # Connections reference memory_id via source_memory_id / target_memory_id
    session.execute(
        sa_text(
            "DELETE FROM connections "
            "WHERE source_memory_id = :mid OR target_memory_id = :mid"
        ),
        {"mid": memory_id},
    )
    # Clear parent_id on children (self-referential FK)
    session.execute(
        sa_text("UPDATE memories SET parent_id = NULL WHERE parent_id = :mid"),
        {"mid": memory_id},
    )

    # Clean up Qdrant vectors (best-effort)
    try:
        embedding_svc = getattr(request.app.state, "embedding_service", None)
        if embedding_svc:
            await embedding_svc.delete_memory_vectors(memory_id)
    except Exception:
        logger.warning("Vector cleanup failed for memory %s", memory_id, exc_info=True)

    session.delete(memory)
    session.commit()
