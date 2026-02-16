from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi.testclient import TestClient

from app.main import app as fastapi_app
from app.db import get_session
from app.dependencies import get_encryption_service, get_llm_service, require_auth
from app.models.memory import Memory
from app.models.reflection import ReflectionPrompt
from app.services.llm import LLMError, LLMResponse, LLMService


@pytest.fixture(name="mock_llm")
def mock_llm_fixture() -> MagicMock:
    """Mock LLMService that returns a known reflection prompt (no cloud fallback)."""
    mock = MagicMock(spec=LLMService)
    mock.has_fallback = False  # Local-only — safe to send decrypted content
    mock.generate = AsyncMock(
        return_value=LLMResponse(
            text="How did this experience shape your perspective?",
            model="test-model",
            total_duration_ms=50,
            backend="ollama",
        )
    )
    return mock


@pytest.fixture(name="reflect_client")
def reflect_client_fixture(session, mock_llm):
    """TestClient with session, auth, encryption, and LLM overrides for reflect tests."""
    from app.services.encryption import EncryptionService

    def _get_session_override():
        yield session

    def _require_auth_override() -> str:
        return "test-session-id"

    # Use a dummy encryption service — reflect tests with content_dek
    # will need a properly-initialized one, but for plain text tests this suffices.
    dummy_enc = EncryptionService(b"\x00" * 32)

    fastapi_app.dependency_overrides[get_session] = _get_session_override
    fastapi_app.dependency_overrides[require_auth] = _require_auth_override
    fastapi_app.dependency_overrides[get_llm_service] = lambda: mock_llm
    fastapi_app.dependency_overrides[get_encryption_service] = lambda: dummy_enc

    with TestClient(fastapi_app) as tc:
        yield tc

    fastapi_app.dependency_overrides.clear()


class TestReflect:
    """Tests for GET /api/memories/{memory_id}/reflect."""

    def test_reflect_returns_prompt_from_llm(self, session, reflect_client, mock_llm):
        """Create a plaintext memory, call reflect, assert LLM response returned."""
        memory = Memory(
            title="Beach Trip",
            content="We went to the beach and watched the sunset.",
            captured_at=datetime(2023, 7, 15, tzinfo=timezone.utc),
        )
        session.add(memory)
        session.commit()

        resp = reflect_client.get(f"/api/memories/{memory.id}/reflect")
        assert resp.status_code == 200
        data = resp.json()
        assert "prompt" in data
        assert data["prompt"] == "How did this experience shape your perspective?"
        mock_llm.generate.assert_called_once()

    def test_reflect_returns_cached_prompt(self, session, reflect_client, mock_llm):
        """Pre-insert a fresh cached prompt; endpoint should return it without calling LLM."""
        memory = Memory(
            title="Cached Memory",
            content="Some content here.",
            captured_at=datetime(2022, 3, 10, tzinfo=timezone.utc),
        )
        session.add(memory)
        session.flush()

        cached = ReflectionPrompt(
            memory_id=memory.id,
            prompt_text="What made this moment special to you?",
            generated_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        session.add(cached)
        session.commit()

        resp = reflect_client.get(f"/api/memories/{memory.id}/reflect")
        assert resp.status_code == 200
        assert resp.json()["prompt"] == "What made this moment special to you?"
        mock_llm.generate.assert_not_called()

    def test_reflect_regenerates_after_24_hours(self, session, reflect_client, mock_llm):
        """Expired cached prompt (>24h) should trigger a new LLM call."""
        memory = Memory(
            title="Old Cache",
            content="Content from long ago.",
            captured_at=datetime(2021, 12, 1, tzinfo=timezone.utc),
        )
        session.add(memory)
        session.flush()

        expired = ReflectionPrompt(
            memory_id=memory.id,
            prompt_text="Stale prompt",
            generated_at=datetime.now(timezone.utc) - timedelta(hours=25),
        )
        session.add(expired)
        session.commit()

        resp = reflect_client.get(f"/api/memories/{memory.id}/reflect")
        assert resp.status_code == 200
        # Should get the new LLM-generated prompt, not the stale one
        assert resp.json()["prompt"] == "How did this experience shape your perspective?"
        mock_llm.generate.assert_called_once()

    def test_reflect_memory_not_found(self, reflect_client):
        """Non-existent memory ID should return 404."""
        resp = reflect_client.get("/api/memories/nonexistent-id/reflect")
        assert resp.status_code == 404

    def test_reflect_llm_unavailable(self, session, reflect_client, mock_llm):
        """LLM raising LLMError should return 503 with generic message."""
        mock_llm.generate = AsyncMock(side_effect=LLMError("connection refused"))

        memory = Memory(
            title="LLM Down",
            content="Content when LLM is down.",
            captured_at=datetime(2024, 1, 1, tzinfo=timezone.utc),
        )
        session.add(memory)
        session.commit()

        resp = reflect_client.get(f"/api/memories/{memory.id}/reflect")
        assert resp.status_code == 503
        data = resp.json()
        assert data["detail"] == "Reflection generation unavailable"
        # Should NOT leak internal error details
        assert "connection refused" not in str(data)

    def test_reflect_requires_auth(self, session):
        """The endpoint rejects unauthenticated requests."""
        from app.services.encryption import EncryptionService

        mock_llm = MagicMock(spec=LLMService)
        mock_llm.has_fallback = False
        dummy_enc = EncryptionService(b"\x00" * 32)

        def _get_session_override():
            yield session

        # Override everything EXCEPT require_auth so auth rejection is tested
        fastapi_app.dependency_overrides[get_session] = _get_session_override
        fastapi_app.dependency_overrides[get_llm_service] = lambda: mock_llm
        fastapi_app.dependency_overrides[get_encryption_service] = lambda: dummy_enc

        try:
            with TestClient(fastapi_app) as tc:
                resp = tc.get("/api/memories/some-id/reflect")
            assert resp.status_code in (401, 403)
        finally:
            fastapi_app.dependency_overrides.clear()

    def test_reflect_unencrypted_memory(self, session, reflect_client, mock_llm):
        """Memory with no content_dek should use content as-is (no decryption)."""
        memory = Memory(
            title="Plain Text",
            content="This is plain text, not encrypted.",
            content_dek=None,
            captured_at=datetime(2020, 5, 20, tzinfo=timezone.utc),
        )
        session.add(memory)
        session.commit()

        resp = reflect_client.get(f"/api/memories/{memory.id}/reflect")
        assert resp.status_code == 200
        assert "prompt" in resp.json()
        # Verify LLM was called with the plain text content
        call_args = mock_llm.generate.call_args
        assert call_args.kwargs["prompt"] == "This is plain text, not encrypted."

    def test_reflect_blocked_when_cloud_fallback_configured(self, session):
        """Reflect refuses to send decrypted content when a cloud LLM fallback is active."""
        from app.services.encryption import EncryptionService

        mock_llm_with_fallback = MagicMock(spec=LLMService)
        mock_llm_with_fallback.has_fallback = True  # Cloud fallback configured

        def _get_session_override():
            yield session

        dummy_enc = EncryptionService(b"\x00" * 32)

        fastapi_app.dependency_overrides[get_session] = _get_session_override
        fastapi_app.dependency_overrides[require_auth] = lambda: "test-session-id"
        fastapi_app.dependency_overrides[get_llm_service] = lambda: mock_llm_with_fallback
        fastapi_app.dependency_overrides[get_encryption_service] = lambda: dummy_enc

        memory = Memory(
            title="Secret Memory",
            content="Top secret content that must not leave local.",
            captured_at=datetime(2023, 6, 1, tzinfo=timezone.utc),
        )
        session.add(memory)
        session.commit()

        try:
            with TestClient(fastapi_app) as tc:
                resp = tc.get(f"/api/memories/{memory.id}/reflect")
            assert resp.status_code == 503
            assert resp.json()["detail"] == "Reflection generation unavailable"
            # LLM should never have been called
            mock_llm_with_fallback.generate.assert_not_called()
        finally:
            fastapi_app.dependency_overrides.clear()
