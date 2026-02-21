"""Owner router — profile management, family listing, GEDCOM upload."""

from __future__ import annotations

import logging
import uuid
from dataclasses import asdict
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, UploadFile, File, Query
from sqlmodel import Session, select

from app.config import get_settings
from app.db import get_session
from app.dependencies import require_auth
from app.models.owner import OwnerProfile, OwnerProfileRead, OwnerProfileUpdate
from app.models.person import Person, PersonRead
from app.services.gedcom_import import import_gedcom_file, GedcomImportResult

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/owner", tags=["owner"])


def _get_or_create_profile(db: Session) -> OwnerProfile:
    """Get or lazy-create the singleton OwnerProfile (id=1)."""
    profile = db.get(OwnerProfile, 1)
    if profile is None:
        profile = OwnerProfile(id=1)
        db.add(profile)
        db.commit()
        db.refresh(profile)
    return profile


@router.get("/profile", response_model=OwnerProfileRead)
async def get_owner_profile(
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
) -> OwnerProfileRead:
    """Get owner profile, creating a default if it doesn't exist yet."""
    profile = _get_or_create_profile(db)
    return OwnerProfileRead.model_validate(profile)


@router.put("/profile", response_model=OwnerProfileRead)
async def update_owner_profile(
    body: OwnerProfileUpdate,
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
) -> OwnerProfileRead:
    """Update owner profile fields.

    When person_id is set (non-None), mark that Person's
    relationship_to_owner as "self".
    """
    profile = _get_or_create_profile(db)

    update_data = body.model_dump(exclude_unset=True)

    # If person_id is being set, validate person exists and mark relationship
    if "person_id" in update_data and update_data["person_id"] is not None:
        person = db.get(Person, update_data["person_id"])
        if person is None:
            raise HTTPException(status_code=404, detail="Person not found")
        person.relationship_to_owner = "self"
        person.updated_at = datetime.now(timezone.utc)
        db.add(person)

    for key, value in update_data.items():
        setattr(profile, key, value)
    profile.updated_at = datetime.now(timezone.utc)

    db.add(profile)
    db.commit()
    db.refresh(profile)
    return OwnerProfileRead.model_validate(profile)


@router.get("/family", response_model=list[PersonRead])
async def get_owner_family(
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
) -> list[PersonRead]:
    """List family members — persons with relationship_to_owner set, excluding 'self'.

    Ordered by relationship_to_owner then name.
    """
    statement = (
        select(Person)
        .where(Person.relationship_to_owner != None)  # noqa: E711
        .where(Person.relationship_to_owner != "self")
        .order_by(Person.relationship_to_owner, Person.name)  # type: ignore[arg-type]
    )
    persons = db.exec(statement).all()
    return [PersonRead.model_validate(p) for p in persons]


@router.post("/gedcom")
async def upload_gedcom(
    file: UploadFile = File(...),
    owner_gedcom_id: str | None = Query(None, description="GEDCOM ID of the owner in the file"),
    _session_id: str = Depends(require_auth),
    db: Session = Depends(get_session),
) -> dict:
    """Upload a GEDCOM file to import family tree data.

    Parses the .ged file, creates/updates Person records, and computes
    family relationships relative to the owner (if owner_gedcom_id provided).
    """
    if not file.filename or not file.filename.lower().endswith(".ged"):
        raise HTTPException(status_code=422, detail="File must be a .ged GEDCOM file")

    settings = get_settings()
    tmp_dir = settings.tmp_dir
    tmp_dir.mkdir(parents=True, exist_ok=True)
    tmp_path = tmp_dir / f"gedcom_{uuid.uuid4().hex}.ged"

    try:
        contents = await file.read()
        tmp_path.write_bytes(contents)
    except Exception:
        logger.exception("Failed to save uploaded GEDCOM file to temp storage")
        tmp_path.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail="Failed to process GEDCOM file")

    try:
        result = import_gedcom_file(tmp_path, db, owner_gedcom_id)
        logger.info(
            "GEDCOM import: %d created, %d updated, %d skipped, %d errors",
            result.persons_created,
            result.persons_updated,
            result.persons_skipped,
            len(result.errors),
        )
        return asdict(result)
    except Exception:
        logger.exception("GEDCOM import failed unexpectedly")
        raise HTTPException(status_code=500, detail="Failed to process GEDCOM file")
    finally:
        tmp_path.unlink(missing_ok=True)
