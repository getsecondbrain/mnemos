"""Tests for suggestions API endpoints."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta

import pytest
from sqlmodel import Session

from app.models.memory import Memory
from app.models.suggestion import Suggestion, SuggestionStatus, SuggestionType
from app.models.tag import MemoryTag, Tag
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
    created_at: datetime | None = None,
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
    if created_at:
        suggestion.created_at = created_at
    session.add(suggestion)
    session.flush()
    return suggestion


@pytest.fixture(name="enc_client")
def enc_client_fixture(client, encryption_service):
    """Client fixture with encryption service override for suggestion endpoints."""
    from app.main import app as fastapi_app
    from app.dependencies import get_encryption_service

    def _enc_override():
        return encryption_service

    fastapi_app.dependency_overrides[get_encryption_service] = _enc_override
    yield client
    # Note: client fixture's cleanup will clear all overrides


# --- List suggestions ---


def test_list_suggestions_empty(enc_client):
    resp = enc_client.get("/api/suggestions")
    assert resp.status_code == 200
    assert resp.json() == []


def test_list_suggestions_returns_pending_only(enc_client, session, encryption_service):
    memory = _create_memory(session)
    _create_suggestion(session, encryption_service, memory.id, status=SuggestionStatus.PENDING.value)
    _create_suggestion(session, encryption_service, memory.id, status=SuggestionStatus.PENDING.value, content="tag2")
    _create_suggestion(session, encryption_service, memory.id, status=SuggestionStatus.DISMISSED.value, content="tag3")
    session.commit()

    resp = enc_client.get("/api/suggestions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert all(s["status"] == "pending" for s in data)


def test_list_suggestions_ordered_most_recent_first(enc_client, session, encryption_service):
    memory = _create_memory(session)
    now = datetime.now(timezone.utc)
    s1 = _create_suggestion(
        session, encryption_service, memory.id,
        content="older", created_at=now - timedelta(hours=2),
    )
    s2 = _create_suggestion(
        session, encryption_service, memory.id,
        content="newer", created_at=now,
    )
    session.commit()

    resp = enc_client.get("/api/suggestions")
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2
    assert data[0]["id"] == s2.id
    assert data[1]["id"] == s1.id


def test_list_suggestions_pagination(enc_client, session, encryption_service):
    memory = _create_memory(session)
    now = datetime.now(timezone.utc)
    for i in range(5):
        _create_suggestion(
            session, encryption_service, memory.id,
            content=f"tag{i}", created_at=now - timedelta(minutes=i),
        )
    session.commit()

    resp = enc_client.get("/api/suggestions", params={"skip": 2, "limit": 2})
    assert resp.status_code == 200
    data = resp.json()
    assert len(data) == 2


def test_list_suggestions_requires_auth(client_no_auth):
    resp = client_no_auth.get("/api/suggestions")
    assert resp.status_code in (401, 403)


# --- Accept suggestions ---


def test_accept_tag_suggestion_creates_tag_and_associates(enc_client, session, encryption_service):
    memory = _create_memory(session)
    suggestion = _create_suggestion(
        session, encryption_service, memory.id,
        suggestion_type=SuggestionType.TAG_SUGGEST.value,
        content="newtag",
    )
    session.commit()

    resp = enc_client.post(f"/api/suggestions/{suggestion.id}/accept")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "accepted"

    # Verify tag was created
    tag = session.exec(
        __import__("sqlmodel").select(Tag).where(Tag.name == "newtag")
    ).first()
    assert tag is not None

    # Verify memory-tag association was created
    assoc = session.exec(
        __import__("sqlmodel").select(MemoryTag).where(
            MemoryTag.memory_id == memory.id,
            MemoryTag.tag_id == tag.id,
        )
    ).first()
    assert assoc is not None


def test_accept_tag_suggestion_existing_tag(enc_client, session, encryption_service):
    memory = _create_memory(session)
    # Create the tag first
    existing_tag = Tag(name="existing")
    session.add(existing_tag)
    session.flush()

    suggestion = _create_suggestion(
        session, encryption_service, memory.id,
        suggestion_type=SuggestionType.TAG_SUGGEST.value,
        content="existing",
    )
    session.commit()

    resp = enc_client.post(f"/api/suggestions/{suggestion.id}/accept")
    assert resp.status_code == 200

    # Verify no duplicate tag was created
    from sqlmodel import select
    tags = session.exec(select(Tag).where(Tag.name == "existing")).all()
    assert len(tags) == 1

    # Verify memory-tag association was created
    assoc = session.exec(
        select(MemoryTag).where(
            MemoryTag.memory_id == memory.id,
            MemoryTag.tag_id == existing_tag.id,
        )
    ).first()
    assert assoc is not None


def test_accept_tag_suggestion_already_associated(enc_client, session, encryption_service):
    memory = _create_memory(session)
    tag = Tag(name="mytag")
    session.add(tag)
    session.flush()
    # Create association before accepting
    session.add(MemoryTag(memory_id=memory.id, tag_id=tag.id))
    session.flush()

    suggestion = _create_suggestion(
        session, encryption_service, memory.id,
        suggestion_type=SuggestionType.TAG_SUGGEST.value,
        content="mytag",
    )
    session.commit()

    resp = enc_client.post(f"/api/suggestions/{suggestion.id}/accept")
    assert resp.status_code == 200
    assert resp.json()["status"] == "accepted"

    # Verify still only one association
    from sqlmodel import select
    assocs = session.exec(
        select(MemoryTag).where(
            MemoryTag.memory_id == memory.id,
            MemoryTag.tag_id == tag.id,
        )
    ).all()
    assert len(assocs) == 1


def test_accept_enrich_suggestion(enc_client, session, encryption_service):
    memory = _create_memory(session)
    suggestion = _create_suggestion(
        session, encryption_service, memory.id,
        suggestion_type=SuggestionType.ENRICH_PROMPT.value,
        content="Some enrichment prompt text",
    )
    session.commit()

    resp = enc_client.post(f"/api/suggestions/{suggestion.id}/accept")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "accepted"


def test_accept_tag_suggestion_memory_deleted(enc_client, session, encryption_service):
    """Accept should 404 when the referenced memory no longer exists."""
    memory = _create_memory(session)
    suggestion = _create_suggestion(
        session, encryption_service, memory.id,
        suggestion_type=SuggestionType.TAG_SUGGEST.value,
        content="orphan-tag",
    )
    session.commit()

    # Simulate an orphaned suggestion by pointing memory_id at a non-existent ID.
    # We must disable FK checks temporarily because SQLite enforces them.
    from sqlalchemy import text
    session.execute(text("PRAGMA foreign_keys=OFF"))
    session.execute(
        text("UPDATE suggestions SET memory_id = :fake WHERE id = :sid"),
        {"fake": "deleted-memory-id", "sid": suggestion.id},
    )
    session.commit()
    session.execute(text("PRAGMA foreign_keys=ON"))

    resp = enc_client.post(f"/api/suggestions/{suggestion.id}/accept")
    assert resp.status_code == 404
    assert "memory" in resp.json()["detail"].lower()


def test_accept_suggestion_not_found(enc_client):
    resp = enc_client.post("/api/suggestions/nonexistent-id/accept")
    assert resp.status_code == 404


def test_accept_suggestion_already_processed(enc_client, session, encryption_service):
    memory = _create_memory(session)
    suggestion = _create_suggestion(
        session, encryption_service, memory.id,
        status=SuggestionStatus.DISMISSED.value,
    )
    session.commit()

    resp = enc_client.post(f"/api/suggestions/{suggestion.id}/accept")
    assert resp.status_code == 409


# --- Dismiss suggestions ---


def test_dismiss_suggestion(client, session, encryption_service):
    memory = _create_memory(session)
    suggestion = _create_suggestion(
        session, encryption_service, memory.id,
    )
    session.commit()

    resp = client.post(f"/api/suggestions/{suggestion.id}/dismiss")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == "dismissed"


def test_dismiss_suggestion_not_found(client):
    resp = client.post("/api/suggestions/nonexistent-id/dismiss")
    assert resp.status_code == 404


def test_dismiss_suggestion_already_processed(client, session, encryption_service):
    memory = _create_memory(session)
    suggestion = _create_suggestion(
        session, encryption_service, memory.id,
        status=SuggestionStatus.ACCEPTED.value,
    )
    session.commit()

    resp = client.post(f"/api/suggestions/{suggestion.id}/dismiss")
    assert resp.status_code == 409
