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


# -- S2: the synthetic-leak probes (review S6b r1 B2) ---------------------------------------------

RECORD: dict[str, Any] = e2e_demo.SAMPLES["json"]
#: /detect's findings for the console's KYC sample, by path (the CRITICAL card number is nested).
FINDINGS: list[dict[str, Any]] = [
    {"entity_type": t, "locator": {"path": p}}
    for t, p in (
        ("PERSON", "$.full_name"),
        ("DATE_OF_BIRTH", "$.dob"),
        ("AADHAAR", "$.aadhaar"),
        ("PAN_IN", "$.pan"),
        ("EMAIL", "$.email"),
        ("PHONE", "$.phone"),
        ("CARD_PAN", "$.card.pan"),
        ("CARD_EXPIRY", "$.card.expiry"),
        ("ADDRESS", "$.address"),
    )
]


def test_s2_probes_every_string_leaf_of_a_detected_field_never_a_repr() -> None:
    probes = e2e_demo.detected_values(RECORD, FINDINGS)
    assert RECORD["card"]["pan"] in probes and RECORD["card"]["expiry"] in probes
    assert not any(p.startswith(("{", "[")) for p in probes)  # no dict or list repr
    assert set(probes) == {
        RECORD[k] for k in ("full_name", "dob", "aadhaar", "pan", "email", "phone", "address")
    } | {RECORD["card"]["pan"], RECORD["card"]["expiry"]}
    # a span finding probes its top-level field's leaves; an unknown or root path probes nothing
    spans: list[dict[str, Any]] = [
        {"locator": {"path": "$.notes", "start": 5, "end": 9}},
        {"locator": {"path": "$"}},
    ]
    assert e2e_demo.detected_values(RECORD, spans) == [RECORD["notes"]]
    assert e2e_demo.detected_values(RECORD, [{"locator": {"path": "$.nope"}}]) == []


class FakeDps:
    """``DpsClient.post``/``get`` answering from a table of routes; the bodies it was sent."""

    def __init__(self, answers: dict[str, Any]) -> None:
        self.answers = answers
        self.sent: list[tuple[str, Any]] = []

    def post(self, path: str, body: Any = None) -> Any:
        self.sent.append((path, body))
        answer = self.answers[path]
        return answer(body) if callable(answer) else answer

    def get(self, path: str, **params: Any) -> Any:
        return self.answers[path]


def _demo(c: FakeDps, *, skip_oracle: bool = False) -> e2e_demo.Demo:
    import argparse

    args = argparse.Namespace(skip_oracle=skip_oracle, tenant="retail-banking-apac")
    group = {"group_id": "grp_demo", "key_material": {"kid": "k1"}}
    return e2e_demo.Demo(c, c, None, args, group)  # type: ignore[arg-type]


def _playground(synthetic: dict[str, Any]) -> FakeDps:
    report = {"verification": {"passed": True}, "policy": "retail-kyc", "llm": {"raw_pii_sent": 0}}
    return FakeDps(
        {
            "/detect": {"findings": FINDINGS},
            "/obfuscate": {"result": {}, "key_material": {}, "reveal_hints": [], "report": report},
            "/reveal": {"result": RECORD},
            "/synthesize": {"result": synthetic, "report": {"llm": {"raw_pii_sent": 0}}},
        }
    )


SYNTHETIC: dict[str, Any] = {
    "customer_id": "CUST-0000001",
    "full_name": "Meera Iyer",
    "dob": "1979-02-11",
    "aadhaar": "5021 3344 1290",
    "pan": "BQRPS7731L",
    "email": "meera.iyer@example.org",
    "phone": "+91 99887 10234",
    "card": {"pan": "5500 0000 0000 0004", "expiry": "11/30"},
    "address": "14 Lake View Road, Pune 411001",
    "risk_score": 403,
    "notes": "Prefers e-mail.",
}


def test_s2_passes_a_synthetic_record_without_input_values_and_counts_the_nested_probes() -> None:
    facts, detail = _demo(_playground(SYNTHETIC)).s2()
    assert facts["synthetic_input_values"] == 0
    assert "synthetic carries 0 of 9 detected values" in detail  # card.pan and card.expiry too


@pytest.mark.parametrize(
    "leaked",
    [
        pytest.param({"card": {**SYNTHETIC["card"], "pan": RECORD["card"]["pan"]}}, id="card pan"),
        pytest.param({"card": {**SYNTHETIC["card"], "expiry": RECORD["card"]["expiry"]}}, id="expiry"),
        pytest.param({"notes": f"call {RECORD['email']}"}, id="email in another field"),
    ],
)  # fmt: skip
def test_s2_fails_when_the_synthetic_record_carries_a_detected_value(
    leaked: dict[str, Any],
) -> None:
    with pytest.raises(StepFailed, match="1 detected input values in the synthetic record"):
        _demo(_playground({**SYNTHETIC, **leaked})).s2()


# -- S7: the consistency check joins this run's S6 jobs (review S6b r1 B3) ------------------------


def _column(
    path: str, entity: str | None, method: str = "fpe_digits", **flags: Any
) -> dict[str, Any]:
    return {
        "path": path,
        "entity_type": entity,
        "is_key": flags.get("is_key", False),
        "anchor": flags.get("anchor", False),
        "text_column": flags.get("text_column", False),
        "method": {"method": method},
    }


def test_the_join_path_is_the_snapshots_joinable_customer_id_column() -> None:
    columns = [
        _column("$._id", None, "keep", is_key=True),
        _column("$.legacy.customerId", "CUSTOMER_ID"),
        _column("$.customerId", "CUSTOMER_ID"),
        _column("$.crmRef", "CUSTOMER_ID", "keep"),  # kept: the server never joins it
        _column("$.notes", "CUSTOMER_ID", text_column=True),  # span-transformed text
        _column("$.email", "EMAIL"),
    ]
    assert e2e_demo.customer_id_path(columns) == "$.customerId"  # the shortest joinable path
    assert (
        e2e_demo.customer_id_path([*columns, _column("$.zz", "CUSTOMER_ID", anchor=True)]) == "$.zz"
    )
    assert (
        e2e_demo.customer_id_path([*columns, _column("$.k", "CUSTOMER_ID", is_key=True)]) == "$.k"
    )
    assert e2e_demo.customer_id_path([c for c in columns if "ustomerId" not in c["path"]]) is None


RUNS = [
    {"engine": "postgres", "job_id": "j_pg_run"},
    {"engine": "mongo", "job_id": "j_mongo_run"},
    {"engine": "oracle", "job_id": "j_oracle_run"},
]
PATHS = {"postgres": "$.customer_id", "mongo": "$.customerId", "oracle": "$.CUSTOMER_ID"}


def test_consistency_pairs_are_one_per_s6_job_anchored_on_the_last_submitted() -> None:
    assert e2e_demo.consistency_pairs(RUNS, PATHS) == [
        {"job_id": "j_oracle_run", "path": "$.CUSTOMER_ID"},
        {"job_id": "j_mongo_run", "path": "$.customerId"},
        {"job_id": "j_pg_run", "path": "$.customer_id"},
    ]
    assert e2e_demo.consistency_pairs(RUNS[:2], PATHS) == [  # --skip-oracle
        {"job_id": "j_mongo_run", "path": "$.customerId"},
        {"job_id": "j_pg_run", "path": "$.customer_id"},
    ]
    with pytest.raises(StepFailed, match="oracle: no customer id column"):
        e2e_demo.consistency_pairs(RUNS, {k: v for k, v in PATHS.items() if k != "oracle"})


def _check_result(job_ids: list[str]) -> dict[str, Any]:
    n = len(job_ids)
    return {
        "members": [{"engine": "x", "job_id": j, "form": "numeric", "path": "$.p"} for j in job_ids],
        "pairs": [
            {"left": i, "right": k, "checked": 500, "matched": 500, "coverage": 1.0, "consistent": True}
            for i in range(n)
            for k in range(i + 1, n)
        ],
        "sampled": 500,
        "all_members_matched": 500,
        "consistent_pairs": n * (n - 1) // 2,
        "total_pairs": n * (n - 1) // 2,
        "passed": True,
    }  # fmt: skip


def _s7(result: dict[str, Any], *, skip_oracle: bool = False) -> tuple[FakeDps, Any]:
    c = FakeDps({"/job-groups/grp_demo/consistency-check": result})
    demo = _demo(c, skip_oracle=skip_oracle)
    demo.ctx["runs"] = RUNS[:2] if skip_oracle else RUNS
    demo.ctx["join_paths"] = PATHS
    return c, demo


@pytest.mark.parametrize("skip_oracle", [False, True])
def test_s7_posts_this_runs_jobs_and_passes_when_the_check_joined_them(skip_oracle: bool) -> None:
    runs = RUNS[:2] if skip_oracle else RUNS
    c, demo = _s7(_check_result([r["job_id"] for r in reversed(runs)]), skip_oracle=skip_oracle)
    facts, _detail = demo.s7()
    (path, body), = c.sent  # fmt: skip
    assert path == "/job-groups/grp_demo/consistency-check"
    assert body == {"pairs": e2e_demo.consistency_pairs(runs, PATHS), "sample": 500}
    assert sorted(m["job_id"] for m in facts["members"]) == sorted(r["job_id"] for r in runs)


@pytest.mark.parametrize(
    "members",
    [
        pytest.param(["j_oracle_old", "j_mongo_run", "j_pg_run"], id="an older run of the group"),
        pytest.param(["j_mongo_run", "j_pg_run"], id="a member missing"),
        pytest.param(
            ["j_oracle_old", "j_oracle_run", "j_mongo_run", "j_pg_run"], id="one too many"
        ),
    ],
)
def test_s7_fails_when_the_check_did_not_join_exactly_this_runs_jobs(members: list[str]) -> None:
    _c, demo = _s7(_check_result(members))
    with pytest.raises(StepFailed, match="this run's S6 jobs"):
        demo.s7()


# -- the driver: any other exception of a step is an ERROR (review S6b r1 N1) ---------------------


def test_an_unforeseen_exception_is_a_value_free_error_and_the_report_is_still_written() -> None:
    class TimeoutError(Exception):  # noqa: A001 - Playwright's own name, not the builtin
        pass

    results = run_steps(
        [
            ("S11", "admin", _step()),
            ("S12", "walk", _raise(TimeoutError("waiting for 'main h1' with text Ananya"))),
        ],
        echo=lambda _line: None,
    )
    assert [r.status for r in results] == ["PASS", "ERROR"]
    assert results[1].detail == "TimeoutError"
