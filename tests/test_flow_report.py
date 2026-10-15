"""``tools/flow_report.py`` — the value-free route-coverage report (M5 Task 24).

Offline: the expected-status table is checked against the routes the app registers, and the whole
flow runs against a stand-in stack (``httpx.MockTransport``) that answers each route template with
its expected status and a plausible body. The live run is ``python tools/flow_report.py`` against
``docker compose --profile full``.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import httpx
import pytest

from dps.app import create_app
from dps.config import Settings
from tools import flow_report
from tools.demo_client import DpsClient
from tools.flow_report import EXPECT, TENANT_KEY_EXPECT, Row, render, top_keys, verdict

#: Routes the app serves that are not contract §9 route groups: the API docs, and the console's
#: index and SPA fallback (registered only when ``frontend/dist`` is built, as in every image).
NOT_IN_SECTION_9 = {
    ("GET", "/docs"),
    ("GET", "/docs/oauth2-redirect"),
    ("GET", "/api/v1/openapi.json"),
    ("GET", "/"),
    ("GET", "/{full_path:path}"),
}
SECRET = "123-45-6789"
IDS = {
    "connection_id": "c_01JA0000000000000000000001",
    "job_id": "j_01JA0000000000000000000002",
    "snapshot_id": "snap_01JA00000000000000000003",
    "group_id": "grp_01JA000000000000000000004",
    "evidence_id": "ev_01JA0000000000000000000005",
    "artefact_id": "art_01JA000000000000000000006",
    "key_id": "key_01JA000000000000000000007",
    "scan_id": "s_01JA0000000000000000000008",
}


def _walk(routes: list[Any], prefix: str = "") -> set[tuple[str, str]]:
    """``(method, path)`` of every route, through FastAPI's included routers."""
    out: set[tuple[str, str]] = set()
    for route in routes:
        if type(route).__name__ == "_IncludedRouter":
            out |= _walk(route.original_router.routes, prefix + route.include_context.prefix)
        elif getattr(route, "methods", None) and hasattr(route, "path"):
            out |= {(m, prefix + route.path) for m in route.methods - {"HEAD"}}
    return out


def _app_routes() -> set[tuple[str, str]]:
    app = create_app(Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub"))
    return _walk(list(app.routes))


def test_the_table_has_one_row_per_route_and_valid_statuses() -> None:
    pairs = [(m, t) for m, t, _ in EXPECT]
    assert len(pairs) == len(set(pairs))
    assert all(100 <= s <= 599 for _, _, s in EXPECT)
    assert set(TENANT_KEY_EXPECT) <= set(pairs)
    assert all(t.startswith("/") and "?" not in t for t in (t for _, t in pairs))


def test_every_route_the_app_serves_is_in_the_table() -> None:
    """A route added without a flow row fails here (contract §9 is the flow's checklist)."""
    served = _app_routes() - NOT_IN_SECTION_9
    expected = {(m, t) for m, t, _ in EXPECT}
    assert served - expected == set(), "routes without a flow row"
    # M4 is merged: every row is a route the app serves (the scanner rows match its templates).
    assert expected - served == set(), "flow rows the app does not serve"


def test_every_row_belongs_to_a_named_group() -> None:
    groups = {flow_report.group_of(t) for _, t, _ in EXPECT}
    assert {
        "ops (M0)",
        "Layer 1 (M0)",
        "connections (M2)",
        "admin (M5)",
        "UI scanner (M4)",
    } <= groups


def _response(status: int, body: Any = None, *, ctype: str = "application/json") -> httpx.Response:
    if body is None:
        return httpx.Response(status)
    if ctype == "application/json":
        return httpx.Response(status, json=body)
    return httpx.Response(status, content=body, headers={"content-type": ctype})


def test_top_keys_names_identifier_keys_only() -> None:
    assert top_keys(_response(200, {"items": [], "next_cursor": None})) == "items, next_cursor"
    assert top_keys(_response(200, {"result": {}, SECRET: 1})) == "result (+1 other keys)"
    assert top_keys(_response(200, [1, 2, 3])) == "[3 items]"
    assert top_keys(_response(204)) == "(empty)"
    assert top_keys(_response(200, b"%PDF-1.4", ctype="application/pdf")) == (
        "(application/pdf, 8 bytes)"
    )


def test_the_verdict_fails_on_a_mismatch_or_a_call_that_could_not_run() -> None:
    ok = Row("GET", "/health", 200, 200)
    skipped = Row("POST", "/api/v1/scans", 202, note="skipped: no browser worker")
    assert verdict([ok, skipped]) == 0
    assert verdict([ok, Row("GET", "/readyz", 200, 503)]) == 1
    assert verdict([ok, Row("GET", "/readyz", 200, note="not run: no stack")]) == 1
    assert verdict([]) == 1


def test_the_report_holds_templates_statuses_and_key_names_only() -> None:
    rows = [
        Row("GET", "/api/v1/jobs/{job_id}", 200, 200, 12.3, "id, state"),
        Row("POST", "/api/v1/jobs/{job_id}/retry", 409, 404, 5.0, "code, detail"),
    ]
    text = render(rows, meta={"Stack": "http://127.0.0.1:8417"})
    assert "`/api/v1/jobs/{job_id}`" in text and "404 ✗" in text
    assert "**Verdict: FAIL**" in text and "| jobs (M2) | 2 | 1 | 0 |" in text


def _args(**kw: Any) -> argparse.Namespace:
    base = {"demo_keys": None, "key_name": "demo-console", "tenant_key": False}
    return argparse.Namespace(**{**base, **kw})


def test_the_key_comes_from_the_environment_the_demo_keys_or_the_local_literal(
    tmp_path: Path,
) -> None:
    resolve = flow_report.resolve_key
    assert resolve(_args(), {}) == (flow_report.BOOTSTRAP_KEY, "local bootstrap key", False)
    assert resolve(_args(), {"DPS_API_KEY": "dps_a"}) == ("dps_a", "DPS_API_KEY", False)
    assert resolve(_args(tenant_key=True), {"DPS_API_KEY": "dps_a"})[2] is True
    keys = tmp_path / "demo-keys.json"
    keys.write_text(json.dumps({"demo-console": "dps_demo"}), encoding="utf-8")
    keys.chmod(0o600)
    assert resolve(_args(demo_keys=str(keys)), {"DPS_API_KEY": "dps_a"}) == (
        "dps_demo",
        "demo key demo-console",
        True,
    )
    with pytest.raises(SystemExit):
        resolve(_args(demo_keys=str(keys), key_name="nope"), {})


# -- the whole flow against a stand-in stack ------------------------------------------------------
class FakeStack:
    """Answers every request with its route's expected status and a plausible body."""

    def __init__(self, *, browser: bool, browser_details: list[str] | None = None) -> None:
        self.browser = browser
        #: ``/readyz`` answers ``browser: {ok: false, detail: <next>}`` while details remain (a
        #: freshly started app reads "unknown" until its first heartbeat refresh), then ``browser``.
        self.browser_details = list(browser_details or [])
        self.readyz = 0
        self.expect = {(m, t): s for m, t, s in EXPECT}
        self.patterns = [
            (m, t, re.compile("^" + re.sub(r"\{[a-z_]+\}", r"[^/]+", t) + "$"))
            for m, t, _ in EXPECT
        ]
        self.seen: list[tuple[str, str]] = []
        self.polls = 0

    def match(self, method: str, path: str) -> str:
        # literal templates first (`/jobs/stats` before `/jobs/{job_id}`)
        found = [t for m, t, rx in self.patterns if m == method and rx.match(path)]
        assert found, (method, path)
        return min(found, key=lambda t: t.count("{"))

    def __call__(self, request: httpx.Request) -> httpx.Response:
        method, path = request.method, request.url.path
        template = self.match(method, path)
        self.seen.append((method, template))
        status = self.expect[(method, template)]
        if template == "/readyz":
            self.readyz += 1
            if self.browser_details:
                browser = {"ok": False, "detail": self.browser_details.pop(0)}
            else:
                browser = {"ok": self.browser, "detail": "1 online" if self.browser else "none"}
            components = {"db": {"ok": True}, "browser": browser}
            return _response(200, {"ready": True, "components": components})
        if status == 204:
            return _response(204)
        if template.endswith("/pdf"):
            return _response(200, b"%PDF-1.4 fake", ctype="application/pdf")
        if template.endswith(("/html",)) or template == "/api/v1/artefacts/{artefact_id}":
            return _response(200, b"<html></html>", ctype="text/html")
        if template == "/metrics":
            return _response(200, b"dps_llm_raw_pii_sent_total 0.0\n", ctype="text/plain")
        return _response(status, self.body(template, status))

    def body(self, template: str, status: int) -> dict[str, Any]:
        if status >= 400:
            return {"detail": f"refused {SECRET}", "code": "job_terminal"}
        bodies: dict[str, dict[str, Any]] = {
            "/api/v1/obfuscate": {"result": {"email": "x"}, "key_material": {"kid": "k"}},
            "/api/v1/schema/generator-spec": {"spec": {"fields": []}},
            "/api/v1/connections": {"id": IDS["connection_id"], "items": [], "next_cursor": None},
            "/api/v1/jobs/discover": {"job": {"id": IDS["job_id"]}},
            "/api/v1/jobs/{job_id}": {
                "id": IDS["job_id"],
                "state": "succeeded",
                "progress": {"snapshot_ids": [IDS["snapshot_id"]]},
            },
            "/api/v1/job-groups": {
                "group": {"id": IDS["group_id"]},
                "key_material": {"kid": "k", "dek": SECRET},
                "items": [],
            },
            "/api/v1/jobs/run": {"job": {"id": IDS["job_id"]}, "key_material": {"dek": SECRET}},
            "/api/v1/evidence": {"items": [{"id": IDS["evidence_id"]}], "next_cursor": None},
            "/api/v1/evidence/{evidence_id}": {
                "id": IDS["evidence_id"],
                "artefacts": [
                    {"id": "art_01JA00000000000000000000J", "kind": "evidence_json"},
                    {"id": IDS["artefact_id"], "kind": "evidence_html"},
                ],
            },
            "/api/v1/reports/api-rollups": {"items": [{"day": "2026-09-30", "action": "detect"}]},
            "/api/v1/policies/{name}/publish": {"version": 2},
            "/api/v1/job-groups/{group_id}/consistency-check": {"passed": True, "total_pairs": 1},
            "/api/v1/admin/tenants": {"items": [{"tenant_id": "default"}], "tenant_id": "x"},
            "/api/v1/admin/keys": {"key": "dps_" + "s" * 32, "record": {"id": IDS["key_id"]}},
            "/api/v1/admin/keys/{key_id}/rotate": {"new": {"record": {"id": IDS["key_id"]}}},
            "/api/v1/scans": {"scan": {"id": IDS["scan_id"]}, "items": []},
            "/api/v1/scans/{scan_id}": {"scan": {"id": IDS["scan_id"], "state": "succeeded"}},
            "/api/v1/scans/{scan_id}/pages": {"items": [{"page_no": 1}]},
        }
        return bodies.get(template, {"ok": True})


def _run(
    stack: FakeStack, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, *extra: str
) -> tuple[int, str]:
    class _Client(DpsClient):
        _transport = httpx.MockTransport(stack)

    monkeypatch.setattr(flow_report.time, "sleep", lambda _s: None)
    out = tmp_path / "flow-report.md"
    code = flow_report.main(
        ["--base-url", "http://dps.test", "--out", str(out), *extra], client_factory=_Client
    )
    return code, out.read_text(encoding="utf-8")


def test_the_flow_calls_every_route_and_passes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stack = FakeStack(browser=True)
    code, report = _run(stack, tmp_path, monkeypatch)
    assert code == 0, capsys.readouterr().out
    assert {(m, t) for m, t, _ in EXPECT} <= set(stack.seen)
    assert "**Verdict: PASS**" in report
    for value in [*IDS.values(), SECRET, "dps_" + "s" * 32, "2026-09-30"]:
        assert value not in report, value
    assert SECRET not in capsys.readouterr().out


def test_without_a_browser_worker_the_scanner_calls_are_skipped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = FakeStack(browser=False)
    code, report = _run(stack, tmp_path, monkeypatch)
    assert code == 0
    assert not any(t.startswith(flow_report.SCAN_PREFIX) for _, t in stack.seen)
    scan_rows = sum(1 for _, t, _ in EXPECT if t.startswith(flow_report.SCAN_PREFIX))
    assert report.count("skipped: /readyz reports no browser worker") == scan_rows


def test_the_full_stack_gate_requires_the_scanner(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """``--require-scanner`` (the DoD run on ``--profile full``): without a browser worker every
    scanner call is "not run" and the report fails — it cannot pass without the scanner."""
    monkeypatch.setattr(flow_report, "BROWSER_WAIT_SECONDS", 0.0)
    stack = FakeStack(browser=False)
    code, report = _run(stack, tmp_path, monkeypatch, "--require-scanner")
    assert code == 1 and "**Verdict: FAIL**" in report
    assert not any(t.startswith(flow_report.SCAN_PREFIX) for _, t in stack.seen)
    scan_rows = sum(1 for _, t, _ in EXPECT if t.startswith(flow_report.SCAN_PREFIX))
    assert report.count("not run: no browser worker online (--require-scanner)") == scan_rows
    assert "skipped" not in report.split("## Route groups")[0].lower()
    assert "UNEXPECTED  POST /api/v1/scans: expected 202" in capsys.readouterr().out


def test_a_fresh_stack_is_given_time_for_its_browser_readiness(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Right after ``up`` the app reads ``browser: unknown`` until its first heartbeat refresh
    (HANDOFF §15 trap 8): the flow re-reads ``/readyz`` (unrecorded) instead of skipping the
    scanner calls, with or without ``--require-scanner``."""
    for extra in ((), ("--require-scanner",)):
        stack = FakeStack(browser=True, browser_details=["unknown", "unknown", "unknown"])
        code, report = _run(stack, tmp_path, monkeypatch, *extra)
        assert code == 0, extra
        assert {(m, t) for m, t, _ in EXPECT} <= set(stack.seen)
        assert report.count("| `/readyz` |") == 1  # the polls are not rows
        assert stack.readyz >= 4


def test_without_the_switch_no_browser_worker_skips_at_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A stack without the ``scanner`` profile ("none online") is not waited for."""
    stack = FakeStack(browser=False, browser_details=["none online"])
    code, report = _run(stack, tmp_path, monkeypatch)
    assert code == 0 and "**Verdict: PASS**" in report
    assert stack.readyz == 2  # the recorded one, then one fresh read before the scanner calls


def test_an_unexpected_status_fails_the_run_and_names_the_route(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    stack = FakeStack(browser=False)
    stack.expect[("GET", "/api/v1/admin/posture")] = 500
    code, report = _run(stack, tmp_path, monkeypatch)
    assert code == 1 and "**Verdict: FAIL**" in report
    assert (
        "UNEXPECTED  GET /api/v1/admin/posture: expected 200, HTTP 500" in capsys.readouterr().out
    )


def test_a_refused_discovery_marks_its_dependants_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    stack = FakeStack(browser=False)
    stack.expect[("POST", "/api/v1/jobs/discover")] = 422
    code, report = _run(stack, tmp_path, monkeypatch)
    assert code == 1
    assert (
        "not run: discover refused" in report and "not run: no snapshot, target or group" in report
    )
    assert ("POST", "/api/v1/jobs/run") not in stack.seen  # never submitted without a snapshot


def test_a_run_that_does_not_succeed_fails_the_report(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A 200 is not enough where the flow relies on an outcome: a job's final state, a
    connection test, a consistency check (value-free enums and booleans only)."""
    stack = FakeStack(browser=False)
    original = stack.body

    def body(template: str, status: int) -> dict[str, Any]:
        out = original(template, status)
        if template == "/api/v1/jobs/{job_id}":
            out = {**out, "state": "failed"}
        return out

    stack.body = body  # type: ignore[method-assign]
    code, report = _run(stack, tmp_path, monkeypatch)
    assert code == 1 and "final state failed" in report
    assert "UNEXPECTED  GET /api/v1/jobs/{job_id}: expected 200, HTTP 200; final state failed" in (
        capsys.readouterr().out
    )


def test_refusals_name_their_error_code(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code, report = _run(FakeStack(browser=False), tmp_path, monkeypatch)
    assert code == 0
    assert "| code job_terminal |" in report  # the 409s of the controls on a finished job
    assert SECRET not in report  # a refusal's detail is never quoted
