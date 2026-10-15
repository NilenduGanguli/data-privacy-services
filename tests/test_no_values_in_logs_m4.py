"""No page value, key, seed, session or URL query reaches a log line (contract §0.1, M4).

Static part (offline): every log call in an M4 module (the scanner package, the browser-worker
process and the scans router) is ``logger.<level>("<snake_case_event>", <allow-listed field>=…)``,
with no positional format arguments, no ``**`` splat, no ``print`` and no stdlib logger (which would
bypass the allow-list). Dynamic part: the allow-list drops a scanner call site's value fields
(unit); a whole in-memory crawl of the demo site — request guard, scope proxy, fills, captures —
logs none of the values the site renders, its session cookie, the start URL's query or the
display persona's values (``browser``); the scans API logs none of the start URL's query, the
inline storage state or the returned key and seed (``db``). A self-test plants a value through an
allow-listed field and checks the scan catches it, also with its separators changed.
"""

from __future__ import annotations

import ast
import io
import json
import logging
from collections.abc import Iterable, Iterator
from pathlib import Path
from typing import Any

import pytest

import tests.scanner.conftest as scanner_fixtures
from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.engine.verify import normalise
from dps.observability import _EVENT_NAME, _SAFE_FIELDS, get_logger
from tests.scanner.helpers import DEMO_VALUE_KEYS, demo_cookie_state, make_runtime, received

# The scanner fixtures (demo site, Chromium, scan settings and engine) live in
# tests/scanner/conftest.py; contract §0.1 puts this file at the top of tests/, so it re-exports them.
chromium = scanner_fixtures.chromium
demo_data = scanner_fixtures.demo_data
demo_site_url = scanner_fixtures.demo_site_url
scan_engine = scanner_fixtures.scan_engine
scan_settings = scanner_fixtures.scan_settings

ROOT = Path(__file__).resolve().parents[1]
DPS = ROOT / "dps"
M4_MODULES = sorted(
    [
        *(DPS / "layers" / "scanner").glob("*.py"),
        DPS / "browser_worker.py",
        DPS / "layers" / "api" / "routers" / "scans.py",
    ]
)
LOG_METHODS = {"debug", "info", "warning", "error", "exception", "critical"}
#: The demo session cookie (a storage-state value) and the start URL's query (a page value).
SESSION_VALUE = "demo-valid"
SESSION_NAME = "demo_session"
QUERY_EMAIL = "ananya.k@example-mail.in"
QUERY_EMAIL_URL = "ananya.k%40example-mail.in"
#: data.json keys a scan of the demo site reads (rendered values plus identifiers). ``dob`` is left
#: out: ``14031988`` is the shape of a ``14:03:19.88`` timestamp once separators are dropped.
DEMO_SCAN_KEYS = (*DEMO_VALUE_KEYS, "account_number", "customer_id", "application_ref")
#: A normalised needle shorter than this is too common to mean anything (``f``, ``in``, ``yes``).
MIN_NEEDLE = 6


# ---------------------------------------------------------------- static: every M4 log call


def _log_calls(tree: ast.AST) -> Iterator[ast.Call]:
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr in LOG_METHODS
            and isinstance(node.func.value, ast.Name)
            and node.func.value.id == "logger"
        ):
            yield node


def _calls_named(tree: ast.AST, name: str) -> list[int]:
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name) and node.func.id == name
    ]


def _stdlib_logging(tree: ast.AST) -> list[int]:
    """``logging.getLogger(...)`` / ``logging.info(...)``: the stdlib bypasses the allow-list."""
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "logging"
        and node.func.attr in LOG_METHODS | {"getLogger", "log"}
    ]


def test_the_scan_covers_every_m4_module() -> None:
    assert all(p.is_file() for p in M4_MODULES)
    names = {p.name for p in M4_MODULES}
    assert {
        "__init__.py",
        "crawler.py",
        "guard.py",
        "proxy.py",
        "browser.py",
        "capture.py",
        "personas_fill.py",
        "browser_worker.py",
        "scans.py",
    } <= names
    assert len(M4_MODULES) >= 16


@pytest.mark.parametrize("path", M4_MODULES, ids=lambda p: str(p.relative_to(DPS)))
def test_log_calls_use_snake_case_events_and_safe_fields(path: Path) -> None:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for call in _log_calls(tree):
        where = f"{path.name}:{call.lineno}"
        assert (
            call.args
            and isinstance(call.args[0], ast.Constant)
            and isinstance(call.args[0].value, str)
        ), f"{where} log event must be a string literal"
        event = call.args[0].value
        assert _EVENT_NAME.fullmatch(event), f"{where} log event {event!r} is not a snake_case name"
        assert len(call.args) == 1, f"{where} no positional format args"
        assert not any(kw.arg is None for kw in call.keywords), f"{where} a ** splat in a log call"
        unsafe = {kw.arg for kw in call.keywords if kw.arg is not None} - _SAFE_FIELDS
        assert not unsafe, f"{where} unsafe log fields {unsafe}"
    assert not _calls_named(tree, "print"), f"{path.name} prints at {_calls_named(tree, 'print')}"
    assert not _stdlib_logging(tree), f"{path.name} uses stdlib logging at {_stdlib_logging(tree)}"


# ---------------------------------------------------------------- the scan of captured output


@pytest.fixture
def json_logs() -> Iterator[io.StringIO]:
    """structlog as in production (JSON, DEBUG) plus a root handler; the old config is restored."""
    import structlog

    from dps.observability import configure_logging

    old = structlog.get_config()
    stream = io.StringIO()
    handler = logging.StreamHandler(stream)
    root = logging.getLogger()
    old_level = root.level
    configure_logging("DEBUG", json_output=True)
    root.addHandler(handler)
    root.setLevel(logging.DEBUG)
    try:
        yield stream
    finally:
        root.removeHandler(handler)
        root.setLevel(old_level)
        structlog.configure(**old)


def _captured(json_logs: io.StringIO, capsys: pytest.CaptureFixture[str]) -> str:
    cap = capsys.readouterr()
    return json_logs.getvalue() + cap.out + cap.err


def _strings(obj: Any) -> Iterator[str]:
    """Every string (keys too) in a parsed JSON value."""
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for k, v in obj.items():
            yield str(k)
            yield from _strings(v)
    elif isinstance(obj, list):
        for v in obj:
            yield from _strings(v)
    elif obj is not None:
        yield str(obj)


def _haystacks(captured: str) -> tuple[str, list[str]]:
    """The raw text (case-folded) and the normalised pieces: one per string of a JSON line, one
    per other line. Normalising per piece (never the whole log at once) keeps a value from being
    "found" across the boundary of two adjacent fields."""
    pieces: list[str] = []
    for line in captured.splitlines():
        try:
            record = json.loads(line)
        except ValueError:
            pieces.append(normalise(line))
            continue
        pieces.extend(normalise(s) for s in _strings(record))
    return captured.casefold(), pieces


def _leaked(needles: Iterable[str], captured: str) -> list[int]:
    """Positions (never values) of the ``needles`` found in ``captured``: verbatim (any case) or
    with separators, case and accents dropped."""
    raw, pieces = _haystacks(captured)
    out: list[int] = []
    for i, needle in enumerate(needles):
        norm = normalise(needle)
        if needle.casefold() in raw or (
            len(norm) >= MIN_NEEDLE and any(norm in piece for piece in pieces)
        ):
            out.append(i)
    return out


def _assert_allow_listed(captured: str) -> int:
    """Every JSON line carries allow-listed keys only; returns how many JSON lines there were."""
    lines = 0
    for line in captured.splitlines():
        if not line.startswith("{"):
            continue
        record = json.loads(line)
        if "event" in record:  # a structlog line (a third-party JSON line has no business here)
            assert set(record) <= _SAFE_FIELDS, sorted(set(record) - _SAFE_FIELDS)
            lines += 1
    return lines


def test_the_log_scan_catches_a_planted_value(
    json_logs: io.StringIO, capsys: pytest.CaptureFixture[str]
) -> None:
    """Self-test: a value logged through an allow-listed field is caught verbatim, and with its
    separators changed; a value logged under a dropped field is not there to catch."""
    planted = get_logger("dps.layers.scanner.logscan_selftest")
    planted.warning("planted_leak", reason="2341-2341-2346", error="BQPKR4432L")
    planted.warning("planted_drop", value="Krishnan Ananya")
    captured = _captured(json_logs, capsys)
    assert '"planted_leak"' in captured and '"planted_drop"' in captured
    needles = ["2341 2341 2346", "bqpkr4432l", "Krishnan Ananya", "ab"]
    assert _leaked(needles, captured) == [0, 1]


# ---------------------------------------------------------------- dynamic


def test_scanner_loggers_drop_value_fields(
    json_logs: io.StringIO, capsys: pytest.CaptureFixture[str]
) -> None:
    log = get_logger("dps.layers.scanner.crawler")
    log.info(
        "scan_page",
        page_no=3,
        url="http://h/x?email=a@b.co",
        label="Ananya",
        value="2341 2341 2346",
        storage_state={"cookies": []},
    )
    captured = _captured(json_logs, capsys)
    lines = [json.loads(line) for line in captured.splitlines() if '"scan_page"' in line]
    assert len(lines) == 1 and lines[0]["page_no"] == 3
    assert set(lines[0]) <= _SAFE_FIELDS
    for needle in ("email=", "Ananya", "2341", "cookies", "storage_state", "label", "url"):
        assert needle not in captured, needle


@pytest.mark.browser
async def test_guard_and_crawl_logs_are_value_free(
    chromium: Any,
    scan_settings: Settings,
    scan_engine: PrivacyEngine,
    demo_site_url: str,
    demo_data: dict[str, str],
    json_logs: io.StringIO,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from dps.layers.scanner.crawler import ScanRunner
    from dps.layers.scanner.runtime import MemoryScanSink

    before = received(demo_site_url)
    sink = MemoryScanSink()
    runtime = make_runtime(
        demo_site_url,
        scan_settings,
        scan_engine,
        path=f"/apply?email={QUERY_EMAIL_URL}",
        max_pages=6,
    )
    await ScanRunner(
        settings=scan_settings,
        engine=scan_engine,
        sink=sink,
        browser=chromium,
        runtime=runtime,
    ).run()
    captured = _captured(json_logs, capsys)
    after = received(demo_site_url)
    assert after["non_get"] == before["non_get"] and after["form_get"] == before["form_get"]

    done = [p for p in sink.pages.values() if p.status == "done"]
    assert len(done) >= 3, "the crawl must have processed pages for the scan to mean anything"
    persona = sorted(
        {
            v
            for fields in sink.fields.values()
            for f in fields
            for v in (f.fill_synthesize, f.fill_obfuscate)
            if v and len(normalise(v)) >= MIN_NEEDLE
        }
    )
    assert len(persona) >= 5, "the crawl must have filled fields with persona values and tokens"
    demo_values = [demo_data[k] for k in DEMO_SCAN_KEYS]
    needles = [
        *demo_values,
        *demo_values[0].split(),  # each part of the applicant's name
        QUERY_EMAIL,
        QUERY_EMAIL_URL,
        "?email=",
        SESSION_NAME,
        SESSION_VALUE,
        runtime.key.dek,
        runtime.seed.seed,
        *persona,
    ]
    leaked = _leaked(needles, captured)
    assert not leaked, f"{len(leaked)} values reached the logs at positions {leaked}"
    assert _assert_allow_listed(captured) >= len(done), "every processed page logs scan_page"
    assert '"scan_page"' in captured


@pytest.fixture
def logging_api(
    db_settings: Settings, tmp_path: Path, json_logs: io.StringIO
) -> Iterator[tuple[Any, list[str]]]:
    """``(TestClient, audit actions)`` of an app whose audit sink is the log (every audit event
    becomes an ``audit`` line to scan), built inside the capture window so its startup and
    shutdown lines are scanned too; JSON output (the app configures the console renderer for
    ``env=local``). Every table is truncated afterwards, as ``api_client`` does."""
    import asyncio

    from fastapi.testclient import TestClient

    from dps.app import create_app
    from dps.audit import AuditEvent, LogAuditSink
    from dps.observability import configure_logging
    from tests.fixtures.db import _truncate, api_settings

    actions: list[str] = []

    class RecordingLogSink(LogAuditSink):
        def write(self, event: AuditEvent) -> None:
            actions.append(f"{event.action}:{event.outcome}")
            super().write(event)

    app = create_app(api_settings(db_settings, tmp_path), audit_sink=RecordingLogSink())
    configure_logging("DEBUG", json_output=True)
    try:
        with TestClient(app) as client:
            yield client, actions
    finally:
        asyncio.run(_truncate(db_settings))


@pytest.mark.db
def test_api_logs_are_value_free(
    logging_api: tuple[Any, list[str]],
    json_logs: io.StringIO,
    capsys: pytest.CaptureFixture[str],
) -> None:
    from tests.api.test_api_connections import FULL

    client, actions = logging_api
    url = f"http://127.0.0.1:8410/apply?email={QUERY_EMAIL_URL}"
    state = demo_cookie_state("127.0.0.1")
    refused = client.post(
        "/api/v1/scans",
        json={
            "url": url.replace("127.0.0.1", "unregistered.example"),
            "auth": {"storage_state": state},
        },
        headers=FULL,
    )
    assert (refused.status_code, refused.json()["code"]) == (422, "host_not_registered")
    r = client.post(
        "/api/v1/scans", json={"url": url, "auth": {"storage_state": state}}, headers=FULL
    )
    assert r.status_code == 202, r.json().get("code")
    body = r.json()
    scan_id = body["scan"]["id"]
    for path in ("/pages", "/events", "", "/inventory"):
        got = client.get(f"/api/v1/scans/{scan_id}{path}", headers=FULL)
        assert got.status_code in (200, 409), (path, got.status_code)
    assert client.post(f"/api/v1/scans/{scan_id}/cancel", headers=FULL).status_code == 200
    captured = _captured(json_logs, capsys)
    assert {"scan.create:ok", "scan.create:rejected", "scan.cancel:ok"} <= set(actions), actions
    needles = [
        "ananya",
        QUERY_EMAIL,
        QUERY_EMAIL_URL,
        "?email=",
        "unregistered.example",
        SESSION_NAME,
        SESSION_VALUE,
        body["key_material"]["dek"],
        body["synthesis_seed"]["seed"],
    ]
    leaked = _leaked(needles, captured)
    assert not leaked, f"{len(leaked)} values reached the logs at positions {leaked}"
    events = [json.loads(line) for line in captured.splitlines() if line.startswith("{")]
    assert sum(e.get("event") == "audit" for e in events) >= 3, "the audit lines must be scanned"
    assert _assert_allow_listed(captured) >= 3
