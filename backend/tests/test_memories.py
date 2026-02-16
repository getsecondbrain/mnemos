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


# ── Visibility ────────────────────────────────────────────────────────


def test_create_memory_default_visibility(client):
    """POST /api/memories should default visibility to 'public'."""
    resp = client.post("/api/memories", json={"title": "T", "content": "C"})
    assert resp.status_code == 201
    assert resp.json()["visibility"] == "public"


def test_create_memory_with_visibility(client):
    """POST /api/memories with explicit visibility='private'."""
    resp = client.post("/api/memories", json={
        "title": "Private", "content": "Secret", "visibility": "private"
    })
    assert resp.status_code == 201
    assert resp.json()["visibility"] == "private"


def test_list_memories_visibility_filter(client):
    """GET /api/memories?visibility= filters correctly."""
    client.post("/api/memories", json={"title": "Pub", "content": "C", "visibility": "public"})
    client.post("/api/memories", json={"title": "Priv", "content": "C", "visibility": "private"})

    # Default (public) should only return public
    resp = client.get("/api/memories")
    data = resp.json()
    assert all(m["visibility"] == "public" for m in data)

    # Explicit private
    resp = client.get("/api/memories?visibility=private")
    data = resp.json()
    assert all(m["visibility"] == "private" for m in data)

    # All
    resp = client.get("/api/memories?visibility=all")
    data = resp.json()
    visibilities = {m["visibility"] for m in data}
    assert "public" in visibilities
    assert "private" in visibilities


def test_update_memory_visibility(client):
    """PUT /api/memories/{id} can change visibility."""
    resp = client.post("/api/memories", json={"title": "T", "content": "C"})
    memory_id = resp.json()["id"]
    assert resp.json()["visibility"] == "public"

    resp = client.put(f"/api/memories/{memory_id}", json={"visibility": "private"})
    assert resp.status_code == 200
    assert resp.json()["visibility"] == "private"

    # Verify via GET
    resp = client.get(f"/api/memories/{memory_id}")
    assert resp.json()["visibility"] == "private"


def test_create_memory_invalid_visibility(client):
    """POST /api/memories with invalid visibility should return 422."""
    resp = client.post("/api/memories", json={
        "title": "Bad", "content": "C", "visibility": "banana"
    })
    assert resp.status_code == 422


def test_update_memory_invalid_visibility(client):
    """PUT /api/memories/{id} with invalid visibility should return 422."""
    resp = client.post("/api/memories", json={"title": "T", "content": "C"})
    memory_id = resp.json()["id"]
    resp = client.put(f"/api/memories/{memory_id}", json={"visibility": "banana"})
    assert resp.status_code == 422


def test_list_memories_invalid_visibility_query(client):
    """GET /api/memories?visibility=banana should return 422."""
    resp = client.get("/api/memories?visibility=banana")
    assert resp.status_code == 422


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


# ── Cascade Delete ────────────────────────────────────────────────────


class TestDeleteMemoryCascade:
    """Tests for cascade deletion of vault files on memory delete."""

    def test_delete_memory_removes_vault_files(
        self, session, vault_service, mock_embedding_service
    ):
        """DELETE /api/memories/{id} should delete vault .age files."""
        from app.main import app as fastapi_app
        from app.db import get_session
        from app.dependencies import get_vault_service, require_auth
        from app.models.memory import Memory
        from app.models.source import Source
        from fastapi.testclient import TestClient

        # Create a memory with a source that has vault files
        memory = Memory(title="Test", content="Body")
        session.add(memory)
        session.flush()

        # Store two files in the vault
        vault_path, _ = vault_service.store_file(b"original data", "2026", "02")
        preserved_path, _ = vault_service.store_file(b"preserved data", "2026", "02")

        source = Source(
            memory_id=memory.id,
            original_filename_encrypted="deadbeef",
            vault_path=vault_path,
            preserved_vault_path=preserved_path,
            file_size=100,
            original_size=50,
            mime_type="application/pdf",
            preservation_format="pdf",
            content_type="document",
            content_hash="abc123",
        )
        session.add(source)
        session.commit()

        # Verify files exist
        assert vault_service.file_exists(vault_path) is True
        assert vault_service.file_exists(preserved_path) is True

        def _get_session_override():
            yield session

        def _vault_override():
            return vault_service

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        fastapi_app.dependency_overrides[get_vault_service] = _vault_override
        fastapi_app.dependency_overrides[require_auth] = lambda: "test-session"

        # Set mock embedding service on app state
        fastapi_app.state.embedding_service = mock_embedding_service

        try:
            with TestClient(fastapi_app) as tc:
                resp = tc.delete(f"/api/memories/{memory.id}")
                assert resp.status_code == 204

            # Verify vault files are gone
            assert vault_service.file_exists(vault_path) is False
            assert vault_service.file_exists(preserved_path) is False
        finally:
            fastapi_app.dependency_overrides.clear()
            fastapi_app.state.embedding_service = None

    def test_delete_memory_succeeds_when_vault_delete_fails(
        self, session, mock_embedding_service
    ):
        """Memory deletion should succeed even if vault file deletion fails."""
        from unittest.mock import MagicMock
        from app.main import app as fastapi_app
        from app.db import get_session
        from app.dependencies import get_vault_service, require_auth
        from app.models.memory import Memory
        from app.models.source import Source
        from app.services.vault import VaultService
        from fastapi.testclient import TestClient

        memory = Memory(title="Test", content="Body")
        session.add(memory)
        session.flush()
        memory_id = memory.id

        source = Source(
            memory_id=memory_id,
            original_filename_encrypted="deadbeef",
            vault_path="2026/02/fake.age",
            preserved_vault_path=None,
            file_size=100,
            original_size=50,
            mime_type="text/plain",
            preservation_format="txt",
            content_type="text",
            content_hash="abc123",
        )
        session.add(source)
        session.commit()

        # Mock vault service that raises on delete
        mock_vault = MagicMock(spec=VaultService)
        mock_vault.delete_file.side_effect = OSError("disk error")

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        fastapi_app.dependency_overrides[get_vault_service] = lambda: mock_vault
        fastapi_app.dependency_overrides[require_auth] = lambda: "test-session"
        fastapi_app.state.embedding_service = mock_embedding_service

        try:
            with TestClient(fastapi_app) as tc:
                resp = tc.delete(f"/api/memories/{memory.id}")
                # Should still succeed despite vault error
                assert resp.status_code == 204

            # Verify vault delete was attempted
            mock_vault.delete_file.assert_called_once_with("2026/02/fake.age")

            # Verify memory is actually gone from DB (use fresh query to bypass ORM cache)
            session.expire_all()
            assert session.get(Memory, memory_id) is None
        finally:
            fastapi_app.dependency_overrides.clear()
            fastapi_app.state.embedding_service = None

    def test_delete_memory_with_no_sources(
        self, session, vault_service, mock_embedding_service
    ):
        """DELETE should work for memories that have no Source records."""
        from app.main import app as fastapi_app
        from app.db import get_session
        from app.dependencies import get_vault_service, require_auth
        from app.models.memory import Memory
        from fastapi.testclient import TestClient

        memory = Memory(title="No Source", content="text only")
        session.add(memory)
        session.commit()
        memory_id = memory.id

        def _get_session_override():
            yield session

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        fastapi_app.dependency_overrides[get_vault_service] = lambda: vault_service
        fastapi_app.dependency_overrides[require_auth] = lambda: "test-session"
        fastapi_app.state.embedding_service = mock_embedding_service

        try:
            with TestClient(fastapi_app) as tc:
                resp = tc.delete(f"/api/memories/{memory.id}")
                assert resp.status_code == 204

            # Verify memory is gone (expire ORM cache to force fresh DB read)
            session.expire_all()
            assert session.get(Memory, memory_id) is None
        finally:
            fastapi_app.dependency_overrides.clear()
            fastapi_app.state.embedding_service = None
