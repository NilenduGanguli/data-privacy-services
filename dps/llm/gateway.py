"""Async LLM gateway in the org's pattern (DPC ``ArrangeLlmClient``, retrieval ``get_llm.py``).

Providers:

* ``stellar`` — the OpenAI-compatible gateway behind COIN OAuth2 client-credentials. Client id,
  secret and scope arrive base64-encoded and are decoded at use; the effective scope is
  ``"coinscope" + scope``; the token is re-minted after the TTL on a **monotonic** clock (one
  mint at a time, under a lock). The token mint deliberately skips TLS verification (VDI
  intercepting proxy, per the org handoff); API calls verify against ``DPS_SSL_CERT_FILE`` when
  set.
* ``vertex`` — Gemini via ``google-genai`` (lazy import), the laptop path.
* ``stub`` — deterministic echo for tests and offline demos.

No payload or response content is ever logged. Errors name the provider, the host, the HTTP
status and the exception type, and are raised ``from None``: a chained ``httpx`` exception would
quote the URL (which may carry credentials) and a provider SDK exception may quote the prompt.
"""

from __future__ import annotations

import asyncio
import base64
import binascii
import time
from collections.abc import Callable
from typing import Any

import httpx
from pydantic import BaseModel, Field

from dps.config import Settings
from dps.observability import LLM_CALLS, get_logger

logger = get_logger(__name__)

_MINT_TIMEOUT_SECONDS = 20.0


class LlmUnavailable(RuntimeError):
    """No provider is configured, or its prerequisites are missing."""


class LlmCallError(RuntimeError):
    """A call failed; the message carries provider, host, status and error type only."""


class LlmResult(BaseModel):
    """One completion: the text and token usage (never logged)."""

    text: str
    provider: str
    model: str
    usage: dict[str, int] = Field(default_factory=dict)


def _b64(value: str) -> str:
    try:
        return base64.b64decode(value.strip(), validate=True).decode("utf-8")
    except (binascii.Error, ValueError, UnicodeDecodeError) as exc:
        raise LlmCallError(f"COIN credential is not valid base64 ({type(exc).__name__})") from None


def _http_failure(what: str, exc: httpx.HTTPError) -> LlmCallError:
    status = f" {exc.response.status_code}" if isinstance(exc, httpx.HTTPStatusError) else ""
    return LlmCallError(f"{what} failed: {type(exc).__name__}{status}")


def _count(raw: Any) -> int:
    try:
        return max(0, int(raw))
    except (TypeError, ValueError):
        return 0


class LlmGateway:
    """Chat completions through the configured provider; see the module docstring."""

    def __init__(
        self,
        settings: Settings,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._s = settings
        self._transport = transport
        self._clock = clock
        self._token: str | None = None
        self._minted = 0.0
        self._lock = asyncio.Lock()

    @property
    def provider(self) -> str:
        """The configured provider name (``""`` when none)."""
        return self._s.llm_provider

    @property
    def model(self) -> str:
        """The configured model id."""
        return self._s.llm_model

    def available(self) -> bool:
        """True when the provider is configured and its prerequisites are installed."""
        if self.provider == "stub":
            return True
        if self.provider == "stellar":
            return bool(self._s.stellar_base_url and self._s.coin_url)
        if self.provider == "vertex":
            try:
                import google.genai  # noqa: F401 - availability probe only
            except ImportError:
                return False
            return bool(self._s.vertex_project)
        return False

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 512,
        purpose: str = "rewrite",
    ) -> LlmResult:
        """One chat completion. ``messages`` must already be free of raw sensitive values.

        Raises:
            LlmUnavailable: no provider is configured or its prerequisites are missing.
            LlmCallError: the call failed or the response was malformed (no content in the
                message).
        """
        provider = self.provider or "none"
        if not self.available():
            LLM_CALLS.labels(provider, purpose, "unavailable").inc()
            raise LlmUnavailable(f"llm provider {provider!r} is not configured")
        started = time.perf_counter()
        try:
            if self.provider == "stub":
                result = self._stub(messages)
            elif self.provider == "stellar":
                result = await self._stellar(messages, temperature, max_tokens)
            else:
                result = await asyncio.to_thread(self._vertex, messages, temperature, max_tokens)
        except LlmCallError as exc:
            LLM_CALLS.labels(provider, purpose, "error").inc()
            logger.warning(
                "llm.call",
                provider=provider,
                purpose=purpose,
                model=self.model,
                outcome="error",
                error=type(exc).__name__,
                duration_ms=round((time.perf_counter() - started) * 1000, 2),
            )
            raise
        LLM_CALLS.labels(provider, purpose, "ok").inc()
        logger.info(
            "llm.call",
            provider=provider,
            purpose=purpose,
            model=result.model,
            outcome="ok",
            duration_ms=round((time.perf_counter() - started) * 1000, 2),
        )
        return result

    # -- stub ---------------------------------------------------------------------------------
    def _stub(self, messages: list[dict[str, str]]) -> LlmResult:
        last = next((m["content"] for m in reversed(messages) if m.get("role") == "user"), "")
        return LlmResult(
            text=last, provider="stub", model="stub", usage={"prompt": 0, "completion": 0}
        )

    # -- stellar ------------------------------------------------------------------------------
    async def _coin_token(self) -> str:
        async with self._lock:
            if self._token and self._clock() - self._minted < self._s.coin_token_ttl_seconds:
                return self._token
            data = {
                "grant_type": "client_credentials",
                "client_id": _b64(self._s.coin_client_id),
                "client_secret": _b64(self._s.coin_client_secret),
                "scope": "coinscope" + _b64(self._s.coin_scope),
            }
            try:
                # verify=False is intentional for the mint only (org handoff: VDI TLS interception).
                async with httpx.AsyncClient(
                    timeout=_MINT_TIMEOUT_SECONDS, verify=False, transport=self._transport
                ) as client:
                    response = await client.post(self._s.coin_url, data=data)
                    response.raise_for_status()
                    body = response.json()
            except httpx.HTTPError as exc:
                raise _http_failure("coin mint", exc) from None
            except ValueError:
                raise LlmCallError("coin mint returned a body that is not JSON") from None
            token = body.get("access_token") if isinstance(body, dict) else None
            if not isinstance(token, str) or not token:
                raise LlmCallError("coin mint returned no access_token")
            self._token, self._minted = token, self._clock()
            return token

    async def _stellar(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> LlmResult:
        token = await self._coin_token()
        verify: Any = self._s.ssl_cert_file or True
        url = self._s.stellar_base_url.rstrip("/") + "/chat/completions"
        host = httpx.URL(url).host
        try:
            async with httpx.AsyncClient(
                timeout=self._s.llm_timeout_seconds, verify=verify, transport=self._transport
            ) as client:
                response = await client.post(
                    url,
                    headers={"Authorization": f"Bearer {token}"},
                    json={
                        "model": self._s.llm_model,
                        "temperature": temperature,
                        "max_tokens": max_tokens,
                        "messages": messages,
                    },
                )
                response.raise_for_status()
                body = response.json()
        except httpx.HTTPError as exc:
            raise _http_failure(f"stellar call to {host!r}", exc) from None
        except ValueError:
            raise LlmCallError(f"stellar call to {host!r} returned a non-JSON body") from None
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LlmCallError("stellar response missing choices content") from None
        if not isinstance(content, str):
            raise LlmCallError("stellar response content is not text")
        usage = body.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        return LlmResult(
            text=content,
            provider="stellar",
            model=self._s.llm_model,
            usage={
                "prompt": _count(usage.get("prompt_tokens")),
                "completion": _count(usage.get("completion_tokens")),
            },
        )

    # -- vertex -------------------------------------------------------------------------------
    def _vertex(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> LlmResult:
        from google import genai
        from google.genai import types

        system = "\n".join(m["content"] for m in messages if m.get("role") == "system") or None
        contents = [m["content"] for m in messages if m.get("role") != "system"]
        try:
            client = genai.Client(
                vertexai=True,
                project=self._s.vertex_project,
                location=self._s.vertex_location,
                http_options=types.HttpOptions(timeout=int(self._s.llm_timeout_seconds * 1000)),
            )
            response = client.models.generate_content(
                model=self._s.llm_model,
                contents=contents,
                config=types.GenerateContentConfig(
                    temperature=temperature,
                    max_output_tokens=max_tokens,
                    system_instruction=system,
                    thinking_config=types.ThinkingConfig(thinking_budget=0),
                ),
            )
            text = str(response.text or "")
        except Exception as exc:  # noqa: BLE001 - the SDK's errors may quote the prompt
            raise LlmCallError(f"vertex call failed: {type(exc).__name__}") from None
        return LlmResult(text=text, provider="vertex", model=self._s.llm_model)


def get_gateway(settings: Settings) -> LlmGateway:
    """A gateway for ``settings`` (the app keeps one per process so the token is reused)."""
    return LlmGateway(settings)
