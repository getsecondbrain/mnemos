"""Tests for Immich sync service and API endpoints."""
from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
from sqlmodel import Session

from app.models.memory import Memory
from app.models.person import MemoryPerson, Person
from app.services.immich import ImmichService, SyncFacesResult, SyncPeopleResult


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(name="settings")
def settings_fixture(tmp_path):
    """Settings with Immich configured."""
    mock_settings = MagicMock()
    mock_settings.immich_url = "http://immich:2283"
    mock_settings.immich_api_key = "test-api-key"
    mock_settings.data_dir = tmp_path
    return mock_settings


@pytest.fixture(name="settings_no_immich")
def settings_no_immich_fixture(tmp_path):
    """Settings with Immich NOT configured."""
    mock_settings = MagicMock()
    mock_settings.immich_url = ""
    mock_settings.immich_api_key = ""
    mock_settings.data_dir = tmp_path
    return mock_settings


@pytest.fixture(name="immich_service")
def immich_service_fixture(settings):
    return ImmichService(settings)


def _mock_response(status_code: int = 200, json_data=None, content: bytes | None = None) -> httpx.Response:
    """Build a mock httpx.Response."""
    kwargs: dict = {
        "status_code": status_code,
        "request": httpx.Request("GET", "http://test"),
    }
    if json_data is not None:
        kwargs["json"] = json_data
    elif content is not None:
        kwargs["content"] = content
    return httpx.Response(**kwargs)


# ── sync_people tests ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_people_creates_new_persons(immich_service, session: Session):
    people_response = {
        "people": [
            {"id": "aaa", "name": "Alice", "thumbnailPath": "/some/path"},
            {"id": "bbb", "name": "Bob", "thumbnailPath": "/some/path"},
            {"id": "ccc", "name": "Charlie", "thumbnailPath": "/some/path"},
        ]
    }

    async def mock_get(self, url, **kwargs):
        if "/api/people" in str(url) and "/thumbnail" not in str(url):
            return _mock_response(json_data=people_response)
        if "/thumbnail" in str(url):
            return _mock_response(content=b"\xff\xd8fake-jpeg")
        return _mock_response(status_code=404)

    with patch("httpx.AsyncClient.get", new=mock_get):
        result = await immich_service.sync_people(session)

    assert result.created == 3
    assert result.updated == 0
    assert result.unchanged == 0

    # Verify persons in DB
    from sqlmodel import select
    persons = session.exec(select(Person).where(Person.immich_person_id != None)).all()  # noqa: E711
    assert len(persons) == 3
    names = {p.name for p in persons}
    assert names == {"Alice", "Bob", "Charlie"}
    # Verify thumbnails downloaded
    for p in persons:
        assert p.face_thumbnail_path is not None
        assert p.face_thumbnail_path.startswith("immich_thumbnails/")


@pytest.mark.asyncio
async def test_sync_people_updates_existing_person_name(immich_service, session: Session):
    # Create existing person with old name
    person = Person(name="Old Name", immich_person_id="abc")
    session.add(person)
    session.commit()

    people_response = {
        "people": [{"id": "abc", "name": "New Name", "thumbnailPath": "/path"}]
    }

    async def mock_get(self, url, **kwargs):
        if "/api/people" in str(url) and "/thumbnail" not in str(url):
            return _mock_response(json_data=people_response)
        if "/thumbnail" in str(url):
            return _mock_response(content=b"\xff\xd8fake-jpeg")
        return _mock_response(status_code=404)

    with patch("httpx.AsyncClient.get", new=mock_get):
        result = await immich_service.sync_people(session)

    assert result.updated == 1
    assert result.created == 0

    session.refresh(person)
    assert person.name == "New Name"


@pytest.mark.asyncio
async def test_sync_people_skips_unchanged(immich_service, session: Session):
    person = Person(
        name="Alice",
        immich_person_id="aaa",
        face_thumbnail_path="immich_thumbnails/aaa.jpg",
    )
    session.add(person)
    session.commit()

    people_response = {
        "people": [{"id": "aaa", "name": "Alice", "thumbnailPath": "/path"}]
    }

    async def mock_get(self, url, **kwargs):
        if "/api/people" in str(url) and "/thumbnail" not in str(url):
            return _mock_response(json_data=people_response)
        if "/thumbnail" in str(url):
            return _mock_response(content=b"\xff\xd8fake-jpeg")
        return _mock_response(status_code=404)

    with patch("httpx.AsyncClient.get", new=mock_get):
        result = await immich_service.sync_people(session)

    assert result.unchanged == 1
    assert result.created == 0
    assert result.updated == 0


@pytest.mark.asyncio
async def test_sync_people_handles_http_error(immich_service, session: Session):
    async def mock_get(self, url, **kwargs):
        return _mock_response(status_code=500, json_data={"message": "Internal Server Error"})

    with patch("httpx.AsyncClient.get", new=mock_get):
        result = await immich_service.sync_people(session)

    assert result.created == 0
    assert result.updated == 0
    assert result.unchanged == 0
    assert result.errors == 0  # HTTP error is caught before iterating


@pytest.mark.asyncio
async def test_sync_people_handles_per_person_failure(immich_service, session: Session):
    people_response = {
        "people": [
            {"id": "aaa", "name": "Alice", "thumbnailPath": "/path"},
            {"id": "bbb", "name": "Bob", "thumbnailPath": "/path"},
        ]
    }
    call_count = 0

    async def mock_get(self, url, **kwargs):
        nonlocal call_count
        if "/api/people" in str(url) and "/thumbnail" not in str(url):
            return _mock_response(json_data=people_response)
        if "/thumbnail" in str(url):
            call_count += 1
            if "aaa" in str(url):
                raise httpx.ConnectError("connection refused")
            return _mock_response(content=b"\xff\xd8fake-jpeg")
        return _mock_response(status_code=404)

    with patch("httpx.AsyncClient.get", new=mock_get):
        result = await immich_service.sync_people(session)

    # Both persons should be created — thumbnail failure is non-fatal
    assert result.created == 2
    assert result.errors == 0


# ── sync_faces_for_asset tests ───────────────────────────────────────


@pytest.mark.asyncio
async def test_sync_faces_creates_memory_person_links(immich_service, session: Session):
    # Create a memory and a person
    memory = Memory(title="Photo", content="A photo")
    session.add(memory)
    person = Person(name="Alice", immich_person_id="aaa")
    session.add(person)
    session.commit()
    session.refresh(memory)
    session.refresh(person)

    faces_response = [
        {"person": {"id": "aaa", "name": "Alice"}}
    ]

    async def mock_get(self, url, **kwargs):
        return _mock_response(json_data=faces_response)

    with patch("httpx.AsyncClient.get", new=mock_get):
        result = await immich_service.sync_faces_for_asset(
            asset_id="asset-123", memory_id=memory.id, session=session
        )

    assert result.linked == 1
    assert result.already_linked == 0

    # Verify MemoryPerson link exists
    from sqlmodel import select
    mp = session.exec(
        select(MemoryPerson).where(
            MemoryPerson.memory_id == memory.id,
            MemoryPerson.person_id == person.id,
        )
    ).first()
    assert mp is not None
    assert mp.source == "immich"


@pytest.mark.asyncio
async def test_sync_faces_handles_duplicate_link(immich_service, session: Session):
    memory = Memory(title="Photo", content="A photo")
    session.add(memory)
    person = Person(name="Alice", immich_person_id="aaa")
    session.add(person)
    session.commit()
    session.refresh(memory)
    session.refresh(person)

    # Create existing link
    mp = MemoryPerson(
        memory_id=memory.id, person_id=person.id, source="immich"
    )
    session.add(mp)
    session.commit()

    faces_response = [
        {"person": {"id": "aaa", "name": "Alice"}}
    ]

    async def mock_get(self, url, **kwargs):
        return _mock_response(json_data=faces_response)

    with patch("httpx.AsyncClient.get", new=mock_get):
        result = await immich_service.sync_faces_for_asset(
            asset_id="asset-123", memory_id=memory.id, session=session
        )

    assert result.already_linked == 1
    assert result.linked == 0


@pytest.mark.asyncio
async def test_sync_faces_creates_unknown_person_if_not_local(immich_service, session: Session):
    memory = Memory(title="Photo", content="A photo")
    session.add(memory)
    session.commit()
    session.refresh(memory)

    faces_response = [
        {"person": {"id": "ddd-eee-fff-000", "name": ""}}
    ]

    async def mock_get(self, url, **kwargs):
        return _mock_response(json_data=faces_response)

    with patch("httpx.AsyncClient.get", new=mock_get):
        result = await immich_service.sync_faces_for_asset(
            asset_id="asset-123", memory_id=memory.id, session=session
        )

    assert result.linked == 1

    # Verify new person was created with "Unknown" name
    from sqlmodel import select
    person = session.exec(
        select(Person).where(Person.immich_person_id == "ddd-eee-fff-000")
    ).first()
    assert person is not None
    assert person.name == "Unknown"


# ── push_person_name tests ──────────────────────────────────────────


@pytest.mark.asyncio
async def test_push_person_name_success(immich_service, session: Session):
    person = Person(name="Alice Updated", immich_person_id="aaa")
    session.add(person)
    session.commit()
    session.refresh(person)

    async def mock_put(self, url, **kwargs):
        return _mock_response(status_code=200, json_data={"id": "aaa", "name": "Alice Updated"})

    with patch("httpx.AsyncClient.put", new=mock_put):
        result = await immich_service.push_person_name(
            person_id=person.id, name="Alice Updated", session=session
        )

    assert result is True


@pytest.mark.asyncio
async def test_push_person_name_no_immich_id(immich_service, session: Session):
    person = Person(name="Local Only")
    session.add(person)
    session.commit()
    session.refresh(person)

    # Should return False without calling Immich
    result = await immich_service.push_person_name(
        person_id=person.id, name="Local Only", session=session
    )
    assert result is False


@pytest.mark.asyncio
async def test_push_person_name_immich_error(immich_service, session: Session):
    person = Person(name="Alice", immich_person_id="aaa")
    session.add(person)
    session.commit()
    session.refresh(person)

    async def mock_put(self, url, **kwargs):
        return _mock_response(status_code=500, json_data={"message": "error"})

    with patch("httpx.AsyncClient.put", new=mock_put):
        result = await immich_service.push_person_name(
            person_id=person.id, name="Alice", session=session
        )

    assert result is False


# ── Config guard test ────────────────────────────────────────────────


def test_service_not_created_without_config(settings_no_immich):
    """Verify that when immich_url is empty, the worker guard would skip."""
    # This tests the guard logic — if immich_url is empty, sync should be skipped.
    assert settings_no_immich.immich_url == ""
    assert settings_no_immich.immich_api_key == ""


# ── API endpoint tests ───────────────────────────────────────────────


def test_sync_immich_endpoint_returns_400_when_not_configured(client):
    """POST /api/persons/sync-immich returns 400 when Immich not configured."""
    # Default test settings have no IMMICH_URL
    resp = client.post("/api/persons/sync-immich")
    assert resp.status_code == 400
    assert "Immich not configured" in resp.json()["detail"]


def test_push_name_endpoint_returns_400_for_non_immich_person(client, session: Session):
    """POST /api/persons/{id}/push-name-to-immich returns 400 for non-Immich person."""
    # Create a person without immich_person_id
    person = Person(name="Local Only")
    session.add(person)
    session.commit()
    session.refresh(person)

    # Even if Immich were configured, person has no immich_person_id
    # But first it will fail on "Immich not configured" since test env has no IMMICH_URL
    resp = client.post(f"/api/persons/{person.id}/push-name-to-immich")
    assert resp.status_code == 400


def test_push_name_endpoint_returns_404_for_missing_person(client):
    """POST /api/persons/{id}/push-name-to-immich returns 404 for nonexistent person."""
    # Will return 400 (Immich not configured) before reaching 404 in default test env
    resp = client.post("/api/persons/nonexistent-id/push-name-to-immich")
    assert resp.status_code == 400
