"""Immich integration service — sync people and faces between Immich and Mnemos."""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

import httpx
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from app.config import Settings
from app.models.person import MemoryPerson, Person

logger = logging.getLogger(__name__)

# Immich person IDs are UUIDs — only allow hex digits and dashes.
_SAFE_ID_RE = re.compile(r"^[a-fA-F0-9\-]+$")


def _validate_id(value: str) -> str:
    """Validate that an ID from Immich contains only safe characters.

    Prevents path traversal and URL injection from a compromised Immich server.
    """
    if not value or not _SAFE_ID_RE.match(value):
        raise ValueError(f"Invalid Immich ID: {value!r}")
    return value


@dataclass
class SyncPeopleResult:
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    errors: int = 0


@dataclass
class SyncFacesResult:
    linked: int = 0
    already_linked: int = 0
    errors: int = 0


class ImmichService:
    """Sync people and face data between Immich and Mnemos."""

    def __init__(self, settings: Settings) -> None:
        self._base_url = settings.immich_url.rstrip("/")
        self._api_key = settings.immich_api_key
        self._timeout = 30.0
        self._thumbnails_dir = settings.data_dir / "immich_thumbnails"

    async def _get(
        self, path: str, params: dict[str, str] | None = None
    ) -> httpx.Response:
        """Make an authenticated GET request to Immich API."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.get(
                f"{self._base_url}{path}",
                params=params,
                headers={"x-api-key": self._api_key, "Accept": "application/json"},
            )

    async def _put(self, path: str, json: dict) -> httpx.Response:
        """Make an authenticated PUT request to Immich API."""
        async with httpx.AsyncClient(timeout=self._timeout) as client:
            return await client.put(
                f"{self._base_url}{path}",
                json=json,
                headers={"x-api-key": self._api_key, "Accept": "application/json"},
            )

    async def _download_thumbnail(self, person_id: str) -> str | None:
        """Download a person's face thumbnail from Immich.

        Returns relative path string on success, None on failure.
        """
        try:
            safe_id = _validate_id(person_id)
            self._thumbnails_dir.mkdir(parents=True, exist_ok=True)
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                resp = await client.get(
                    f"{self._base_url}/api/people/{safe_id}/thumbnail",
                    headers={"x-api-key": self._api_key},
                )
                resp.raise_for_status()
                out_path = self._thumbnails_dir / f"{safe_id}.jpg"
                out_path.write_bytes(resp.content)
                return f"immich_thumbnails/{safe_id}.jpg"
        except Exception:
            logger.warning("Failed to download thumbnail for person %s", person_id, exc_info=True)
            return None

    async def sync_people(self, session: Session) -> SyncPeopleResult:
        """Sync all people from Immich into the local Person table."""
        result = SyncPeopleResult()

        try:
            resp = await self._get("/api/people", params={"withHidden": "true"})
            resp.raise_for_status()
        except Exception:
            logger.warning("Failed to fetch people from Immich", exc_info=True)
            return result

        data = resp.json()
        people = data.get("people", [])

        for immich_person in people:
            nested = session.begin_nested()
            try:
                immich_id = _validate_id(immich_person["id"])
                immich_name = immich_person.get("name", "").strip()
                display_name = immich_name if immich_name else "Unknown"

                # Look up existing local person
                existing = session.exec(
                    select(Person).where(Person.immich_person_id == immich_id)
                ).first()

                if existing:
                    changed = False
                    # Update name if Immich has a non-empty name and it differs
                    if immich_name and existing.name != immich_name:
                        existing.name = immich_name
                        changed = True

                    # Download/update thumbnail
                    thumb_path = await self._download_thumbnail(immich_id)
                    if thumb_path and existing.face_thumbnail_path != thumb_path:
                        existing.face_thumbnail_path = thumb_path
                        changed = True

                    if changed:
                        session.add(existing)
                        result.updated += 1
                    else:
                        result.unchanged += 1
                else:
                    # Download thumbnail for new person
                    thumb_path = await self._download_thumbnail(immich_id)

                    person = Person(
                        name=display_name,
                        immich_person_id=immich_id,
                        face_thumbnail_path=thumb_path,
                    )
                    session.add(person)
                    result.created += 1

                nested.commit()

            except Exception:
                nested.rollback()
                logger.warning(
                    "Failed to sync person %s from Immich",
                    immich_person.get("id", "unknown"),
                    exc_info=True,
                )
                result.errors += 1

        session.commit()
        return result

    async def sync_faces_for_asset(
        self, asset_id: str, memory_id: str, session: Session
    ) -> SyncFacesResult:
        """Sync detected faces for an Immich asset into MemoryPerson links."""
        result = SyncFacesResult()

        try:
            resp = await self._get("/api/faces", params={"id": asset_id})
            resp.raise_for_status()
        except Exception:
            logger.warning("Failed to fetch faces for asset %s", asset_id, exc_info=True)
            return result

        faces = resp.json()

        for face in faces:
            try:
                face_person = face.get("person")
                if not face_person or not face_person.get("id"):
                    continue

                immich_person_id = _validate_id(face_person["id"])
                immich_name = face_person.get("name", "").strip()
                display_name = immich_name if immich_name else "Unknown"

                # Find or create local person
                person = session.exec(
                    select(Person).where(Person.immich_person_id == immich_person_id)
                ).first()

                if not person:
                    person = Person(
                        name=display_name,
                        immich_person_id=immich_person_id,
                    )
                    session.add(person)
                    session.flush()

                # Create MemoryPerson link using a savepoint so that
                # an IntegrityError (duplicate) only rolls back this insert,
                # not the entire transaction (which would lose prior flushes).
                mp = MemoryPerson(
                    memory_id=memory_id,
                    person_id=person.id,
                    source="immich",
                    confidence=None,
                )
                nested = session.begin_nested()
                try:
                    session.add(mp)
                    session.flush()
                    result.linked += 1
                except IntegrityError:
                    nested.rollback()
                    result.already_linked += 1

            except Exception:
                logger.warning(
                    "Failed to sync face for asset %s",
                    asset_id,
                    exc_info=True,
                )
                result.errors += 1

        session.commit()
        return result

    async def push_person_name(
        self, person_id: str, name: str, session: Session
    ) -> bool:
        """Push a person's name from Mnemos back to Immich."""
        person = session.get(Person, person_id)
        if not person or not person.immich_person_id:
            return False

        try:
            safe_id = _validate_id(person.immich_person_id)
            resp = await self._put(
                f"/api/people/{safe_id}",
                json={"name": name},
            )
            if resp.status_code == 200:
                return True
            logger.warning(
                "Immich returned %d when pushing name for person %s",
                resp.status_code,
                person.immich_person_id,
            )
            return False
        except Exception:
            logger.warning(
                "Failed to push name to Immich for person %s",
                person.immich_person_id,
                exc_info=True,
            )
            return False
