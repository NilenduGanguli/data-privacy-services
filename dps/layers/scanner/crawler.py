"""The scan loop (spec §8): a breadth-first crawl of one registered site in one isolated context,
the request guard and its scope proxy installed before the first page exists. Every page is
processed in fresh tabs:

1. visit → extract → classify (and, when the policy opts in, T3 metadata labelling of the fields
   left unclassified: one value-free LLM call per page, M5 Task 8B) → risk flags → original and
   annotated screenshots (masked);
2. parallel view: fill with the display persona (synthesize) and with tokens of it (obfuscate),
   each in a fresh tab armed before the first value is set and closed after its screenshot;
3. an obfuscated capture when the page renders personal data or arrived pre-filled;
4. discovery: in-scope links (depth/page/template budgets, deny list) and click-only SPA routes
   (a clickable whose resolved target, a hash route included, the scope refuses is never clicked).
   A clickable with no target to check (``href="#"`` with an ``onclick``, ``role="link"``, a
   ``javascript:`` link) may run a route the scope refuses in the same document (a hash route or
   ``pushState`` set by its handler): no request exists for the guard to refuse, so the route has
   run. Such a landing is never recorded as a skipped page or a queued route: a value-free
   ``spa``/``reached`` event names it, ``RunOutcome.scope_routes_reached`` counts it (evidence
   pack ``scan`` block), and that tab is not processed further (M4 verify r1 R3-B1).

There is no code path that sends a form: the only clicks are on in-scope link-like elements
outside any form on unfilled tabs (SPA discovery and its replay, both on ``[data-dps-click]``
locators the extraction stamps), fills never press a key or click, and the guard aborts every
non-GET request and every navigation of a filled tab. Every tab is closed through
``ScanGuard.close`` (unload traffic aborted) and the context through ``close_scan_context``.

Page data (values, labels, URLs with their queries) stays in this process: what reaches the sink
is value-free (redacted URLs and text, counts, codes) or synthetic (persona values and tokens).
"""

from __future__ import annotations

import asyncio
import contextlib
import functools
import re
import secrets
import time
from collections import Counter, deque
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Literal
from urllib.parse import urlsplit

from playwright.async_api import Browser, BrowserContext, Dialog, Frame, Page, Response
from playwright.async_api import Error as PlaywrightError
from playwright.async_api import TimeoutError as PlaywrightTimeoutError

from dps.config import Settings
from dps.engine.detect.llm_label import LLM_LABEL_TIER, gateway_usable, job_llm_metrics
from dps.engine.detect.regex_guard import PatternTooSlow
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import seed_bytes
from dps.engine.taxonomy import metric_entity_label
from dps.layers.scanner.browser import close_scan_context, new_scan_context
from dps.layers.scanner.capture import (
    capture_screen,
    draw_overlays,
    field_boxes,
    obfuscated_capture,
    shown_values,
)
from dps.layers.scanner.classify import (
    classify_field,
    classify_fields,
    classify_rendered,
    draws_placeholder,
    label_leftover_fields,
    page_protected,
    redact_text,
    sensitive_prefilled,
    value_classifier,
)
from dps.layers.scanner.dom import (
    CLICK_TARGET_JS,
    CLICKABLES_JS,
    SUBMIT_ATTEMPTS_JS,
    extract_page,
    frame_map,
)
from dps.layers.scanner.guard import BlockedRequest, ScanGuard, stored_host
from dps.layers.scanner.inventory import field_records, field_risks, rendered_risks, url_risks
from dps.layers.scanner.models import (
    FieldClassification,
    FieldFacts,
    FieldRecord,
    PageRecord,
    PageSnapshot,
    RenderedFinding,
    ScanEvent,
)
from dps.layers.scanner.personas_fill import (
    FieldPlan,
    FillResult,
    LeakageError,
    PersonaFill,
    fill_page,
    persona_values,
    plan_field,
    prepare_fill,
    tokens_for,
    validation_counts,
    validation_records,
)
from dps.layers.scanner.runtime import (  # re-exported for callers that import the crawler
    LeaseLost,
    MemoryScanSink,
    RunOutcome,
    ScanRuntime,
    ScanSink,
)
from dps.layers.scanner.urls import canonical, redact_origin, redact_url, template
from dps.observability import SCAN_FIELDS, SCAN_FILLS, SCAN_PAGES, get_logger

__all__ = [
    "ClickReplay",
    "LeaseLost",
    "MemoryScanSink",
    "RunOutcome",
    "ScanRunner",
    "ScanRuntime",
    "ScanSink",
    "Target",
]

logger = get_logger(__name__)
LOGIN_PATH = re.compile(r"/(log-?in|sign-?in|auth|sso)(/|$)", re.IGNORECASE)
_OTHER_HOST = ("other_origin", "unregistered_host")
_MODES: tuple[Literal["synthesize", "obfuscate"], ...] = ("synthesize", "obfuscate")
NETWORKIDLE_MAX_MS = 5000
CLICK_TIMEOUT_MS = 3000
#: Before a click: a mark only this document holds (a fresh token in a non-enumerable property).
#: After it: whether the tab still shows the document that was clicked (a same-document navigation:
#: a hash route or ``pushState``), as opposed to a new document the guard saw being requested.
_MARK_DOCUMENT_JS = (
    "(t) => { Object.defineProperty(window, '__dpsClickDoc', "
    "{ value: t, configurable: true, enumerable: false, writable: false }); return true; }"
)
_SAME_DOCUMENT_JS = "(t) => window.__dpsClickDoc === t"


class _NavFailed(Exception):
    """A page could not be reached. ``skipped``: the scope refused the replay click's target
    (``code`` is the navigation verdict), so the page is recorded as skipped, not failed."""

    def __init__(self, code: str, *, skipped: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.skipped = skipped


@dataclass(frozen=True)
class ClickReplay:
    """How to reach an SPA route again: load ``parent``, click its ``ordinal``-th clickable."""

    parent: Target
    frame_index: int
    ordinal: int


@dataclass(frozen=True)
class Target:
    """A page to visit (``url`` is the canonical URL, memory only: it may carry a query)."""

    page_no: int
    url: str = field(repr=False)
    depth: int
    parent_page_no: int | None
    via: Literal["start", "link", "spa_click", "redirect"]
    replay: ClickReplay | None = None


def _signature(f: FieldFacts) -> tuple[Any, ...]:
    return (f.tag, f.input_type, f.name, f.id, f.label)


def count_field_metrics(classes: Iterable[FieldClassification]) -> None:
    """``dps_scan_fields_total`` for every personal field of a page; a custom entity type (a
    policy's own name) is labelled ``custom``, so caller-chosen names never reach the anonymous
    ``/metrics`` (S3 security review F-10, as ``dps_findings_total`` does)."""
    for c in classes:
        if c.entity_type:
            SCAN_FIELDS.labels(metric_entity_label(c.entity_type)).inc()


def _same_page(a: str, b: str) -> bool:
    """Whether two canonical URLs name the same document (path, query and hash route)."""
    pa, pb = urlsplit(a), urlsplit(b)
    return (pa.netloc, pa.path, pa.query, pa.fragment) == (
        pb.netloc,
        pb.path,
        pb.query,
        pb.fragment,
    )


class ScanRunner:
    """One crawl. ``run()`` returns the outcome or raises :class:`LeaseLost`."""

    def __init__(
        self,
        *,
        settings: Settings,
        engine: PrivacyEngine,
        runtime: ScanRuntime,
        sink: ScanSink,
        browser: Browser,
        harden_js: bool = True,
    ) -> None:
        self.s, self.engine, self.rt, self.sink = settings, engine, runtime, sink
        self.browser, self.harden_js = browser, harden_js
        self.detector = engine.detector(runtime.policy, runtime.locale)
        self.classify_value = value_classifier(self.detector)
        self.display = runtime.spec.fill.display_persona
        self.seed_raw = seed_bytes(runtime.seed)
        self.persona = persona_values(
            engine,
            seed=runtime.seed,
            persona_no=self.display,
            locale=runtime.locale,
            policy=runtime.policy,
        )
        self.tokens = functools.partial(
            tokens_for,
            engine,
            key=runtime.key,
            policy=runtime.policy,
            locale=runtime.locale,
            persona_no=self.display,
        )
        self.outcome = RunOutcome()
        #: T3 metadata labelling is asked for (M5 Task 8B); the calls it made so far
        self._t3 = LLM_LABEL_TIER in runtime.policy.detect.tiers
        self._label_calls = 0
        self.frontier: deque[Target] = deque()
        self.guard: ScanGuard | None = None
        self.context: BrowserContext | None = None
        self._seen: set[str] = set()
        self._per_template: Counter[str] = Counter()
        self._skip_keys: set[tuple[str, str]] = set()
        self._next_no = 0
        self._seq = 0
        self._pending: list[PageRecord] = []
        self._events: list[ScanEvent] = []
        self._current: Target | None = None

    def __repr__(self) -> str:
        return f"ScanRunner({self.rt!r})"

    # -- bookkeeping ---------------------------------------------------------------------------
    def _alloc(self) -> int:
        self._next_no += 1
        return self._next_no

    def _redact(self, url: str) -> str:
        return redact_url(url, self.classify_value)

    def _template(self, url: str) -> str:
        return template(url, self.classify_value)

    def _event(
        self,
        kind: str,
        result: str,
        *,
        page_no: int | None = None,
        method: str | None = None,
        target: str = "",
        note: str = "",
        counts: dict[str, int] | None = None,
    ) -> None:
        if self._seq >= self.s.scan_max_events:
            self.outcome.events_dropped += 1
            return
        self._seq += 1
        self._events.append(
            ScanEvent(
                seq=self._seq,
                ts=datetime.now(UTC),
                kind=kind,  # type: ignore[arg-type]
                method=method,
                result=result,
                page_no=page_no,
                target=target,
                note_code=note,
                counts=counts or {},
            )
        )

    @property
    def _guard(self) -> ScanGuard:
        assert self.guard is not None
        return self.guard

    @property
    def _context(self) -> BrowserContext:
        assert self.context is not None
        return self.context

    # -- lifecycle -----------------------------------------------------------------------------
    async def run(self) -> RunOutcome:
        """Crawl until the frontier is empty, the page budget is spent or the scan's time is up."""
        self.guard = ScanGuard(
            host_allowed=self.rt.scope.network_host_allowed,
            path_verdict=self.rt.scope.request_path_verdict,
            allow_loopback=self.s.env == "local",  # S5: the address rule of every hop
        )
        self.context = await new_scan_context(
            self.browser,
            self.s,
            storage_state=self.rt.storage_state,
            locale=self.rt.locale,
            guard=self.guard,
            harden_js=self.harden_js,
        )
        self.context.on("page", self._on_page)
        start = Target(
            page_no=self._alloc(), url=self.rt.start_url, depth=0, parent_page_no=None, via="start"
        )
        self._seen.add(start.url)
        self._per_template[self._template(start.url)] += 1
        self.frontier.append(start)
        self._pending.append(self._queued(start))
        try:
            async with asyncio.timeout(self.s.scan_job_timeout_seconds):
                while self.frontier:
                    target = self.frontier.popleft()
                    if self.outcome.pages_visited >= self.rt.max_pages:
                        self.outcome.truncated = "pages"
                        self._mark_budget(target)
                        continue
                    self._current = target
                    await self._process(target)
                    self._current = None
        except TimeoutError:
            self.outcome.truncated = "time"
            if self._current is not None:
                self._pending.append(self._record(self._current, status="failed", reason="timeout"))
            for target in self.frontier:
                self._mark_budget(target)
            self.frontier.clear()
        except PatternTooSlow as exc:
            # S3 security review F-02: a policy pattern ran past its CPU budget. That is
            # deterministic (each run is charged its own CPU), so the crawl stops and the scan
            # fails with the code — nothing is skipped silently. Nothing below may run a pattern
            # again (the call's allowance is spent): what was recorded is kept, the request counts
            # are saved, blocked requests get no redacted navigation-log entry.
            self.outcome.error_code = exc.code
            self.frontier.clear()
        finally:
            await close_scan_context(self.context, self.guard)  # never context.close() alone
            self.outcome.websockets_closed = self.guard.websockets_closed
            self.outcome.llm = self._llm_figures()
        blocked = self.guard.drain_blocked()
        if self.outcome.error_code is None:
            self._blocked_events(blocked)
        counts = self.guard.drain_counts()
        if self._pending or self._events or counts:
            await self.sink.save_batch(
                pages=self._take_pending(), fields={}, requests=counts, events=self._take_events()
            )
        return self.outcome

    def _queued(self, t: Target) -> PageRecord:
        return self._record(t, status="queued")

    def _record(self, t: Target, *, status: str, reason: str | None = None) -> PageRecord:
        return PageRecord(
            page_no=t.page_no,
            url_redacted=self._redact(t.url),
            depth=t.depth,
            parent_page_no=t.parent_page_no,
            status=status,  # type: ignore[arg-type]
            status_reason=reason,
            discovered_via=t.via,
            template=self._template(t.url),
        )

    def _mark_budget(self, t: Target) -> None:
        self._pending.append(self._record(t, status="skipped", reason="budget"))

    def _take_pending(self) -> list[PageRecord]:
        out, self._pending = self._pending, []
        return out

    def _take_events(self) -> list[ScanEvent]:
        out, self._events = self._events, []
        return out

    async def _on_page(self, page: Page) -> None:
        """A popup (a page with an opener) is never followed: closed through the guard."""
        try:
            opener = await page.opener()
        except PlaywrightError:
            return
        if opener is not None:
            self.outcome.popups_closed += 1
            self._event("popup", "closed", note="popup")
            await self._guard.close(page)

    async def _on_dialog(self, dialog: Dialog) -> None:
        page = dialog.page
        if dialog.type == "beforeunload" and page is not None and self._guard.is_closing(page):
            return  # guard.close answers it ("leave")
        self.outcome.dialogs_dismissed += 1
        self._event("dialog", "dismissed", note=dialog.type)
        with contextlib.suppress(PlaywrightError):
            await dialog.dismiss()

    # -- navigation ----------------------------------------------------------------------------
    async def _open(self, t: Target) -> tuple[Page, Response | None]:
        page = await self._context.new_page()
        self._guard.bind(page, t.page_no)
        page.on("dialog", self._on_dialog)
        timeout = self.s.scan_page_timeout_seconds * 1000
        try:
            response = await self._navigate(page, t, timeout)
            with contextlib.suppress(PlaywrightTimeoutError):
                await page.wait_for_load_state(
                    "networkidle", timeout=min(NETWORKIDLE_MAX_MS, timeout)
                )
            await page.wait_for_timeout(self.s.scan_settle_ms)
            return page, response
        except BaseException as exc:
            await self._close(page)
            if isinstance(exc, PlaywrightTimeoutError):
                raise _NavFailed("timeout") from None
            if isinstance(exc, PlaywrightError):
                raise _NavFailed("blocked") from None
            raise

    async def _navigate(self, page: Page, t: Target, timeout: int) -> Response | None:
        if t.replay is None:
            return await page.goto(t.url, wait_until="domcontentloaded", timeout=timeout)
        response = await self._navigate(page, t.replay.parent, timeout)
        await page.wait_for_timeout(self.s.scan_settle_ms)
        frames = await frame_map(page, iframes=self.rt.spec.scope.iframes)
        frame = frames.get(t.replay.frame_index)
        if frame is None:
            raise _NavFailed("replay")
        try:
            mark = await self._mark_document(page)
            verdict, _ = await self._click(frame, t.replay.frame_index, t.replay.ordinal)
        except PlaywrightError:
            raise _NavFailed("replay") from None
        if verdict != "ok":  # the element now points somewhere the scope refuses: not clicked
            raise _NavFailed(verdict, skipped=True)
        await page.wait_for_timeout(self.s.scan_settle_ms)
        if await self._reached_refused_route(page, mark, t):
            raise _NavFailed("scope_reached")  # the page's own handler ran it: stop this tab
        return response

    async def _click(self, frame: Frame, frame_index: int, ordinal: int) -> tuple[str, str | None]:
        """Re-stamp the frame's clickables (same predicate as the extraction) and click one —
        only when the scope allows a navigation to its resolved target.

        Returns ``(verdict, target)``; on any verdict but ``ok`` nothing was clicked. A hash route
        (``<a href="#/logout">``) never reaches the network, so the request guard cannot refuse
        it: the page's own route handler would run. Its target is therefore checked here, before
        the click, with the same :meth:`CrawlScope.navigation_verdict` as a link (a hash route is
        checked against the deny list).

        An element without ``href`` (``role="link"``), a ``javascript:`` link and an ``href="#"``
        link whose ``onclick`` navigates have no target to check before the click. A navigation
        to a new document meets the guard (refused and counted when the scope refuses its path).
        A same-document one — a hash route or ``pushState`` the handler sets — sends no request
        and cannot be refused: the route has run when the click returns. The callers therefore
        check where the tab landed (:meth:`_reached_refused_route`) and record such a landing as
        reached, never as skipped.
        """
        await frame.evaluate(
            CLICKABLES_JS, {"frameIndex": frame_index, "shadow": self.rt.spec.scope.shadow_dom}
        )
        ref = f"{frame_index}:{ordinal}"
        target: str | None = await frame.locator(f'[data-dps-click="{ref}"]').evaluate(
            CLICK_TARGET_JS, timeout=CLICK_TIMEOUT_MS
        )
        verdict = self._click_verdict(target)
        if verdict != "ok":
            return verdict, target
        # Click 1 of 2 in the scanner: an in-scope link-like element outside any form, stamped by
        # CLICKABLES_JS, on an unfilled tab (the guard still blocks every non-GET it could cause).
        await frame.locator(f'[data-dps-click="{ref}"]').click(
            timeout=CLICK_TIMEOUT_MS, no_wait_after=True
        )
        return verdict, target

    def _click_verdict(self, target: str | None) -> str:
        """``ok`` or why a clickable's resolved target is out of scope (``''`` → ``scheme``)."""
        if target is None or target[:11].lower() == "javascript:":
            return "ok"
        return self.rt.scope.navigation_verdict(target)

    def _refuse_click(self, target: str | None, parent: Target, verdict: str) -> None:
        """Record a discovery click the scope refused: a value-free ``spa``/``skipped`` event for
        every refusal and, once per target, the same skipped page row a refused link gets."""
        url = canonical(target) if target else None
        if url is None:
            shown = ""
        elif verdict in _OTHER_HOST:
            shown = self._origin_of(url)
        else:
            shown = self._redact(url)
        self._event("spa", "skipped", page_no=parent.page_no, target=shown, note=verdict)
        if url is not None and verdict != "scheme" and url not in self._seen:
            self._seen.add(url)
            self._skip(url, parent, verdict)

    async def _mark_document(self, page: Page) -> str:
        """Mark the main frame's document before a click (see ``_MARK_DOCUMENT_JS``)."""
        token = secrets.token_hex(12)
        with contextlib.suppress(PlaywrightError):
            await page.main_frame.evaluate(_MARK_DOCUMENT_JS, token)
        return token

    async def _reached_refused_route(self, page: Page, mark: str, parent: Target) -> bool:
        """After a click: whether the tab is still in the document that was clicked (``mark``)
        while its URL is one the scope refuses — a hash route or ``pushState`` the page's own
        handler set, with no request the guard could refuse, so the route has run. Then a
        value-free ``spa``/``reached`` event (the redacted URL or the origin, and the verdict) is
        written and ``scope_routes_reached`` counted; the caller neither queues nor skips the URL
        and stops processing that tab. A new document is not such a landing: its navigation
        request met the guard, which refused it (counted ``blocked``) or, for an HTTPS redirect
        hop it cannot see into, counted it in ``scope_paths_reached``."""
        url = canonical(page.url)
        if url is None:
            return False
        verdict = self.rt.scope.navigation_verdict(url)
        if verdict == "ok":
            return False
        try:
            same = bool(await page.main_frame.evaluate(_SAME_DOCUMENT_JS, mark))
        except PlaywrightError:
            same = False  # navigating or an error page: a new document
        if not same:
            return False
        self.outcome.scope_routes_reached += 1
        shown = self._origin_of(url) if verdict in _OTHER_HOST else self._redact(url)
        self._event("spa", "reached", page_no=parent.page_no, target=shown, note=verdict)
        return True

    async def _close(self, page: Page) -> None:
        await self._guard.close(page)  # unload traffic aborted; never page.close()
        self._guard.release(page)

    # -- one page ------------------------------------------------------------------------------
    async def _process(self, t: Target) -> None:
        started = time.monotonic()
        record = self._record(t, status="visiting")
        fields: list[FieldRecord] = []
        try:
            page, response = await self._open(t)
        except _NavFailed as exc:
            outcome: Literal["skipped", "failed"] = "skipped" if exc.skipped else "failed"
            record.status, record.status_reason = outcome, exc.code
            self._event(
                "navigate",
                outcome,
                page_no=t.page_no,
                method="GET",
                target=record.url_redacted,
                note=exc.code,
            )
            await self.sink.audit_navigation(page_no=t.page_no, outcome=outcome)
            await self._finish(record, fields)
            return
        try:
            fields = await self._pipeline(t, page, response, record)
        except (PlaywrightError, LeakageError, _NavFailed):
            record.status, record.status_reason = "failed", "error"
            self._event("error", "failed", page_no=t.page_no, note="page_pipeline")
        finally:
            await self._close(page)
        record.timings["total_ms"] = int((time.monotonic() - started) * 1000)
        await self._finish(record, fields)

    async def _extract(self, page: Page) -> tuple[PageSnapshot, dict[int, Frame]]:
        scope = self.rt.spec.scope
        snap = await extract_page(
            page,
            iframes=scope.iframes,
            shadow_dom=scope.shadow_dom,
            max_fields=self.s.scan_max_fields_per_page,
        )
        return snap, await frame_map(page, iframes=scope.iframes)

    def _classify(self, snap: PageSnapshot) -> dict[str, FieldClassification]:
        """Every field of the page, with main's mixed-PII rules (``classify_fields``)."""
        return classify_fields(
            snap.fields, detector=self.detector, min_score=self.rt.policy.detect.min_score
        )

    def _llm_figures(self) -> dict[str, int] | None:
        """``progress.metrics.llm`` of this scan (``None`` when T3 was not asked for)."""
        return job_llm_metrics(self._label_calls) if self._t3 else None

    async def _labelled(
        self,
        snap: PageSnapshot,
        classes: dict[str, FieldClassification],
        rendered: list[RenderedFinding],
    ) -> dict[str, FieldClassification]:
        """T3 metadata labelling of the fields the classifiers left without an entity (M5 Task
        8B): one value-free call per page when the policy opts in and a real LLM provider is
        configured; the page's own values (:func:`~dps.layers.scanner.classify.page_protected`)
        stay in this process and are checked against the prompt before it leaves."""
        if not self._t3 or not gateway_usable(self.engine.gateway):
            return classes
        labelled, outcome = await label_leftover_fields(
            snap.fields,
            classes,
            gateway=self.engine.gateway,
            settings=self.s,
            policy=self.rt.policy,
            detector=self.detector,
            protected=page_protected(snap, detector=self.detector, rendered=rendered),
        )
        if outcome is not None:
            self._label_calls += outcome.llm_calls
        return labelled

    @staticmethod
    def _with_labels(
        classes: dict[str, FieldClassification],
        snap: PageSnapshot,
        base: PageSnapshot,
        labelled: dict[str, FieldClassification],
    ) -> dict[str, FieldClassification]:
        """``classes`` of a re-extracted tab with the page's T3 labels carried over to the fields
        that are the same field as in the first visit (same key and signature), so every capture
        of the page masks what the inventory says is personal."""
        expected = {f.key: _signature(f) for f in base.fields}
        out = dict(classes)
        for f in snap.fields:
            label = labelled.get(f.key)
            current = out.get(f.key)
            if (
                label is not None
                and LLM_LABEL_TIER in label.detectors
                and (current is None or current.entity_type is None)
                and expected.get(f.key) == _signature(f)
            ):
                out[f.key] = label
        return out

    async def _pipeline(
        self, t: Target, page: Page, response: Response | None, record: PageRecord
    ) -> list[FieldRecord]:
        final = canonical(page.url) or t.url
        record.http_status = response.status if response is not None else None
        if not self.rt.scope.network_host_allowed(urlsplit(final).hostname or ""):
            record.status, record.status_reason = "failed", "host"  # a hop left the hosts
            return []
        self._event(
            "navigate",
            str(record.http_status or 200),
            page_no=t.page_no,
            method="GET",
            target=record.url_redacted,
            note=t.via,
        )
        await self.sink.audit_navigation(page_no=t.page_no, outcome="ok")
        if urlsplit(final).path != urlsplit(t.url).path:
            record.status = "redirected"
            path = urlsplit(final).path
            record.status_reason = "auth_redirect" if LOGIN_PATH.search(path) else "redirect"
            record.final_url_redacted = self._redact(final)
            await self._consider(final, t, via="redirect")
            return []
        if record.http_status is not None and record.http_status >= 400:
            record.status, record.status_reason = "failed", f"http_{record.http_status // 100}xx"
            return []
        scope = self.rt.spec.scope
        snap, frames = await self._extract(page)
        rendered = classify_rendered(snap.texts, detector=self.detector)
        classes = await self._labelled(snap, self._classify(snap), rendered)
        plans = {f.key: plan_field(f, classes[f.key]) for f in snap.fields}
        prefilled = sensitive_prefilled(snap.fields, classes, self.detector)
        external = [
            u
            for link in snap.links
            if (u := canonical(link.href, base=final)) is not None
            and self.rt.scope.navigation_verdict(u) in _OTHER_HOST
        ]
        flags = (
            field_risks(t.page_no, [(f, classes[f.key]) for f in snap.fields], snap.forms)
            + url_risks(t.page_no, final, external, classify_value=self.classify_value)
            + rendered_risks(t.page_no, rendered)
        )
        record.title_redacted = redact_text(snap.title, self.detector)
        record.form_count, record.field_count = len(snap.forms), len(snap.fields)
        record.personal_field_count = sum(1 for c in classes.values() if c.entity_type)
        record.critical_field_count = sum(
            1 for c in classes.values() if c.sensitivity == "CRITICAL"
        )
        record.forms = [
            {
                "form_index": fm.form_index,
                "method": fm.method.strip().lower()[:8],
                "field_count": fm.field_count,
                "action_redacted": self._redact(canonical(fm.action, base=final) or final)
                if fm.action
                else "",
                "frame_index": fm.frame_index,
                "in_shadow": fm.in_shadow,
            }
            for fm in snap.forms
        ]
        record.rendered_findings = dict(Counter(r.entity_type for r in rendered))
        record.risk_flags = flags
        count_field_metrics(classes.values())
        if self.rt.spec.capture.screenshots:
            await self._original(t, page, frames, snap, rendered, prefilled, record)
        await self._close(page)
        fills: dict[str, PersonaFill] = {}
        results: dict[str, dict[str, FillResult]] = {}
        if self.rt.spec.parallel_view and any(p.kind != "skip" for p in plans.values()):
            for mode in _MODES:
                done = await self._fill_and_capture(t, mode, snap, classes, plans, record)
                if done is not None:
                    fills[mode], results[mode] = done
        # a personal select that draws only its placeholder is masked, but is no value the page
        # arrived with: it alone needs no obfuscated capture (S2 captures-privacy r3 B1)
        if rendered or any(not draws_placeholder(f) for f, _ in prefilled):
            await self._capture(t, record, snap, classes)
        else:
            record.capture_status = "not_needed"
        if t.depth < self.rt.max_depth:
            for link in snap.links:
                if not link.download and (u := canonical(link.href, base=final)) is not None:
                    await self._consider(u, t, via="link")
            if scope.spa_clicks and snap.clickables:
                await self._discover_clicks(t, snap, final)
        record.status = "done"
        return field_records(
            t.page_no,
            snap.fields,
            classes,
            plans,
            fills,
            results,
            flags,
            detector=self.detector,
        )

    async def _original(
        self,
        t: Target,
        page: Page,
        frames: dict[int, Frame],
        snap: PageSnapshot,
        rendered: list[RenderedFinding],
        prefilled: list[tuple[FieldFacts, str]],
        record: PageRecord,
    ) -> None:
        """The original screenshot (every rendered finding and pre-filled value masked) and the
        same with numbered field outlines; withheld (``None``) when a mask cannot be placed."""
        boxes = await field_boxes(frames, snap.fields)
        original = await capture_screen(
            page,
            frames,
            snap,
            rendered=rendered,
            prefilled=prefilled,
            max_height=self.s.scan_screenshot_max_height,
            unmasked=self.s.scan_store_unmasked_originals,
            detector=self.detector,
        )
        if original is None:
            self._event("capture", "withheld", page_no=t.page_no, note="original")
            return
        record.artefacts["original"] = await self._png(original, t.page_no, "original")
        annotated = draw_overlays(original, boxes, variant="original")
        record.artefacts["annotated"] = await self._png(annotated, t.page_no, "annotated")

    async def _png(self, data: bytes, page_no: int, variant: str) -> str:
        return await self.sink.save_artefact(
            kind="screenshot",
            data=data,
            content_type="image/png",
            meta={"page_no": page_no, "variant": variant},
        )

    async def _fill_and_capture(
        self,
        t: Target,
        mode: Literal["synthesize", "obfuscate"],
        base: PageSnapshot,
        classes: dict[str, FieldClassification],
        plans: dict[str, FieldPlan],
        record: PageRecord,
    ) -> tuple[PersonaFill, dict[str, FillResult]] | None:
        try:
            page, _ = await self._open(t)
        except _NavFailed as exc:
            self.outcome.fills_skipped += 1
            self._event(
                "fill", "failed", page_no=t.page_no, target=record.url_redacted, note=exc.code
            )
            return None
        try:
            snap, frames = await self._extract(page)
            expected = {f.key: _signature(f) for f in base.fields}
            fresh = [f for f in snap.fields if expected.get(f.key) == _signature(f)]
            try:
                fill = prepare_fill(
                    fresh,
                    classes,
                    plans,
                    persona=self.persona,
                    persona_no=self.display,
                    seed=self.seed_raw,
                    locale=self.rt.locale,
                    mode=mode,
                    tokens=self.tokens if mode == "obfuscate" else None,
                )
            except LeakageError:
                return self._fill_failed(t, mode, record, "leakage")
            if not await self._guard.arm(page, fill.needles):  # a frame not locked: no fill
                return self._fill_failed(t, mode, record, "guard_lock")
            results = await fill_page(page, frames, fill.actions, settle_ms=self.s.scan_settle_ms)
            with contextlib.suppress(PlaywrightError):
                attempts = await page.evaluate(SUBMIT_ATTEMPTS_JS)
                self.outcome.submit_attempts_neutralised += int(attempts or 0)
            counts = validation_counts(
                validation_records(results, mode=mode, detector=self.detector)
            )
            record.validation[mode] = counts
            for r in results.values():
                SCAN_FILLS.labels(mode, r.status).inc()
            still = canonical(page.url)
            if still is None or not _same_page(still, canonical(snap.url) or t.url):
                # an aborted navigation leaves Chromium's error page in the tab: never captured
                self._event(
                    "fill",
                    "failed",
                    page_no=t.page_no,
                    target=record.url_redacted,
                    note="navigated",
                )
                return fill, results
            if self.rt.spec.capture.screenshots:
                await self._filled_screen(t, mode, page, snap, classes, fill, record)
            self._event(
                "fill",
                "captured",
                page_no=t.page_no,
                target=record.url_redacted,
                note=mode,
                counts={
                    "fields": len(results),
                    "rejected": counts["rejected"],
                    "persona": self.display,
                },
            )
            return fill, results
        finally:
            await self._close(page)

    def _fill_failed(
        self, t: Target, mode: str, record: PageRecord, note: str
    ) -> tuple[PersonaFill, dict[str, FillResult]] | None:
        self.outcome.fills_skipped += 1
        record.validation[mode] = {"failed": 1}
        self._event("fill", "failed", page_no=t.page_no, target=record.url_redacted, note=note)
        return None

    async def _filled_screen(
        self,
        t: Target,
        mode: str,
        page: Page,
        before: PageSnapshot,
        classes: dict[str, FieldClassification],
        fill: PersonaFill,
        record: PageRecord,
    ) -> None:
        """The filled tab, read again now that it shows persona values: every value the page
        rendered or pre-filled itself is masked (and every copy of what it showed before the fill
        swept), the filled fields stay readable and are outlined with their numbers — except a
        select: a fill cannot put a persona value into one, it picks one of the page's own
        options, so a select that is personal (or shows a detected value) stays masked whoever
        chose its option (S2 fix round 2)."""
        snap, frames = await self._extract(page)
        filled = {a.key for a in fill.actions}
        now = {
            f.key: classes.get(f.key)
            or classify_field(f, detector=self.detector, min_score=self.rt.policy.detect.min_score)
            for f in snap.fields
        }
        untouched = [
            (f, entity)
            for f, entity in sensitive_prefilled(snap.fields, now, self.detector)
            if f.key not in filled or f.tag == "select"
        ]
        earlier = classify_rendered(before.texts, detector=self.detector)
        text_of = {(x.frame_index, x.i): x.text for x in before.texts}
        sweep = [
            (text_of[(r.frame_index, r.i)][r.start : r.end], r.entity_type)
            for r in earlier
            if (r.frame_index, r.i) in text_of
        ]
        sweep += shown_values(sensitive_prefilled(before.fields, classes, self.detector))
        png = await capture_screen(
            page,
            frames,
            snap,
            rendered=classify_rendered(snap.texts, detector=self.detector),
            prefilled=untouched,
            max_height=self.s.scan_screenshot_max_height,
            detector=self.detector,
            sweep=sweep,
        )
        if png is None:
            self._event("capture", "withheld", page_no=t.page_no, note=f"filled_{mode}")
            return
        boxes = await field_boxes(frames, [f for f in snap.fields if f.key in filled])
        record.artefacts[f"filled_{mode}"] = await self._png(
            draw_overlays(png, boxes, variant=mode), t.page_no, f"filled_{mode}"
        )

    async def _capture(
        self,
        t: Target,
        record: PageRecord,
        base: PageSnapshot,
        labelled: dict[str, FieldClassification],
    ) -> None:
        """The obfuscated capture of a page that renders personal data or arrived pre-filled
        (``base``/``labelled``: the first visit's snapshot and classifications, whose T3 labels
        carry over)."""
        try:
            page, _ = await self._open(t)
        except _NavFailed as exc:
            record.capture_status = "withheld"
            self._event("capture", "withheld", page_no=t.page_no, note=exc.code)
            return
        try:
            snap, frames = await self._extract(page)
            classes = self._with_labels(self._classify(snap), snap, base, labelled)
            result = await obfuscated_capture(
                page,
                frames,
                snap,
                rendered=classify_rendered(snap.texts, detector=self.detector),
                prefilled=sensitive_prefilled(snap.fields, classes, self.detector),
                engine=self.engine,
                policy=self.rt.policy,
                mode=self.rt.spec.mode,
                key=self.rt.key,
                seed=self.seed_raw,
                locale=self.rt.locale,
                guard=self._guard,
                settings=self.s,
                page_no=t.page_no,
            )
            record.capture_status = result.status
            if result.status == "captured" and result.png is not None:
                record.artefacts["masked_capture"] = await self.sink.save_artefact(
                    kind="capture",
                    data=result.png,
                    content_type="image/png",
                    meta={"page_no": t.page_no, "variant": "masked"},
                )
                if result.capture_json is not None:
                    record.artefacts["capture_json"] = await self.sink.save_artefact(
                        kind="capture",
                        data=result.capture_json,
                        content_type="application/json",
                        meta={
                            "page_no": t.page_no,
                            "variant": "reveal_map",
                            "mode": self.rt.spec.mode,
                        },
                    )
            self._event(
                "capture",
                result.status,
                page_no=t.page_no,
                target=record.url_redacted,
                note=result.reason or self.rt.spec.mode,
            )
        finally:
            await self._close(page)

    # -- discovery -----------------------------------------------------------------------------
    async def _consider(
        self, url: str, parent: Target, *, via: str, replay: ClickReplay | None = None
    ) -> None:
        if url in self._seen:
            return
        self._seen.add(url)
        verdict = self.rt.scope.navigation_verdict(url)
        if verdict == "scheme":
            return
        if verdict != "ok":
            self._skip(url, parent, verdict)
            return
        tpl = self._template(url)
        if self._per_template[tpl] >= self.s.scan_max_per_template:
            self._skip(url, parent, "template")
            return
        self._per_template[tpl] += 1
        depth = parent.depth if via == "redirect" else parent.depth + 1
        target = Target(
            page_no=self._alloc(),
            url=url,
            depth=depth,
            parent_page_no=parent.page_no,
            via=via,  # type: ignore[arg-type]
            replay=replay,
        )
        self.frontier.append(target)
        self._pending.append(self._queued(target))
        if via == "spa_click":
            self.outcome.spa_routes += 1
            self._event(
                "spa", "queued", page_no=target.page_no, target=self._redact(url), note="spa_click"
            )

    def _skip(self, url: str, parent: Target, reason: str) -> None:
        other = reason in _OTHER_HOST
        shown = self._origin_of(url) if other else self._redact(url)
        # the template column is value-free (PageRecord checks it): an other-host row keys on the
        # origin's template ("host[:port]/"), every other row on the URL's template
        key = (reason, self._template(shown) if other else self._template(url))
        if key in self._skip_keys or len(self._skip_keys) >= self.s.scan_max_skipped_rows:
            return
        self._skip_keys.add(key)
        no = self._alloc()
        self._pending.append(
            PageRecord(
                page_no=no,
                url_redacted=shown,
                depth=parent.depth + 1,
                parent_page_no=parent.page_no,
                status="skipped",
                status_reason=reason,
                discovered_via="link",
                template=key[1],
            )
        )
        self._event("skip", "skipped", page_no=no, method="GET", target=shown, note=reason)

    def _origin_of(self, url: str) -> str:
        """A value-free origin for a URL outside the scope: the host is kept only when the scan
        may reach it, else reduced like the request log's hosts (``guard.stored_host``) — a page
        can put data in a host name."""
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        if host and self.rt.scope.network_host_allowed(host):
            return redact_origin(url)
        scheme = parts.scheme.lower() if parts.scheme.lower() in ("http", "https") else "http"
        return f"{scheme}://{stored_host(host, allowed=False)}"

    async def _discover_clicks(self, t: Target, snap: PageSnapshot, final: str) -> None:
        base = self._template(canonical(snap.url) or final)
        for c in snap.clickables[: self.s.scan_max_spa_clicks_per_page]:
            try:
                page, _ = await self._open(t)
            except _NavFailed:
                return
            try:
                frames = await frame_map(page, iframes=self.rt.spec.scope.iframes)
                frame = frames.get(c.frame_index)
                if frame is None:
                    continue
                # Click 2 of 2 in the scanner: SPA route discovery — the same kind of element as
                # the replay click (``_click``), on a fresh, unfilled tab; never one whose target
                # the scope refuses (``_click`` checks it first).
                mark = await self._mark_document(page)
                verdict, target = await self._click(frame, c.frame_index, c.ordinal)
                if verdict != "ok":
                    self._refuse_click(target, t, verdict)
                    continue
                await page.wait_for_timeout(self.s.scan_settle_ms)
                if await self._reached_refused_route(page, mark, t):
                    continue  # the page's own handler ran a refused route: recorded as reached
                url = canonical(page.url)
                if url is not None and self._template(url) != base:
                    replay = ClickReplay(parent=t, frame_index=c.frame_index, ordinal=c.ordinal)
                    await self._consider(url, t, via="spa_click", replay=replay)
            except PlaywrightError:
                self._event("error", "failed", page_no=t.page_no, note="spa_click")
            finally:
                await self._close(page)

    def _blocked_events(self, blocked: list[BlockedRequest]) -> None:
        for b in blocked:
            self._event(
                "request",
                "blocked",
                page_no=b.page_no,
                method=b.method,
                target=self._blocked_target(b),
                note=b.reason,
            )

    def _blocked_target(self, b: BlockedRequest) -> str:
        try:
            host = (urlsplit(b.url).hostname or "").lower()
        except ValueError:
            return ""
        if not host or not self.rt.scope.network_host_allowed(host):
            return self._origin_of(b.url) if host else ""
        return self._redact(b.url)

    async def _finish(self, record: PageRecord, fields: list[FieldRecord]) -> None:
        self.outcome.pages_visited += 1
        self.outcome.fields += len(fields)
        SCAN_PAGES.labels(record.status).inc()
        self._blocked_events(self._guard.drain_blocked())
        await self.sink.save_batch(
            pages=[*self._take_pending(), record],
            fields={record.page_no: fields},
            requests=self._guard.drain_counts(),
            events=self._take_events(),
        )
        logger.info(
            "scan_page",
            page_no=record.page_no,
            status=record.status,
            fields=len(fields),
            blocked=self._guard.blocked_total,
        )
        progress: dict[str, Any] = {
            "pages": self.outcome.pages_visited,
            "fields": self.outcome.fields,
            "blocked_requests": self._guard.blocked_total,
        }
        llm = self._llm_figures()
        if llm is not None:
            progress["llm"] = llm
        ok = await self.sink.progress(progress)
        if not ok:
            raise LeaseLost("lease_lost")
