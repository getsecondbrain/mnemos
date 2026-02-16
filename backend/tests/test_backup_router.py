"""Tests for the backup router endpoints (/api/backup/status, /history, /trigger)."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app.main import app as fastapi_app
from app.db import get_session
from app.dependencies import get_backup_service, require_auth
from app.models.backup import BackupRecord, BackupRecordRead, BackupStatusResponse
from app.services.backup import BackupService


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(name="mock_backup_service")
def mock_backup_service_fixture():
    """Mock BackupService with configurable behavior."""
    mock = MagicMock(spec=BackupService)
    # These attributes are accessed directly by the trigger endpoint
    mock._running = False
    mock._settings = MagicMock()
    mock._settings.restic_repository_local = ""
    mock._settings.restic_repository_b2 = ""
    mock._settings.restic_repository_s3 = ""
    return mock


@pytest.fixture(name="backup_client")
def backup_client_fixture(session, mock_backup_service):
    """TestClient with auth, DB, and backup service overrides."""

    def _get_session_override():
        yield session

    def _auth_override():
        return "test-session-id"

    def _backup_svc_override():
        return mock_backup_service

    fastapi_app.dependency_overrides[get_session] = _get_session_override
    fastapi_app.dependency_overrides[require_auth] = _auth_override
    fastapi_app.dependency_overrides[get_backup_service] = _backup_svc_override

    with TestClient(fastapi_app) as tc:
        yield tc
    fastapi_app.dependency_overrides.clear()


@pytest.fixture(name="backup_client_no_auth")
def backup_client_no_auth_fixture(session, mock_backup_service):
    """TestClient with DB and backup service but NO auth — for testing 401s.

    We still inject the mock_backup_service so the endpoint doesn't 503
    before reaching the auth check.
    """

    def _get_session_override():
        yield session

    def _backup_svc_override():
        return mock_backup_service

    fastapi_app.dependency_overrides[get_session] = _get_session_override
    fastapi_app.dependency_overrides[get_backup_service] = _backup_svc_override
    # Explicitly remove require_auth override if set by another fixture,
    # rather than clearing ALL overrides (which could race with parallel tests).
    fastapi_app.dependency_overrides.pop(require_auth, None)

    with TestClient(fastapi_app) as tc:
        yield tc
    fastapi_app.dependency_overrides.clear()


# ── GET /api/backup/status ───────────────────────────────────────────


class TestBackupStatus:
    def test_backup_status_no_records(self, backup_client, mock_backup_service):
        """No backup records → is_healthy=false, all null fields."""
        mock_backup_service.get_status.return_value = BackupStatusResponse(
            last_successful_backup=None,
            last_backup_status=None,
            last_error=None,
            hours_since_last_success=None,
            is_healthy=False,
            is_running=False,
            recent_records=[],
        )

        resp = backup_client.get("/api/backup/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["last_successful_backup"] is None
        assert data["is_healthy"] is False
        assert data["is_running"] is False
        assert data["recent_records"] == []

    def test_backup_status_healthy(self, backup_client, mock_backup_service):
        """Recent successful backup → is_healthy=true."""
        mock_backup_service.get_status.return_value = BackupStatusResponse(
            last_successful_backup=datetime.now(timezone.utc),
            last_backup_status="succeeded",
            last_error=None,
            hours_since_last_success=2.5,
            is_healthy=True,
            is_running=False,
            recent_records=[],
        )

        resp = backup_client.get("/api/backup/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_healthy"] is True
        assert data["last_backup_status"] == "succeeded"
        assert data["hours_since_last_success"] == 2.5

    def test_backup_status_running(self, backup_client, mock_backup_service):
        """Backup currently in progress → is_running=true."""
        mock_backup_service.get_status.return_value = BackupStatusResponse(
            last_successful_backup=None,
            last_backup_status="in_progress",
            last_error=None,
            hours_since_last_success=None,
            is_healthy=False,
            is_running=True,
            recent_records=[],
        )

        resp = backup_client.get("/api/backup/status")
        assert resp.status_code == 200
        data = resp.json()
        assert data["is_running"] is True

    def test_backup_status_auth_required(self, backup_client_no_auth):
        """GET without auth should return 401 or 403."""
        resp = backup_client_no_auth.get("/api/backup/status")
        assert resp.status_code in (401, 403)


# ── GET /api/backup/history ──────────────────────────────────────────


class TestBackupHistory:
    def test_backup_history_empty(self, backup_client, mock_backup_service):
        """No records → empty list."""
        mock_backup_service.get_history.return_value = []

        resp = backup_client.get("/api/backup/history")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_backup_history_with_records(self, backup_client, mock_backup_service):
        """History returns a list of BackupRecordRead objects."""
        now = datetime.now(timezone.utc)
        mock_backup_service.get_history.return_value = [
            BackupRecordRead(
                id="rec-1",
                started_at=now,
                completed_at=now,
                status="succeeded",
                backup_type="manual",
                repository="/tmp/repo",
                snapshot_id="abc12345",
                size_bytes=1024,
                duration_seconds=5.0,
                error_message=None,
            ),
            BackupRecordRead(
                id="rec-2",
                started_at=now,
                completed_at=now,
                status="failed",
                backup_type="scheduled",
                repository="/tmp/repo",
                snapshot_id=None,
                size_bytes=None,
                duration_seconds=1.0,
                error_message="disk full",
            ),
        ]

        resp = backup_client.get("/api/backup/history")
        assert resp.status_code == 200
        records = resp.json()
        assert len(records) == 2
        assert records[0]["id"] == "rec-1"
        assert records[0]["status"] == "succeeded"
        assert records[1]["id"] == "rec-2"
        assert records[1]["status"] == "failed"
        assert records[1]["error_message"] == "disk full"

    def test_backup_history_auth_required(self, backup_client_no_auth):
        """GET without auth should return 401 or 403."""
        resp = backup_client_no_auth.get("/api/backup/history")
        assert resp.status_code in (401, 403)


# ── POST /api/backup/trigger ────────────────────────────────────────


class TestBackupTrigger:
    def test_trigger_backup_success(self, backup_client, mock_backup_service, session):
        """Trigger with available repo → 200, message='Backup started'."""
        mock_backup_service._running = False
        mock_backup_service._settings.restic_repository_local = "/tmp/repo"

        resp = backup_client.post("/api/backup/trigger")
        assert resp.status_code == 200
        data = resp.json()
        assert data["message"] == "Backup started"
        assert data["record_id"] is not None
        assert len(data["record_id"]) > 0

        # Verify a BackupRecord was created in the DB
        record = session.get(BackupRecord, data["record_id"])
        assert record is not None
        assert record.status == "in_progress"
        assert record.backup_type == "manual"

    def test_trigger_backup_already_running(self, backup_client, mock_backup_service):
        """Trigger while backup is running → 409."""
        mock_backup_service._running = True

        resp = backup_client.post("/api/backup/trigger")
        assert resp.status_code == 409
        assert "already in progress" in resp.json()["detail"]

    def test_trigger_backup_no_repos_configured(self, backup_client, mock_backup_service):
        """Trigger with no repos configured → 400."""
        mock_backup_service._running = False
        mock_backup_service._settings.restic_repository_local = ""
        mock_backup_service._settings.restic_repository_b2 = ""
        mock_backup_service._settings.restic_repository_s3 = ""

        resp = backup_client.post("/api/backup/trigger")
        assert resp.status_code == 400
        assert "No backup repositories configured" in resp.json()["detail"]

    def test_trigger_backup_auth_required(self, backup_client_no_auth):
        """POST without auth should return 401 or 403."""
        resp = backup_client_no_auth.post("/api/backup/trigger")
        assert resp.status_code in (401, 403)
