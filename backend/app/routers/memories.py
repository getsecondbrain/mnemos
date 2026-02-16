from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from math import cos, radians
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlmodel import Session, func, or_, select

from app.config import get_settings
from app.db import get_session
from app.dependencies import get_encryption_service, get_llm_service, require_auth
from app.models.memory import Memory, MemoryChildInfo, MemoryCreate, MemoryRead, MemoryTagInfo, MemoryUpdate
from app.models.person import MemoryPerson
from app.models.reflection import ReflectionPrompt
from app.models.tag import MemoryTag, Tag
from app.services.encryption import EncryptedEnvelope, EncryptionService
from app.services.git_ops import GitOpsService
from app.services.llm import LLMError, LLMService

from sqlalchemy.exc import IntegrityError

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/memories", tags=["memories"])


def _attach_tags(memories: list[Memory], session: Session) -> list[MemoryRead]:
    """Convert Memory models to MemoryRead with tags and children populated."""
    if not memories:
        return []
    memory_ids = [m.id for m in memories]
    rows = session.exec(
        select(MemoryTag.memory_id, Tag.id, Tag.name, Tag.color)
        .join(Tag, MemoryTag.tag_id == Tag.id)  # type: ignore[arg-type]
        .where(MemoryTag.memory_id.in_(memory_ids))  # type: ignore[union-attr]
    ).all()
    tags_by_memory: dict[str, list[MemoryTagInfo]] = {}
    for mid, tid, tname, tcolor in rows:
        tags_by_memory.setdefault(mid, []).append(
            MemoryTagInfo(tag_id=tid, tag_name=tname, tag_color=tcolor)
        )
    # Fetch children for these memories (exclude soft-deleted)
    child_rows = session.exec(
        select(Memory)
        .where(Memory.parent_id.in_(memory_ids))  # type: ignore[union-attr]
        .where(Memory.deleted_at == None)  # noqa: E711
    ).all()
    children_by_parent: dict[str, list[MemoryChildInfo]] = {}
    for child in child_rows:
        children_by_parent.setdefault(child.parent_id, []).append(  # type: ignore[arg-type]
            MemoryChildInfo(id=child.id, source_id=child.source_id, content_type=child.content_type)
        )
    result = []
    for m in memories:
        read = MemoryRead.model_validate(m)
        read.tags = tags_by_memory.get(m.id, [])
        read.children = children_by_parent.get(m.id, [])
        result.append(read)
    return result


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
        visibility=body.visibility,
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
    visibility: Literal["public", "private", "all"] = Query("public", description="Filter: public, private, or all"),
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict:
    """Return memory counts grouped by year for the timeline bar."""
    from sqlalchemy import text as sa_text

    # Must use session.execute() (not session.exec()) because exec() calls
    # .scalars() which returns only the first column, dropping the count.
    sql = (
        "SELECT strftime('%Y', captured_at) AS year, COUNT(*) AS count "
        "FROM memories WHERE parent_id IS NULL AND deleted_at IS NULL"
    )
    if visibility != "all":
        sql += " AND visibility = :vis"
    sql += " GROUP BY 1 ORDER BY 1"

    if visibility != "all":
        rows = session.execute(sa_text(sql), {"vis": visibility}).all()
    else:
        rows = session.execute(sa_text(sql)).all()

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


@router.get("/on-this-day", response_model=list[MemoryRead])
async def on_this_day(
    visibility: Literal["public", "private", "all"] = Query("public", description="Filter: public, private, or all"),
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[MemoryRead]:
    """Return up to 10 memories from previous years on today's month+day."""
    from sqlalchemy import text as sa_text

    now = datetime.now(timezone.utc)
    current_month = now.strftime("%m")
    current_day = now.strftime("%d")
    current_year = now.strftime("%Y")

    sql = (
        "SELECT id FROM memories "
        "WHERE parent_id IS NULL AND deleted_at IS NULL "
        "AND strftime('%m', captured_at) = :month "
        "AND strftime('%d', captured_at) = :day "
        "AND strftime('%Y', captured_at) < :year"
    )
    params: dict[str, str] = {
        "month": current_month,
        "day": current_day,
        "year": current_year,
    }
    if visibility != "all":
        sql += " AND visibility = :vis"
        params["vis"] = visibility
    sql += " ORDER BY captured_at DESC LIMIT 10"

    rows = session.execute(sa_text(sql), params).all()
    if not rows:
        return []

    memory_ids = [r[0] for r in rows]
    results = session.exec(
        select(Memory).where(Memory.id.in_(memory_ids))  # type: ignore[union-attr]
    ).all()
    # Restore the captured_at DESC order from the raw SQL query
    mem_by_id = {m.id: m for m in results}
    memories = [mem_by_id[mid] for mid in memory_ids if mid in mem_by_id]

    return _attach_tags(memories, session)


@router.get("/{memory_id}/reflect")
async def reflect_on_memory(
    memory_id: str,
    _session_id: str = Depends(require_auth),
    enc: EncryptionService = Depends(get_encryption_service),
    llm: LLMService = Depends(get_llm_service),
    session: Session = Depends(get_session),
) -> dict:
    """Generate a short LLM reflection prompt for a memory.

    Returns a cached result if one exists and is less than 24 hours old.
    Falls back gracefully — returns 503 if LLM is unavailable.
    """
    from datetime import timedelta

    # 1. Check memory exists and is not soft-deleted
    memory = session.get(Memory, memory_id)
    if not memory or memory.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")

    # 2. Refuse to send decrypted content to a cloud fallback endpoint.
    #    Check BEFORE decryption to avoid producing plaintext unnecessarily.
    if llm.has_fallback:
        logger.warning(
            "Reflect endpoint blocked: cloud LLM fallback is configured. "
            "Decrypted content will not be sent to a third-party API."
        )
        raise HTTPException(
            status_code=503,
            detail="Reflection generation unavailable",
        )

    # 3. Check cache (24-hour TTL)
    cached = session.exec(
        select(ReflectionPrompt).where(ReflectionPrompt.memory_id == memory_id)
    ).first()

    now = datetime.now(timezone.utc)
    if cached:
        # SQLite stores datetimes without tzinfo; treat as UTC for comparison
        cached_at = cached.generated_at.replace(tzinfo=timezone.utc) if cached.generated_at.tzinfo is None else cached.generated_at
        if (now - cached_at) < timedelta(hours=24):
            return {"prompt": cached.prompt_text}

    # 4. Decrypt memory content
    if memory.content_dek:
        try:
            plaintext = enc.decrypt(EncryptedEnvelope(
                ciphertext=bytes.fromhex(memory.content),
                encrypted_dek=bytes.fromhex(memory.content_dek),
                algo=memory.encryption_algo,
                version=memory.encryption_version,
            )).decode("utf-8")
        except Exception:
            logger.warning("Failed to decrypt memory %s for reflection", memory_id)
            raise HTTPException(status_code=503, detail="Reflection generation unavailable")
    else:
        # Unencrypted/legacy memory — use content directly
        plaintext = memory.content

    # 5. Determine the year for the system prompt
    year = memory.captured_at.year

    # 6. Call LLM (local Ollama only — no fallback configured)
    system_prompt = (
        f"Given this memory from {year}, generate a single short question "
        "(under 15 words) that invites the user to reflect on it. "
        "Be warm, personal, and specific to the content. "
        "Return ONLY the question, nothing else."
    )
    try:
        response = await llm.generate(
            prompt=plaintext[:2000],  # Limit to avoid token overflow
            system=system_prompt,
            temperature=0.8,
        )
        prompt_text = response.text.strip().removeprefix('"').removesuffix('"')
    except Exception:
        logger.warning("LLM unavailable for reflection on memory %s", memory_id, exc_info=True)
        raise HTTPException(status_code=503, detail="Reflection generation unavailable")

    # 7. Cache the result (upsert with race-condition handling)
    if cached:
        cached.prompt_text = prompt_text
        cached.generated_at = now
        session.add(cached)
    else:
        session.add(ReflectionPrompt(
            memory_id=memory_id,
            prompt_text=prompt_text,
            generated_at=now,
        ))
    try:
        session.commit()
    except IntegrityError:
        # Another request already inserted a row for this memory_id.
        # Roll back and return our generated prompt (the cached row is fine).
        session.rollback()
        logger.debug("Reflection cache race: another request won for memory %s", memory_id)

    return {"prompt": prompt_text}


@router.get("", response_model=list[MemoryRead])
async def list_memories(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    content_type: str | None = None,
    tag_ids: list[str] | None = Query(None, description="Filter by tag IDs (AND logic)"),
    person_ids: list[str] | None = Query(None, description="Filter by person IDs (AND logic)"),
    year: int | None = Query(None, description="Filter by captured_at year"),
    order_by: str = Query("captured_at", description="Sort field: captured_at or created_at"),
    visibility: Literal["public", "private", "all"] = Query("public", description="Filter: public, private, or all"),
    date_from: str | None = Query(None, description="ISO date lower bound (inclusive), e.g. 2024-01-01"),
    date_to: str | None = Query(None, description="ISO date upper bound (inclusive), e.g. 2024-12-31"),
    near: str | None = Query(None, description="Filter by location: lat,lng,radius_km (e.g. '48.8566,2.3522,10')"),
    has_location: bool | None = Query(None, description="If true, only return memories with lat/lng set"),
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[Memory]:
    order_col = Memory.captured_at if order_by == "captured_at" else Memory.created_at
    statement = select(Memory).where(Memory.parent_id == None).where(Memory.deleted_at == None).order_by(order_col.desc())  # type: ignore[union-attr]  # noqa: E711
    if content_type:
        types = [t.strip() for t in content_type.split(",")]
        if len(types) == 1:
            statement = statement.where(Memory.content_type == types[0])  # type: ignore[arg-type]
        else:
            statement = statement.where(Memory.content_type.in_(types))  # type: ignore[union-attr]
    if year is not None:
        start_dt = datetime(year, 1, 1, tzinfo=timezone.utc)
        end_dt = datetime(year, 12, 31, 23, 59, 59, tzinfo=timezone.utc)
        statement = statement.where(Memory.captured_at >= start_dt).where(Memory.captured_at <= end_dt)  # type: ignore[arg-type]
    if date_from is not None:
        try:
            dt_from = datetime.fromisoformat(date_from)
            if dt_from.tzinfo is None:
                dt_from = dt_from.replace(tzinfo=timezone.utc)
            statement = statement.where(Memory.captured_at >= dt_from)  # type: ignore[arg-type]
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date_from format. Use ISO date (YYYY-MM-DD)")
    if date_to is not None:
        try:
            dt_to = datetime.fromisoformat(date_to)
            if dt_to.tzinfo is None:
                dt_to = dt_to.replace(tzinfo=timezone.utc)
            # Only apply inclusive-day heuristic for date-only strings (no "T"),
            # e.g. "2024-12-31" → include the entire day via < next day.
            # Explicit datetimes like "2024-12-31T00:00:00" are treated as-is.
            is_date_only = "T" not in date_to
            if is_date_only:
                dt_to = dt_to + timedelta(days=1)
                statement = statement.where(Memory.captured_at < dt_to)  # type: ignore[arg-type]
            else:
                statement = statement.where(Memory.captured_at <= dt_to)  # type: ignore[arg-type]
        except ValueError:
            raise HTTPException(status_code=422, detail="Invalid date_to format. Use ISO date (YYYY-MM-DD)")
    if visibility != "all":
        statement = statement.where(Memory.visibility == visibility)
    if tag_ids:
        statement = (
            statement
            .join(MemoryTag, Memory.id == MemoryTag.memory_id)  # type: ignore[arg-type]
            .where(MemoryTag.tag_id.in_(tag_ids))  # type: ignore[union-attr]
            .group_by(Memory.id)  # type: ignore[arg-type]
            .having(func.count(func.distinct(MemoryTag.tag_id)) == len(tag_ids))
        )
    if person_ids:
        person_subq = (
            select(MemoryPerson.memory_id)
            .where(MemoryPerson.person_id.in_(person_ids))  # type: ignore[union-attr]
            .group_by(MemoryPerson.memory_id)
            .having(func.count(func.distinct(MemoryPerson.person_id)) == len(person_ids))
        )
        statement = statement.where(Memory.id.in_(person_subq))  # type: ignore[arg-type]
    if near is not None:
        parts = near.split(",")
        if len(parts) != 3:
            raise HTTPException(422, "near must be lat,lng,radius_km (e.g. '48.8566,2.3522,10')")
        try:
            near_lat = float(parts[0])
            near_lng = float(parts[1])
            near_radius_km = float(parts[2])
        except ValueError:
            raise HTTPException(422, "near values must be numeric")
        if not (-90 <= near_lat <= 90) or not (-180 <= near_lng <= 180):
            raise HTTPException(422, "near lat/lng out of range")
        if near_radius_km <= 0:
            raise HTTPException(422, "near radius must be positive")

        # Pre-filter: bounding box (cheap, narrows results before expensive trig)
        # 1 degree latitude ~ 111 km
        lat_delta = near_radius_km / 111.0
        lng_delta = near_radius_km / (111.0 * max(cos(radians(near_lat)), 0.01))
        statement = statement.where(Memory.latitude != None)  # noqa: E711
        statement = statement.where(Memory.longitude != None)  # noqa: E711
        statement = statement.where(Memory.latitude >= near_lat - lat_delta)
        statement = statement.where(Memory.latitude <= near_lat + lat_delta)
        lng_min = near_lng - lng_delta
        lng_max = near_lng + lng_delta
        if lng_min < -180 or lng_max > 180:
            # Bounding box crosses the antimeridian — split into two ranges
            statement = statement.where(
                or_(
                    Memory.longitude >= (lng_min + 360 if lng_min < -180 else lng_min),
                    Memory.longitude <= (lng_max - 360 if lng_max > 180 else lng_max),
                )
            )
        else:
            statement = statement.where(Memory.longitude >= lng_min)
            statement = statement.where(Memory.longitude <= lng_max)

        # Precise Haversine filter via raw SQL
        # Requires SQLite 3.35.0+ with math functions (default in Python 3.12+ / Debian 12)
        from sqlalchemy import text as sa_text

        haversine_sql = sa_text(
            "acos(min(1.0, max(-1.0,"
            "  sin(radians(:near_lat)) * sin(radians(latitude))"
            "  + cos(radians(:near_lat)) * cos(radians(latitude))"
            "    * cos(radians(longitude - :near_lng))"
            "))) * 6371 < :radius_km"
        ).bindparams(
            near_lat=near_lat,
            near_lng=near_lng,
            radius_km=near_radius_km,
        )
        statement = statement.where(haversine_sql)
    if has_location is True:
        statement = statement.where(Memory.latitude != None)  # noqa: E711
        statement = statement.where(Memory.longitude != None)  # noqa: E711
    statement = statement.offset(skip).limit(limit)
    memories = list(session.exec(statement).all())
    return _attach_tags(memories, session)


@router.get("/{memory_id}", response_model=MemoryRead)
async def get_memory(
    memory_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> MemoryRead:
    memory = session.get(Memory, memory_id)
    if not memory or memory.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _attach_tags([memory], session)[0]


@router.put("/{memory_id}", response_model=MemoryRead)
async def update_memory(
    memory_id: str,
    body: MemoryUpdate,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Memory:
    memory = session.get(Memory, memory_id)
    if not memory or memory.deleted_at is not None:
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


@router.delete("/{memory_id}", status_code=200)
async def delete_memory(
    memory_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict:
    """Soft-delete a memory (and descendants) by setting deleted_at."""
    memory = session.get(Memory, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    now = datetime.now(timezone.utc)

    # Collect all descendant memory IDs recursively
    all_memories: list[Memory] = [memory]
    visited: set[str] = {memory_id}
    ids_to_check = [memory_id]
    while ids_to_check:
        children = list(session.exec(
            select(Memory).where(Memory.parent_id.in_(ids_to_check))  # type: ignore[union-attr]
        ).all())
        ids_to_check = []
        for child in children:
            if child.id not in visited:
                visited.add(child.id)
                all_memories.append(child)
                ids_to_check.append(child.id)

    for m in all_memories:
        m.deleted_at = now
        session.add(m)
    session.commit()

    return {"id": memory_id}


@router.post("/{memory_id}/undelete", response_model=MemoryRead)
async def undelete_memory(
    memory_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> MemoryRead:
    """Restore a soft-deleted memory (and descendants)."""
    memory = session.get(Memory, memory_id)
    if not memory:
        raise HTTPException(status_code=404, detail="Memory not found")

    # Collect all descendant memory IDs recursively
    all_memories: list[Memory] = [memory]
    visited: set[str] = {memory_id}
    ids_to_check = [memory_id]
    while ids_to_check:
        children = list(session.exec(
            select(Memory).where(Memory.parent_id.in_(ids_to_check))  # type: ignore[union-attr]
        ).all())
        ids_to_check = []
        for child in children:
            if child.id not in visited:
                visited.add(child.id)
                all_memories.append(child)
                ids_to_check.append(child.id)

    for m in all_memories:
        m.deleted_at = None
        session.add(m)
    session.commit()
    session.refresh(memory)

    return _attach_tags([memory], session)[0]
