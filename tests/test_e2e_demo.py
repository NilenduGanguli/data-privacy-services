"""``tools/e2e_demo.py`` — the automated ten-minute demo (M5 Task 26), offline parts.

The steps themselves run against a stack (the rehearsal); here: the D4 rule with its positive
controls (the smoke's), the step driver (stop at the first failure, later steps ``NOT RUN``,
skips), the reports (``dps.e2e-demo.v1`` as ``tools/build_deck.py`` reads it; the Markdown's last
line), the value-free helpers and the console walk's page list.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from tools import build_deck, e2e_demo
from tools.demo_client import DemoError
from tools.e2e_demo import StepFailed, StepSkipped, d4_verdict, run_steps

BEFORE = {"non_get": 8, "form_get": 2, "logout_hits": 1, "admin_hits": 1, "get": 40}
SCAN = {
    "pages": 21,
    "fields": 63,
    "personas": 3,
    "submissions": 0,
    "non_get_allowed": 0,
    "blocked_requests": {"GET": 1, "POST": 60},
}


def _after(**moved: int) -> dict[str, int]:
    out = dict(BEFORE)
    out["get"] += 175
    for name, n in moved.items():
        out[name] += n
    return out


def test_d4_passes_only_with_both_positive_controls() -> None:
    ok, facts, detail = d4_verdict(BEFORE, _after(), SCAN)
    assert ok
    assert facts["received"] == {
        "non_get": 0, "form_get": 0, "logout_hits": 0, "admin_hits": 0, "get": 175,
    }  # fmt: skip
    assert facts["sends_blocked"] == 60
    assert facts["scan"] == {k: SCAN[k] for k in facts["scan"]}
    assert set(facts["scan"]) == {"pages", "fields", "personas", "submissions", "non_get_allowed"}
    assert "non_get +0" in detail and "sends blocked 60" in detail


@pytest.mark.parametrize(
    ("after", "scan"),
    [
        pytest.param(_after(non_get=1), SCAN, id="a POST reached the site"),
        pytest.param(_after(form_get=1), SCAN, id="a GET form was sent"),
        pytest.param(_after(logout_hits=1), SCAN, id="a denied path was hit"),
        pytest.param(_after(admin_hits=1), SCAN, id="admin was hit"),
        pytest.param({**_after(), "get": BEFORE["get"] + 20}, SCAN, id="fewer GETs than pages"),
        pytest.param({**_after(), "get": BEFORE["get"]}, SCAN, id="the site served nothing"),
        pytest.param(_after(), {**SCAN, "blocked_requests": {"GET": 3}}, id="no send blocked"),
        pytest.param(_after(), {**SCAN, "blocked_requests": None}, id="no blocked counts"),
        pytest.param(_after(), {**SCAN, "non_get_allowed": 1}, id="a send was allowed"),
        pytest.param(_after(), {**SCAN, "non_get_allowed": None}, id="allowed count missing"),
    ],
)
def test_d4_fails_without_proof_or_with_a_moved_counter(
    after: dict[str, int], scan: dict[str, Any]
) -> None:
    ok, _facts, _detail = d4_verdict(BEFORE, after, scan)
    assert not ok


def test_d4_fails_for_a_site_that_does_not_count_what_it_serves() -> None:
    before = {k: v for k, v in BEFORE.items() if k != "get"}
    after = {k: v for k, v in _after().items() if k != "get"}
    ok, facts, _ = d4_verdict(before, after, SCAN)
    assert not ok and "get" not in facts["received"]


def test_sends_blocked_counts_only_non_get_methods() -> None:
    assert e2e_demo.sends_blocked({"GET": 4, "HEAD": 2, "POST": 3, "put": 1}) == 4
    assert e2e_demo.sends_blocked(None) == 0


def _step(facts: dict[str, Any] | None = None, detail: str = "ok") -> Any:
    return lambda: (facts or {"n": 1}, detail)


def _raise(exc: Exception) -> Any:
    def body() -> Any:
        raise exc

    return body


def test_the_first_failure_stops_the_run_and_later_steps_are_not_run() -> None:
    ticks = iter(range(100))
    results = run_steps(
        [
            ("S1", "one", _step()),
            ("S2", "two", _raise(StepSkipped("tokenizer not ready"))),
            ("S3", "three", None),
            ("S4", "four", _raise(StepFailed("3 findings (8 expected)"))),
            ("S5", "five", _step()),
            ("S6", "six", _step()),
        ],
        clock=lambda: float(next(ticks)),
        echo=lambda _line: None,
    )
    assert [r.status for r in results] == ["PASS", "SKIP", "SKIP", "FAIL", "NOT RUN", "NOT RUN"]
    assert results[0].facts == {"n": 1} and results[0].seconds == 1.0
    assert results[3].detail == "3 findings (8 expected)"
    assert results[4].facts == {} and results[4].seconds == 0.0


@pytest.mark.parametrize(
    "exc",
    [DemoError("GET", "/api/v1/jobs/j_01M50AX3AJPYEFHF617VWD2NVC", 500, "internal_error"),
     KeyError("customer value 4111 1111 1111 1111")],
)  # fmt: skip
def test_an_unexpected_answer_is_an_error_whose_detail_is_value_free(exc: Exception) -> None:
    results = run_steps(
        [("S1", "one", _raise(exc)), ("S2", "two", _step())], echo=lambda _line: None
    )
    assert [r.status for r in results] == ["ERROR", "NOT RUN"]
    assert "4111" not in results[0].detail and "j_01M50" not in results[0].detail


def _results(statuses: dict[str, str]) -> list[e2e_demo.StepResult]:
    return [
        e2e_demo.StepResult(sid, title, statuses.get(sid, "PASS"), 1.5, {"x": 1}, "d")
        for sid, title in e2e_demo.STEPS
    ]


def _doc(results: list[e2e_demo.StepResult], total: float = 431.6) -> dict[str, Any]:
    from datetime import UTC, datetime

    return e2e_demo.report_json(
        results,
        tenant="retail-banking-apac",
        version="1.0.0",
        commit="2b31b7a",
        total=total,
        generated_at=datetime(2026, 10, 15, 18, 0, tzinfo=UTC),
    )


def test_the_json_report_is_the_deck_contract(tmp_path: Path) -> None:
    doc = _doc(_results({"S3": "SKIP"}))
    assert doc["schema"] == build_deck.REPORT_SCHEMA == "dps.e2e-demo.v1"
    assert [s["id"] for s in doc["steps"]] == [f"S{n}" for n in range(1, 13)]
    assert doc["generated_at"] == "2026-10-15T18:00:00Z"
    path = tmp_path / "e2e-demo-report.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    report = build_deck.Report.load(path)
    assert report.incomplete() is None  # a complete rehearsal: slide 1 may claim it
    assert report.skipped() == ["S3"]
    assert report.total_seconds() == 431.6

    failed = _doc(_results({"S5": "FAIL", **{f"S{n}": "NOT RUN" for n in range(6, 13)}}))
    path.write_text(json.dumps(failed), encoding="utf-8")
    assert build_deck.Report.load(path).incomplete() == "first failure: S5"


def test_the_markdown_report_ends_with_the_wall_time() -> None:
    doc = _doc(_results({}), total=431.6)
    text = e2e_demo.report_markdown(doc, base_url="http://127.0.0.1:8418")
    assert text.rstrip().splitlines()[-1] == "demo flow completed in 7:12"
    for sid, _title in e2e_demo.STEPS:
        assert f"| {sid} |" in text
    assert "Verdict: **PASS**" in text
    failed = e2e_demo.report_markdown(
        _doc(_results({"S9": "FAIL"})), base_url="http://127.0.0.1:8418"
    )
    assert "Verdict: **FAIL**" in failed


def test_mmss() -> None:
    assert e2e_demo.mmss(431.6) == "7:12"
    assert e2e_demo.mmss(59.5) == "1:00"
    assert e2e_demo.mmss(0) == "0:00"


def test_value_helpers() -> None:
    record = {"a": "Ananya Krishnan", "b": {"c": ["x@example-mail.in", 3]}, "d": 712}
    assert e2e_demo.leaves(record) == ["Ananya Krishnan", "x@example-mail.in"]
    assert e2e_demo.found_in(["… ananya krishnan …"], ["Ananya Krishnan", "abc"]) == 1
    assert e2e_demo.found_in(["nothing here"], e2e_demo.leaves(record)) == 0


def test_the_console_walk_covers_the_plans_nine_pages_and_the_decks_screens() -> None:
    names = [name for name, _route, _selector, _text in e2e_demo.PAGES]
    assert len(names) == 9 and len(set(names)) == 9
    for wanted in ("playground", "job", "scan-parallel", "evidence"):  # tools/build_deck.py
        assert wanted in names
    routes = {n: r for n, r, _s, _t in e2e_demo.PAGES}
    # each page is recognised by its own heading, never by the sidebar's section names
    assert all(sel.startswith(("main", "article")) for _n, _r, sel, _t in e2e_demo.PAGES)
    assert routes["policies"] == "/policies/retail-kyc"
    assert "{scan}" in routes["scan-parallel"] and "tab=parallel" in routes["scan-parallel"]
    assert e2e_demo.THEMES == ("light", "dark")


def test_main_without_the_seed_files_fails_cleanly(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = e2e_demo.main(
        ["--keys", str(tmp_path / "none.json"), "--group-file", str(tmp_path / "g.json")]
    )
    assert code == 1
    assert "demo_seed" in capsys.readouterr().out
