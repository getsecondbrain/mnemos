"""Tests for the LLM service.

Covers generate, stream, health check, model presence verification,
and automatic failover to OpenAI-compatible fallback endpoints.
"""

from __future__ import annotations

import json
import time
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from app.services.llm import LLMError, LLMResponse, LLMService


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture(name="llm_service")
def llm_service_fixture() -> LLMService:
    return LLMService(ollama_url="http://fake-ollama:11434", model="llama3.2:8b")


@pytest.fixture(name="llm_with_fallback")
def llm_with_fallback_fixture() -> LLMService:
    return LLMService(
        ollama_url="http://fake-ollama:11434",
        model="llama3.2:8b",
        fallback_url="https://api.openai.com/v1",
        fallback_api_key="sk-test-key",
        fallback_model="gpt-4o-mini",
    )


# ── Helper: mock OpenAI chat/completions response ────────────────────


def _openai_response(text: str = "Fallback answer.", model: str = "gpt-4o-mini") -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "choices": [{"message": {"content": text}}],
        "model": model,
    }
    mock_response.raise_for_status = MagicMock()
    return mock_response


def _ollama_response(text: str = "The answer is 42.", model: str = "llama3.2:8b") -> MagicMock:
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "response": text,
        "model": model,
        "total_duration": 5_000_000_000,
    }
    mock_response.raise_for_status = MagicMock()
    return mock_response


# ── TestGenerate ──────────────────────────────────────────────────────


class TestGenerate:
    """Tests for LLMService.generate."""

    @pytest.mark.asyncio
    async def test_successful_generation(self, llm_service: LLMService) -> None:
        """Mock Ollama returns a valid response; verify LLMResponse fields."""
        mock_response = _ollama_response()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await llm_service.generate("What is the meaning of life?")

        assert isinstance(result, LLMResponse)
        assert result.text == "The answer is 42."
        assert result.model == "llama3.2:8b"
        assert result.total_duration_ms == 5000
        assert result.backend == "ollama"

    @pytest.mark.asyncio
    async def test_with_system_prompt(self, llm_service: LLMService) -> None:
        """Verify system prompt is passed in the request body."""
        mock_response = _ollama_response("I am a helpful assistant.")

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            await llm_service.generate(
                "Hello", system="You are a memory assistant."
            )

        call_kwargs = mock_client.post.call_args
        request_body = call_kwargs[1]["json"]
        assert request_body["system"] == "You are a memory assistant."

    @pytest.mark.asyncio
    async def test_ollama_unreachable(self, llm_service: LLMService) -> None:
        """Connect error raises LLMError with 'Cannot connect'."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(LLMError, match="Cannot connect"):
                await llm_service.generate("test prompt")

    @pytest.mark.asyncio
    async def test_ollama_error_response(self, llm_service: LLMService) -> None:
        """HTTP 500 raises LLMError."""
        mock_response = MagicMock()
        mock_response.status_code = 500
        mock_response.raise_for_status.side_effect = httpx.HTTPStatusError(
            "Server Error",
            request=MagicMock(),
            response=mock_response,
        )

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(LLMError, match="Ollama returned HTTP"):
                await llm_service.generate("test prompt")


# ── TestStream ────────────────────────────────────────────────────────


class TestStream:
    """Tests for LLMService.stream."""

    @pytest.mark.asyncio
    async def test_successful_stream(self, llm_service: LLMService) -> None:
        """Mock Ollama streams NDJSON; collect tokens and verify joined output."""
        lines = [
            json.dumps({"response": "hello "}),
            json.dumps({"response": "world", "done": True}),
        ]

        mock_response = AsyncMock()
        mock_response.raise_for_status = MagicMock()

        async def fake_aiter_lines():
            for line in lines:
                yield line

        mock_response.aiter_lines = fake_aiter_lines

        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(return_value=mock_response)
        stream_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=stream_cm)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            tokens = []
            async for token in llm_service.stream("Say hello world"):
                tokens.append(token)

        assert "".join(tokens) == "hello world"

    @pytest.mark.asyncio
    async def test_stream_ollama_unreachable(self, llm_service: LLMService) -> None:
        """Connect error during stream raises LLMError."""
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        stream_cm.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock(return_value=stream_cm)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(LLMError, match="Cannot connect"):
                async for _ in llm_service.stream("test prompt"):
                    pass


# ── TestCheckHealth ───────────────────────────────────────────────────


class TestCheckHealth:
    """Tests for LLMService.check_health."""

    @pytest.mark.asyncio
    async def test_healthy(self, llm_service: LLMService) -> None:
        """200 response to /api/tags returns True."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            assert await llm_service.check_health() is True

    @pytest.mark.asyncio
    async def test_unhealthy(self, llm_service: LLMService) -> None:
        """Connect error returns False."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            assert await llm_service.check_health() is False


# ── TestEnsureModel ───────────────────────────────────────────────────


class TestEnsureModel:
    """Tests for LLMService.ensure_model."""

    @pytest.mark.asyncio
    async def test_model_present(self, llm_service: LLMService) -> None:
        """/api/tags lists the model; returns True."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {
            "models": [{"name": "llama3.2:8b"}, {"name": "nomic-embed-text"}]
        }
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            assert await llm_service.ensure_model() is True

    @pytest.mark.asyncio
    async def test_model_absent(self, llm_service: LLMService) -> None:
        """/api/tags returns empty model list; returns False."""
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.json.return_value = {"models": []}
        mock_response.raise_for_status = MagicMock()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            assert await llm_service.ensure_model() is False


# ── TestFallbackGenerate ──────────────────────────────────────────────


class TestFallbackGenerate:
    """Tests for automatic failover to OpenAI-compatible fallback."""

    @pytest.mark.asyncio
    async def test_fallback_on_ollama_connect_error(self, llm_with_fallback: LLMService) -> None:
        """When Ollama fails, generate falls back to OpenAI endpoint."""
        call_count = 0

        async def side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: Ollama fails
                raise httpx.ConnectError("Connection refused")
            # Second call: OpenAI succeeds
            return _openai_response()

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = side_effect
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await llm_with_fallback.generate("test prompt")

        assert result.backend == "fallback"
        assert result.text == "Fallback answer."
        assert result.model == "gpt-4o-mini"

    @pytest.mark.asyncio
    async def test_no_fallback_raises_when_not_configured(self, llm_service: LLMService) -> None:
        """Without fallback, Ollama failure raises LLMError as before."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(LLMError, match="Cannot connect"):
                await llm_service.generate("test prompt")

    @pytest.mark.asyncio
    async def test_both_fail_raises_error(self, llm_with_fallback: LLMService) -> None:
        """When both Ollama AND fallback fail, LLMError is raised."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.side_effect = httpx.ConnectError("Connection refused")
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            with pytest.raises(LLMError, match="Cannot connect to fallback"):
                await llm_with_fallback.generate("test prompt")

    @pytest.mark.asyncio
    async def test_ollama_recovery_after_cooldown(self, llm_with_fallback: LLMService) -> None:
        """After cooldown, Ollama is re-tested and traffic returns."""
        # Mark Ollama down and simulate elapsed cooldown
        llm_with_fallback._mark_ollama_down()
        llm_with_fallback._last_ollama_fail_time = time.monotonic() - 120  # well past 60s

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _ollama_response()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await llm_with_fallback.generate("test prompt")

        assert result.backend == "ollama"
        assert llm_with_fallback._ollama_healthy is True

    @pytest.mark.asyncio
    async def test_health_suppression_skips_ollama(self, llm_with_fallback: LLMService) -> None:
        """During cooldown, Ollama is skipped, fallback used directly."""
        # Mark Ollama down with recent timestamp (within cooldown)
        llm_with_fallback._ollama_healthy = False
        llm_with_fallback._last_ollama_fail_time = time.monotonic()

        assert llm_with_fallback._should_try_ollama() is False

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _openai_response()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await llm_with_fallback.generate("test prompt")

        assert result.backend == "fallback"
        # Verify only 1 call (to fallback), not 2 (Ollama was skipped)
        assert mock_client.post.call_count == 1


# ── TestFallbackStream ────────────────────────────────────────────────


class TestFallbackStream:
    """Tests for streaming with fallback."""

    @pytest.mark.asyncio
    async def test_stream_fallback_on_ollama_failure(self, llm_with_fallback: LLMService) -> None:
        """Stream falls back to OpenAI SSE format."""
        # First call (Ollama stream) fails
        ollama_stream_cm = MagicMock()
        ollama_stream_cm.__aenter__ = AsyncMock(
            side_effect=httpx.ConnectError("Connection refused")
        )
        ollama_stream_cm.__aexit__ = AsyncMock(return_value=False)

        # Second call (OpenAI stream) succeeds with SSE format
        sse_lines = [
            'data: {"choices":[{"delta":{"content":"hello "}}]}',
            'data: {"choices":[{"delta":{"content":"world"}}]}',
            "data: [DONE]",
        ]
        openai_response = AsyncMock()
        openai_response.raise_for_status = MagicMock()

        async def fake_aiter_lines():
            for line in sse_lines:
                yield line

        openai_response.aiter_lines = fake_aiter_lines

        openai_stream_cm = MagicMock()
        openai_stream_cm.__aenter__ = AsyncMock(return_value=openai_response)
        openai_stream_cm.__aexit__ = AsyncMock(return_value=False)

        call_count = 0

        def stream_side_effect(*args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                return ollama_stream_cm
            return openai_stream_cm

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.stream = MagicMock(side_effect=stream_side_effect)
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            tokens = []
            async for token in llm_with_fallback.stream("test prompt"):
                tokens.append(token)

        assert "".join(tokens) == "hello world"


# ── TestFallbackHealth ────────────────────────────────────────────────


class TestFallbackHealth:
    """Tests for check_fallback_health."""

    @pytest.mark.asyncio
    async def test_fallback_healthy(self, llm_with_fallback: LLMService) -> None:
        """Fallback /models returns 200."""
        mock_response = MagicMock()
        mock_response.status_code = 200

        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.get.return_value = mock_response
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            assert await llm_with_fallback.check_fallback_health() is True

    @pytest.mark.asyncio
    async def test_fallback_not_configured(self, llm_service: LLMService) -> None:
        """Returns False when no fallback URL."""
        assert await llm_service.check_fallback_health() is False

    @pytest.mark.asyncio
    async def test_has_fallback_property(self, llm_service: LLMService, llm_with_fallback: LLMService) -> None:
        """has_fallback is False without config, True with config."""
        assert llm_service.has_fallback is False
        assert llm_with_fallback.has_fallback is True


# ── TestLLMResponseBackend ────────────────────────────────────────────


class TestLLMResponseBackend:
    """Test that LLMResponse.backend field is populated correctly."""

    @pytest.mark.asyncio
    async def test_ollama_backend_field(self, llm_service: LLMService) -> None:
        """Successful Ollama call sets backend='ollama'."""
        with patch("httpx.AsyncClient") as mock_client_cls:
            mock_client = AsyncMock()
            mock_client.post.return_value = _ollama_response()
            mock_client.__aenter__ = AsyncMock(return_value=mock_client)
            mock_client.__aexit__ = AsyncMock(return_value=False)
            mock_client_cls.return_value = mock_client

            result = await llm_service.generate("test")

        assert result.backend == "ollama"
