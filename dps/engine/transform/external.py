"""Adapter to a bank-owned tokenization service (spec §5.5 ``external_tokenize``, D8).

DPS does not hold these keys or vaults; the bank's service (Voltage, Protegrity, Thales, or an
in-house vaultless tokenizer) does, which keeps DPS out of that system's PCI scope. The wire
contract (HANDOFF §8, for the bank's integration team)::

    POST {base}/v1/tokenize   {"profile": str|null, "entity_type": "SSN", "values": [str, ...]}
                              → 200 {"tokens": [str, ...]}
    POST {base}/v1/detokenize {"profile": str|null, "entity_type": "SSN", "tokens": [str, ...]}
                              → 200 {"values": [str, ...]}
    GET  {base}/health        → 200
    Authorization: Bearer <DPS_TOKENIZER_TOKEN>; TLS verified with DPS_TOKENIZER_CA_FILE
    (else DPS_SSL_CERT_FILE, else the system CAs). Order-preserving, same length, at most
    DPS_TOKENIZER_BATCH items per call, deterministic for (profile, entity_type, value).

Calls are batched (one per entity type and profile per request or batch), retried on 429/5xx and
transport errors with a short exponential backoff, and their responses validated: same length,
strings only, and never a token equal to its input (a service that echoes values would otherwise
"tokenize" by copying). Nothing about a value — request or response body — reaches a log or an
error message; the bearer token is held by the HTTP client only.

**Binding (SEC-4, decision D-B).** DPS holds this service's credentials, so ``/reveal`` must never
detokenize a token a caller merely names. Each external token's reveal hint carries a ``bind`` tag
(:func:`~dps.engine.transform.crypto.external_binding_tag`) keyed by the caller's DEK **and** a
server-held key from :func:`binding_keys`; reveal checks every tag in constant time before the
first detokenize call and refuses the request (``external_token_not_bound``) otherwise.
"""

from __future__ import annotations

import ssl
import time
from collections.abc import Callable, Sequence
from contextvars import ContextVar
from typing import Any, Literal, Protocol

import httpx

from dps.config import Settings
from dps.engine.transform.crypto import binding_key_from_kek
from dps.observability import EXTERNAL_CALLS, get_logger
from dps.secrets import kek_ring

logger = get_logger(__name__)
MAX_TOKEN_CHARS = 512
_RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
#: ``(entity_type, plaintext) → token`` (tokenize) or ``(entity_type, token) → plaintext``.
ExternalCache = dict[tuple[str, str], str]


#: F-11: the audit hook of the current Layer 1 call, ``(entity_type, profile, count)``, called once
#: per tokenization batch before the service is called (bound by ``begin_call``; worker threads
#: copy the context). ``None`` outside an audited call.
TOKENIZE_AUDIT: ContextVar[Callable[[str, str | None, int], None] | None] = ContextVar(
    "dps_tokenize_audit", default=None
)


class ExternalServiceError(RuntimeError):
    """The tokenization service failed or answered out of contract (value-free message)."""


class ExternalTokenizer(Protocol):
    """A bank tokenization service (``HttpTokenizer``, or a test double)."""

    name: str

    def tokenize(
        self, entity_type: str, values: Sequence[str], *, profile: str | None
    ) -> list[str]:
        """Tokens for ``values``, in order."""
        ...

    def detokenize(
        self, entity_type: str, tokens: Sequence[str], *, profile: str | None
    ) -> list[str]:
        """Plaintexts for ``tokens``, in order."""
        ...

    def health(self) -> bool:
        """True when the service answers its health check."""
        ...


class HttpTokenizer:
    """``ExternalTokenizer`` over the HTTP wire contract above."""

    name = "http"

    def __init__(
        self,
        base_url: str,
        *,
        token: str = "",
        verify: bool | str | ssl.SSLContext = True,
        timeout: float = 10.0,
        batch: int = 500,
        retries: int = 2,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        self.batch, self.retries = max(1, batch), max(0, retries)
        if isinstance(verify, str):
            verify = ssl.create_default_context(cafile=verify)
        self._client = httpx.Client(
            base_url=base_url.rstrip("/"),
            headers=headers,
            verify=verify,
            timeout=timeout,
            transport=transport,
        )

    def __repr__(self) -> str:
        return f"HttpTokenizer(base_url={str(self._client.base_url)!r})"

    def tokenize(
        self, entity_type: str, values: Sequence[str], *, profile: str | None
    ) -> list[str]:
        """Tokens for ``values`` (batched; validated).

        Raises:
            ExternalServiceError: unavailable, refused, or out of contract (value-free).
        """
        return self._call("tokenize", entity_type, values, profile)

    def detokenize(
        self, entity_type: str, tokens: Sequence[str], *, profile: str | None
    ) -> list[str]:
        """Plaintexts for ``tokens`` (batched; validated).

        Raises:
            ExternalServiceError: unavailable, refused, or out of contract (value-free).
        """
        return self._call("detokenize", entity_type, tokens, profile)

    def health(self) -> bool:
        """``GET /health`` answered 200."""
        try:
            return self._client.get("/health").status_code == 200
        except httpx.HTTPError:
            return False

    def close(self) -> None:
        """Close the HTTP client."""
        self._client.close()

    def _call(
        self,
        op: Literal["tokenize", "detokenize"],
        entity_type: str,
        items: Sequence[str],
        profile: str | None,
    ) -> list[str]:
        out: list[str] = []
        field_in, field_out = ("values", "tokens") if op == "tokenize" else ("tokens", "values")
        for start in range(0, len(items), self.batch):
            chunk = [str(x) for x in items[start : start + self.batch]]
            payload = self._post(
                f"/v1/{op}", {"profile": profile, "entity_type": entity_type, field_in: chunk}, op
            )
            result = payload.get(field_out) if isinstance(payload, dict) else None
            if (
                not isinstance(result, list)
                or len(result) != len(chunk)
                or not all(isinstance(x, str) for x in result)
            ):
                EXTERNAL_CALLS.labels(op, "malformed").inc()
                raise ExternalServiceError("tokenization service returned a malformed response")
            if op == "tokenize" and any(
                t == v or not t or len(t) > MAX_TOKEN_CHARS
                for t, v in zip(result, chunk, strict=True)
            ):
                EXTERNAL_CALLS.labels(op, "malformed").inc()
                raise ExternalServiceError(
                    "tokenization service returned an untransformed or oversized token"
                )
            out.extend(result)
        return out

    def _post(self, path: str, body: dict[str, Any], op: str) -> Any:
        status: int | None = None
        for attempt in range(self.retries + 1):
            try:
                response = self._client.post(path, json=body)
            except httpx.HTTPError:
                status = None
            else:
                status = response.status_code
                if status == 200:
                    EXTERNAL_CALLS.labels(op, "ok").inc()
                    try:
                        return response.json()
                    except ValueError:
                        EXTERNAL_CALLS.labels(op, "malformed").inc()
                        raise ExternalServiceError(
                            "tokenization service returned a malformed response"
                        ) from None
                if status not in _RETRY_STATUS:
                    EXTERNAL_CALLS.labels(op, "refused").inc()
                    raise ExternalServiceError(
                        f"tokenization service refused the request (HTTP {status})"
                    )
            if attempt < self.retries:
                time.sleep(0.2 * 2**attempt)
        EXTERNAL_CALLS.labels(op, "unavailable").inc()
        logger.warning("tokenizer_unavailable", status=status, attempt=self.retries + 1)
        raise ExternalServiceError("tokenization service unavailable")


def build_tokenizer(settings: Settings) -> ExternalTokenizer | None:
    """The configured adapter, or ``None`` when ``DPS_TOKENIZER_PROVIDER`` is empty.

    Raises:
        ValueError: egress TLS is required and the URL is not https (value-free).
    """
    if settings.tokenizer_provider != "http" or not settings.tokenizer_url:
        return None
    if settings.egress_tls_required and not settings.tokenizer_url.startswith("https://"):
        raise ValueError("DPS_TOKENIZER_URL must be https when egress TLS is required")
    verify: bool | str = settings.tokenizer_ca_file or settings.ssl_cert_file or True
    return HttpTokenizer(
        settings.tokenizer_url,
        token=settings.tokenizer_token,
        verify=verify,
        timeout=settings.tokenizer_timeout_seconds,
        batch=settings.tokenizer_batch,
        retries=settings.tokenizer_retries,
    )


def binding_keys(settings: Settings) -> tuple[bytes, ...]:
    """The server-held keys that bind external tokens to caller keys (SEC-4, decision D-B):
    one per job KEK in the ring (current first, then ``DPS_JOB_KEK_PREVIOUS`` during a
    rotation). Empty when no KEK is configured: ``external_tokenize`` then fails closed.
    """
    return tuple(binding_key_from_kek(kek) for kek in kek_ring(settings))
