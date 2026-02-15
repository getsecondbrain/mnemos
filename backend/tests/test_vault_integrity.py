"""Tests for vault-wide integrity verification (D7.3)."""

from __future__ import annotations

from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.models.memory import Memory
from app.models.source import Source
from app.services.vault import VaultService


# ── Helpers ──────────────────────────────────────────────────────────


def _make_memory(session) -> str:
    """Create a minimal Memory record and return its ID."""
    mem = Memory(
        title="test",
        content="test",
        encryption_algo="aes-256-gcm",
        encryption_version=1,
        content_hash="fakehash",
        content_type="document",
    )
    session.add(mem)
    session.flush()
    return mem.id


def _make_source(session, vault_service: VaultService, memory_id: str, data: bytes = b"test", **overrides):
    """Store file in vault + create matching Source record."""
    vault_path, content_hash = vault_service.store_file(data, "2026", "02")
    source = Source(
        memory_id=memory_id,
        original_filename_encrypted="encrypted-name",
        vault_path=vault_path,
        file_size=100,
        original_size=len(data),
        mime_type="application/octet-stream",
        preservation_format="raw",
        content_type="document",
        content_hash=content_hash,
        **overrides,
    )
    session.add(source)
    session.commit()
    return source


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(name="vault_client")
def vault_client_fixture(session, vault_service):
    """Client with vault service + auth overrides for vault health endpoint."""
    from app.main import app as fastapi_app
    from app.db import get_session
    from app.dependencies import require_auth, get_vault_service

    def _get_session_override():
        yield session

    def _require_auth_override() -> str:
        return "test-session-id"

    def _vault_override():
        return vault_service

    fastapi_app.dependency_overrides[get_session] = _get_session_override
    fastapi_app.dependency_overrides[require_auth] = _require_auth_override
    fastapi_app.dependency_overrides[get_vault_service] = _vault_override

    with TestClient(fastapi_app) as tc:
        yield tc
    fastapi_app.dependency_overrides.clear()


# ── VaultService.verify_all() tests ─────────────────────────────────


def test_verify_all_healthy_vault(session, vault_service):
    """Create 5 Sources with matching vault files. Assert healthy=True, all counts 0."""
    for _ in range(5):
        mem_id = _make_memory(session)
        _make_source(session, vault_service, mem_id, data=f"file-{_}".encode())

    result = vault_service.verify_all(session)

    assert result["healthy"] is True
    assert result["total_sources"] == 5
    assert result["missing_count"] == 0
    assert result["orphan_count"] == 0
    assert result["hash_mismatch_count"] == 0


def test_verify_all_missing_file(session, vault_service, vault_dir):
    """Create Source record, don't create .age file. Assert missing_count=1, healthy=False."""
    mem_id = _make_memory(session)
    source = _make_source(session, vault_service, mem_id)

    # Delete the .age file from disk
    full_path = vault_dir / source.vault_path
    full_path.unlink()

    result = vault_service.verify_all(session)

    assert result["healthy"] is False
    assert result["missing_count"] == 1
    assert result["missing_files"][0]["source_id"] == source.id
    assert result["missing_files"][0]["type"] == "original"


def test_verify_all_orphan_file(session, vault_service, vault_dir):
    """Create .age files on disk with no Source record. Assert orphan_count>0, healthy=False."""
    # Create an orphan .age file
    orphan_dir = vault_dir / "2026" / "01"
    orphan_dir.mkdir(parents=True, exist_ok=True)
    orphan_file = orphan_dir / f"{uuid4()}.age"
    orphan_file.write_bytes(b"orphan data")

    result = vault_service.verify_all(session)

    assert result["healthy"] is False
    assert result["orphan_count"] >= 1
    assert any("2026/01/" in f for f in result["orphan_files"])


def test_verify_all_decrypt_error(session, vault_service, vault_dir):
    """Store file, then overwrite .age on disk with garbage. Assert decrypt_error_count=1.

    Garbage data causes pyrage.DecryptError which is now tracked separately
    from hash mismatches (possible key rotation vs actual corruption).
    """
    mem_id = _make_memory(session)
    source = _make_source(session, vault_service, mem_id, data=b"real content")

    # Overwrite the .age file with garbage — causes DecryptError, not hash mismatch
    full_path = vault_dir / source.vault_path
    full_path.write_bytes(b"corrupted garbage data")

    result = vault_service.verify_all(session, sample_pct=1.0)

    assert result["healthy"] is False
    assert result["decrypt_error_count"] == 1
    assert result["decrypt_errors"][0]["source_id"] == source.id
    # Hash mismatches should be 0 — this is a decrypt error, not a hash mismatch
    assert result["hash_mismatch_count"] == 0


def test_verify_all_hash_mismatch(session, vault_service, vault_dir):
    """Store file with wrong content_hash in DB. Assert hash_mismatch_count=1."""
    import pyrage
    from app.models.source import Source as SourceModel

    mem_id = _make_memory(session)
    source = _make_source(session, vault_service, mem_id, data=b"real content")

    # Tamper with the stored hash in the DB so it won't match the actual file
    source.content_hash = "0000000000000000000000000000000000000000000000000000000000000000"
    session.add(source)
    session.commit()

    result = vault_service.verify_all(session, sample_pct=1.0)

    assert result["healthy"] is False
    assert result["hash_mismatch_count"] == 1
    assert result["hash_mismatches"][0]["source_id"] == source.id
    assert result["decrypt_error_count"] == 0


def test_verify_all_zero_sample_skips_hashes(session, vault_service):
    """verify_all(sample_pct=0.0) — assert hash_checked=0."""
    mem_id = _make_memory(session)
    _make_source(session, vault_service, mem_id)

    result = vault_service.verify_all(session, sample_pct=0.0)

    assert result["hash_checked"] == 0
    assert result["hash_mismatch_count"] == 0


def test_verify_all_empty_vault(session, vault_service):
    """No sources, no files. Assert healthy=True, all counts 0."""
    result = vault_service.verify_all(session)

    assert result["healthy"] is True
    assert result["total_sources"] == 0
    assert result["total_disk_files"] == 0
    assert result["missing_count"] == 0
    assert result["orphan_count"] == 0
    assert result["hash_checked"] == 0


def test_verify_all_preserved_path_checked(session, vault_service, vault_dir):
    """Create Source with preserved_vault_path. Delete preserved file. Assert missing includes preserved type."""
    mem_id = _make_memory(session)

    # Store the preserved file too
    preserved_path, _ = vault_service.store_file(b"preserved content", "2026", "02")

    source = _make_source(
        session, vault_service, mem_id, data=b"original content",
        preserved_vault_path=preserved_path,
    )

    # Delete the preserved file
    full_path = vault_dir / preserved_path
    full_path.unlink()

    result = vault_service.verify_all(session)

    assert result["healthy"] is False
    assert result["missing_count"] == 1
    missing = result["missing_files"][0]
    assert missing["source_id"] == source.id
    assert missing["type"] == "preserved"
    assert missing["vault_path"] == preserved_path


# ── API endpoint tests ───────────────────────────────────────────────


def test_health_vault_endpoint_returns_result(session, vault_service, vault_client):
    """GET /api/health/vault with auth. Assert 200 with expected structure."""
    # Create a source so there's something to check
    mem_id = _make_memory(session)
    _make_source(session, vault_service, mem_id)

    resp = vault_client.get("/api/health/vault")

    assert resp.status_code == 200
    data = resp.json()
    assert "healthy" in data
    assert "total_sources" in data
    assert "missing_count" in data
    assert "orphan_count" in data
    assert "hash_checked" in data
    assert "checked_at" in data
    assert "decrypt_error_count" in data
    assert data["healthy"] is True


def test_health_vault_endpoint_requires_auth(session, vault_service):
    """GET /api/health/vault without auth. Assert 401/403."""
    from app.main import app as fastapi_app
    from app.db import get_session
    from app.dependencies import get_vault_service

    def _get_session_override():
        yield session

    def _vault_override():
        return vault_service

    fastapi_app.dependency_overrides[get_session] = _get_session_override
    fastapi_app.dependency_overrides[get_vault_service] = _vault_override
    # NOTE: require_auth is NOT overridden — so auth is enforced

    with TestClient(fastapi_app) as tc:
        resp = tc.get("/api/health/vault")

    fastapi_app.dependency_overrides.clear()
    assert resp.status_code in (401, 403)


def test_health_vault_endpoint_caps_sample_pct(session, vault_service, vault_client):
    """GET /api/health/vault?sample_pct=1.0 — server caps to 0.5 max."""
    # Create several sources so we can observe the cap in action
    for i in range(10):
        mem_id = _make_memory(session)
        _make_source(session, vault_service, mem_id, data=f"file-{i}".encode())

    resp = vault_client.get("/api/health/vault?sample_pct=1.0")

    assert resp.status_code == 200
    data = resp.json()
    # With 10 files and a cap of 0.5, at most 5 should be hash-checked
    assert data["hash_checked"] <= 5


def test_health_endpoint_includes_vault_summary(session, vault_service, vault_client):
    """Set app.state.last_vault_health, then GET /api/health. Assert vault key in checks."""
    from app.main import app as fastapi_app

    # Simulate a previous vault check result cached on app state
    fastapi_app.state.last_vault_health = {
        "checked_at": "2026-02-15T00:00:00+00:00",
        "healthy": True,
        "total_sources": 5,
        "total_disk_files": 5,
        "missing_count": 0,
        "orphan_count": 0,
        "hash_checked": 1,
        "hash_mismatch_count": 0,
        "decrypt_error_count": 0,
    }

    resp = vault_client.get("/api/health")

    assert resp.status_code == 200
    data = resp.json()
    assert "vault" in data["checks"]
    vault = data["checks"]["vault"]
    assert vault["status"] == "healthy"
    assert vault["last_checked"] == "2026-02-15T00:00:00+00:00"
    assert vault["missing_count"] == 0
    assert vault["orphan_count"] == 0
    assert vault["hash_mismatch_count"] == 0
    assert vault["decrypt_error_count"] == 0

    # Clean up
    if hasattr(fastapi_app.state, "last_vault_health"):
        del fastapi_app.state.last_vault_health
