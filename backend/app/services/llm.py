"""LLM service for Mnemos — Ollama with optional OpenAI-compatible fallback.

Provides generate (single response) and stream (token-by-token) interfaces.
Primary backend is Ollama. When configured, falls back to any OpenAI-compatible
API if Ollama is unreachable or returns errors.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import AsyncIterator
from dataclasses import dataclass

import httpx

logger = logging.getLogger(__name__)


class LLMError(Exception):
    """Raised when LLM generation fails on ALL backends."""


@dataclass(frozen=True, slots=True)
class LLMResponse:
    """Result from an LLM generation call."""

    text: str
    model: str
    total_duration_ms: int | None
    backend: str  # "ollama" or "fallback"


class LLMService:
    """Abstraction layer over Ollama + optional OpenAI-compatible fallback.

    All LLM interactions in the project go through this service.
    When a fallback is configured and Ollama fails, requests are automatically
    routed to the fallback endpoint. Health checks periodically re-test Ollama
    so traffic returns to the primary when it recovers.
    """

    # How long (seconds) to suppress Ollama retries after a failure
    _HEALTH_RECHECK_INTERVAL = 60

    __slots__ = (
        "ollama_url",
        "model",
        "_timeout",
        "_fallback_url",
        "_fallback_api_key",
        "_fallback_model",
        "_ollama_healthy",
        "_last_ollama_fail_time",
    )

    def __init__(
        self,
        ollama_url: str,
        model: str = "llama3.2:8b",
        timeout: float = 120.0,
        fallback_url: str = "",
        fallback_api_key: str = "",
        fallback_model: str = "",
    ) -> None:
        self.ollama_url = ollama_url
        self.model = model
        self._timeout = timeout
        self._fallback_url = fallback_url.rstrip("/") if fallback_url else ""
        self._fallback_api_key = fallback_api_key
        self._fallback_model = fallback_model or model
        self._ollama_healthy: bool = True
        self._last_ollama_fail_time: float = 0.0

    @property
    def has_fallback(self) -> bool:
        """True if a fallback endpoint is configured."""
        return bool(self._fallback_url)

    def _should_try_ollama(self) -> bool:
        """Determine if Ollama should be attempted.

        Returns True if Ollama is healthy or if enough time has passed
        since the last failure to re-test.
        """
        if self._ollama_healthy:
            return True
        elapsed = time.monotonic() - self._last_ollama_fail_time
        if elapsed >= self._HEALTH_RECHECK_INTERVAL:
            logger.info("Re-checking Ollama health after %.0fs cooldown", elapsed)
            return True
        return False

    def _mark_ollama_down(self) -> None:
        """Record that Ollama failed, entering cooldown period."""
        self._ollama_healthy = False
        self._last_ollama_fail_time = time.monotonic()
        logger.warning("Ollama marked as unhealthy, will retry after %ds", self._HEALTH_RECHECK_INTERVAL)

    def _mark_ollama_up(self) -> None:
        """Record that Ollama succeeded."""
        if not self._ollama_healthy:
            logger.info("Ollama is healthy again, resuming primary routing")
        self._ollama_healthy = True

    # ── generate ─────────────────────────────────────────────────

    async def generate(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
        local_only: bool = False,
    ) -> LLMResponse:
        """Generate a complete response, with automatic fallback.

        Args:
            local_only: If True, never fall back to cloud LLM. Use this when
                the prompt contains decrypted user content that must not leave
                the local machine.
        """
        # Try Ollama first (if healthy or cooldown expired)
        if self._should_try_ollama():
            try:
                result = await self._generate_ollama(prompt, system, temperature)
                self._mark_ollama_up()
                return result
            except LLMError:
                self._mark_ollama_down()
                if local_only or not self.has_fallback:
                    raise

        # local_only callers must not use cloud fallback
        if local_only:
            raise LLMError("Ollama is unavailable and local_only=True prevents cloud fallback")

        # Fallback to OpenAI-compatible endpoint
        if self.has_fallback:
            logger.info("Falling back to cloud LLM for generate")
            return await self._generate_openai(prompt, system, temperature)

        raise LLMError("Ollama is unavailable and no fallback is configured")

    async def _generate_ollama(
        self, prompt: str, system: str | None, temperature: float
    ) -> LLMResponse:
        """Generate via Ollama /api/generate."""
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "options": {"temperature": temperature},
        }
        if system is not None:
            payload["system"] = system

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self.ollama_url}/api/generate",
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                return LLMResponse(
                    text=data["response"],
                    model=data["model"],
                    total_duration_ms=data.get("total_duration", 0) // 1_000_000,
                    backend="ollama",
                )
        except httpx.ConnectError as exc:
            raise LLMError(f"Cannot connect to Ollama at {self.ollama_url}: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Ollama returned HTTP {exc.response.status_code}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise LLMError(f"Ollama request timed out after {self._timeout}s: {exc}") from exc

    async def _generate_openai(
        self, prompt: str, system: str | None, temperature: float
    ) -> LLMResponse:
        """Generate via OpenAI-compatible /chat/completions endpoint."""
        messages: list[dict] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._fallback_api_key}",
        }
        payload = {
            "model": self._fallback_model,
            "messages": messages,
            "temperature": temperature,
            "stream": False,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                response = await client.post(
                    f"{self._fallback_url}/chat/completions",
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                data = response.json()
                text = data["choices"][0]["message"]["content"]
                model = data.get("model", self._fallback_model)
                return LLMResponse(
                    text=text,
                    model=model,
                    total_duration_ms=None,
                    backend="fallback",
                )
        except httpx.ConnectError as exc:
            raise LLMError(f"Cannot connect to fallback LLM at {self._fallback_url}: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Fallback LLM returned HTTP {exc.response.status_code}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise LLMError(f"Fallback LLM request timed out after {self._timeout}s: {exc}") from exc
        except (KeyError, IndexError) as exc:
            raise LLMError(f"Unexpected response from fallback LLM: {exc}") from exc

    # ── stream ───────────────────────────────────────────────────

    async def stream(
        self,
        prompt: str,
        system: str | None = None,
        temperature: float = 0.7,
    ) -> AsyncIterator[str]:
        """Stream tokens, with automatic fallback."""
        if self._should_try_ollama():
            try:
                async for token in self._stream_ollama(prompt, system, temperature):
                    yield token
                self._mark_ollama_up()
                return
            except LLMError:
                self._mark_ollama_down()
                if not self.has_fallback:
                    raise

        if self.has_fallback:
            logger.info("Falling back to cloud LLM for stream")
            async for token in self._stream_openai(prompt, system, temperature):
                yield token
            return

        raise LLMError("Ollama is unavailable and no fallback is configured")

    async def _stream_ollama(
        self, prompt: str, system: str | None, temperature: float
    ) -> AsyncIterator[str]:
        """Stream via Ollama /api/generate."""
        payload: dict = {
            "model": self.model,
            "prompt": prompt,
            "stream": True,
            "options": {"temperature": temperature},
        }
        if system is not None:
            payload["system"] = system

        # Use a longer read timeout for streaming — Ollama may need to load the
        # model into GPU memory before producing the first token.
        stream_timeout = httpx.Timeout(self._timeout, read=max(self._timeout, 300.0))
        try:
            async with httpx.AsyncClient(timeout=stream_timeout) as client:
                async with client.stream(
                    "POST", f"{self.ollama_url}/api/generate", json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line:
                            continue
                        chunk = json.loads(line)
                        if "response" in chunk:
                            yield chunk["response"]
        except httpx.ConnectError as exc:
            raise LLMError(f"Cannot connect to Ollama at {self.ollama_url}: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Ollama returned HTTP {exc.response.status_code}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise LLMError(f"Ollama streaming timed out after {self._timeout}s: {exc}") from exc

    async def _stream_openai(
        self, prompt: str, system: str | None, temperature: float
    ) -> AsyncIterator[str]:
        """Stream via OpenAI-compatible /chat/completions with stream=True."""
        messages: list[dict] = []
        if system is not None:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})

        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {self._fallback_api_key}",
        }
        payload = {
            "model": self._fallback_model,
            "messages": messages,
            "temperature": temperature,
            "stream": True,
        }

        try:
            async with httpx.AsyncClient(timeout=self._timeout) as client:
                async with client.stream(
                    "POST", f"{self._fallback_url}/chat/completions",
                    headers=headers, json=payload,
                ) as response:
                    response.raise_for_status()
                    async for line in response.aiter_lines():
                        if not line or not line.startswith("data: "):
                            continue
                        data_str = line[6:]  # strip "data: " prefix
                        if data_str.strip() == "[DONE]":
                            break
                        chunk = json.loads(data_str)
                        delta = chunk.get("choices", [{}])[0].get("delta", {})
                        content = delta.get("content")
                        if content:
                            yield content
        except httpx.ConnectError as exc:
            raise LLMError(f"Cannot connect to fallback LLM at {self._fallback_url}: {exc}") from exc
        except httpx.HTTPStatusError as exc:
            raise LLMError(f"Fallback LLM returned HTTP {exc.response.status_code}: {exc}") from exc
        except httpx.TimeoutException as exc:
            raise LLMError(f"Fallback LLM streaming timed out after {self._timeout}s: {exc}") from exc

    # ── health ───────────────────────────────────────────────────

    async def check_health(self) -> bool:
        """Check if Ollama is reachable by querying /api/tags."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.ollama_url}/api/tags")
                return response.status_code == 200
        except (httpx.ConnectError, httpx.HTTPError):
            return False

    async def check_fallback_health(self) -> bool:
        """Check if the fallback endpoint is reachable via /models."""
        if not self.has_fallback:
            return False
        try:
            headers = {"Authorization": f"Bearer {self._fallback_api_key}"}
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(
                    f"{self._fallback_url}/models", headers=headers,
                )
                return response.status_code == 200
        except (httpx.ConnectError, httpx.HTTPError):
            return False

    async def ensure_model(self) -> bool:
        """Check if the configured model is available in Ollama."""
        try:
            async with httpx.AsyncClient(timeout=10.0) as client:
                response = await client.get(f"{self.ollama_url}/api/tags")
                response.raise_for_status()
                data = response.json()
                models = data.get("models", [])
                return any(m.get("name") == self.model for m in models)
        except (httpx.ConnectError, httpx.HTTPError):
            return False
