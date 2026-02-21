"""Person router — CRUD for persons and memory-person associations."""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

from app.config import get_settings
from app.db import get_session
from app.dependencies import require_auth
from app.models.memory import Memory
from app.models.person import (
    LinkPersonRequest,
    MemoryPerson,
    MemoryPersonRead,
    Person,
    PersonCreate,
    PersonDetailRead,
    PersonRead,
    PersonUpdate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/persons", tags=["persons"])
memory_persons_router = APIRouter(prefix="/api/memories", tags=["persons"])


# --- Person CRUD ---


@router.post("", response_model=PersonRead, status_code=201)
async def create_person(
    body: PersonCreate,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> PersonRead:
    name = body.name.strip()
    if not name:
        raise HTTPException(status_code=422, detail="Person name cannot be empty")

    person = Person(
        name=name,
        name_encrypted=body.name_encrypted,
        name_dek=body.name_dek,
        immich_person_id=body.immich_person_id,
        relationship_to_owner=body.relationship_to_owner,
        is_deceased=body.is_deceased,
        gedcom_id=body.gedcom_id,
    )
    session.add(person)
    session.commit()
    session.refresh(person)
    return PersonRead.model_validate(person)


@router.get("", response_model=list[PersonRead])
async def list_persons(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    q: str | None = Query(None, description="Filter persons by name substring"),
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[PersonRead]:
    statement = select(Person).order_by(Person.name)  # type: ignore[arg-type]
    if q:
        statement = statement.where(Person.name.contains(q.strip()))  # type: ignore[union-attr]
    statement = statement.offset(skip).limit(limit)
    persons = session.exec(statement).all()
    return [PersonRead.model_validate(p) for p in persons]


@router.post("/sync-immich", status_code=200)
async def trigger_immich_sync(
    request: Request,
    _session_id: str = Depends(require_auth),
) -> dict:
    """Trigger an immediate Immich people sync."""
    settings = get_settings()
    if not settings.immich_url or not settings.immich_api_key:
        raise HTTPException(status_code=400, detail="Immich not configured")

    worker = getattr(request.app.state, "worker", None)
    if worker is None:
        raise HTTPException(status_code=503, detail="Worker unavailable")

    from app.worker import Job, JobType
    worker.submit_job(Job(job_type=JobType.IMMICH_SYNC, payload={}))
    return {"status": "sync_submitted"}


@router.post("/{person_id}/push-name-to-immich", status_code=200)
async def push_name_to_immich(
    person_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> dict:
    """Push a person's name from Mnemos back to Immich."""
    settings = get_settings()
    if not settings.immich_url or not settings.immich_api_key:
        raise HTTPException(status_code=400, detail="Immich not configured")

    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")
    if not person.immich_person_id:
        raise HTTPException(status_code=400, detail="Person is not linked to Immich")

    from app.services.immich import ImmichService
    immich_service = ImmichService(settings)

    success = await immich_service.push_person_name(
        person_id=person_id, name=person.name, session=session
    )

    if success:
        return {"status": "pushed", "immich_person_id": person.immich_person_id}
    else:
        raise HTTPException(status_code=502, detail="Failed to push name to Immich")


@router.get("/{person_id}", response_model=PersonDetailRead)
async def get_person(
    person_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> PersonDetailRead:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    memory_count = session.exec(
        select(func.count())
        .select_from(MemoryPerson)
        .join(Memory, MemoryPerson.memory_id == Memory.id)  # type: ignore[arg-type]
        .where(MemoryPerson.person_id == person_id)
        .where(Memory.deleted_at == None)  # noqa: E711
    ).one()

    return PersonDetailRead(
        **PersonRead.model_validate(person).model_dump(),
        memory_count=memory_count,
    )


_SAFE_ID_RE = re.compile(r"^[a-zA-Z0-9\-]+$")


@router.get("/{person_id}/thumbnail")
async def get_person_thumbnail(
    person_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> Response:
    if not _SAFE_ID_RE.match(person_id):
        raise HTTPException(status_code=400, detail="Invalid person ID format")

    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    if not person.face_thumbnail_path:
        raise HTTPException(status_code=404, detail="No thumbnail available")

    settings = get_settings()
    thumb_path = (settings.data_dir / person.face_thumbnail_path).resolve()

    # Guard against path traversal (e.g. face_thumbnail_path containing "../")
    if not thumb_path.is_relative_to(settings.data_dir.resolve()):
        raise HTTPException(status_code=400, detail="Invalid thumbnail path")

    if not thumb_path.is_file():
        raise HTTPException(status_code=404, detail="Thumbnail file not found")

    file_bytes = thumb_path.read_bytes()
    return Response(content=file_bytes, media_type="image/jpeg")


@router.put("/{person_id}", response_model=PersonRead)
async def update_person(
    person_id: str,
    body: PersonUpdate,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> PersonRead:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    update_data = body.model_dump(exclude_unset=True)

    if "name" in update_data:
        name = (update_data["name"] or "").strip()
        if not name:
            raise HTTPException(status_code=422, detail="Person name cannot be empty")
        person.name = name

    if "name_encrypted" in update_data:
        person.name_encrypted = update_data["name_encrypted"]
    if "name_dek" in update_data:
        person.name_dek = update_data["name_dek"]
    if "relationship_to_owner" in update_data:
        person.relationship_to_owner = update_data["relationship_to_owner"]
    if "is_deceased" in update_data:
        person.is_deceased = update_data["is_deceased"]
    if "gedcom_id" in update_data:
        person.gedcom_id = update_data["gedcom_id"]

    person.updated_at = datetime.now(timezone.utc)
    session.add(person)
    session.commit()
    session.refresh(person)
    return PersonRead.model_validate(person)


@router.delete("/{person_id}", status_code=204)
async def delete_person(
    person_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> None:
    person = session.get(Person, person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    # Delete all memory-person associations first
    associations = session.exec(
        select(MemoryPerson).where(MemoryPerson.person_id == person_id)
    ).all()
    for assoc in associations:
        session.delete(assoc)

    session.delete(person)
    session.commit()


# --- Memory-Person association endpoints ---


@memory_persons_router.post(
    "/{memory_id}/persons", response_model=MemoryPersonRead, status_code=201
)
async def link_person_to_memory(
    memory_id: str,
    body: LinkPersonRequest,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> MemoryPersonRead:
    memory = session.get(Memory, memory_id)
    if not memory or memory.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")

    person = session.get(Person, body.person_id)
    if not person:
        raise HTTPException(status_code=404, detail="Person not found")

    # Idempotent: return existing link if already exists
    existing = session.exec(
        select(MemoryPerson).where(
            MemoryPerson.memory_id == memory_id,
            MemoryPerson.person_id == body.person_id,
        )
    ).first()
    if existing:
        return MemoryPersonRead(
            id=existing.id,
            memory_id=existing.memory_id,
            person_id=existing.person_id,
            person_name=person.name,
            source=existing.source,
            confidence=existing.confidence,
            created_at=existing.created_at,
        )

    mp = MemoryPerson(
        memory_id=memory_id,
        person_id=body.person_id,
        source=body.source,
        confidence=body.confidence,
    )
    session.add(mp)
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        # Concurrent insert won the race — fetch the existing row
        existing = session.exec(
            select(MemoryPerson).where(
                MemoryPerson.memory_id == memory_id,
                MemoryPerson.person_id == body.person_id,
            )
        ).first()
        if existing:
            return MemoryPersonRead(
                id=existing.id,
                memory_id=existing.memory_id,
                person_id=existing.person_id,
                person_name=person.name,
                source=existing.source,
                confidence=existing.confidence,
                created_at=existing.created_at,
            )
        raise  # pragma: no cover — should not happen
    session.refresh(mp)

    return MemoryPersonRead(
        id=mp.id,
        memory_id=mp.memory_id,
        person_id=mp.person_id,
        person_name=person.name,
        source=mp.source,
        confidence=mp.confidence,
        created_at=mp.created_at,
    )


@memory_persons_router.delete("/{memory_id}/persons/{person_id}", status_code=204)
async def unlink_person_from_memory(
    memory_id: str,
    person_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> None:
    memory = session.get(Memory, memory_id)
    if not memory or memory.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")

    assoc = session.exec(
        select(MemoryPerson).where(
            MemoryPerson.memory_id == memory_id,
            MemoryPerson.person_id == person_id,
        )
    ).first()
    if not assoc:
        raise HTTPException(status_code=404, detail="Person link not found")

    session.delete(assoc)
    session.commit()


@memory_persons_router.get(
    "/{memory_id}/persons", response_model=list[MemoryPersonRead]
)
async def list_memory_persons(
    memory_id: str,
    _session_id: str = Depends(require_auth),
    session: Session = Depends(get_session),
) -> list[MemoryPersonRead]:
    memory = session.get(Memory, memory_id)
    if not memory or memory.deleted_at is not None:
        raise HTTPException(status_code=404, detail="Memory not found")
    return _get_memory_persons(memory_id, session)


def _get_memory_persons(memory_id: str, session: Session) -> list[MemoryPersonRead]:
    """Fetch all persons linked to a memory, joining MemoryPerson with Person."""
    results = session.exec(
        select(MemoryPerson, Person)
        .where(MemoryPerson.memory_id == memory_id)
        .where(MemoryPerson.person_id == Person.id)
        .order_by(Person.name)  # type: ignore[arg-type]
    ).all()
    return [
        MemoryPersonRead(
            id=mp.id,
            memory_id=mp.memory_id,
            person_id=mp.person_id,
            person_name=person.name,
            source=mp.source,
            confidence=mp.confidence,
            created_at=mp.created_at,
        )
        for mp, person in results
    ]
