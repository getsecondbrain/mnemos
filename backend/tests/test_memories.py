from __future__ import annotations


def test_create_memory(client):
    """POST /api/memories should create a new memory and return 201."""
    payload = {
        "title": "Test Memory",
        "content": "This is a test memory for integration testing.",
    }
    response = client.post("/api/memories", json=payload)
    assert response.status_code == 201
    data = response.json()
    assert data["title"] == "Test Memory"
    assert data["content"] == "This is a test memory for integration testing."
    assert data["content_type"] == "text"
    assert data["source_type"] == "manual"
    assert "id" in data
    assert "created_at" in data
    assert "updated_at" in data
    assert "captured_at" in data


def test_create_memory_missing_fields(client):
    """POST /api/memories with missing required fields should return 422."""
    response = client.post("/api/memories", json={"title": "No content"})
    assert response.status_code == 422


def test_list_memories(client):
    """GET /api/memories should return a list."""
    # Create two memories first
    client.post("/api/memories", json={"title": "Mem 1", "content": "Content 1"})
    client.post("/api/memories", json={"title": "Mem 2", "content": "Content 2"})

    response = client.get("/api/memories")
    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 2


def test_list_memories_pagination(client):
    """GET /api/memories with skip and limit should paginate."""
    # Create 3 memories
    for i in range(3):
        client.post("/api/memories", json={"title": f"Page {i}", "content": f"Content {i}"})

    response = client.get("/api/memories?skip=0&limit=2")
    assert response.status_code == 200
    data = response.json()
    assert len(data) <= 2


def test_get_memory(client):
    """GET /api/memories/{id} should return the specific memory."""
    create_resp = client.post("/api/memories", json={"title": "Fetch Me", "content": "Body"})
    memory_id = create_resp.json()["id"]

    response = client.get(f"/api/memories/{memory_id}")
    assert response.status_code == 200
    data = response.json()
    assert data["id"] == memory_id
    assert data["title"] == "Fetch Me"


def test_get_memory_not_found(client):
    """GET /api/memories/{bad_id} should return 404."""
    response = client.get("/api/memories/nonexistent-id")
    assert response.status_code == 404


def test_update_memory(client):
    """PUT /api/memories/{id} should update the memory."""
    create_resp = client.post("/api/memories", json={"title": "Original", "content": "Body"})
    memory_id = create_resp.json()["id"]

    response = client.put(
        f"/api/memories/{memory_id}",
        json={"title": "Updated Title", "content": "Updated Body"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Updated Title"
    assert data["content"] == "Updated Body"


def test_update_memory_partial(client):
    """PUT /api/memories/{id} with partial fields should only update those fields."""
    create_resp = client.post("/api/memories", json={"title": "Keep Me", "content": "Original"})
    memory_id = create_resp.json()["id"]

    response = client.put(f"/api/memories/{memory_id}", json={"content": "Changed"})
    assert response.status_code == 200
    data = response.json()
    assert data["title"] == "Keep Me"
    assert data["content"] == "Changed"


def test_delete_memory(client):
    """DELETE /api/memories/{id} should return 204 and remove the memory."""
    create_resp = client.post("/api/memories", json={"title": "Delete Me", "content": "Body"})
    memory_id = create_resp.json()["id"]

    response = client.delete(f"/api/memories/{memory_id}")
    assert response.status_code == 204

    # Verify it's gone
    get_resp = client.get(f"/api/memories/{memory_id}")
    assert get_resp.status_code == 404


def test_delete_memory_not_found(client):
    """DELETE /api/memories/{bad_id} should return 404."""
    response = client.delete("/api/memories/nonexistent-id")
    assert response.status_code == 404


# ── Edge Cases ────────────────────────────────────────────────────────


class TestMemoryEdgeCases:
    def test_create_memory_with_empty_title(self, client):
        """POST with empty title still succeeds (title is just ciphertext)."""
        resp = client.post("/api/memories", json={"title": "", "content": "body"})
        assert resp.status_code == 201
        assert resp.json()["title"] == ""

    def test_create_memory_max_length_content(self, client):
        """POST with large content (1MB hex string) succeeds."""
        big_content = "a" * (1024 * 1024)
        resp = client.post("/api/memories", json={"title": "Big", "content": big_content})
        assert resp.status_code == 201

    def test_create_memory_with_metadata_json(self, client):
        """POST with metadata_json field stores it."""
        payload = {
            "title": "Meta",
            "content": "Body",
            "metadata_json": '{"tags": ["test"]}',
        }
        resp = client.post("/api/memories", json=payload)
        assert resp.status_code == 201

    def test_list_memories_empty_db(self, client):
        """GET /api/memories on empty DB returns empty list."""
        resp = client.get("/api/memories")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_memories_pagination_bounds(self, client):
        """GET /api/memories with skip > total returns empty list."""
        client.post("/api/memories", json={"title": "One", "content": "C"})
        resp = client.get("/api/memories?skip=100&limit=10")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_update_nonexistent_memory_404(self, client):
        """PUT /api/memories/{bad_id} returns 404."""
        resp = client.put(
            "/api/memories/nonexistent-id",
            json={"title": "Nope", "content": "Nope"},
        )
        assert resp.status_code == 404

    def test_delete_already_deleted_404(self, client):
        """DELETE a memory twice returns 404 on second attempt."""
        create_resp = client.post("/api/memories", json={"title": "Del2x", "content": "Body"})
        memory_id = create_resp.json()["id"]

        client.delete(f"/api/memories/{memory_id}")
        resp = client.delete(f"/api/memories/{memory_id}")
        assert resp.status_code == 404

    def test_concurrent_creates(self, client):
        """Multiple POSTs produce unique IDs."""
        ids = set()
        for i in range(5):
            resp = client.post("/api/memories", json={"title": f"C{i}", "content": f"C{i}"})
            assert resp.status_code == 201
            ids.add(resp.json()["id"])
        assert len(ids) == 5
