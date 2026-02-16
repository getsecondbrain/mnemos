"""Tests for the admin reprocess-sources endpoint (POST /api/admin/reprocess-sources)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.main import app as fastapi_app
from app.db import get_session
from app.dependencies import (
    get_current_session_id,
    get_encryption_service,
    get_vault_service,
)
from app.models.memory import Memory
from app.models.source import Source
from app.services.encryption import EncryptionService
from app.services.vault import VaultService


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(name="admin_client")
def admin_client_fixture(session, encryption_service, vault_service):
    """TestClient with auth, DB, encryption, and vault overrides for admin endpoints."""

    def _get_session_override():
        yield session

    def _session_id_override():
        return "test-session-id"

    def _enc_override():
        return encryption_service

    def _vault_override():
        return vault_service

    fastapi_app.dependency_overrides[get_session] = _get_session_override
    fastapi_app.dependency_overrides[get_current_session_id] = _session_id_override
    fastapi_app.dependency_overrides[get_encryption_service] = _enc_override
    fastapi_app.dependency_overrides[get_vault_service] = _vault_override

    with TestClient(fastapi_app) as tc:
        # Set mock worker AFTER TestClient enters (lifespan has run),
        # so we overwrite whatever lifespan set — not the other way around.
        _had_worker = hasattr(fastapi_app.state, "worker")
        orig_worker = getattr(fastapi_app.state, "worker", None)
        mock_worker = MagicMock()
        fastapi_app.state.worker = mock_worker

        yield tc

        # Restore original worker state before exiting the 'with' block
        # (i.e., before lifespan shutdown runs).
        if _had_worker:
            fastapi_app.state.worker = orig_worker
        else:
            try:
                delattr(fastapi_app.state, "worker")
            except AttributeError:
                pass

    fastapi_app.dependency_overrides.clear()


@pytest.fixture(name="admin_client_no_auth")
def admin_client_no_auth_fixture(session, encryption_service, vault_service):
    """TestClient with DB, encryption, and vault overrides but NO auth.

    Overrides vault/encryption deps so they don't 503 if resolved, ensuring
    the auth check fails regardless of FastAPI's dependency resolution order.
    """

    def _get_session_override():
        yield session

    def _enc_override():
        return encryption_service

    def _vault_override():
        return vault_service

    fastapi_app.dependency_overrides[get_session] = _get_session_override
    fastapi_app.dependency_overrides[get_encryption_service] = _enc_override
    fastapi_app.dependency_overrides[get_vault_service] = _vault_override
    # Explicitly do NOT override get_current_session_id — auth must fail

    with TestClient(fastapi_app) as tc:
        # Manage app.state.worker the same way admin_client does, so
        # lifespan shutdown doesn't crash on a real worker set by lifespan.
        _had_worker = hasattr(fastapi_app.state, "worker")
        orig_worker = getattr(fastapi_app.state, "worker", None)
        fastapi_app.state.worker = MagicMock()

        yield tc

        if _had_worker:
            fastapi_app.state.worker = orig_worker
        else:
            try:
                delattr(fastapi_app.state, "worker")
            except AttributeError:
                pass

    fastapi_app.dependency_overrides.clear()


# ── Helpers ──────────────────────────────────────────────────────────


def _create_source_and_memory(
    session: Session,
    mime_type: str = "application/pdf",
    has_text_extract: bool = False,
) -> tuple[Memory, Source]:
    """Insert a Memory + Source pair into the DB. Returns (memory, source)."""
    memory = Memory(
        title="test",
        content="test-content",
        content_type="document",
        source_type="import",
    )
    session.add(memory)
    session.commit()
    session.refresh(memory)

    source = Source(
        memory_id=memory.id,
        original_filename_encrypted="aabbcc",
        filename_dek="ddeeff",
        vault_path="2024/01/test.age",
        file_size=1024,
        original_size=512,
        mime_type=mime_type,
        preservation_format="pdf",
        content_type="document",
        content_hash="abc123",
        text_extract_encrypted="existing" if has_text_extract else None,
        text_extract_dek="existing_dek" if has_text_extract else None,
    )
    session.add(source)
    session.commit()
    session.refresh(source)
    return memory, source


# ── Tests ────────────────────────────────────────────────────────────


class TestReprocessSources:
    @patch("app.routers.admin.PreservationService")
    def test_reprocess_no_candidates(self, _mock_pres_cls, admin_client, session):
        """No eligible sources → total_found=0.

        PreservationService is still patched because admin.py creates it
        before querying for candidates (line 73), so the real constructor
        would run with get_settings().tmp_dir even when no candidates exist.
        """
        resp = admin_client.post("/api/admin/reprocess-sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_found"] == 0
        assert data["reprocessed"] == 0
        assert data["failed"] == 0
        assert data["skipped"] == 0
        assert data["details"] == []

    @patch("app.routers.admin.PreservationService")
    def test_reprocess_happy_path(
        self, mock_pres_cls, admin_client, session, vault_service
    ):
        """One eligible PDF source → reprocessed=1, source gets text_extract."""
        memory, source = _create_source_and_memory(session, mime_type="application/pdf")

        # Mock vault retrieval
        vault_service.retrieve_file = MagicMock(return_value=b"fake-pdf-bytes")

        # Mock preservation service
        mock_pres = mock_pres_cls.return_value
        mock_pres.convert = AsyncMock(
            return_value=MagicMock(
                text_extract="Extracted text from PDF",
                preserved_data=b"preserved",
                conversion_performed=False,
            )
        )

        resp = admin_client.post("/api/admin/reprocess-sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_found"] == 1
        assert data["reprocessed"] == 1
        assert data["failed"] == 0
        assert data["skipped"] == 0

        # Verify source was updated in DB
        session.refresh(source)
        assert source.text_extract_encrypted is not None
        assert source.text_extract_dek is not None

        # Verify memory content was updated
        session.refresh(memory)
        assert memory.content_dek is not None

        # Verify worker.submit_job was called
        worker = fastapi_app.state.worker
        assert worker.submit_job.called

    @patch("app.routers.admin.PreservationService")
    def test_reprocess_skips_already_extracted(self, mock_pres_cls, admin_client, session):
        """Source with existing text_extract should not appear as a candidate."""
        _create_source_and_memory(session, has_text_extract=True)

        resp = admin_client.post("/api/admin/reprocess-sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_found"] == 0

    @patch("app.routers.admin.PreservationService")
    def test_reprocess_skips_non_reprocessable_mime(self, mock_pres_cls, admin_client, session):
        """Source with audio/mp3 MIME type should not appear as a candidate."""
        _create_source_and_memory(session, mime_type="audio/mp3")

        resp = admin_client.post("/api/admin/reprocess-sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_found"] == 0

    @patch("app.routers.admin.PreservationService")
    def test_reprocess_skips_empty_extraction(
        self, mock_pres_cls, admin_client, session, vault_service
    ):
        """Preservation returns empty text → skipped=1."""
        _create_source_and_memory(session, mime_type="application/pdf")
        vault_service.retrieve_file = MagicMock(return_value=b"fake-pdf")

        mock_pres = mock_pres_cls.return_value
        mock_pres.convert = AsyncMock(
            return_value=MagicMock(
                text_extract="",
                preserved_data=b"preserved",
                conversion_performed=False,
            )
        )

        resp = admin_client.post("/api/admin/reprocess-sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_found"] == 1
        assert data["skipped"] == 1

    @patch("app.routers.admin.PreservationService")
    def test_reprocess_vault_retrieval_failure(
        self, mock_pres_cls, admin_client, session, vault_service
    ):
        """Vault retrieval raises FileNotFoundError → failed=1."""
        _create_source_and_memory(session, mime_type="application/pdf")
        vault_service.retrieve_file = MagicMock(side_effect=FileNotFoundError("not found"))

        resp = admin_client.post("/api/admin/reprocess-sources")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_found"] == 1
        assert data["failed"] == 1
        assert data["details"][0]["status"] == "failed"
        assert data["details"][0]["error"] is not None

    @patch("app.routers.admin.PreservationService")
    def test_reprocess_idempotent_second_call(
        self, mock_pres_cls, admin_client, session, vault_service
    ):
        """Second reprocess call finds no candidates because first call filled text_extract."""
        _create_source_and_memory(session, mime_type="application/pdf")
        vault_service.retrieve_file = MagicMock(return_value=b"fake-pdf")

        mock_pres = mock_pres_cls.return_value
        mock_pres.convert = AsyncMock(
            return_value=MagicMock(
                text_extract="Extracted text",
                preserved_data=b"preserved",
                conversion_performed=False,
            )
        )

        # First call
        resp1 = admin_client.post("/api/admin/reprocess-sources")
        assert resp1.status_code == 200
        assert resp1.json()["reprocessed"] == 1

        # Second call — source already has text_extract now
        resp2 = admin_client.post("/api/admin/reprocess-sources")
        assert resp2.status_code == 200
        assert resp2.json()["total_found"] == 0

    def test_reprocess_auth_required(self, admin_client_no_auth):
        """POST without auth should return 401 or 403.

        Uses admin_client_no_auth which overrides vault/encryption deps (so they
        don't 503 if resolved) but does NOT override auth, ensuring the auth
        check fails regardless of FastAPI's dependency resolution order.
        """
        resp = admin_client_no_auth.post("/api/admin/reprocess-sources")
        assert resp.status_code in (401, 403)
