"""Tests for the WebSocket chat endpoint at /ws/chat.

The chat WebSocket bypasses FastAPI's DI for auth — it manually calls
_decode_token() and auth_state.get_master_key(). Tests patch those
directly rather than relying on dependency overrides.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.main import app as fastapi_app
from app.db import get_session


# ── Fixtures ─────────────────────────────────────────────────────────


@pytest.fixture(name="chat_client")
def chat_client_fixture(session):
    """TestClient with DB override only — no auth override (WebSocket handles its own auth)."""

    def _get_session_override():
        yield session

    fastapi_app.dependency_overrides[get_session] = _get_session_override
    with TestClient(fastapi_app) as tc:
        yield tc
    fastapi_app.dependency_overrides.clear()


@pytest.fixture(name="mock_ai_services")
def mock_ai_services_fixture():
    """Temporarily set mock embedding_service and llm_service on app.state.

    Saves originals and restores them after the test, preventing state
    leakage between tests even under parallel execution.
    """
    orig_emb = getattr(fastapi_app.state, "embedding_service", None)
    orig_llm = getattr(fastapi_app.state, "llm_service", None)
    fastapi_app.state.embedding_service = MagicMock()
    fastapi_app.state.llm_service = MagicMock()
    yield
    fastapi_app.state.embedding_service = orig_emb
    fastapi_app.state.llm_service = orig_llm


def _mock_stream_query(text, **kwargs):
    """Side-effect for AsyncMock(RAGService.stream_query).

    Matches the real signature ``stream_query(text, *, top_k=…)`` and
    returns a (token_stream, source_ids) tuple. ``kwargs`` is accepted
    (and intentionally unused) so the mock doesn't break if the caller
    passes ``top_k`` or other keyword arguments.
    """

    async def _tokens():
        for t in ["Hello", " world"]:
            yield t

    return _tokens(), ["mem-1"]


# ── Auth tests ───────────────────────────────────────────────────────


class TestChatAuth:
    def test_auth_required_wrong_type(self, chat_client):
        """Sending a non-auth first message should close with 4001."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with chat_client.websocket_connect("/ws/chat") as ws:
                ws.send_text(json.dumps({"type": "not_auth", "token": "x"}))
                data = ws.receive_json()
                assert data["type"] == "error"
                assert "First message must be" in data["detail"]
                # Server closes the connection after the error
                ws.receive_json()  # triggers WebSocketDisconnect
        assert exc_info.value.code == 4001

    def test_auth_required_missing_token(self, chat_client):
        """Sending auth without a token field should close with 4001."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with chat_client.websocket_connect("/ws/chat") as ws:
                ws.send_text(json.dumps({"type": "auth"}))
                data = ws.receive_json()
                assert data["type"] == "error"
                assert "First message must be" in data["detail"]
                ws.receive_json()
        assert exc_info.value.code == 4001

    def test_auth_invalid_token(self, chat_client):
        """Sending an invalid JWT should close with 4001."""
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with chat_client.websocket_connect("/ws/chat") as ws:
                ws.send_text(json.dumps({"type": "auth", "token": "bad-jwt"}))
                data = ws.receive_json()
                assert data["type"] == "error"
                assert "Invalid or expired token" in data["detail"]
                ws.receive_json()
        assert exc_info.value.code == 4001

    @patch("app.routers.chat.auth_state")
    @patch("app.routers.chat._decode_token")
    def test_auth_session_expired(self, mock_decode, mock_auth, chat_client):
        """Valid token but no master key in auth_state → Session expired, close 4001."""
        mock_decode.return_value = {"sub": "test-session"}
        mock_auth.get_master_key.return_value = None

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with chat_client.websocket_connect("/ws/chat") as ws:
                ws.send_text(json.dumps({"type": "auth", "token": "valid-token"}))
                data = ws.receive_json()
                assert data["type"] == "error"
                assert "Session expired" in data["detail"]
                ws.receive_json()
        assert exc_info.value.code == 4001


# ── Service availability tests ───────────────────────────────────────


class TestChatServices:
    @pytest.fixture(autouse=False, name="no_ai_services")
    def no_ai_services_fixture(self):
        """Temporarily set AI services to None on app.state."""
        orig_emb = getattr(fastapi_app.state, "embedding_service", None)
        orig_llm = getattr(fastapi_app.state, "llm_service", None)
        fastapi_app.state.embedding_service = None
        fastapi_app.state.llm_service = None
        yield
        fastapi_app.state.embedding_service = orig_emb
        fastapi_app.state.llm_service = orig_llm

    @patch("app.routers.chat.auth_state")
    @patch("app.routers.chat._decode_token")
    def test_ai_services_unavailable(self, mock_decode, mock_auth, chat_client, no_ai_services):
        """Auth succeeds but embedding_service is None → AI services unavailable, close 4003."""
        mock_decode.return_value = {"sub": "test-session"}
        mock_auth.get_master_key.return_value = b"\x00" * 32

        with pytest.raises(WebSocketDisconnect) as exc_info:
            with chat_client.websocket_connect("/ws/chat") as ws:
                ws.send_text(json.dumps({"type": "auth", "token": "valid-token"}))
                data = ws.receive_json()
                assert data["type"] == "error"
                assert "AI services unavailable" in data["detail"]
                ws.receive_json()
        assert exc_info.value.code == 4003


# ── Message loop tests ───────────────────────────────────────────────


class TestChatMessageLoop:
    """Tests that authenticate successfully then exercise the message loop."""

    def _authenticate_and_setup_mocks(self, mock_decode, mock_auth, mock_rag_cls):
        """Common setup: patch auth to succeed and RAGService to use mock stream_query."""
        mock_decode.return_value = {"sub": "test-session"}
        mock_auth.get_master_key.return_value = b"\x00" * 32

        mock_rag_instance = MagicMock()
        mock_rag_instance.stream_query = AsyncMock(side_effect=_mock_stream_query)
        mock_rag_cls.return_value = mock_rag_instance
        return mock_rag_instance

    @patch("app.routers.chat.RAGService")
    @patch("app.routers.chat.auth_state")
    @patch("app.routers.chat._decode_token")
    def test_auth_success_then_question(self, mock_decode, mock_auth, mock_rag_cls, chat_client, mock_ai_services):
        """Full flow: auth → question → token stream → sources → done."""
        mock_rag = self._authenticate_and_setup_mocks(mock_decode, mock_auth, mock_rag_cls)

        with chat_client.websocket_connect("/ws/chat") as ws:
            # Auth
            ws.send_text(json.dumps({"type": "auth", "token": "valid-token"}))
            # Question
            ws.send_text(json.dumps({"type": "question", "text": "hi"}))

            # Expect token messages
            t1 = ws.receive_json()
            assert t1 == {"type": "token", "text": "Hello"}
            t2 = ws.receive_json()
            assert t2 == {"type": "token", "text": " world"}

            # Expect sources
            sources = ws.receive_json()
            assert sources["type"] == "sources"
            assert sources["memory_ids"] == ["mem-1"]

            # Expect done
            done = ws.receive_json()
            assert done["type"] == "done"

    @patch("app.routers.chat.RAGService")
    @patch("app.routers.chat.auth_state")
    @patch("app.routers.chat._decode_token")
    def test_invalid_json_in_message_loop(self, mock_decode, mock_auth, mock_rag_cls, chat_client, mock_ai_services):
        """Sending non-JSON after auth should return error but keep connection open."""
        self._authenticate_and_setup_mocks(mock_decode, mock_auth, mock_rag_cls)

        with chat_client.websocket_connect("/ws/chat") as ws:
            # Auth
            ws.send_text(json.dumps({"type": "auth", "token": "valid-token"}))
            # Send garbage
            ws.send_text("not-valid-json{{{")
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "Invalid JSON" in data["detail"]

            # Connection should still be open — send a valid question
            ws.send_text(json.dumps({"type": "question", "text": "test"}))
            t1 = ws.receive_json()
            assert t1["type"] == "token"

    @patch("app.routers.chat.RAGService")
    @patch("app.routers.chat.auth_state")
    @patch("app.routers.chat._decode_token")
    def test_wrong_message_type_in_loop(self, mock_decode, mock_auth, mock_rag_cls, chat_client, mock_ai_services):
        """Sending wrong message type after auth should return error but keep connection."""
        self._authenticate_and_setup_mocks(mock_decode, mock_auth, mock_rag_cls)

        with chat_client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "auth", "token": "valid-token"}))
            ws.send_text(json.dumps({"type": "foo"}))
            data = ws.receive_json()
            assert data["type"] == "error"
            assert "Expected {type: 'question'" in data["detail"]

    @patch("app.routers.chat.RAGService")
    @patch("app.routers.chat.auth_state")
    @patch("app.routers.chat._decode_token")
    def test_top_k_clamping(self, mock_decode, mock_auth, mock_rag_cls, chat_client, mock_ai_services):
        """top_k should be clamped to max 20."""
        mock_rag = self._authenticate_and_setup_mocks(mock_decode, mock_auth, mock_rag_cls)

        with chat_client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "auth", "token": "valid-token"}))
            ws.send_text(json.dumps({"type": "question", "text": "hi", "top_k": 100}))

            # Consume the streaming output
            ws.receive_json()  # token
            ws.receive_json()  # token
            ws.receive_json()  # sources
            ws.receive_json()  # done

        # Assert stream_query was called with top_k=20 (clamped from 100)
        mock_rag.stream_query.assert_called_once_with("hi", top_k=20)

    @patch("app.routers.chat.RAGService")
    @patch("app.routers.chat.auth_state")
    @patch("app.routers.chat._decode_token")
    def test_top_k_minimum_is_one(self, mock_decode, mock_auth, mock_rag_cls, chat_client, mock_ai_services):
        """top_k should be clamped to min 1."""
        mock_rag = self._authenticate_and_setup_mocks(mock_decode, mock_auth, mock_rag_cls)

        with chat_client.websocket_connect("/ws/chat") as ws:
            ws.send_text(json.dumps({"type": "auth", "token": "valid-token"}))
            ws.send_text(json.dumps({"type": "question", "text": "hi", "top_k": -5}))

            ws.receive_json()  # token
            ws.receive_json()  # token
            ws.receive_json()  # sources
            ws.receive_json()  # done

        mock_rag.stream_query.assert_called_once_with("hi", top_k=1)
