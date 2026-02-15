"""Tests for /api/vault router endpoints."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from sqlmodel import Session

from app.main import app as fastapi_app
from app.db import get_session
from app.dependencies import get_vault_service, require_auth
from app.models.memory import Memory
from app.models.source import Source
from app.services.vault import VaultService


def _create_test_memory(session: Session) -> Memory:
    """Create a minimal Memory record so Source FK constraint is satisfied."""
    memory = Memory(
        title="test-title",
        content="test-content",
        content_type="text",
        source_type="manual",
    )
    session.add(memory)
    session.commit()
    session.refresh(memory)
    return memory


class TestRetrieveOriginal:
    def test_retrieve_existing_source(self, client, session, vault_service, vault_dir):
        """GET /api/vault/{source_id} returns decrypted content."""
        # Store a file in the vault
        data = b"original file content"
        vault_path, content_hash = vault_service.store_file(data, "2026", "02")

        # Create a Memory first (FK requirement)
        memory = _create_test_memory(session)

        # Create a Source record
        source = Source(
            memory_id=memory.id,
            original_filename_encrypted="encrypted-name",
            filename_dek="fake-dek",
            vault_path=vault_path,
            file_size=100,
            original_size=len(data),
            mime_type="text/plain",
            preservation_format="markdown",
            content_type="text",
            content_hash=content_hash,
        )
        session.add(source)
        session.commit()
        session.refresh(source)

        # Override vault_service dependency
        fastapi_app.dependency_overrides[get_vault_service] = lambda: vault_service

        resp = client.get(f"/api/vault/{source.id}")
        assert resp.status_code == 200
        assert resp.content == data

        fastapi_app.dependency_overrides.pop(get_vault_service, None)

    def test_retrieve_nonexistent_source_404(self, client):
        """GET /api/vault/{bad_id} returns 404."""
        resp = client.get("/api/vault/nonexistent-source-id")
        assert resp.status_code == 404

    def test_unauthenticated_401(self, client_no_auth):
        """GET /api/vault/{id} without auth returns 401/403."""
        resp = client_no_auth.get("/api/vault/any-source-id")
        assert resp.status_code in (401, 403)


class TestRetrievePreserved:
    def test_retrieve_preserved_copy(self, client, session, vault_service, vault_dir):
        """GET /api/vault/{source_id}/preserved returns archival copy."""
        orig_data = b"original"
        pres_data = b"preserved archival copy"
        orig_path, content_hash = vault_service.store_file(orig_data, "2026", "02")
        pres_path, _ = vault_service.store_file(pres_data, "2026", "02")

        # Create a Memory first (FK requirement)
        memory = _create_test_memory(session)

        source = Source(
            memory_id=memory.id,
            original_filename_encrypted="enc-name",
            filename_dek="fake-dek",
            vault_path=orig_path,
            preserved_vault_path=pres_path,
            file_size=100,
            original_size=len(orig_data),
            mime_type="image/jpeg",
            preservation_format="png",
            content_type="photo",
            content_hash=content_hash,
        )
        session.add(source)
        session.commit()
        session.refresh(source)

        fastapi_app.dependency_overrides[get_vault_service] = lambda: vault_service

        resp = client.get(f"/api/vault/{source.id}/preserved")
        assert resp.status_code == 200
        assert resp.content == pres_data

        fastapi_app.dependency_overrides.pop(get_vault_service, None)

    def test_no_preserved_copy_404(self, client, session, vault_service):
        """GET /api/vault/{id}/preserved when no preserved copy returns 404."""
        data = b"no preserved"
        vault_path, content_hash = vault_service.store_file(data, "2026", "02")

        # Create a Memory first (FK requirement)
        memory = _create_test_memory(session)

        source = Source(
            memory_id=memory.id,
            original_filename_encrypted="enc",
            filename_dek="dek",
            vault_path=vault_path,
            preserved_vault_path=None,
            file_size=100,
            original_size=len(data),
            mime_type="text/plain",
            preservation_format="markdown",
            content_type="text",
            content_hash=content_hash,
        )
        session.add(source)
        session.commit()
        session.refresh(source)

        fastapi_app.dependency_overrides[get_vault_service] = lambda: vault_service

        resp = client.get(f"/api/vault/{source.id}/preserved")
        assert resp.status_code == 404

        fastapi_app.dependency_overrides.pop(get_vault_service, None)

    def test_nonexistent_source_404(self, client):
        """GET /api/vault/{bad_id}/preserved returns 404."""
        resp = client.get("/api/vault/nonexistent-id/preserved")
        assert resp.status_code == 404
