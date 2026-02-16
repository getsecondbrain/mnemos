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
