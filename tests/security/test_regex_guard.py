"""S3: policy-supplied regexes cannot pin a CPU (ReDoS).

Layer 1, the static filter, refuses the classic shapes when a policy is validated. Layer 2, the
bound (S3 review round 1 B1): policy patterns run on the ``regex`` module under the call's CPU
budget, linear in what they scan, so the stacked repeats the filter cannot see
(``\\w*\\w*\\w*\\w*\\w*!``, ``.*a.*a.*a.*b``, ``\\d+\\d+\\d+x`` …) either finish or are refused
(422 ``custom_pattern_too_slow``) within about a second. Declared-field globs are matched
without a regex, linear in the path. The timing checks run in a child process with a hard time
limit, so a regression to a backtracking matcher fails the test instead of hanging the run.
"""

from __future__ import annotations

import json
import os
import random
import re
import subprocess
import sys
import time

import pytest

from dps.engine.call_scope import call_scope
from dps.engine.detect.custom import (
    PolicyCustomDetector,
    RecognizerSpec,
    custom_detector_from,
    path_glob,
)
from dps.engine.detect.regex_guard import (
    BUDGET_BASE_SECONDS,
    BUDGET_PER_CHAR_SECONDS,
    BUDGET_PER_RUN_SECONDS,
    PatternBudget,
    PatternTooSlow,
    UnsafePattern,
    call_budget,
    check_pattern,
    compile_policy_pattern,
)


@pytest.mark.parametrize(
    "pattern",
    [
        r"\bEMP-\d{6}\b",
        r"[A-Z]{5}\d{4}[A-Z]",
        r"(?:\+91[ -]?)?\d{10}",
        r"\b\d{3}-\d{2}-\d{4}\b",
        r"(?:\d{1,3}\.){3}\d{1,3}",
        r"(?:\d+\.){3}\d+",
        r"\b(?:Mr|Mrs|Ms)\.?\s+\w+",
        r"(?:ab|cd)+",
    ],
)
def test_ordinary_patterns_pass(pattern: str) -> None:
    check_pattern(pattern)


@pytest.mark.parametrize(
    "pattern,rule",
    [
        (r"(a+)+$", "nested"),
        (r"(\w+\s?)*$", "nested"),
        (r"(.*a){12}", "nested"),
        (r"(\d+)\1", "backreferences"),
        (r"(a)?(?(1)b|c)", "conditional"),
        (r"a{1,5000}", "bounds"),
        ("a" * 600, "longer"),
        (r"(", "compile"),
        (r"(a|aa)+$", "alternation"),
    ],
)
def test_dangerous_patterns_are_refused(pattern: str, rule: str) -> None:
    with pytest.raises(UnsafePattern, match=rule):
        check_pattern(pattern)


def test_refusals_never_quote_the_pattern() -> None:
    with pytest.raises(UnsafePattern) as caught:
        check_pattern(r"(SENTINELX+)+$")
    assert "SENTINELX" not in str(caught.value)


def test_policy_recognizers_are_guarded() -> None:
    with pytest.raises(ValueError, match="nested"):
        RecognizerSpec(
            name="bad",
            supported_entity="EMPLOYEE_ID",
            patterns=[{"name": "x", "regex": r"(a+)+", "score": 0.5}],
        )
    with pytest.raises(ValueError, match="nested"):  # inline policies take the same path
        custom_detector_from(
            [
                {
                    "name": "bad",
                    "supported_entity": "EMPLOYEE_ID",
                    "patterns": [{"name": "x", "regex": r"(a+)+$", "score": 0.5}],
                }
            ],
            [],
        )


def test_the_finders_probe_is_refused_before_it_can_run() -> None:
    """The S3 finder's probe: ``(a+)+$`` over ~40 ``a`` never returned. Now nothing runs."""
    started = time.monotonic()
    with pytest.raises(ValueError):
        RecognizerSpec(
            name="p",
            supported_entity="EMPLOYEE_ID",
            patterns=[{"name": "p", "regex": "(a+)+$", "score": 0.5}],
        )
    assert time.monotonic() - started < 1.0


def test_stacked_glob_stars_mean_one_star() -> None:
    """Adjacent ``*`` in a declared-field glob mean the same as one (S3 finder)."""
    glob = path_glob("$." + "*" * 25 + "x")
    started = time.monotonic()
    assert not glob.fullmatch("$." + "a" * 60)
    assert glob.fullmatch("$." + "a" * 60 + "x") and glob.fullmatch("$.x")
    assert time.monotonic() - started < 0.5


def _isolated(script: str, *, limit: float) -> dict[str, list[object]]:
    """Run ``script`` in a child process killed after ``limit`` seconds; its last stdout line is
    a JSON object. A child that runs out of time is a policy pattern that pinned a CPU."""
    env = {
        k: v for k, v in os.environ.items() if not k.startswith("DPS_") or k.startswith("DPS_TEST_")
    }
    try:
        done = subprocess.run(
            [sys.executable, "-c", script],
            capture_output=True,
            text=True,
            timeout=limit,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(f"a policy pattern ran for more than {limit:.0f} s: no time bound (ReDoS)")
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]


#: The review's stacked patterns (and two the ``regex`` module's literal pre-checks cannot skip),
#: each with the input that pinned a CPU for more than 20 s under ``re``.
_STACKED = [
    ("stacked5", r"\w*\w*\w*\w*\w*!", "a", 500),
    ("dotstar", r".*a.*a.*a.*b", "a", 500),
    ("stacked3", r"\d+\d+\d+x", "1", 2000),
    ("stacked2", r"\d*\d*x", "1", 20000),
    ("stacked3-class", r"\d+\d+\d+[x!]", "1", 2000),
    ("alternation", r"(?:\w|\d)+$", "1", 20000),
]

_ENGINE_SCRIPT = """
import json, sys, time
from dps.engine.call_scope import call_scope
from dps.engine.detect.custom import PolicyCustomDetector, RecognizerSpec, path_glob
from dps.engine.detect.regex_guard import PatternTooSlow
out = {}
for label, pattern, char, n in json.loads(sys.argv[1] if len(sys.argv) > 1 else CASES):
    spec = RecognizerSpec(name="r", supported_entity="EMPLOYEE_ID",
                          patterns=[{"name": "p", "regex": pattern, "score": 0.5}])
    detector = PolicyCustomDetector([spec], [])
    text = char * n + ("!" if label == "alternation" else "")
    with call_scope():
        started = time.perf_counter()
        try:
            detector.scan(text, None)
            status = "ok"
        except PatternTooSlow as exc:
            status = exc.code
        out[label] = [status, time.perf_counter() - started]
glob = path_glob("$.*a*a*a*a*a*a*a*a*a*b")
for label, path in (("glob63", "$." + "a" * 63), ("glob10k", "$." + "a" * 10000),
                    ("glob_hit", "$." + "a" * 10000 + "b")):
    started = time.perf_counter()
    out[label] = [glob.fullmatch(path), time.perf_counter() - started]
print(json.dumps(out))
"""


def test_stacked_repeats_finish_or_are_refused_within_the_budget() -> None:
    """S3 review round 1 B1: the static filter accepts these; each pinned a CPU for more than
    20 s under ``re`` on a few hundred characters. Now each finishes or is refused within about
    the call's base budget — and a separated-star glob over a long name is matched at once."""
    script = "CASES = " + repr(json.dumps(_STACKED)) + _ENGINE_SCRIPT
    for _label, pattern, _char, _n in _STACKED:
        check_pattern(pattern)  # the filter alone does not catch them
    out = _isolated(script, limit=90)
    bound = BUDGET_BASE_SECONDS + 1.5
    for label, _pattern, _char, _n in _STACKED:
        status, took = out[label]
        assert status in ("ok", "custom_pattern_too_slow"), label
        assert float(took) < bound, (label, took)  # type: ignore[arg-type]
    assert out["stacked3-class"][0] == "custom_pattern_too_slow"
    for label in ("glob63", "glob10k", "glob_hit"):
        assert float(out[label][1]) < 0.05, (label, out[label])  # type: ignore[arg-type]
    assert out["glob63"][0] is False and out["glob10k"][0] is False
    assert out["glob_hit"][0] is True


_API_SCRIPT = """
import json, time
from fastapi.testclient import TestClient
from dps.app import create_app
from dps.config import Settings
client = TestClient(create_app(Settings(_env_file=None, env="local", auth_enabled=False,
    llm_provider="stub", ner_enabled=False, rate_limit_enabled=False)))
def rec(pattern):
    return {"name": "emp", "supported_entity": "EMPLOYEE_ID",
            "patterns": [{"name": "p", "regex": pattern, "score": 0.5}]}
out = {}
cases = (("slow", rec(r"\\d+\\d+\\d+[x!]"), "1" * 2000),
         ("probe", rec(r"\\w*\\w*\\w*\\w*\\w*!"), "a" * 400))
for label, recognizer, text in cases:
    for route in ("test-recognizer", "detect", "obfuscate"):
        started = time.perf_counter()
        if route == "test-recognizer":
            r = client.post("/api/v1/policies/test-recognizer",
                            json={"recognizer": recognizer, "text": text})
        else:
            policy = {"preset": "gdpr-pseudonymise",
                      "detect": {"custom_recognizers": [recognizer]}}
            r = client.post("/api/v1/" + route, json={"text": text, "policy": policy})
        out[label + ":" + route] = [r.status_code, r.json().get("code"),
                                    time.perf_counter() - started]
glob_policy = {"preset": "gdpr-pseudonymise", "detect": {"declared_fields": [
    {"path": "$.*a*a*a*a*a*a*a*a*a*b", "entity_type": "EMPLOYEE_ID"}]}}
started = time.perf_counter()
r = client.post("/api/v1/detect", json={"data": {"a" * 63: "x"}, "policy": glob_policy})
out["glob:detect"] = [r.status_code, r.json().get("code"), time.perf_counter() - started]
print(json.dumps(out))
"""


def test_the_review_probe_through_the_routes_is_answered_within_the_budget() -> None:
    """The reviewer's probe: ``/policies/test-recognizer``, ``/detect`` and ``/obfuscate`` (inline
    policy) gave no answer within 45 s on 400 characters. Each route now answers within about a
    second: the pattern finishes, or the call is refused with 422 ``custom_pattern_too_slow``."""
    out = _isolated(_API_SCRIPT, limit=120)
    bound = BUDGET_BASE_SECONDS + 2.0
    for route in ("test-recognizer", "detect", "obfuscate"):
        status, code, took = out[f"slow:{route}"]
        assert (status, code) == (422, "custom_pattern_too_slow"), route
        assert float(took) < bound, (route, took)  # type: ignore[arg-type]
        status, code, took = out[f"probe:{route}"]
        assert status in (200, 422), (route, status, code)
        assert float(took) < bound, (route, took)  # type: ignore[arg-type]
    status, _code, took = out["glob:detect"]
    assert status == 200 and float(took) < bound  # type: ignore[arg-type]


def test_a_calls_detectors_draw_on_one_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    """All detectors of a call (an NDJSON stream builds one per item) share the call's budget, so
    the base allowance is granted once per call, not once per detector."""
    used: list[int] = []
    real_run = PatternBudget.run

    def recording(self: PatternBudget, pattern: object, text: str) -> list[object]:
        used.append(id(self))
        return real_run(self, pattern, text)  # type: ignore[arg-type]

    monkeypatch.setattr(PatternBudget, "run", recording)
    spec = RecognizerSpec(
        name="r",
        supported_entity="EMPLOYEE_ID",
        patterns=[{"name": "p", "regex": r"\bEMP-\d{6}\b", "score": 0.5}],
        deny_list=["Blue Falcon"],
    )
    with call_scope():
        budget = call_budget()
        assert budget is not None
        for _ in range(3):
            PolicyCustomDetector([spec], []).scan("EMP-123456 and blue falcon", None)
    assert used and set(used) == {id(budget)}
    used.clear()
    PolicyCustomDetector([spec], []).scan("EMP-123456", None)  # outside a call: its own budget
    assert used and id(budget) not in used


def test_the_budget_is_linear_in_what_is_scanned() -> None:
    budget = PatternBudget()
    assert budget.left == pytest.approx(BUDGET_BASE_SECONDS)
    budget.earn(10_000, 3)
    expected = BUDGET_BASE_SECONDS + 3 * (BUDGET_PER_RUN_SECONDS + BUDGET_PER_CHAR_SECONDS * 10_000)
    assert budget.left == pytest.approx(expected)
    spent = PatternBudget(base_seconds=0.0)
    with pytest.raises(PatternTooSlow) as refused:  # nothing left: refused before it runs
        spent.run(compile_policy_pattern(r"\d+"), "123")
    assert refused.value.code == "custom_pattern_too_slow" and refused.value.status == 422
    assert "123" not in refused.value.detail
    spent.earn(3, 1)
    assert spent.run(compile_policy_pattern(r"\d+"), "123") == [(0, 3)]
    capped = PatternBudget(ceiling=0.5)  # R2-B2: an API request's budget never exceeds its cap
    capped.earn(10_000_000, 1000)
    assert capped.left == pytest.approx(0.5)


@pytest.mark.parametrize(
    "pattern",
    [
        r"\bEMP-\d{6}\b",
        r"[A-Z]{5}\d{4}[A-Z]",
        r"(?:\+91[ -]?)?\d{10}",
        r"\b\d{3}-\d{2}-\d{4}\b",
        r"(?:\d{1,3}\.){3}\d{1,3}",
        r"\b(?:Mr|Mrs|Ms)\.?\s+\w+",
        r"(?i)\bacct[ #:]*\d+",
        r"\d*",
        r"(?<!\d)\d{12}(?!\d)",
        r"\w+@\w+\.\w+",
    ],
)
def test_policy_patterns_match_exactly_like_re(pattern: str) -> None:
    """The ``regex`` module in ``VERSION0`` reports the spans ``re`` reports."""
    texts = [
        "EMP-123456 ABCDE1234F +91 9876543210 123-45-6789 10.0.0.1 Mr. Smith acct #: 991",
        "Ms  Ünal 123456789012 1234567890123 jane@bank.com ab 12 ÉMP-000001",
        "",
        "١٢٣ digits ٤٥٦ and EMP-654321",
    ]
    compiled = compile_policy_pattern(pattern)
    for text in texts:
        want = [(m.start(), m.end()) for m in re.finditer(pattern, text)]
        assert PatternBudget().run(compiled, text) == want, (pattern, text)


def _old_path_glob(pattern: str) -> re.Pattern[str]:
    """The regex form of the glob before round 1 (the reference semantics)."""
    out: list[str] = []
    i = 0
    while i < len(pattern):
        if pattern.startswith("[*]", i):
            out.append(r"\[[0-9]+\]")
            i += 3
        elif pattern[i] == "*":
            out.append(r"[^.\[\]]*")
            i += 1
            while i < len(pattern) and pattern[i] == "*":
                i += 1
        else:
            out.append(re.escape(pattern[i]))
            i += 1
    return re.compile("".join(out))


def test_declared_globs_match_exactly_like_the_regex_form() -> None:
    """Differential check of the non-backtracking glob against the old regex semantics on short
    random globs and paths (where the regex is fast)."""
    rnd = random.Random(20261014)
    glob_tokens = ["a", "b", "*", ".", "[*]", "[", "]", "0", "$"]
    path_tokens = ["a", "b", ".", "[", "]", "0", "1", "$", "ab", "[12]"]
    checked = 0
    for _ in range(6000):
        glob = "".join(rnd.choice(glob_tokens) for _ in range(rnd.randint(0, 7)))
        path = "".join(rnd.choice(path_tokens) for _ in range(rnd.randint(0, 7)))
        want = _old_path_glob(glob).fullmatch(path) is not None
        assert path_glob(glob).fullmatch(path) is want, (glob, path)
        checked += want
    assert checked > 50  # the corpus exercises matches, not only refusals
    for glob, path in (
        ("$.accounts[*].iban", "$.accounts[12].iban"),
        ("$.*.tax_ref", "$.customer.tax_ref"),
        ("$.*_id_*", "$.cust_id_no"),
        ("$.a[*]", "$.a[]"),
        ("$.a[*]", "$.a[١]"),
    ):
        assert path_glob(glob).fullmatch(path) is (
            _old_path_glob(glob).fullmatch(path) is not None
        ), (glob, path)


@pytest.mark.parametrize(
    "pattern,rule",
    [
        (r"(?:ACCT1234){e<=2}", "repeat count"),
        (r"\bID{x}\d+", "repeat count"),
        (r"\d{}", "repeat count"),
        (r"[[:digit:]]+", "inside a set"),
        (r"[a[b]", "inside a set"),
    ],
)
def test_constructs_the_engine_reads_differently_from_re_are_refused(
    pattern: str, rule: str
) -> None:
    """Review round 2 (N-r2-1): ``re`` reads these literally, the ``regex`` module as a fuzzy match
    or a POSIX class — refused with the rule, never the pattern."""
    with pytest.raises(UnsafePattern, match=rule) as caught:
        check_pattern(pattern)
    assert "ACCT" not in str(caught.value)


@pytest.mark.parametrize(
    "pattern",
    [r"\{\d+\}", r"a{2,3}", r"a{,3}", r"a{3,}", r"x{2}", r"[\[\]]", r"[]a]", r"[^]a]+", r"\[x\]"],
)
def test_escaped_braces_brackets_and_repeat_counts_pass(pattern: str) -> None:
    check_pattern(pattern)
    want = [m.span() for m in re.finditer(pattern, "{12} aa aaa x xx [x] ]a a] b")]
    assert PatternBudget().run(compile_policy_pattern(pattern), "{12} aa aaa x xx [x] ]a a] b") == (
        want
    )
