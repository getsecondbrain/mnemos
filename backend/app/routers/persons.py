"""Person router — CRUD for persons and memory-person associations."""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, func, select

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
        select(func.count()).where(MemoryPerson.person_id == person_id)
    ).one()

    return PersonDetailRead(
        **PersonRead.model_validate(person).model_dump(),
        memory_count=memory_count,
    )


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

    if body.name is not None:
        name = body.name.strip()
        if not name:
            raise HTTPException(status_code=422, detail="Person name cannot be empty")
        person.name = name

    if body.name_encrypted is not None:
        person.name_encrypted = body.name_encrypted
    if body.name_dek is not None:
        person.name_dek = body.name_dek

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
    if not memory:
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
    if not memory:
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
    if not memory:
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
