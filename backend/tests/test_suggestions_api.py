"""Tests for loop settings API endpoints and additional suggestion integration tests."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest
from sqlmodel import Session

from app.models.memory import Memory
from app.models.suggestion import (
    LoopState,
    Suggestion,
    SuggestionStatus,
    SuggestionType,
)
from app.services.encryption import EncryptionService


def _create_memory(session: Session) -> Memory:
    """Create a test memory and return it."""
    memory = Memory(title="Test Memory", content="Test content")
    session.add(memory)
    session.flush()
    return memory


def _create_suggestion(
    session: Session,
    enc: EncryptionService,
    memory_id: str,
    suggestion_type: str = SuggestionType.TAG_SUGGEST.value,
    status: str = SuggestionStatus.PENDING.value,
    content: str = "test-tag",
) -> Suggestion:
    """Create a suggestion with encrypted content."""
    envelope = enc.encrypt(content.encode("utf-8"))
    suggestion = Suggestion(
        memory_id=memory_id,
        suggestion_type=suggestion_type,
        content_encrypted=envelope.ciphertext.hex(),
        content_dek=envelope.encrypted_dek.hex(),
        encryption_algo=envelope.algo,
        encryption_version=envelope.version,
        status=status,
    )
    session.add(suggestion)
    session.flush()
    return suggestion


def _seed_loop_states(session: Session) -> list[LoopState]:
    """Create loop state records for testing."""
    now = datetime.now(timezone.utc)
    loops = [
        LoopState(
            loop_name="tag_suggest",
            last_run_at=now - timedelta(hours=1),
            next_run_at=now + timedelta(hours=5),
            enabled=True,
        ),
        LoopState(
            loop_name="enrich_prompt",
            last_run_at=None,
            next_run_at=now + timedelta(hours=12),
            enabled=True,
        ),
        LoopState(
            loop_name="connection_rescan",
            last_run_at=now - timedelta(days=1),
            next_run_at=now + timedelta(days=1),
            enabled=False,
        ),
    ]
    for ls in loops:
        session.add(ls)
    session.flush()
    session.commit()
    return loops


# --- Loop settings: GET ---


def test_get_loop_settings_empty(client):
    """Returns empty list when no loop states exist."""
    resp = client.get("/api/settings/loops")
    assert resp.status_code == 200
    assert resp.json() == []


def test_get_loop_settings(client, session):
    """Returns all loop states."""
    _seed_loop_states(session)

    resp = client.get("/api/settings/loops")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 3

    names = {d["loop_name"] for d in data}
    assert names == {"tag_suggest", "enrich_prompt", "connection_rescan"}

    # Check structure of a loop state
    for item in data:
        assert "loop_name" in item
        assert "last_run_at" in item
        assert "next_run_at" in item
        assert "enabled" in item


def test_get_loop_settings_requires_auth(client_no_auth):
    resp = client_no_auth.get("/api/settings/loops")
    assert resp.status_code in (401, 403)


# --- Loop settings: PUT ---


def test_update_loop_enabled(client, session):
    """Toggle a loop off, verify it's disabled."""
    _seed_loop_states(session)

    resp = client.put(
        "/api/settings/loops/tag_suggest",
        json={"enabled": False},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["loop_name"] == "tag_suggest"
    assert data["enabled"] is False


def test_update_loop_enable(client, session):
    """Toggle a disabled loop on, verify it's enabled."""
    _seed_loop_states(session)

    resp = client.put(
        "/api/settings/loops/connection_rescan",
        json={"enabled": True},
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["loop_name"] == "connection_rescan"
    assert data["enabled"] is True


def test_update_loop_nonexistent(client):
    """Returns 404 for non-existent loop name."""
    resp = client.put(
        "/api/settings/loops/nonexistent_loop",
        json={"enabled": True},
    )
    assert resp.status_code == 404


def test_update_loop_persists(client, session):
    """Verify the toggle persists across requests."""
    _seed_loop_states(session)

    # Disable
    resp = client.put(
        "/api/settings/loops/enrich_prompt",
        json={"enabled": False},
    )
    assert resp.status_code == 200

    # Verify via GET
    resp = client.get("/api/settings/loops")
    assert resp.status_code == 200
    data = resp.json()
    enrich = next(d for d in data if d["loop_name"] == "enrich_prompt")
    assert enrich["enabled"] is False


def test_update_loop_requires_auth(client_no_auth):
    resp = client_no_auth.put(
        "/api/settings/loops/tag_suggest",
        json={"enabled": False},
    )
    assert resp.status_code in (401, 403)
