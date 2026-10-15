"""S3 review round 3: policy patterns never freeze the server, and deny-list builds are charged.

R3-B1: ``/synthesize`` and ``/documents/synthesize`` ran detection on the event loop. A
policy-pattern run that left its 5 ms slice then blocked the loop — first in an unbounded wait for a
matcher process, then for the whole run (up to the call's ceiling) — so ``/health`` stopped
answering for every tenant (the reviewer: 26 of 29 liveness checks failed under back-to-back
synthesize requests; ``/health`` blocked 19.5 s behind six ``/detect`` runs). Every route now runs
its engine work off the loop, a run on the loop that serves a request is refused (a server defect,
never a frozen server), and a run waits a bounded time for a matcher process.

R3-B2: building a deny list's trie sat outside every budget and cost about 10 times the round-1
flat compile (one 2.7 MB ``/detect`` with 50 inline deny lists: 34 CPU-seconds). A build now costs
about what the flat compile did, is charged to the call (within an allowance per term character),
and a call past its ceiling builds no further list.

Every text, term and pattern here is synthetic. The heavy end-to-end check runs in a child process
with a hard time limit, so a regression fails the test instead of hanging the run.
"""

from __future__ import annotations

import asyncio
import gc
import json
import os
import random
import re
import string
import subprocess
import sys
import threading
import time
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any

import pytest
import regex
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dps.app import create_app
from dps.config import Settings
from dps.engine.call_scope import call_scope
from dps.engine.detect import custom
from dps.engine.detect.custom import (
    PolicyCustomDetector,
    RecognizerSpec,
    compile_deny_list,
    deny_list_pattern,
    flat_deny_list_pattern,
)
from dps.engine.detect.llm_label import LLM_LABEL_TIER
from dps.engine.detect.pattern_process import MatcherBusy, MatcherPool
from dps.engine.detect.regex_guard import (
    BUDGET_BASE_SECONDS,
    PatternBudget,
    PatternMatcherBusy,
    PatternOnEventLoop,
    PatternTooSlow,
    budget_scope,
    call_budget,
    compile_policy_pattern,
)
from dps.engine.service import Payload, PrivacyEngine, RequestOptions
from dps.llm.gateway import LlmGateway, LlmResult

_ATTACK = r"\d+\d+\d+[x!]"  # stacked repeats: passes the static filter, cubic on a digit run
_HEALTH_LIMIT = 0.5  # seconds: a liveness probe that waits longer than this counts as frozen


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None,
        env="local",
        auth_enabled=False,
        llm_provider="stub",
        ner_enabled=False,
        rate_limit_enabled=False,
    )
    return Settings(**{**base, **kw})


def _attack_policy(patterns: int = 1000) -> dict[str, Any]:
    """The reviewer's policy: one stacked pattern among ``patterns`` linear ones (50 x 20)."""
    pats = [
        {"name": f"p{i}", "regex": _ATTACK if i == 0 else r"\bEMP-\d{5}\b", "score": 0.5}
        for i in range(patterns)
    ]
    recognizers = [
        {"name": f"r{k}", "supported_entity": f"CUSTOM_{k}", "patterns": pats[k * 20 : k * 20 + 20]}
        for k in range((patterns + 19) // 20)
    ]
    return {"preset": "gdpr-pseudonymise", "detect": {"custom_recognizers": recognizers}}


def _random_terms(count: int, length: int, seed: int) -> list[str]:
    rnd = random.Random(seed)
    out: set[str] = set()
    while len(out) < count:
        out.add("".join(rnd.choice(string.ascii_letters) for _ in range(length)))
    return sorted(out)


def _deny_policy(lists: int, length: int, seed: int) -> dict[str, Any]:
    recognizers = [
        {
            "name": f"r{r}",
            "supported_entity": f"CUSTOM_{r}",
            "deny_list": _random_terms(1000, length, seed * 1000 + r),
        }
        for r in range(lists)
    ]
    return {"preset": "gdpr-pseudonymise", "detect": {"custom_recognizers": recognizers}}


@contextmanager
def _health_watch(client: TestClient) -> Iterator[list[float]]:
    """Poll ``GET /health`` every 20 ms on a thread of its own until the block ends; yields the
    list of answer times (seconds)."""
    stop = threading.Event()
    times: list[float] = []

    def poll() -> None:
        while not stop.is_set():
            started = time.perf_counter()
            answer = client.get("/health")
            times.append(time.perf_counter() - started)
            assert answer.status_code == 200
            time.sleep(0.02)

    thread = threading.Thread(target=poll, daemon=True)
    thread.start()
    time.sleep(0.2)
    try:
        yield times
    finally:
        time.sleep(0.1)
        stop.set()
        thread.join(60)


def _code(answer: Any) -> tuple[int, str | None]:
    return answer.status_code, answer.json().get("code")


# --- R3-B1: the event loop keeps answering ------------------------------------------------------


def test_health_answers_while_synthesize_runs_a_policy_pattern() -> None:
    """The reviewer's probe through the app: a 1000-pattern policy whose stacked pattern runs
    until the call's ceiling (2 s here) on 10 KB of digits. ``/synthesize`` answers 422 as
    ``/detect`` does — and ``/health`` keeps answering meanwhile (before the fix it waited for
    the whole run)."""
    attack = {"text": "1" * 10_000, "policy": _attack_policy()}
    with TestClient(create_app(_settings(pattern_cpu_seconds=2.0))) as client:
        with _health_watch(client) as times:
            answer = client.post("/api/v1/synthesize", json=attack)
    assert _code(answer) == (422, "custom_pattern_too_slow")
    assert times and max(times) < _HEALTH_LIMIT, max(times)


def test_health_answers_while_a_document_synthesize_runs_a_policy_pattern() -> None:
    """The same attack as an uploaded text document on ``/documents/synthesize``."""
    with TestClient(create_app(_settings(pattern_cpu_seconds=2.0))) as client:
        with _health_watch(client) as times:
            answer = client.post(
                "/api/v1/documents/synthesize",
                files={"file": ("digits.txt", b"1" * 10_000, "text/plain")},
                data={"request": json.dumps({"policy": _attack_policy()})},
            )
    assert _code(answer) == (422, "custom_pattern_too_slow")
    assert times and max(times) < _HEALTH_LIMIT, max(times)


def test_health_answers_while_synthesize_waits_for_matchers_busy_with_other_requests() -> None:
    """Four ``/detect`` attacks occupy both matcher processes, then a ``/synthesize`` attack
    arrives: it waits for a matcher — off the loop. Before the fix the loop itself waited (the
    reviewer: ``/health`` blocked 19.5 s behind six runs)."""
    attack = {"text": "1" * 10_000, "policy": _attack_policy()}
    with TestClient(create_app(_settings(pattern_cpu_seconds=1.5))) as client:
        with _health_watch(client) as times, ThreadPoolExecutor(4) as pool:
            detects = [pool.submit(client.post, "/api/v1/detect", json=attack) for _ in range(4)]
            time.sleep(0.5)
            synth = client.post("/api/v1/synthesize", json=attack)
            answers = [f.result(timeout=120) for f in detects]
    assert _code(synth) == (422, "custom_pattern_too_slow")
    assert [_code(a) for a in answers] == [(422, "custom_pattern_too_slow")] * 4
    assert times and max(times) < _HEALTH_LIMIT, max(times)


def test_back_to_back_synthesize_attacks_do_not_starve_health_checks() -> None:
    """One client sends the attack to ``/synthesize`` back to back (the reviewer's duty-cycle
    probe: 26 of 29 health checks failed); every liveness check still answers in time."""
    attack = {"text": "1" * 10_000, "policy": _attack_policy()}
    stop = threading.Event()
    codes: list[tuple[int, str | None]] = []
    with TestClient(create_app(_settings(pattern_cpu_seconds=1.0))) as client:

        def attacker() -> None:
            while not stop.is_set():
                codes.append(_code(client.post("/api/v1/synthesize", json=attack)))

        with _health_watch(client) as times:
            thread = threading.Thread(target=attacker, daemon=True)
            thread.start()
            time.sleep(4.0)
            stop.set()
            thread.join(60)
    assert len(codes) >= 2 and set(codes) == {(422, "custom_pattern_too_slow")}, codes
    assert len(times) >= 50, len(times)
    assert max(times) < _HEALTH_LIMIT, max(times)


def test_health_answers_while_synthesize_builds_new_deny_lists() -> None:
    """R3-B2 on the loop: twenty new inline deny lists (1000 x 50 characters each) on
    ``/synthesize``. Their build runs off the loop (before the fix: about 0.6 s of frozen loop per
    list)."""
    body = {"text": "hello again", "policy": _deny_policy(20, 50, seed=11)}
    with TestClient(create_app(_settings())) as client:
        with _health_watch(client) as times:
            answer = client.post("/api/v1/synthesize", json=body)
    assert answer.status_code == 200, answer.text[:300]
    assert times and max(times) < _HEALTH_LIMIT, max(times)


def test_policy_patterns_never_run_on_the_loop_that_serves_a_request() -> None:
    """The guard: inside a request's scope, a run on the loop's own thread is refused (a server
    defect, value-free, never a frozen server); the same run off the loop answers; outside a
    request (tools, the worker) nothing changes."""
    compiled = compile_policy_pattern(r"\bEMP-\d{5}\b")

    async def serving() -> list[tuple[int, int]]:
        with call_scope(serving=True):
            budget = call_budget()
            assert budget is not None
            with pytest.raises(PatternOnEventLoop) as refused:
                budget.run(compiled, "id EMP-12345")
            assert "EMP" not in str(refused.value)
            return await asyncio.to_thread(budget.run, compiled, "id EMP-12345")

    assert asyncio.run(serving()) == [(3, 12)]

    async def unscoped() -> list[tuple[int, int]]:
        with call_scope():
            budget = call_budget()
            assert budget is not None
            return budget.run(compiled, "id EMP-12345")

    assert asyncio.run(unscoped()) == [(3, 12)]


def test_deny_lists_are_never_built_on_the_loop_that_serves_a_request() -> None:
    spec = RecognizerSpec(
        name="vip", supported_entity="VIP_NAME", deny_list=_random_terms(50, 12, seed=91)
    )

    async def serving() -> PolicyCustomDetector:
        with call_scope(serving=True):
            with pytest.raises(PatternOnEventLoop):
                PolicyCustomDetector([spec], [])
            return await asyncio.to_thread(PolicyCustomDetector, [spec], [])

    assert asyncio.run(serving()).text_entities == frozenset({"VIP_NAME"})


class _FakeGateway(LlmGateway):
    """A configured ``stellar`` provider that labels nothing; only the provider call is replaced
    (``complete`` and its egress verifier run as in production)."""

    async def _stellar(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> LlmResult:
        return LlmResult(text="[]", provider="stellar", model="t")


def _t3_engine() -> PrivacyEngine:
    settings = _settings(
        llm_provider="stellar",
        stellar_base_url="https://stellar.test",
        coin_url="https://coin.test",
        llm_egress_check="strict",
        fpe_enabled=False,
    )
    return PrivacyEngine(settings, gateway=_FakeGateway(settings), tokenizer=None)


def _t3_policy() -> dict[str, Any]:
    policy = _deny_policy(1, 12, seed=93)
    policy["detect"]["tiers"] = ["structural", "patterns", LLM_LABEL_TIER]
    return policy


def test_t3_labelling_builds_its_detector_off_the_serving_loop() -> None:
    """``label_leftovers`` / ``label_templates`` (T3) build the request's detector — deny lists
    included — on a worker thread, never on the loop (they resolve the policy there too)."""
    engine = _t3_engine()
    document = {"ref_cd": "AB-1234", "note": "plain words"}

    async def serving() -> tuple[int, int]:
        with call_scope(serving=True):
            leftovers = await engine.label_leftovers(
                Payload(data=document), _t3_policy(), RequestOptions()
            )
            labels = await engine.label_templates([document], _t3_policy(), RequestOptions())
        return leftovers.llm_calls, labels.llm_calls

    assert asyncio.run(serving()) == (1, 1)


def test_synthesize_runs_its_engine_work_off_the_serving_loop() -> None:
    """``PrivacyEngine.synthesize`` (text and JSON) runs detection, the plan, the transform and
    the verification on worker threads: inside a request's scope it answers instead of tripping
    the guard."""
    engine = PrivacyEngine(_settings(), tokenizer=None)
    policy = _deny_policy(1, 12, seed=95)
    term = policy["detect"]["custom_recognizers"][0]["deny_list"][0]

    async def serving() -> tuple[str, Any]:
        with call_scope(serving=True):
            text = await engine.synthesize(
                Payload(text=f"met {term} today"), policy, RequestOptions()
            )
            data = await engine.synthesize(Payload(data={"who": term}), policy, RequestOptions())
        return text.result, data.result

    text_out, data_out = asyncio.run(serving())
    assert term not in text_out and term not in json.dumps(data_out)


def test_a_run_waits_a_bounded_time_for_a_matcher() -> None:
    """Every wait is bounded: with the only matcher process busy, a run that cannot get one in
    time is refused (``MatcherBusy``; on a route: 503 ``pattern_matcher_busy``, retryable,
    value-free) instead of queueing forever."""
    pool = MatcherPool(size=1)
    attack = compile_policy_pattern(_ATTACK)
    busy = threading.Thread(target=pool.run, args=(attack.pattern, attack.flags, "1" * 5000, 3.0))
    busy.start()
    try:
        time.sleep(0.3)
        started = time.perf_counter()
        with pytest.raises(MatcherBusy):
            pool.run(r"\d+", regex.VERSION0, "a 12", 1.0, wait=0.3)
        assert time.perf_counter() - started < 2.0
    finally:
        busy.join(30)
        pool.close()


def test_a_busy_pool_refuses_the_call_with_a_retryable_code(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def busy(*_args: object, **_kw: object) -> object:
        raise MatcherBusy

    monkeypatch.setattr(MatcherPool, "run", busy)
    with pytest.raises(PatternMatcherBusy) as refused:
        PatternBudget(base_seconds=1.0).run(compile_policy_pattern(_ATTACK), "1" * 3000)
    assert (refused.value.status, refused.value.code) == (503, "pattern_matcher_busy")


# --- R3-B2: the deny-list build -----------------------------------------------------------------


def _isolated(script: str, *, limit: float) -> dict[str, Any]:
    """Run ``script`` in a child process killed after ``limit`` seconds; its last stdout line is a
    JSON object."""
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
        pytest.fail(f"a deny-list build ran for more than {limit:.0f} s: no ceiling")
    assert done.returncode == 0, done.stderr[-3000:]
    return json.loads(done.stdout.strip().splitlines()[-1])  # type: ignore[no-any-return]


_BUILD_SCRIPT = """
import json, random, string, time
from fastapi.testclient import TestClient
from dps.app import create_app
from dps.config import Settings
def terms(seed):
    rnd = random.Random(seed)
    out = set()
    while len(out) < 1000:
        out.add("".join(rnd.choice(string.ascii_letters) for _ in range(50)))
    return sorted(out)
def body(lists, first_seed):
    policy = {"preset": "gdpr-pseudonymise", "detect": {"custom_recognizers": [
        {"name": "r%d" % r, "supported_entity": "CUSTOM_%d" % r, "deny_list": terms(first_seed + r)}
        for r in range(lists)]}}
    return {"text": "hello world, nothing to see", "policy": policy}
out = {}
for label, ceiling, lists, seed in (("tight", 1.0, 50, 7000), ("default", 5.0, 20, 8000)):
    client = TestClient(create_app(Settings(_env_file=None, env="local", auth_enabled=False,
        llm_provider="stub", ner_enabled=False, rate_limit_enabled=False,
        pattern_cpu_seconds=ceiling)))
    request = body(lists, seed)  # every list new: nothing cached
    started, cpu = time.perf_counter(), time.process_time()
    r = client.post("/api/v1/detect", json=request)
    out[label] = [r.status_code, r.json().get("code"), time.perf_counter() - started,
                  time.process_time() - cpu, len(json.dumps(request)) / 1e6]
print(json.dumps(out))
"""


def test_the_reviewers_2_7_mb_deny_list_policy_answers_within_the_ceiling() -> None:
    """The reviewer's request: 50 new inline deny lists of 1000 terms of 50 characters (2.7 MB).
    Under a 1 s ceiling the builds stop once the call has spent it (422 within about the ceiling)
    — before the fix the request built every list first, 34 CPU-seconds outside any budget. Under
    the default 5 s ceiling, 20 new such lists answer 200 at about the round-1 cost."""
    out = _isolated(_BUILD_SCRIPT, limit=150)
    status, code, took, _cpu, mb = out["tight"]
    assert 2.5 < float(mb) < 3.0, out
    assert (status, code) == (422, "custom_pattern_too_slow"), out
    assert float(took) < 1.0 + 3.0, out
    status, _code, _took, cpu, _mb = out["default"]
    assert status == 200, out
    assert float(cpu) < 4.0, out


def test_a_deny_list_builds_at_about_the_round_1_cost() -> None:
    """R3-B2: building a 1000 x 50 deny list (pattern text and compile) costs about what the
    round-1 flat compile did — round 2's character classes cost 10 times that. Measured with the
    cyclic garbage collector paused (a collection on a large heap lands in whichever timing it
    interrupts) and the best of three lists each."""
    custom._cased()  # the one-off case table (built once per process)
    for char in string.ascii_letters:
        custom._matches(char)
    trie: list[float] = []
    round_1: list[float] = []
    gc.collect()
    gc.disable()
    try:
        for seed in (101, 102, 103):
            terms = _random_terms(1000, 50, seed=seed)
            started = time.thread_time()
            compile_policy_pattern(deny_list_pattern(terms), regex.IGNORECASE)
            trie.append(time.thread_time() - started)
            started = time.thread_time()
            compile_policy_pattern(flat_deny_list_pattern(terms), regex.IGNORECASE)
            round_1.append(time.thread_time() - started)
    finally:
        gc.enable()
    assert min(trie) < 1.6 * min(round_1) + 0.005, (trie, round_1)


def test_a_deny_list_build_is_charged_to_the_call() -> None:
    """A build counts toward the call's spending (so toward its ceiling) and leaves the call's run
    allowance as it was (the texts' earnings)."""
    spec = RecognizerSpec(
        name="vip", supported_entity="VIP_NAME", deny_list=_random_terms(1000, 30, seed=103)
    )
    budget = PatternBudget(ceiling=5.0, run_cap=5.0)
    with budget_scope(budget):
        PolicyCustomDetector([spec], [])
    assert budget.spent > 0.001, budget.spent
    assert budget.left == pytest.approx(BUDGET_BASE_SECONDS, abs=1e-6)


def test_a_call_past_its_ceiling_builds_no_further_deny_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    built: list[int] = []
    real = custom._compile_deny_list

    def counting(terms: tuple[str, ...]) -> regex.Pattern[str]:
        built.append(len(terms))
        return real(terms)

    monkeypatch.setattr(custom, "_compile_deny_list", counting)
    specs = [
        RecognizerSpec(
            name=f"r{k}",
            supported_entity=f"CUSTOM_{k}",
            deny_list=_random_terms(1000, 50, seed=200 + k),
        )
        for k in range(6)
    ]
    budget = PatternBudget(ceiling=0.03, run_cap=0.03)
    with budget_scope(budget), pytest.raises(PatternTooSlow):
        PolicyCustomDetector(specs, [])
    assert 1 <= len(built) < 6, built


def test_deny_list_terms_are_capped() -> None:
    """A term longer than 256 characters is refused at validation (value-free): one list's build
    stays bounded."""
    long_term = "Zq" * 129
    with pytest.raises(ValidationError) as refused:
        RecognizerSpec(name="vip", supported_entity="VIP_NAME", deny_list=[long_term])
    assert long_term not in str(refused.value)
    assert "256" in str(refused.value)
    RecognizerSpec(name="vip", supported_entity="VIP_NAME", deny_list=["Zq" * 128])


def test_the_trie_still_matches_like_the_flat_alternation_on_wide_case_classes() -> None:
    """The cheaper trie (plain characters under ``IGNORECASE``; explicit case-sensitive classes
    only where the matcher's case relation is not an equivalence) keeps the flat form's exact
    spans — the reviewer's wide alphabets: Greek symbol variants, titlecase digraphs, the Kelvin,
    Angstrom, Ohm and micro signs, ypogegrammeni, Cherokee, Georgian, the Turkish i's, long s."""
    groups = [
        "ǄǅǆDZDzdz", "ǇǈǉLJLjlj", "ǊǋǌNJNjnj", "ǱǲǳDZDzdz", "βϐΒ", "θϑΘϴ", "φϕΦ", "πϖΠ",
        "κϰΚ", "ρϱΡ", "εϵΕ", "σςΣ", "ιͅιΙ", "μµΜ", "ωΩΩ", "åÅÅ", "kKK", "sſS", "ṡẛṠ",
        "ᏸᏰꮨᎠ", "ꭰᎠ", "ᲐაႠⴀ", "iIıİ", "ßẞ", "ᾳᾼ",
    ]  # fmt: skip
    alphabet = sorted(set("".join(groups) + "ab -.12"))
    rnd = random.Random(20261015)
    hits = 0
    for _ in range(600):
        terms = sorted(
            {
                "".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 5))).strip(" ") or "a"
                for _ in range(rnd.randint(1, 15))
            }
        )
        pieces = []
        for _ in range(rnd.randint(1, 30)):
            roll = rnd.random()
            if roll < 0.4:
                term = rnd.choice(terms)
                pieces.append(
                    "".join(rnd.choice(next((g for g in groups if c in g), c)) for c in term)
                )
            elif roll < 0.6:
                pieces.append(rnd.choice(terms))
            else:
                pieces.append("".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 4))))
        text = rnd.choice([" ", "", "-"]).join(pieces)
        alternation = "|".join(re.escape(w) for w in sorted(terms, key=len, reverse=True))
        flat = compile_policy_pattern(rf"(?<!\w)(?:{alternation})(?!\w)", regex.IGNORECASE)
        want = [m.span() for m in flat.finditer(text)]
        assert [m.span() for m in compile_deny_list(terms).finditer(text)] == want, terms
        hits += len(want)
    assert hits > 600


def test_a_matcher_process_charges_its_compile() -> None:
    """The stage-2 child compiles a pattern it has not seen inside the run's charge: its CPU is
    reported (``compile_cpu``) and charged to the call like a build; a cached pattern costs
    nothing to compile again."""
    pool = MatcherPool(size=1)
    try:
        pattern = deny_list_pattern(_random_terms(1000, 50, seed=107))
        first = pool.run(pattern, regex.IGNORECASE | regex.VERSION0, "nothing here", 5.0)
        again = pool.run(pattern, regex.IGNORECASE | regex.VERSION0, "nothing here", 5.0)
    finally:
        pool.close()
    assert first.spans == [] and again.spans == []
    assert first.compile_cpu > 0.005, first.compile_cpu
    assert again.compile_cpu == 0.0


def test_matcher_processes_never_dump_core() -> None:
    """N-r3-3: the CPU backstop's SIGXCPU must not write a core holding request texts."""
    script = (
        "import resource, runpy, sys; "
        f"mod = runpy.run_path({custom.__file__.replace('custom.py', 'pattern_child.py')!r}); "
        "mod['_no_core_dumps'](); print(resource.getrlimit(resource.RLIMIT_CORE)[0])"
    )
    done = subprocess.run(
        [sys.executable, "-I", "-c", script], capture_output=True, text=True, check=True
    )
    assert done.stdout.strip() == "0"
