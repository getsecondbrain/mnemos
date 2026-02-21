"""Tests for chat intelligence — conversation persistence, AI title generation,
and system prompt construction with owner context.

Task A6.3
"""
from __future__ import annotations

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from sqlmodel import Session

from app.models.conversation import Conversation
from app.routers.chat import _persist_exchange, _generate_title, _clean_title
from app.services.llm import LLMResponse, LLMService
from app.services.rag import RAGService


# ── _persist_exchange tests ───────────────────────────────────────────


class TestPersistExchange:
    def test_persist_exchange_returns_needs_title(self, session):
        """_persist_exchange returns (conv, True) when title is 'New conversation'."""
        conv = Conversation(title="New conversation")
        session.add(conv)
        session.commit()
        session.refresh(conv)

        result, needs_ai_title = _persist_exchange(session, conv, "hello", "world")

        assert needs_ai_title is True
        assert isinstance(result, Conversation)
        assert result.id == conv.id

    def test_persist_exchange_no_title_needed_for_existing_title(self, session):
        """_persist_exchange returns (conv, False) when title already set."""
        conv = Conversation(title="Already titled conversation")
        session.add(conv)
        session.commit()
        session.refresh(conv)

        result, needs_ai_title = _persist_exchange(session, conv, "hello", "world")

        assert needs_ai_title is False
        assert result.title == "Already titled conversation"

    def test_persist_exchange_updates_timestamp(self, session):
        """_persist_exchange updates conversation.updated_at."""
        conv = Conversation(title="Test conv")
        session.add(conv)
        session.commit()
        session.refresh(conv)

        original_updated = conv.updated_at
        _persist_exchange(session, conv, "q", "a")
        session.refresh(conv)

        assert conv.updated_at is not None
        assert conv.updated_at >= original_updated


# ── _generate_title tests ────────────────────────────────────────────


class TestAITitleGeneration:
    @pytest.mark.asyncio
    async def test_ai_title_generation(self, engine, session):
        """_generate_title calls LLM, cleans title, updates DB, sends WS message."""
        # Create a conversation in the DB
        conv = Conversation(title="New conversation")
        session.add(conv)
        session.commit()
        session.refresh(conv)
        conv_id = conv.id

        # Mock WebSocket
        ws_mock = AsyncMock()

        # Mock LLM — returns quoted title (testing _clean_title integration)
        llm_mock = MagicMock(spec=LLMService)
        llm_mock.generate = AsyncMock(
            return_value=LLMResponse(
                text='"Travel Plans for Europe"',
                model="test-model",
                total_duration_ms=50,
                backend="ollama",
            )
        )

        # Patch engine used by _generate_title's lazy import
        with patch("app.db.engine", engine):
            await _generate_title(
                ws_mock,
                llm_mock,
                conv_id,
                "I want to plan a trip to Europe",
                "Sure! Let me help you plan your European adventure.",
            )

        # Verify LLM was called
        llm_mock.generate.assert_called_once()
        call_kwargs = llm_mock.generate.call_args
        assert call_kwargs.kwargs.get("temperature") == 0.3

        # Verify WebSocket received title_update message
        ws_mock.send_json.assert_called_once()
        sent_msg = ws_mock.send_json.call_args[0][0]
        assert sent_msg["type"] == "title_update"
        assert sent_msg["conversation_id"] == conv_id
        assert sent_msg["title"] == "Travel Plans for Europe"  # quotes stripped

        # Verify DB was updated
        session.expire_all()
        updated_conv = session.get(Conversation, conv_id)
        assert updated_conv is not None
        assert updated_conv.title == "Travel Plans for Europe"


# ── System prompt tests ──────────────────────────────────────────────


class TestSystemPrompt:
    def _make_rag_service(self, owner_name: str = "", family_context: str = "") -> RAGService:
        """Create a RAGService with mock dependencies for prompt testing."""
        return RAGService(
            embedding_service=MagicMock(),
            llm_service=MagicMock(),
            encryption_service=MagicMock(),
            db_session=None,
            owner_name=owner_name,
            family_context=family_context,
        )

    def test_system_prompt_includes_date(self):
        """System prompt includes today's date in 'DayOfWeek, Month DD, YYYY' format."""
        rag = self._make_rag_service()

        # Patch datetime.now() in the rag module for deterministic test
        fixed_dt = datetime(2026, 2, 21, 12, 0, 0)
        with patch("app.services.rag.datetime") as mock_datetime:
            mock_datetime.now.return_value = fixed_dt
            mock_datetime.side_effect = lambda *a, **kw: datetime(*a, **kw)
            prompt = rag._build_system_prompt("test context")

        assert "Saturday, February 21, 2026" in prompt

    def test_system_prompt_includes_owner_name(self):
        """System prompt personalizes with owner name when provided."""
        rag = self._make_rag_service(owner_name="Alice", family_context="Bob (spouse)")
        prompt = rag._build_system_prompt("test context")

        assert "Alice's second brain" in prompt
        assert "Alice's" in prompt  # possessive form
        assert "Family context: Bob (spouse)." in prompt

    def test_system_prompt_without_owner(self):
        """System prompt uses generic fallback when no owner name is set."""
        rag = self._make_rag_service(owner_name="", family_context="")
        prompt = rag._build_system_prompt("test context")

        assert "a personal second brain" in prompt
        assert "their" in prompt
        assert "Family context:" not in prompt
