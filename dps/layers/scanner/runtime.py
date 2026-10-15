"""The Playwright-free types the scan loop, the job handlers and the API share.

``ScanRuntime`` is everything one scan needs at run time (opened from the job's sealed secret by
``dps.layers.scanner.open_runtime``, or built directly by tests and tools); ``ScanSink`` is where
the crawler writes its value-free rows and artefacts (``MemoryScanSink`` here for tests,
``DbScanSink`` in ``dps.layers.scanner`` for the worker); ``RunOutcome`` is what a crawl reports.

Nothing here imports Playwright or Pillow (the API imports this module through the package).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Protocol

from dps.engine.policy import Policy
from dps.engine.synth.surrogates import SynthesisSeed
from dps.engine.transform.crypto import KeyMaterial
from dps.jobs_models import ScanSpec
from dps.layers.scanner.models import FieldRecord, PageRecord, RequestCount, ScanEvent
from dps.layers.scanner.urls import CrawlScope


class LeaseLost(RuntimeError):
    """The job's lease is gone (canceled or reaped): stop the crawl, the new owner finalizes."""


class ScanSink(Protocol):
    """Where a crawl writes. Every row it receives is value-free (or synthetic)."""

    async def save_batch(
        self,
        *,
        pages: list[PageRecord],
        fields: dict[int, list[FieldRecord]],
        requests: list[RequestCount],
        events: list[ScanEvent],
    ) -> None:
        """Persist pages (upsert), the fields of the pages in ``fields`` (replace), request
        counters (add) and navigation-log events (append) together."""
        ...

    async def save_artefact(
        self, *, kind: str, data: bytes, content_type: str, meta: dict[str, Any]
    ) -> str:
        """Store one artefact (a masked PNG, a capture JSON) and return its id."""
        ...

    async def progress(self, progress: dict[str, Any]) -> bool:
        """Report progress; ``False`` means the lease is lost (canceled or reaped): stop."""
        ...

    async def audit_navigation(self, *, page_no: int, outcome: str) -> None:
        """Audit one navigation (no URL in the audit row)."""
        ...


class MemoryScanSink:
    """A :class:`ScanSink` in memory (tests and local tools; the worker uses ``DbScanSink``).

    ``stop_after_pages``: :meth:`progress` returns ``False`` from that call on (a lost lease)."""

    def __init__(self, *, stop_after_pages: int | None = None) -> None:
        self.pages: dict[int, PageRecord] = {}
        self.fields: dict[int, list[FieldRecord]] = {}
        self.requests: list[RequestCount] = []
        self.events: list[ScanEvent] = []
        self.artefacts: dict[str, tuple[str, bytes, dict[str, Any]]] = {}
        self.navigations = 0
        self._progress_calls = 0
        self._stop = stop_after_pages

    async def save_batch(
        self,
        *,
        pages: list[PageRecord],
        fields: dict[int, list[FieldRecord]],
        requests: list[RequestCount],
        events: list[ScanEvent],
    ) -> None:
        """Keep the rows (pages by number, the newest wins)."""
        for p in pages:
            self.pages[p.page_no] = p
        self.fields.update(fields)
        self.requests += requests
        self.events += events

    async def save_artefact(
        self, *, kind: str, data: bytes, content_type: str, meta: dict[str, Any]
    ) -> str:
        """Keep the bytes under ``a<n>``."""
        aid = f"a{len(self.artefacts) + 1}"
        self.artefacts[aid] = (kind, data, meta)
        return aid

    async def progress(self, progress: dict[str, Any]) -> bool:
        """``True`` until ``stop_after_pages`` calls were made."""
        self._progress_calls += 1
        return self._stop is None or self._progress_calls < self._stop

    async def audit_navigation(self, *, page_no: int, outcome: str) -> None:
        """Count it."""
        self.navigations += 1


@dataclass
class ScanRuntime:
    """One scan at run time. ``start_url`` (it may carry a query), ``key``, ``seed`` and
    ``storage_state`` are confidential: they never reach a ``repr``, a log line or a row."""

    spec: ScanSpec = field(repr=False)
    start_url: str = field(repr=False)
    key: KeyMaterial = field(repr=False)
    seed: SynthesisSeed = field(repr=False)
    storage_state: dict[str, Any] | None = field(repr=False)
    policy: Policy = field(repr=False)
    locale: str
    max_pages: int
    max_depth: int
    scope: CrawlScope = field(repr=False)

    def __repr__(self) -> str:
        return (
            f"ScanRuntime(origin={self.scope.start.text()!r}, locale={self.locale!r}, "
            f"max_pages={self.max_pages}, max_depth={self.max_depth})"
        )


@dataclass
class RunOutcome:
    """What one crawl did (counts only)."""

    pages_visited: int = 0
    fields: int = 0
    truncated: Literal["pages", "time"] | None = None
    spa_routes: int = 0
    events_dropped: int = 0
    submit_attempts_neutralised: int = 0
    dialogs_dismissed: int = 0
    popups_closed: int = 0
    websockets_closed: int = 0
    fills_skipped: int = 0  # a fill not done: leakage, a frame not locked, a page that navigated
    #: clicks whose own handler ran a route the scope refuses in the same document (a hash route or
    #: pushState set by an ``href="#"`` link's onclick): no request existed for the guard to refuse
    scope_routes_reached: int = 0
    #: a deterministic refusal that ended the crawl early — ``custom_pattern_too_slow``: a policy
    #: pattern ran past its CPU budget (S3 security review F-02); the scan fails with it, no retry
    error_code: str | None = None
