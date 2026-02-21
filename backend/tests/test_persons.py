"""Tests for Person CRUD and memory-person link/unlink endpoints."""
from __future__ import annotations

import pytest
from sqlmodel import Session

from app.models.memory import Memory


@pytest.fixture(name="person_id")
def person_id_fixture(client):
    resp = client.post("/api/persons", json={"name": "Test Person"})
    assert resp.status_code == 201
    return resp.json()["id"]


@pytest.fixture(name="memory_id")
def memory_id_fixture(session: Session):
    """Create a test memory directly in the DB session."""
    memory = Memory(title="Test Memory", content="Content")
    session.add(memory)
    session.commit()
    session.refresh(memory)
    return memory.id


# --- Person CRUD ---


def test_create_person(client):
    resp = client.post("/api/persons", json={"name": "Alice"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Alice"
    assert data["id"]
    assert data["immich_person_id"] is None
    assert data["face_thumbnail_path"] is None
    assert data["name_encrypted"] is None
    assert data["name_dek"] is None


def test_create_person_empty_name(client):
    resp = client.post("/api/persons", json={"name": "   "})
    assert resp.status_code == 422


def test_list_persons(client):
    client.post("/api/persons", json={"name": "Charlie"})
    client.post("/api/persons", json={"name": "Alice"})
    client.post("/api/persons", json={"name": "Bob"})

    resp = client.get("/api/persons")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3
    # Should be ordered by name
    assert data[0]["name"] == "Alice"
    assert data[1]["name"] == "Bob"
    assert data[2]["name"] == "Charlie"


def test_list_persons_search(client):
    client.post("/api/persons", json={"name": "Alice Smith"})
    client.post("/api/persons", json={"name": "Bob Jones"})
    client.post("/api/persons", json={"name": "Alice Johnson"})

    resp = client.get("/api/persons", params={"q": "Alice"})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all("Alice" in p["name"] for p in data)


def test_list_persons_pagination(client):
    for i in range(5):
        client.post("/api/persons", json={"name": f"Person {i:02d}"})

    resp = client.get("/api/persons", params={"skip": 2, "limit": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


def test_get_person_detail(client, person_id, memory_id):
    # Link person to memory
    client.post(f"/api/memories/{memory_id}/persons", json={"person_id": person_id})

    resp = client.get(f"/api/persons/{person_id}")
    assert resp.status_code == 200
    data = resp.json()
    assert data["id"] == person_id
    assert data["name"] == "Test Person"
    assert data["memory_count"] == 1


def test_get_person_not_found(client):
    resp = client.get("/api/persons/nonexistent-id")
    assert resp.status_code == 404


def test_update_person_name(client, person_id):
    resp = client.put(f"/api/persons/{person_id}", json={"name": "Updated Name"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "Updated Name"


def test_create_person_with_relationship(client):
    resp = client.post(
        "/api/persons",
        json={"name": "Mom", "relationship_to_owner": "parent"},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "Mom"
    assert data["relationship_to_owner"] == "parent"
    assert data["is_deceased"] is False


def test_update_person_relationship(client, person_id):
    # Set relationship
    resp = client.put(
        f"/api/persons/{person_id}",
        json={"relationship_to_owner": "friend"},
    )
    assert resp.status_code == 200
    assert resp.json()["relationship_to_owner"] == "friend"

    # Change relationship
    resp = client.put(
        f"/api/persons/{person_id}",
        json={"relationship_to_owner": "sibling"},
    )
    assert resp.status_code == 200
    assert resp.json()["relationship_to_owner"] == "sibling"


def test_update_person_deceased(client, person_id):
    resp = client.put(
        f"/api/persons/{person_id}",
        json={"is_deceased": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["is_deceased"] is True
    assert data["name"] == "Test Person"  # name unchanged


def test_update_person_not_found(client):
    resp = client.put("/api/persons/nonexistent-id", json={"name": "X"})
    assert resp.status_code == 404


def test_delete_person(client, person_id):
    resp = client.delete(f"/api/persons/{person_id}")
    assert resp.status_code == 204

    resp = client.get(f"/api/persons/{person_id}")
    assert resp.status_code == 404


def test_delete_person_cascades_links(client, person_id, memory_id):
    # Link person to memory
    client.post(f"/api/memories/{memory_id}/persons", json={"person_id": person_id})

    # Delete person
    resp = client.delete(f"/api/persons/{person_id}")
    assert resp.status_code == 204

    # Verify link is gone
    resp = client.get(f"/api/memories/{memory_id}/persons")
    assert resp.status_code == 200
    assert len(resp.json()) == 0


def test_delete_person_not_found(client):
    resp = client.delete("/api/persons/nonexistent-id")
    assert resp.status_code == 404


# --- Memory-Person link/unlink ---


def test_link_person_to_memory(client, person_id, memory_id):
    resp = client.post(
        f"/api/memories/{memory_id}/persons",
        json={"person_id": person_id},
    )
    assert resp.status_code == 201
    data = resp.json()
    assert data["memory_id"] == memory_id
    assert data["person_id"] == person_id
    assert data["person_name"] == "Test Person"
    assert data["source"] == "manual"
    assert data["confidence"] is None


def test_link_person_to_memory_duplicate(client, person_id, memory_id):
    resp1 = client.post(
        f"/api/memories/{memory_id}/persons",
        json={"person_id": person_id},
    )
    assert resp1.status_code == 201

    # Second link should be idempotent, not error
    resp2 = client.post(
        f"/api/memories/{memory_id}/persons",
        json={"person_id": person_id},
    )
    assert resp2.status_code == 201
    assert resp2.json()["id"] == resp1.json()["id"]


def test_link_person_to_memory_not_found(client, person_id, memory_id):
    # Nonexistent memory
    resp = client.post(
        "/api/memories/nonexistent-id/persons",
        json={"person_id": person_id},
    )
    assert resp.status_code == 404

    # Nonexistent person
    resp = client.post(
        f"/api/memories/{memory_id}/persons",
        json={"person_id": "nonexistent-id"},
    )
    assert resp.status_code == 404


def test_unlink_person_from_memory(client, person_id, memory_id):
    client.post(
        f"/api/memories/{memory_id}/persons",
        json={"person_id": person_id},
    )

    resp = client.delete(f"/api/memories/{memory_id}/persons/{person_id}")
    assert resp.status_code == 204

    # Verify link is gone
    resp = client.get(f"/api/memories/{memory_id}/persons")
    assert resp.status_code == 200
    assert len(resp.json()) == 0


def test_unlink_person_not_found(client, memory_id):
    resp = client.delete(f"/api/memories/{memory_id}/persons/nonexistent-id")
    assert resp.status_code == 404


def test_list_memory_persons(client, memory_id):
    # Create two persons
    r1 = client.post("/api/persons", json={"name": "Alice"})
    r2 = client.post("/api/persons", json={"name": "Bob"})
    pid1 = r1.json()["id"]
    pid2 = r2.json()["id"]

    # Link both to memory
    client.post(f"/api/memories/{memory_id}/persons", json={"person_id": pid1})
    client.post(f"/api/memories/{memory_id}/persons", json={"person_id": pid2})

    resp = client.get(f"/api/memories/{memory_id}/persons")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = {p["person_name"] for p in data}
    assert names == {"Alice", "Bob"}


# --- Thumbnail endpoint ---


def test_get_person_thumbnail_no_thumbnail(client, person_id):
    """Thumbnail returns 404 when person has no face_thumbnail_path."""
    resp = client.get(f"/api/persons/{person_id}/thumbnail")
    assert resp.status_code == 404


def test_get_person_thumbnail_returns_image(client, session, tmp_path):
    """Thumbnail returns image bytes when thumbnail file exists."""
    from unittest.mock import patch
    from app.models.person import Person as PersonModel

    # Create a person with a thumbnail path
    person = PersonModel(name="Thumb Person", face_thumbnail_path="immich_thumbnails/test.jpg")
    session.add(person)
    session.commit()
    session.refresh(person)

    # Create the thumbnail file
    thumb_dir = tmp_path / "immich_thumbnails"
    thumb_dir.mkdir()
    thumb_file = thumb_dir / "test.jpg"
    thumb_file.write_bytes(b"\xff\xd8\xff\xe0fake-jpeg-data")

    # Patch get_settings to use our tmp_path as data_dir
    from app.config import Settings
    fake_settings = Settings(data_dir=tmp_path)
    with patch("app.routers.persons.get_settings", return_value=fake_settings):
        resp = client.get(f"/api/persons/{person.id}/thumbnail")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "image/jpeg"
    assert resp.content == b"\xff\xd8\xff\xe0fake-jpeg-data"


def test_get_person_thumbnail_not_found_person(client):
    """Thumbnail returns 404 for nonexistent person."""
    resp = client.get("/api/persons/nonexistent-id/thumbnail")
    assert resp.status_code == 404


# --- person_ids filter on list_memories ---


def test_list_memories_person_ids_filter(client, session):
    """Filtering memories by person_ids returns only linked memories."""
    from app.models.person import Person as PersonModel, MemoryPerson as MemoryPersonModel

    # Create two memories
    m1 = Memory(title="Memory 1", content="Content 1")
    m2 = Memory(title="Memory 2", content="Content 2")
    session.add_all([m1, m2])
    session.commit()
    session.refresh(m1)
    session.refresh(m2)

    # Create a person and link to m1 only
    p1 = PersonModel(name="Alice")
    session.add(p1)
    session.commit()
    session.refresh(p1)

    mp = MemoryPersonModel(memory_id=m1.id, person_id=p1.id)
    session.add(mp)
    session.commit()

    # Filter by person_ids should return only m1
    resp = client.get("/api/memories", params={"person_ids": [p1.id]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == m1.id


def test_list_memories_person_ids_and_logic(client, session):
    """person_ids filter uses AND logic: only memories linked to ALL persons."""
    from app.models.person import Person as PersonModel, MemoryPerson as MemoryPersonModel

    m1 = Memory(title="Memory 1", content="Content 1")
    m2 = Memory(title="Memory 2", content="Content 2")
    session.add_all([m1, m2])
    session.commit()
    session.refresh(m1)
    session.refresh(m2)

    p1 = PersonModel(name="Alice")
    p2 = PersonModel(name="Bob")
    session.add_all([p1, p2])
    session.commit()
    session.refresh(p1)
    session.refresh(p2)

    # Link both persons to m1, only p1 to m2
    session.add(MemoryPersonModel(memory_id=m1.id, person_id=p1.id))
    session.add(MemoryPersonModel(memory_id=m1.id, person_id=p2.id))
    session.add(MemoryPersonModel(memory_id=m2.id, person_id=p1.id))
    session.commit()

    # Filter by both persons should return only m1
    resp = client.get("/api/memories", params={"person_ids": [p1.id, p2.id]})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["id"] == m1.id


def test_persons_auth_required(client_no_auth):
    # All endpoints should return 401/403 without auth
    assert client_no_auth.get("/api/persons").status_code == 403
    assert client_no_auth.post("/api/persons", json={"name": "X"}).status_code == 403
    assert client_no_auth.get("/api/persons/some-id").status_code == 403
    assert client_no_auth.put("/api/persons/some-id", json={"name": "X"}).status_code == 403
    assert client_no_auth.delete("/api/persons/some-id").status_code == 403
    assert client_no_auth.post("/api/memories/some-id/persons", json={"person_id": "x"}).status_code == 403
    assert client_no_auth.delete("/api/memories/some-id/persons/x").status_code == 403
    assert client_no_auth.get("/api/memories/some-id/persons").status_code == 403
