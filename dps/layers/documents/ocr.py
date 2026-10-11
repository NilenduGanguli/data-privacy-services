"""OCR through the Azure Computer Vision Read v3.2 wire contract.

``POST {endpoint}/vision/v3.2/read/analyze`` (image bytes) → ``202`` + ``Operation-Location`` →
``GET`` it until ``status`` is ``succeeded`` or ``failed``. The same client talks to the compose
mock (Tesseract inside), the org's azure-gateway-api and real Azure. DPS only ever sends single
page images (it rasterises PDFs itself), so results are read from ``readResults[0]``.

Safety: the subscription key is sent only to the configured endpoint's origin — an
``Operation-Location`` on another host is refused; errors carry a code, the stage and the HTTP
status, never a response body or the key.
"""

from __future__ import annotations

import asyncio
import math
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import urljoin, urlsplit

import httpx

from dps.config import Settings
from dps.layers.documents.models import BBox, Word
from dps.observability import OCR_CALLS, OCR_SECONDS, READINESS, get_logger

logger = get_logger(__name__)
ANALYZE_PATH = "/vision/v3.2/read/analyze"
KEY_HEADER = "Ocp-Apim-Subscription-Key"
MAX_SUBMIT_ATTEMPTS = 3


class OcrError(RuntimeError):
    """An OCR failure with a stable code: ocr_unconfigured, ocr_auth, ocr_rejected, ocr_throttled,
    ocr_unavailable, ocr_failed, ocr_timeout, ocr_protocol."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class OcrWord:
    text: str
    polygon: tuple[float, ...]  # x1,y1 … x4,y4 in ``OcrPage.unit``
    confidence: float | None
    line: int  # index of the service's line


@dataclass(frozen=True)
class OcrPage:
    width: float
    height: float
    unit: str  # "pixel" for images (always, for DPS) or "inch"
    angle: float
    words: list[OcrWord]


class OcrClient(Protocol):
    engine: str

    def configured(self) -> bool: ...

    async def read(self, image: bytes, *, content_type: str = "image/png") -> OcrPage: ...

    async def probe(self) -> tuple[bool, str]: ...

    async def aclose(self) -> None: ...


class NullOcrClient:
    """OCR switched off (``DPS_OCR_PROVIDER`` empty or incomplete)."""

    engine = "none"

    def configured(self) -> bool:
        return False

    async def read(self, image: bytes, *, content_type: str = "image/png") -> OcrPage:
        raise OcrError("ocr_unconfigured", "no OCR provider is configured")

    async def probe(self) -> tuple[bool, str]:
        return False, "unconfigured"

    async def aclose(self) -> None:
        return None


def _origin(url: str) -> tuple[str, str, int]:
    parts = urlsplit(url)
    port = parts.port or (443 if parts.scheme == "https" else 80)
    return parts.scheme, (parts.hostname or "").lower(), port


def _status_error(stage: str, status: int) -> OcrError:
    if status in (401, 403):
        code = "ocr_auth"
    elif status == 429:
        code = "ocr_throttled"
    elif status >= 500:
        code = "ocr_unavailable"
    elif 400 <= status < 500:
        code = "ocr_rejected"
    else:
        code = "ocr_protocol"
    return OcrError(code, f"{stage} returned HTTP {status}")


def _retry_after(response: httpx.Response, default: float) -> float:
    try:
        value = float(response.headers.get("Retry-After", default))
    except ValueError:
        return default
    if not math.isfinite(value):  # "nan" would corrupt the event loop's timer ordering
        return default
    return min(max(value, 0.0), 30.0)


def _polygon(raw: object) -> tuple[float, ...] | None:
    """The first 8 numbers of a ``boundingBox`` when all are finite, else ``None``."""
    if not isinstance(raw, list | tuple) or len(raw) < 8:
        return None
    try:
        coords = tuple(float(v) for v in raw[:8])
    except (TypeError, ValueError):
        return None
    return coords if all(math.isfinite(c) for c in coords) else None


def parse_read_result(body: dict[str, Any]) -> OcrPage:
    """``analyzeResult.readResults[0]`` → :class:`OcrPage` (empty words for an empty page).

    Fails closed with ``ocr_protocol``: a result of an unexpected shape, or a word with text but
    no usable box (8 finite numbers), refuses the page. A word that cannot be placed could never
    be boxed, so it is never silently dropped from what the engine sees.
    """
    try:
        return _parse_read_result(body)
    except OcrError:
        raise
    except (AttributeError, TypeError, ValueError, KeyError, IndexError):
        raise OcrError("ocr_protocol", "the OCR result had an unexpected shape") from None


def _parse_read_result(body: dict[str, Any]) -> OcrPage:
    results = (body.get("analyzeResult") or {}).get("readResults") or []
    if not results:
        return OcrPage(width=0.0, height=0.0, unit="pixel", angle=0.0, words=[])
    first = results[0]
    words: list[OcrWord] = []
    for index, line in enumerate(first.get("lines") or []):
        for word in line.get("words") or []:
            text = str(word.get("text") or "").strip()
            if not text:
                continue
            coords = _polygon(word.get("boundingBox"))
            if coords is None:
                raise OcrError("ocr_protocol", "an OCR word had no usable bounding box")
            conf = word.get("confidence")
            words.append(
                OcrWord(
                    text=text,
                    polygon=coords,
                    confidence=(
                        float(conf)
                        if isinstance(conf, int | float) and math.isfinite(conf)
                        else None
                    ),
                    line=index,
                )
            )
    return OcrPage(
        width=float(first.get("width") or 0),
        height=float(first.get("height") or 0),
        unit=str(first.get("unit") or "pixel"),
        angle=float(first.get("angle") or 0.0),
        words=words,
    )


def ocr_words_to_points(page: OcrPage, *, points_per_px: float) -> list[Word]:
    """OCR words → layout words in PDF points (pixel unit × ``points_per_px``; inch × 72)."""
    scale = 72.0 if page.unit == "inch" else points_per_px
    out: list[Word] = []
    for w in page.words:
        xs, ys = w.polygon[0::2], w.polygon[1::2]
        out.append(
            Word(
                text=w.text,
                bbox=BBox(
                    x0=min(xs) * scale, y0=min(ys) * scale, x1=max(xs) * scale, y1=max(ys) * scale
                ),
                source="ocr",
                confidence=w.confidence,
                line_hint=w.line,
            )
        )
    return out


class AzureReadClient:
    """Async Read v3.2 client with bounded concurrency, polling, throttling and a deadline."""

    engine = "azure-read-v3.2"

    def __init__(
        self,
        *,
        endpoint: str,
        key: str,
        timeout_s: float,
        poll_interval_s: float,
        health_path: str = "/health",
        max_concurrency: int = 4,
        verify: str | bool = True,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base = endpoint.rstrip("/")
        self._origin = _origin(self._base)
        self._key = key
        self._timeout = timeout_s
        self._poll = poll_interval_s
        self._health_path = health_path
        self._sem = asyncio.Semaphore(max_concurrency)
        self._sleep = sleep
        self._clock = clock
        self._client = httpx.AsyncClient(
            timeout=httpx.Timeout(min(timeout_s, 30.0)),
            verify=verify,
            transport=transport,
            follow_redirects=False,
        )

    def configured(self) -> bool:
        return bool(self._base and self._key)

    async def read(self, image: bytes, *, content_type: str = "image/png") -> OcrPage:
        """OCR one page image. Raises :class:`OcrError`."""
        async with self._sem:
            started = self._clock()
            outcome = "error"
            try:
                page = await self._read(image, deadline=started + self._timeout)
                outcome = "ok"
                return page
            except OcrError as exc:
                outcome = exc.code
                raise
            finally:
                OCR_CALLS.labels(self.engine, outcome).inc()
                OCR_SECONDS.observe(max(self._clock() - started, 0.0))

    async def _read(self, image: bytes, *, deadline: float) -> OcrPage:
        headers = {KEY_HEADER: self._key}
        response: httpx.Response | None = None
        for _ in range(MAX_SUBMIT_ATTEMPTS):
            try:
                response = await self._client.post(
                    self._base + ANALYZE_PATH,
                    content=image,
                    headers={**headers, "Content-Type": "application/octet-stream"},
                )
            except httpx.HTTPError as exc:
                raise OcrError(
                    "ocr_unavailable", f"analyze request failed ({type(exc).__name__})"
                ) from None
            if response.status_code not in (429, 503) or self._clock() >= deadline:
                break
            await self._sleep(_retry_after(response, 1.0))
        assert response is not None
        if response.status_code != 202:
            raise _status_error("analyze", response.status_code)
        operation = self._same_origin(response.headers.get("Operation-Location", ""))
        delay = self._poll
        while True:
            if self._clock() >= deadline:
                raise OcrError("ocr_timeout", f"no result within {self._timeout:.0f}s")
            await self._sleep(delay)
            try:
                poll = await self._client.get(operation, headers=headers)
            except httpx.HTTPError as exc:
                raise OcrError("ocr_unavailable", f"poll failed ({type(exc).__name__})") from None
            if poll.status_code == 429:
                delay = _retry_after(poll, max(delay * 2, 1.0))
                continue
            if poll.status_code != 200:
                raise _status_error("poll", poll.status_code)
            try:
                body = poll.json()
            except ValueError:
                raise OcrError("ocr_protocol", "poll response was not JSON") from None
            if not isinstance(body, dict):
                raise OcrError("ocr_protocol", "poll response was not a JSON object")
            status = str(body.get("status", "")).lower()
            if status == "succeeded":
                return parse_read_result(body)
            if status == "failed":
                raise OcrError("ocr_failed", "the OCR service reported status=failed")
            if status not in ("notstarted", "running"):
                raise OcrError("ocr_protocol", "unexpected OCR status")
            delay = self._poll

    def _same_origin(self, location: str) -> str:
        if not location:
            raise OcrError("ocr_protocol", "analyze response had no Operation-Location")
        url = urljoin(self._base + "/", location)
        if _origin(url) != self._origin:
            raise OcrError(
                "ocr_protocol",
                "Operation-Location points at another host; the key is not sent there",
            )
        return url

    async def probe(self) -> tuple[bool, str]:
        """``(ok, detail)`` for /readyz: GET the health path (``configured`` when there is none)."""
        if not self._health_path:
            return True, "configured"
        try:
            response = await self._client.get(self._base + self._health_path, timeout=3.0)
        except httpx.HTTPError:
            return False, "down"
        return (
            (True, "up") if response.status_code == 200 else (False, f"down:{response.status_code}")
        )

    async def aclose(self) -> None:
        await self._client.aclose()


def build_ocr_client(settings: Settings) -> OcrClient:
    """:class:`AzureReadClient` when the azure provider has an endpoint and a key, else
    :class:`NullOcrClient`. TLS to the gateway trusts ``DPS_SSL_CERT_FILE`` when set (the
    corporate CA the Stellar client already uses)."""
    if not settings.ocr_configured:
        return NullOcrClient()
    return AzureReadClient(
        endpoint=settings.azure_vision_endpoint,
        key=settings.azure_vision_key,
        timeout_s=settings.ocr_timeout_seconds,
        poll_interval_s=settings.ocr_poll_interval_seconds,
        health_path=settings.ocr_health_path,
        max_concurrency=settings.ocr_max_concurrency,
        verify=settings.ssl_cert_file or True,
    )


async def refresh_ocr_readiness(ocr: OcrClient, settings: Settings) -> None:
    """Set the ``ocr`` readiness component. It is never *required*: JSON and text need no OCR."""
    if not ocr.configured():
        READINESS.set("ocr", False, "unconfigured", provider=settings.ocr_provider or "none")
        return
    ok, detail = await ocr.probe()
    READINESS.set("ocr", ok, detail, provider=settings.ocr_provider, engine=ocr.engine)
    if not ok:
        logger.warning("ocr_probe_failed", engine=ocr.engine, status=detail)
