"""Tests for tag CRUD and memory-tag associations."""
from __future__ import annotations

import pytest


# --- Tag CRUD ---


def test_create_tag(client):
    resp = client.post("/api/tags", json={"name": "Work", "color": "#ff6b6b"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["name"] == "work"  # normalized to lowercase
    assert data["color"] == "#ff6b6b"
    assert "id" in data


def test_create_tag_normalizes_name(client):
    resp = client.post("/api/tags", json={"name": "  Work  "})
    assert resp.status_code == 201
    assert resp.json()["name"] == "work"


def test_create_tag_duplicate_name(client):
    client.post("/api/tags", json={"name": "work"})
    resp = client.post("/api/tags", json={"name": "Work"})
    assert resp.status_code == 409


def test_list_tags(client):
    client.post("/api/tags", json={"name": "alpha"})
    client.post("/api/tags", json={"name": "beta"})
    resp = client.get("/api/tags")
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()]
    assert names == ["alpha", "beta"]  # ordered by name


def test_list_tags_search(client):
    client.post("/api/tags", json={"name": "work"})
    client.post("/api/tags", json={"name": "workout"})
    client.post("/api/tags", json={"name": "family"})
    resp = client.get("/api/tags", params={"q": "wor"})
    assert resp.status_code == 200
    names = [t["name"] for t in resp.json()]
    assert "work" in names
    assert "workout" in names
    assert "family" not in names


def test_get_tag(client):
    create_resp = client.post("/api/tags", json={"name": "health"})
    tag_id = create_resp.json()["id"]
    resp = client.get(f"/api/tags/{tag_id}")
    assert resp.status_code == 200
    assert resp.json()["name"] == "health"


def test_get_tag_not_found(client):
    resp = client.get("/api/tags/nonexistent-id")
    assert resp.status_code == 404


def test_update_tag(client):
    create_resp = client.post("/api/tags", json={"name": "old", "color": "#aaa"})
    tag_id = create_resp.json()["id"]
    resp = client.put(f"/api/tags/{tag_id}", json={"name": "New", "color": "#bbb"})
    assert resp.status_code == 200
    data = resp.json()
    assert data["name"] == "new"  # normalized
    assert data["color"] == "#bbb"


def test_update_tag_duplicate_name(client):
    client.post("/api/tags", json={"name": "first"})
    create_resp = client.post("/api/tags", json={"name": "second"})
    tag_id = create_resp.json()["id"]
    resp = client.put(f"/api/tags/{tag_id}", json={"name": "first"})
    assert resp.status_code == 409


def test_delete_tag(client):
    create_resp = client.post("/api/tags", json={"name": "deleteme"})
    tag_id = create_resp.json()["id"]
    resp = client.delete(f"/api/tags/{tag_id}")
    assert resp.status_code == 204
    # Verify it's gone
    resp = client.get(f"/api/tags/{tag_id}")
    assert resp.status_code == 404


# --- Memory-Tag associations ---


@pytest.fixture(name="memory_id")
def memory_id_fixture(client):
    """Create a test memory and return its ID."""
    resp = client.post(
        "/api/memories",
        json={"title": "Test Memory", "content": "Test content"},
    )
    return resp.json()["id"]


@pytest.fixture(name="tag_id")
def tag_id_fixture(client):
    """Create a test tag and return its ID."""
    resp = client.post("/api/tags", json={"name": "testtag", "color": "#123456"})
    return resp.json()["id"]


def test_add_tags_to_memory(client, memory_id, tag_id):
    resp = client.post(
        f"/api/memories/{memory_id}/tags",
        json={"tag_ids": [tag_id]},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 1
    assert data[0]["tag_id"] == tag_id
    assert data[0]["tag_name"] == "testtag"
    assert data[0]["tag_color"] == "#123456"


def test_add_tags_to_memory_not_found(client, tag_id):
    resp = client.post(
        "/api/memories/nonexistent-id/tags",
        json={"tag_ids": [tag_id]},
    )
    assert resp.status_code == 404


def test_add_duplicate_tag_to_memory(client, memory_id, tag_id):
    """Adding the same tag twice should be idempotent."""
    client.post(
        f"/api/memories/{memory_id}/tags",
        json={"tag_ids": [tag_id]},
    )
    resp = client.post(
        f"/api/memories/{memory_id}/tags",
        json={"tag_ids": [tag_id]},
    )
    assert resp.status_code == 200
    assert len(resp.json()) == 1  # Still just one tag


def test_remove_tag_from_memory(client, memory_id, tag_id):
    client.post(
        f"/api/memories/{memory_id}/tags",
        json={"tag_ids": [tag_id]},
    )
    resp = client.delete(f"/api/memories/{memory_id}/tags/{tag_id}")
    assert resp.status_code == 204
    # Verify it's gone
    resp = client.get(f"/api/memories/{memory_id}/tags")
    assert resp.status_code == 200
    assert len(resp.json()) == 0


def test_list_memory_tags(client, memory_id):
    tag1 = client.post("/api/tags", json={"name": "alpha"}).json()["id"]
    tag2 = client.post("/api/tags", json={"name": "beta"}).json()["id"]
    client.post(
        f"/api/memories/{memory_id}/tags",
        json={"tag_ids": [tag1, tag2]},
    )
    resp = client.get(f"/api/memories/{memory_id}/tags")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    names = [t["tag_name"] for t in data]
    assert "alpha" in names
    assert "beta" in names


def test_delete_tag_removes_associations(client, memory_id, tag_id):
    """Deleting a tag should also remove its memory associations."""
    client.post(
        f"/api/memories/{memory_id}/tags",
        json={"tag_ids": [tag_id]},
    )
    client.delete(f"/api/tags/{tag_id}")
    resp = client.get(f"/api/memories/{memory_id}/tags")
    assert resp.status_code == 200
    assert len(resp.json()) == 0
