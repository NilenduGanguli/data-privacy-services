"""OCR through the Azure Computer Vision Read v3.2 wire contract, or a local Tesseract.

``POST {endpoint}/vision/v3.2/read/analyze`` (image bytes) → ``202`` + ``Operation-Location`` →
``GET`` it until ``status`` is ``succeeded`` or ``failed``. The same client talks to the compose
mock (Tesseract inside), the org's azure-gateway-api and real Azure. DPS only ever sends single
page images (it rasterises PDFs itself), so results are read from ``readResults[0]``.

Safety: the subscription key is sent only to the configured endpoint's origin — an
``Operation-Location`` on another host is refused; errors carry a code, the stage and the HTTP
status, never a response body or the key.

``TesseractOcrClient`` (spec §6 "local Tesseract when unset", M1 Task 19C) runs the ``tesseract``
CLI on the page image and parses its TSV into the same :class:`OcrPage`, so the pipeline's
geometry, hybrid merge and redaction re-read are unchanged; it never reads Tesseract's stderr.
"""

from __future__ import annotations

import asyncio
import contextlib
import math
import os
import re
import shutil
import signal
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


def _httpx_origin(url: httpx.URL) -> tuple[str, str, int]:
    port = url.port or (443 if url.scheme == "https" else 80)
    return url.scheme, url.host.lower(), port


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


def _protocol(detail: str) -> OcrError:
    return OcrError("ocr_protocol", detail)


def _polygon(raw: object) -> tuple[float, ...] | None:
    """The first 8 numbers of a ``boundingBox`` when all are finite, else ``None``."""
    if not isinstance(raw, list | tuple) or len(raw) < 8:
        return None
    try:
        coords = tuple(float(v) for v in raw[:8])
    except (TypeError, ValueError, OverflowError):
        return None
    return coords if all(math.isfinite(c) for c in coords) else None


def _number(raw: object, default: float) -> float:
    """A finite JSON number (``default`` when absent); anything else is a protocol error."""
    if raw is None:
        return default
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        raise _protocol("an OCR page field was not a number")
    value = float(raw)  # OverflowError for an integer beyond a float: caught by the caller
    if not math.isfinite(value):
        raise _protocol("an OCR page field was not a finite number")
    return value


def _confidence(raw: object) -> float | None:
    if isinstance(raw, bool) or not isinstance(raw, int | float):
        return None
    try:
        value = float(raw)
    except OverflowError:
        return None
    return value if math.isfinite(value) else None


def _no_space(text: str) -> str:
    return "".join(text.split())


def parse_read_result(body: dict[str, Any]) -> OcrPage:
    """``analyzeResult.readResults[0]`` → :class:`OcrPage` (empty words for an empty page).

    Fails closed with ``ocr_protocol``: anything but exactly one page with a ``lines`` list, a
    page field of the wrong type, a word without string text or without a usable box (8 finite
    numbers), or a line whose text its words do not carry, refuses the page. A blank page is
    ``"lines": []`` (what the mock and Azure send), so a shape this parser does not know never
    reads as "a page without words" whose values would go undetected and stay visible.
    """
    try:
        return _parse_read_result(body)
    except OcrError:
        raise
    except (AttributeError, TypeError, ValueError, KeyError, IndexError, OverflowError):
        raise _protocol("the OCR result had an unexpected shape") from None


def _parse_read_result(body: dict[str, Any]) -> OcrPage:
    analyze = body.get("analyzeResult")
    results = analyze.get("readResults") if isinstance(analyze, dict) else None
    if not isinstance(results, list) or len(results) != 1 or not isinstance(results[0], dict):
        # DPS sends one page image per call, so the result is exactly one page
        raise _protocol("the OCR result did not hold exactly one page")
    first = results[0]
    lines = first.get("lines")
    if not isinstance(lines, list):
        raise _protocol("the OCR page had no lines list")
    unit = first.get("unit", "pixel")
    if unit not in ("pixel", "inch"):
        raise _protocol("the OCR page had an unknown unit")
    words: list[OcrWord] = []
    for index, line in enumerate(lines):
        if not isinstance(line, dict):
            raise _protocol("an OCR line was not an object")
        line_text = line.get("text", "")
        raw_words = line.get("words", [])
        if not isinstance(line_text, str) or not isinstance(raw_words, list):
            raise _protocol("an OCR line had a field of the wrong type")
        carried: list[str] = []
        for word in raw_words:
            if not isinstance(word, dict) or not isinstance(word.get("text"), str):
                raise _protocol("an OCR word had no text string")
            text = word["text"].strip()
            if not text:
                continue
            coords = _polygon(word.get("boundingBox"))
            if coords is None:
                raise _protocol("an OCR word had no usable bounding box")
            carried.append(text)
            words.append(
                OcrWord(
                    text=text,
                    polygon=coords,
                    confidence=_confidence(word.get("confidence")),
                    line=index,
                )
            )
        if line_text.strip() and _no_space(line_text) != "".join(map(_no_space, carried)):
            # text the words do not carry could never be detected or boxed
            raise _protocol("an OCR line held text its words did not carry")
    return OcrPage(
        width=_number(first.get("width"), 0.0),
        height=_number(first.get("height"), 0.0),
        unit=unit,
        angle=_number(first.get("angle"), 0.0),
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
        for attempt in range(1, MAX_SUBMIT_ATTEMPTS + 1):
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
            if (
                response.status_code not in (429, 503)
                or attempt == MAX_SUBMIT_ATTEMPTS
                or self._clock() >= deadline
            ):
                break  # no sleep after the last attempt: it would only delay the error
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

    def _same_origin(self, location: str) -> httpx.URL:
        """The poll URL: ``location`` resolved against the endpoint. Refused unless the stdlib
        parser and httpx (which sends the key) both place it on the endpoint's origin; a URL
        either cannot parse (a bad port, an unclosed ``[``) is a protocol error."""
        if not location:
            raise OcrError("ocr_protocol", "analyze response had no Operation-Location")
        try:
            url = urljoin(self._base + "/", location)
            target = httpx.URL(url)
            same = _origin(url) == self._origin and _httpx_origin(target) == _httpx_origin(
                httpx.URL(self._base)
            )
        except (ValueError, httpx.InvalidURL):  # UnicodeError is a ValueError
            raise OcrError("ocr_protocol", "Operation-Location is not a valid URL") from None
        if not same:
            raise OcrError(
                "ocr_protocol",
                "Operation-Location points at another host; the key is not sent there",
            )
        return target

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


#: ``tesseract … tsv`` columns (Tesseract 4/5): one row per page, block, paragraph, line and word.
TSV_COLUMNS = (
    "level",
    "page_num",
    "block_num",
    "par_num",
    "line_num",
    "word_num",
    "left",
    "top",
    "width",
    "height",
    "conf",
    "text",
)
_TSV_PAGE, _TSV_WORD = 1, 5


def parse_tsv(tsv: str) -> OcrPage:
    """Tesseract's TSV output → :class:`OcrPage` (unit ``pixel``, as for an image sent to Azure).

    The level-1 row gives the page size; each level-5 row with text is a word (polygon from its
    box, ``confidence = conf / 100``, ``None`` when ``conf`` is negative, ``line`` = the ordinal
    of its ``(block, paragraph, line)``). Fails closed with ``ocr_protocol`` on anything else:
    no header, a row that is not numeric where it must be, or no page row — a shape this parser
    does not know never reads as "a page without words".
    """
    try:
        return _parse_tsv(tsv)
    except OcrError:
        raise
    except (ValueError, IndexError, OverflowError):
        raise _protocol("the tesseract output had an unexpected shape") from None


def _parse_tsv(tsv: str) -> OcrPage:
    rows = [row for row in tsv.split("\n") if row.strip("\r")]
    if not rows or tuple(rows[0].rstrip("\r").split("\t")) != TSV_COLUMNS:
        raise _protocol("the tesseract output had no TSV header")
    width = height = None
    words: list[OcrWord] = []
    lines: dict[tuple[int, int, int], int] = {}
    for row in rows[1:]:
        cols = row.rstrip("\r").split("\t", len(TSV_COLUMNS) - 1)
        if len(cols) == len(TSV_COLUMNS) - 1:
            cols.append("")  # a row without text may end at the conf column
        if len(cols) != len(TSV_COLUMNS):
            raise _protocol("a tesseract TSV row had the wrong number of columns")
        level = int(cols[0])
        left, top, w, h = (int(c) for c in cols[6:10])
        if level == _TSV_PAGE:
            width, height = float(w), float(h)
            continue
        if level != _TSV_WORD:
            continue
        text = cols[11].strip()
        if not text:
            continue
        conf = float(cols[10])
        if not math.isfinite(conf):
            raise _protocol("a tesseract confidence was not a finite number")
        line = lines.setdefault((int(cols[2]), int(cols[3]), int(cols[4])), len(lines))
        x0, y0, x1, y1 = float(left), float(top), float(left + w), float(top + h)
        words.append(
            OcrWord(
                text=text,
                polygon=(x0, y0, x1, y0, x1, y1, x0, y1),
                confidence=None if conf < 0 else conf / 100.0,
                line=line,
            )
        )
    if width is None or height is None:
        raise _protocol("the tesseract output had no page row")
    return OcrPage(width=width, height=height, unit="pixel", angle=0.0, words=words)


def _kill_group(proc: asyncio.subprocess.Process) -> None:
    with contextlib.suppress(ProcessLookupError, PermissionError):
        os.killpg(proc.pid, signal.SIGKILL)


_VERSION = re.compile(r"tesseract\s+v?(\d+)\.(\d+)")


class TesseractOcrClient:
    """Local OCR through the ``tesseract`` CLI (no Python wrapper): the page image on stdin, TSV
    on stdout, bounded by ``timeout_s`` and a semaphore of ``max_concurrency``.

    Errors are :class:`OcrError` codes only — ``ocr_unconfigured`` (binary missing),
    ``ocr_timeout`` (process killed), ``ocr_failed`` (non-zero exit), ``ocr_protocol`` (output of
    an unknown shape). Tesseract's stderr is never read: it can echo recognised text.
    """

    #: The readiness detail when the binary is missing (``refresh_ocr_readiness``).
    unconfigured_detail = "tesseract_missing"

    def __init__(self, *, cmd: str, langs: str, timeout_s: float, max_concurrency: int) -> None:
        self._cmd = cmd
        self._langs = langs
        self._timeout = timeout_s
        self._sem = asyncio.Semaphore(max_concurrency)
        self.engine = "tesseract"  # "tesseract-<major.minor>" once probe() has read the version

    def _binary(self) -> str | None:
        return shutil.which(self._cmd)

    def configured(self) -> bool:
        """True when the binary resolves (on ``PATH``, or the configured path)."""
        return self._binary() is not None

    async def read(self, image: bytes, *, content_type: str = "image/png") -> OcrPage:
        """OCR one page image (PNG or JPEG). Raises :class:`OcrError`."""
        binary = self._binary()
        if binary is None:
            raise OcrError("ocr_unconfigured", "the tesseract binary is not available")
        async with self._sem:
            started = time.monotonic()
            outcome = "error"
            try:
                out = await self._run(binary, image)
                page = parse_tsv(out.decode("utf-8", errors="replace"))
                outcome = "ok"
                return page
            except OcrError as exc:
                outcome = exc.code
                raise
            finally:
                OCR_CALLS.labels("tesseract", outcome).inc()
                OCR_SECONDS.observe(max(time.monotonic() - started, 0.0))

    async def _run(self, binary: str, image: bytes) -> bytes:
        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                "stdin",
                "stdout",
                "-l",
                self._langs,
                "--psm",
                "3",
                "tsv",
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.DEVNULL,
                start_new_session=True,  # its own process group: a timeout kills every child
            )
        except OSError as exc:
            raise OcrError(
                "ocr_failed", f"tesseract could not start ({type(exc).__name__})"
            ) from None
        try:
            out, _ = await asyncio.wait_for(proc.communicate(image), timeout=self._timeout)
        except TimeoutError:
            _kill_group(proc)
            await proc.wait()
            raise OcrError("ocr_timeout", f"no result within {self._timeout:.0f}s") from None
        except BaseException:  # cancelled (the request went away) or failed: never leave it running
            _kill_group(proc)
            raise
        if proc.returncode != 0:
            raise OcrError("ocr_failed", f"tesseract exited with status {proc.returncode}")
        return out

    async def probe(self) -> tuple[bool, str]:
        """``(ok, detail)`` for /readyz: ``tesseract --version`` exits 0 → ``(True, "up")``."""
        binary = self._binary()
        if binary is None:
            return False, self.unconfigured_detail
        try:
            proc = await asyncio.create_subprocess_exec(
                binary,
                "--version",
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,  # older releases print the version on stderr
            )
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=10.0)
        except (OSError, TimeoutError):
            return False, "down"
        if proc.returncode != 0:
            return False, f"down:{proc.returncode}"
        match = _VERSION.search(out.decode("utf-8", errors="replace"))
        if match:
            self.engine = f"tesseract-{match.group(1)}.{match.group(2)}"
        return True, "up"

    async def aclose(self) -> None:
        return None


def build_ocr_client(settings: Settings) -> OcrClient:
    """The client for ``settings.ocr_engine``: :class:`AzureReadClient` (endpoint and key set),
    :class:`TesseractOcrClient` (``tesseract``, or ``auto`` without Azure and with the binary),
    else :class:`NullOcrClient`. TLS to the gateway trusts ``DPS_SSL_CERT_FILE`` when set (the
    corporate CA the Stellar client already uses)."""
    engine = settings.ocr_engine
    if engine == "tesseract":
        return TesseractOcrClient(
            cmd=settings.tesseract_cmd,
            langs=settings.tesseract_langs,
            timeout_s=settings.tesseract_timeout_seconds,
            max_concurrency=settings.ocr_max_concurrency,
        )
    if engine != "azure":
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
        detail = getattr(ocr, "unconfigured_detail", "unconfigured")
        READINESS.set("ocr", False, detail, provider=settings.ocr_provider or "none")
        return
    ok, detail = await ocr.probe()
    READINESS.set("ocr", ok, detail, provider=settings.ocr_provider, engine=ocr.engine)
    if not ok:
        logger.warning("ocr_probe_failed", engine=ocr.engine, status=detail)
