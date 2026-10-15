"""The smoke test's scanner block (M4 Task 24), offline: ``scanner_checks`` against a fake API and a
fake demo site (``httpx.MockTransport``), so every verdict — and the rule that the script prints
check names and counts only — is pinned without a stack. The live run is
``DPS_SMOKE_SCANNER=1 python tools/smoke_test.py`` against ``docker compose --profile scanner``."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx
import pytest

from tools import smoke_test

ROOT = Path(__file__).resolve().parents[1]
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 16
DEK = "c2VjcmV0LWRlay1tYXRlcmlhbC1vZi10aGUtc2Nhbg"
SEED = "c2VjcmV0LXN5bnRoZXNpcy1zZWVkLW9mLXRoZS1zY2Fu"
SCANNER_CHECKS = [
    "browser worker online",
    "scan accepted, key and seed returned once",
    "scan succeeded",
    "classification and risk flags",
    "no submission reached the site",
    "parallel view captured",
    "screenshot served",
    "masked capture served",
    "persona export",
    "evidence: submissions 0",
    "scanner console served",
]


class Fake:
    """A fake DPS app + demo site; ``during_scan`` models what the scan did to the site's counters
    (by default: it served the pages the scan visited, and nothing else moved)."""

    def __init__(self) -> None:
        self.received = {"non_get": 7, "form_get": 2, "logout_hits": 1, "admin_hits": 1, "get": 40}
        self.demo_up = True  # False: nothing listens on the demo site's port (no scanner profile)
        self.during_scan: dict[str, int] = {"get": 58}
        #: The request guard's counts by method: the pages' autosave posts it stopped, a denied
        #: link.
        self.blocked: dict[str, int] = {"POST": 60, "GET": 1}
        self.browser_ok = True
        self.browser_after = 0  # readiness polls answered "unknown" before the worker shows up
        self.ready_polls = 0
        self.state = "succeeded"
        self.export_extra = ""
        self.created: list[dict[str, Any]] = []
        self.polls = 0

    # -- the demo site --------------------------------------------------------------------------
    def demo(self, request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path in ("/__received", "/health")
        if not self.demo_up:
            raise httpx.ConnectError("refused", request=request)
        if request.url.path == "/health":
            return httpx.Response(200, json={"status": "ok"})
        return httpx.Response(200, json=self.received)

    # -- the app --------------------------------------------------------------------------------
    def app(self, request: httpx.Request) -> httpx.Response:  # noqa: PLR0911 - one route each
        path, method = request.url.path, request.method
        if path == "/readyz":
            self.ready_polls += 1
            ok = self.browser_ok and self.ready_polls > self.browser_after
            browser = {"ok": ok, "detail": "1 online" if ok else "unknown"}
            return httpx.Response(200, json={"ready": True, "components": {"browser": browser}})
        if path == "/scanner/new":
            return httpx.Response(200, html='<div id="root"></div>')
        assert request.headers.get("X-API-KEY") == smoke_test.KEY
        if method == "POST" and path == "/api/v1/scans":
            self.created.append(json.loads(request.content))
            for name, extra in self.during_scan.items():  # what the scan did to the site
                self.received[name] += extra
            return httpx.Response(
                202,
                json={
                    "scan": {"id": "s_smoke", "state": "queued"},
                    "key_material": {"dek": DEK, "kid": "k1", "alg": "dps-key-v1"},
                    "synthesis_seed": {"alg": "dps-seed-v1", "seed": SEED},
                },
            )
        if path == "/api/v1/scans/s_smoke":
            self.polls += 1
            summary = {**SUM["summary"], "blocked_requests": self.blocked}
            body = {"scan": {"id": "s_smoke", "state": self.state}, "summary": summary}
            return httpx.Response(200, json=body)
        if path == "/api/v1/scans/s_smoke/pages":
            return httpx.Response(200, json={"items": PAGES, "next_cursor": None})
        if path.startswith("/api/v1/artefacts/"):
            return httpx.Response(200, content=PNG, headers={"content-type": "image/png"})
        if path == "/api/v1/scans/s_smoke/export":
            assert request.url.params.get("format") == "json"
            body = {"format": "dps.scan-export.v1", "personas": 5, "persona_set": []}
            text = json.dumps(body)[:-1] + f', "note": "{self.export_extra}"}}'
            return httpx.Response(
                200, content=text.encode(), headers={"content-type": "application/json"}
            )
        if path == "/api/v1/evidence":
            assert request.url.params.get("job_id") == "s_smoke"
            return httpx.Response(200, json={"items": [{"id": "ev_1"}], "next_cursor": None})
        if path == "/api/v1/evidence/ev_1":
            pack = {"scan": {"submissions": 0, "non_get_allowed": 0}}
            return httpx.Response(200, json={"id": "ev_1", "pack": pack})
        return httpx.Response(404, json={"detail": "not found", "code": "not_found"})


SUM: dict[str, Any] = {
    "summary": {
        "pages_visited": 21,
        "fields": 63,
        "personal_fields": 51,
        "captures": 3,
        "blocked_requests": {"POST": 60, "GET": 1},  # by method (ScanSummary)
        "non_get_allowed": 0,
        "risk_flags": [
            {"flag": "autocomplete_enabled_sensitive", "code": "A1", "path": "/apply/identity"},
            {"flag": "pii_in_query_string", "code": "A2", "path": "/apply/review?email=<EMAIL>"},
            {"flag": "pii_rendered_unmasked", "code": "A3", "path": "/profile"},
        ],
    }
}
PAGES = [
    {"page_no": 1, "url_redacted": "http://demo-site:8410/apply", "artefacts": {"original": "a0"}},
    {
        "page_no": 2,
        "url_redacted": "http://demo-site:8410/apply/identity",
        "artefacts": {
            "original": "a1",
            "annotated": "a2",
            "filled_synthesize": "a3",
            "filled_obfuscate": "a4",
        },
    },
    {
        "page_no": 3,
        "url_redacted": "http://demo-site:8410/profile",
        "artefacts": {"masked_capture": "a5"},
    },
]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    monkeypatch.setattr(smoke_test, "results", [])
    return Fake()


def run(fake: Fake, *, timeout: float = 5.0, browser_wait: float = 0.0) -> None:
    app = httpx.Client(base_url="http://app.test", transport=httpx.MockTransport(fake.app))
    demo = httpx.Client(base_url="http://demo.test", transport=httpx.MockTransport(fake.demo))
    with app, demo:
        smoke_test.scanner_checks(
            app, demo, timeout=timeout, interval=0.0, browser_wait=browser_wait
        )


def names() -> list[str]:
    return [name for name, _, _ in smoke_test.results]


def test_scanner_checks_pass_on_a_clean_scan_and_print_no_value(
    fake: Fake, capsys: pytest.CaptureFixture[str]
) -> None:
    run(fake)
    assert names() == SCANNER_CHECKS
    assert all(ok for _, ok, _ in smoke_test.results)
    out = capsys.readouterr().out
    assert out.count("PASS  ") == len(SCANNER_CHECKS)
    for secret in (DEK, SEED, *smoke_test.DEMO_VALUES):
        assert secret.lower() not in out.lower()
    # The scan the smoke test asks for: the demo site as the browser-worker sees it, the deny list,
    # the mounted demo session, five en_IN personas.
    (spec,) = fake.created
    assert spec["url"] == f"{smoke_test.DEMO_FROM_WORKER}/apply"
    assert spec["scope"]["deny"] == ["/logout", "/admin"]
    assert spec["auth"] == {"storage_state_ref": "dps/ui/demo-onboarding"}
    assert spec["fill"] == {"personas": 5, "locale": "en_IN"}


def test_demo_values_are_the_demo_sites_customer_values() -> None:
    data = json.loads((ROOT / "demo/onboarding/data.json").read_text("utf-8"))
    assert set(smoke_test.DEMO_VALUES) == {v for k, v in data.items() if k != "dob"}
    assert {"Ananya Krishnan", "2341 2341 2346", "BQPKR4432L"} <= set(smoke_test.DEMO_VALUES)


@pytest.mark.parametrize("counter", ["non_get", "form_get", "logout_hits", "admin_hits"])
def test_any_submission_or_deny_list_hit_fails_the_smoke(
    fake: Fake, counter: str, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.during_scan[counter] = 1  # the scan still served its pages: only this counter fails it
    with pytest.raises(SystemExit) as exit_:
        run(fake)
    assert exit_.value.code == 1
    assert names()[-1] == "no submission reached the site"
    assert "FAIL  no submission reached the site" in capsys.readouterr().out


def test_the_d4_verdict_names_counts_only(fake: Fake, capsys: pytest.CaptureFixture[str]) -> None:
    run(fake)
    line = next(ln for ln in capsys.readouterr().out.splitlines() if "no submission" in ln)
    # The counters, the pages served during the scan (against the pages it visited) and the sends
    # the guard stopped (by method).
    assert "non_get +0" in line and "pages served +58 (21 visited)" in line
    assert "non-GET blocked 60" in line and "non_get_allowed 0" in line


def test_a_scan_whose_pages_never_tried_to_send_proves_nothing(
    fake: Fake, capsys: pytest.CaptureFixture[str]
) -> None:
    """D4 is proven by a site that tries to send — the demo pages' autosave posts, beacons and
    auto-submitting select — and receives nothing: with no non-GET request stopped by the guard,
    unmoved counters would prove nothing (a scan that filled nothing, a site whose scripts never
    ran)."""
    fake.blocked = {"GET": 1}
    with pytest.raises(SystemExit):
        run(fake)
    assert names()[-1] == "no submission reached the site"
    assert "FAIL  no submission reached the site" in capsys.readouterr().out


@pytest.mark.parametrize("served", [0, 20, None])
def test_counters_of_a_site_the_scan_did_not_visit_prove_nothing(
    fake: Fake, served: int | None
) -> None:
    """The positive control of the counters: the site whose ``/__received`` the smoke reads
    (``DPS_TEST_DEMO_SITE``) must have served the scan's pages — at least one GET per page the
    scan visited (the demo site sends ``no-store``, so every visit fetches its page) — otherwise
    its unmoved counters belong to another demo site (another lane's, a stale port) than the one
    the browser-worker scanned, and a few stray GETs there do not change that (20 < 21 visited).
    A demo site that does not count what it serves fails the same way."""
    if served is None:
        fake.received.pop("get")
        fake.during_scan = {}
    else:
        fake.during_scan = {"get": served}
    with pytest.raises(SystemExit):
        run(fake)
    assert names()[-1] == "no submission reached the site"


def test_one_get_per_visited_page_is_enough(fake: Fake) -> None:
    fake.during_scan = {"get": SUM["summary"]["pages_visited"]}
    run(fake)
    assert names() == SCANNER_CHECKS and all(ok for _, ok, _ in smoke_test.results)


def test_a_demo_value_in_the_persona_export_fails_the_smoke(
    fake: Fake, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.export_extra = "ANANYA KRISHNAN"
    with pytest.raises(SystemExit):
        run(fake)
    assert names()[-1] == "persona export"
    out = capsys.readouterr().out
    assert "ananya" not in out.lower()  # the verdict names the check, never the value it found


def test_a_scan_that_does_not_finish_in_time_fails(fake: Fake) -> None:
    fake.state = "running"
    with pytest.raises(SystemExit):
        run(fake, timeout=0.0)
    assert names()[-1] == "scan succeeded"
    assert fake.polls >= 1


def test_no_scan_is_started_without_a_browser_worker(fake: Fake) -> None:
    fake.browser_ok = False
    with pytest.raises(SystemExit):
        run(fake)
    assert names() == ["browser worker online"]
    assert fake.created == []


def test_browser_readiness_is_awaited_on_a_fresh_stack(fake: Fake) -> None:
    # The app refreshes the browser component from worker heartbeats every 15 s; right after
    # `up` it still reads "unknown", which is not a failure yet.
    fake.browser_after = 2
    run(fake, browser_wait=5.0)
    assert names() == SCANNER_CHECKS
    assert fake.ready_polls == 3


def test_the_scanner_block_follows_the_switch_and_the_stack() -> None:
    """``DPS_SMOKE_SCANNER`` (M5 Task 24 at the M4 merge, like ``DPS_SMOKE_M2``): ``auto``
    (default) runs the block when the demo site answers — only the ``scanner`` and ``full``
    profiles run it — so a plain smoke run on ``full`` covers the scanner; ``on`` (M4's ``1``)
    runs it whatever the stack (a stack without the scanner then fails); ``off`` (``0``) skips it.
    """
    probes: list[str] = []

    def up() -> bool:
        probes.append("up")
        return True

    def down() -> bool:
        probes.append("down")
        return False

    for setting in (None, "", "auto", " AUTO "):
        assert smoke_test.scanner_wanted(setting, up) == (True, "")
        run, note = smoke_test.scanner_wanted(setting, down)
        assert run is False and note.startswith("note:") and "DPS_SMOKE_SCANNER=on" in note
    probes.clear()
    for setting in ("1", "on", "ON"):
        assert smoke_test.scanner_wanted(setting, down) == (True, "")
    for setting in ("0", "off", "Off"):
        run, note = smoke_test.scanner_wanted(setting, up)
        assert run is False and "DPS_SMOKE_SCANNER=off" in note
    assert probes == []  # on and off never probe the demo site
    for bad in ("yes", "2", "true"):
        with pytest.raises(SystemExit):
            smoke_test.scanner_wanted(bad, up)


def _block(fake: Fake, setting: str | None) -> None:
    app = httpx.Client(base_url="http://app.test", transport=httpx.MockTransport(fake.app))
    demo = httpx.Client(base_url="http://demo.test", transport=httpx.MockTransport(fake.demo))
    with app, demo:
        smoke_test.scanner_block(app, demo, setting, timeout=5.0, interval=0.0, browser_wait=0.0)


def test_by_default_the_block_runs_where_the_demo_site_answers(
    fake: Fake, capsys: pytest.CaptureFixture[str]
) -> None:
    _block(fake, None)  # --profile full or scanner: the demo site answers
    assert names() == SCANNER_CHECKS
    fake.demo_up = False
    smoke_test.results.clear()
    _block(fake, "auto")  # a stack without the scanner profile: a note, no check, no scan
    assert names() == []
    assert len(fake.created) == 1
    assert "note: no demo site answers" in capsys.readouterr().out


def test_on_without_a_demo_site_fails_with_a_verdict_not_a_traceback(
    fake: Fake, capsys: pytest.CaptureFixture[str]
) -> None:
    fake.demo_up = False
    with pytest.raises(SystemExit) as stop:
        _block(fake, "on")
    assert stop.value.code == 1
    assert names() == ["demo site answers"] and fake.created == []
    assert "FAIL  demo site answers" in capsys.readouterr().out


def test_the_demo_site_probe_reads_its_health_only() -> None:
    seen: list[tuple[str, str]] = []

    def health(request: httpx.Request) -> httpx.Response:
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={"status": "ok"})

    def refused(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    with httpx.Client(base_url="http://demo.test", transport=httpx.MockTransport(health)) as demo:
        assert smoke_test.demo_answers(demo) is True
    assert seen == [("GET", "/health")]
    with httpx.Client(base_url="http://demo.test", transport=httpx.MockTransport(refused)) as demo:
        assert smoke_test.demo_answers(demo) is False
    unwell = httpx.MockTransport(lambda request: httpx.Response(503))
    with httpx.Client(base_url="http://demo.test", transport=unwell) as demo:
        assert smoke_test.demo_answers(demo) is False


def test_demo_site_address_is_lane_safe() -> None:
    assert smoke_test.demo_from_here({}) == "http://127.0.0.1:8410"
    assert smoke_test.demo_from_here({"DPS_DEMO_SITE_PORT": "8420"}) == "http://127.0.0.1:8420"
    explicit = {"DPS_TEST_DEMO_SITE": "http://127.0.0.1:9999/", "DPS_DEMO_SITE_PORT": "8420"}
    assert smoke_test.demo_from_here(explicit) == "http://127.0.0.1:9999"
