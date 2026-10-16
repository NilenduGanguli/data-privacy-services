"""S3 review round 2: how a policy pattern is run and charged (``regex_guard.PatternBudget``).

A run starts in the calling thread holding the GIL for a short slice; a run that needs longer
continues in a matcher process (``pattern_process``), where the ``regex`` timeout counts only that
run. These tests pin the properties the budget relies on: a run gives the same spans wherever it
runs (``re``'s spans), a run is charged its own CPU and nothing else, a run never exceeds the run
cap, a dead matcher refuses its run (fail closed) and is replaced, an NDJSON item cannot spend
what other items earned, and a deny list is matched as a trie with the flat alternation's exact
results at a cost per character that does not grow with the number of terms.

Every text and term here is synthetic.
"""

from __future__ import annotations

import os
import random
import re
import signal
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager

import pytest
import regex

from dps.engine.detect import pattern_process, regex_guard
from dps.engine.detect.custom import (
    PolicyCustomDetector,
    RecognizerSpec,
    compile_deny_list,
    deny_list_pattern,
)
from dps.engine.detect.pattern_process import POOL, MatcherFailed, MatcherPool
from dps.engine.detect.regex_guard import (
    BUDGET_BASE_SECONDS,
    BUDGET_PER_CHAR_SECONDS,
    PatternBudget,
    PatternTooSlow,
    budget_scope,
    compile_policy_pattern,
)

_ATTACK = r"\d+\d+\d+[x!]"
_PARITY = [
    r"\bEMP-\d{6}\b",
    r"[A-Z]{5}\d{4}[A-Z]",
    r"(?:\+91[ -]?)?\d{10}",
    r"\b\d{3}-\d{2}-\d{4}\b",
    r"(?i)\bacct[ #:]*\d+",
    r"\d*",
    r"(?<!\d)\d{12}(?!\d)",
    r"\w+@\w+\.\w+",
    r".",
]
_TEXTS = [
    "EMP-123456 ABCDE1234F +91 9876543210 123-45-6789 10.0.0.1 Mr. Smith acct #: 991",
    "Ms  Ünal 123456789012 1234567890123 jane@bank.com ab 12 ÉMP-000001 \U0001f600 x",
    "",
    "١٢٣ digits ٤٥٦ and EMP-654321 \ud800 lone surrogate 4444",
]


@contextmanager
def _busy_python_thread() -> Iterator[None]:
    stop = threading.Event()

    def spin() -> None:
        x = 0
        while not stop.is_set():
            x = (x * 31 + 7) % 1_000_003

    thread = threading.Thread(target=spin, daemon=True)
    thread.start()
    time.sleep(0.05)
    try:
        yield
    finally:
        stop.set()
        thread.join()


@pytest.mark.parametrize("pattern", _PARITY)
def test_a_run_in_a_matcher_process_gives_re_s_spans(pattern: str) -> None:
    """A matcher process returns ``re``'s spans (astral characters and lone surrogates
    included): moving a run out of the request thread never changes what it finds."""
    compiled = compile_policy_pattern(pattern)
    for text in _TEXTS:
        want = [m.span() for m in re.finditer(pattern, text)]
        result = POOL.run(compiled.pattern, compiled.flags, text, 5.0)
        assert result.spans == want, (pattern, text)


def test_runs_that_outgrow_the_slice_continue_in_a_matcher_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no in-thread slice to speak of, every run moves to a matcher process: same spans,
    charged with the matcher's CPU."""
    moved: list[int] = []
    real = MatcherPool.run

    def counting(self: MatcherPool, *args: object, **kw: object) -> object:
        moved.append(1)
        return real(self, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(regex_guard, "SLICE_SECONDS", 1e-9)
    monkeypatch.setattr(regex_guard, "MIN_ESCALATION_SECONDS", 0.0)
    monkeypatch.setattr(MatcherPool, "run", counting)
    long_text = " ".join(_TEXTS) * 200
    for pattern in _PARITY:
        compiled = compile_policy_pattern(pattern)
        budget = PatternBudget(base_seconds=10.0)
        want = [m.span() for m in re.finditer(pattern, long_text)]
        assert budget.run(compiled, long_text) == want, pattern
        assert budget.spent > 0
    assert moved  # the runs did leave the thread


def test_a_run_is_charged_its_own_cpu_while_python_threads_are_busy() -> None:
    """The stacked-repeat attack under a 0.3 s allowance, with a Python thread spinning: it is
    refused once **its own** CPU reaches the allowance — not earlier (other threads' CPU), not much
    later — and the spinning thread never starves it."""
    budget = PatternBudget(base_seconds=0.3)
    with _busy_python_thread():
        started = time.perf_counter()
        with pytest.raises(PatternTooSlow):
            budget.run(compile_policy_pattern(_ATTACK), "1" * 3000)
        took = time.perf_counter() - started
    assert 0.25 <= budget.spent <= 0.4, budget.spent
    assert took < 2.0, took


def test_no_single_run_outlives_the_run_cap() -> None:
    """A worker handler's allowance grows with what it scans (no total ceiling), but no single
    run takes longer than ``DPS_PATTERN_CPU_SECONDS``."""
    budget = PatternBudget(base_seconds=100.0, run_cap=0.3)
    started = time.perf_counter()
    with pytest.raises(PatternTooSlow):
        budget.run(compile_policy_pattern(_ATTACK), "1" * 3000)
    assert budget.spent <= 0.4, budget.spent
    assert time.perf_counter() - started < 2.0


def test_a_dead_matcher_refuses_its_run_and_is_replaced(monkeypatch: pytest.MonkeyPatch) -> None:
    """A matcher process killed mid-run: the run is refused (fail closed, never skipped), the
    process is reaped, and the next run gets a fresh one."""
    pool = MatcherPool(size=1)
    started: list[object] = []
    real_init = pattern_process._Matcher.__init__

    def recording(self: pattern_process._Matcher) -> None:
        real_init(self)
        started.append(self)

    monkeypatch.setattr(pattern_process._Matcher, "__init__", recording)
    outcome: list[object] = []
    compiled = compile_policy_pattern(_ATTACK)

    def run() -> None:
        try:
            outcome.append(pool.run(compiled.pattern, compiled.flags, "1" * 5000, 30.0))
        except MatcherFailed as exc:
            outcome.append(exc)

    worker = threading.Thread(target=run)
    worker.start()
    deadline = time.monotonic() + 10
    while not started and time.monotonic() < deadline:
        time.sleep(0.01)
    time.sleep(0.2)  # mid-run
    victim = started[0]
    os.kill(victim.proc.pid, signal.SIGKILL)  # type: ignore[attr-defined]
    worker.join(10)
    assert not worker.is_alive()
    assert len(outcome) == 1 and isinstance(outcome[0], MatcherFailed)
    assert victim.proc.poll() is not None  # type: ignore[attr-defined]
    again = pool.run(r"\d+", regex.VERSION0, "a 12 b 345", 5.0)
    assert again.spans == [(2, 4), (7, 10)]
    assert len(started) == 2
    pool.close()


def test_a_matcher_failure_refuses_the_call(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(*_args: object, **_kw: object) -> object:
        raise MatcherFailed

    monkeypatch.setattr(MatcherPool, "run", broken)  # the class: an instance patch would linger
    with pytest.raises(PatternTooSlow):
        PatternBudget(base_seconds=1.0).run(compile_policy_pattern(_ATTACK), "1" * 3000)


def test_an_item_spends_only_what_it_earned_plus_the_base() -> None:
    """R2-B2: an NDJSON item's budget draws on the stream, but never on what other items
    earned; the stream as a whole stays within its base plus what its items earned."""
    call = PatternBudget(ceiling=5.0, run_cap=5.0)
    stream = call.for_stream()
    first = stream.item()
    first.earn(10_000, 10)  # a benign item: 10 runs over 10,000 characters
    assert first.left == pytest.approx(BUDGET_BASE_SECONDS + 10 * (20e-6 + 0.5e-6 * 10_000))
    second = stream.item()
    assert second.left == pytest.approx(BUDGET_BASE_SECONDS)  # nothing of the first's
    second.earn(2_000, 1)
    with pytest.raises(PatternTooSlow):
        second.run(compile_policy_pattern(_ATTACK), "1" * 2000)
    assert second.spent <= BUDGET_BASE_SECONDS + 20e-6 + 0.5e-6 * 2000 + 0.002
    huge = stream.item()
    huge.earn(100_000_000, 1000)
    assert huge.left == pytest.approx(5.0, abs=0.01)  # an item is capped like a request
    with budget_scope(huge):
        assert regex_guard.call_budget() is huge
    assert regex_guard.call_budget() is not huge


def _flat_deny(terms: list[str]) -> regex.Pattern[str]:
    """The deny-list pattern as built before round 2 (the reference semantics)."""
    alternation = "|".join(re.escape(w) for w in sorted(terms, key=len, reverse=True))
    return compile_policy_pattern(rf"(?<!\w)(?:{alternation})(?!\w)", regex.IGNORECASE)


def test_deny_lists_match_exactly_like_the_flat_alternation() -> None:
    """Differential check of the trie against the flat alternation on random term sets with
    shared prefixes, case variants and letters whose case forms differ (``ß``/``ẞ``, ``ı``/``I``,
    ``ſ``, ``ς``, the Kelvin sign) — same spans, same order."""
    rnd = random.Random(20261015)
    alphabet = ["a", "b", "A", "B", "c", " ", "-", ".", "é", "É", "ß", "ẞ", "ı", "I", "i", "ſ", "s"]
    alphabet += ["ς", "σ", "Σ", "K", "k", "1", "2"]
    checked = hits = 0
    for _ in range(400):
        terms = sorted(
            {
                "".join(rnd.choice(alphabet) for _ in range(rnd.randint(1, 6))).strip(" ") or "x"
                for _ in range(rnd.randint(1, 12))
            }
        )
        text = " ".join(
            rnd.choice(terms) if rnd.random() < 0.5 else rnd.choice(alphabet) * rnd.randint(1, 3)
            for _ in range(rnd.randint(1, 25))
        )
        want = [m.span() for m in _flat_deny(terms).finditer(text)]
        got = [m.span() for m in compile_deny_list(terms).finditer(text)]
        assert got == want, (terms, text)
        checked += 1
        hits += len(want)
    assert checked == 400 and hits > 400


def test_deep_deny_lists_fall_back_to_flat_endings() -> None:
    """Terms that share a prefix longer than the trie's depth keep matching exactly."""
    terms = ["x" * n for n in range(1, 130)] + ["y" * 60 + "z", "Y" * 60]
    text = " ".join(["x" * 129, "x" * 77, "y" * 60, "y" * 60 + "Z", "x" * 131])
    want = [m.span() for m in _flat_deny(terms).finditer(text)]
    got = [m.span() for m in compile_deny_list(terms).finditer(text)]
    assert got == want and len(want) == 4
    assert "(?i:" in deny_list_pattern(terms)  # the endings below the trie's depth, flat


def test_a_deny_list_costs_the_same_per_character_whatever_its_length() -> None:
    """R2-B2: a 1000-name deny list costs well under a quarter of what it earns per character,
    and a fraction of the flat alternation's cost (0.7-0.9 µs per character, the round-1 matcher's
    against 1 µs earned)."""
    rnd = random.Random(7)
    syllables = ["ka", "vo", "ren", "tal", "mi", "zor", "qui", "lon", "dar", "sev", "nu", "pex"]
    names = sorted(
        {
            f"{''.join(rnd.choice(syllables) for _ in range(2)).capitalize()} "
            f"{''.join(rnd.choice(syllables) for _ in range(3)).capitalize()} {rnd.randint(1, 99)}"
            for _ in range(1100)
        }
    )[:1000]
    words = "the customer called about the loan account and the branch said it closed".split()
    text = " ".join(rnd.choice(words) for _ in range(60_000))
    costs: dict[str, float] = {}
    assert not compile_deny_list(names).flags & regex.IGNORECASE  # the trie, not the fallback
    for label, compiled in (("trie", compile_deny_list(names)), ("flat", _flat_deny(names))):
        started = time.thread_time()
        list(compiled.finditer(text))
        costs[label] = (time.thread_time() - started) / len(text)
    assert costs["trie"] < BUDGET_PER_CHAR_SECONDS / 4, costs
    assert costs["trie"] < costs["flat"] / 4, costs


def test_the_detector_scans_deny_lists_through_the_trie() -> None:
    spec = RecognizerSpec(
        name="vip",
        supported_entity="VIP_NAME",
        deny_list=["Kavo Rentalmi 12", "kavo", "Zorqui Lon"],
        allow_list=["KAVO"],
    )
    hits = PolicyCustomDetector([spec], []).scan(
        "kavo rentalmi 12 met KAVO and zorqui lon, kavo", None
    )
    assert [(h.start, h.end) for h in hits] == [(0, 16), (30, 40), (42, 46)]


def test_a_pattern_that_outgrew_the_slice_goes_straight_to_a_matcher_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Routing: once a pattern's run had to leave the thread, a later run that would outgrow the
    slice again starts in a matcher process (no slice spent, no GIL held); a pattern never seen
    there, or a short text, still runs in the thread first."""
    moved: list[int] = []
    real = MatcherPool.run

    def counting(self: MatcherPool, *args: object, **kw: object) -> object:
        moved.append(1)
        return real(self, *args, **kw)  # type: ignore[arg-type]

    monkeypatch.setattr(MatcherPool, "run", counting)
    compiled = compile_policy_pattern(r"\bROUTE-\d{4}\b")
    budget = PatternBudget(base_seconds=1.0)
    assert budget.run(compiled, "x ROUTE-1234 y") == [(2, 12)]
    assert moved == []  # unknown pattern, short text: in the thread
    monkeypatch.setitem(
        regex_guard._HEAVY, (compiled.pattern, compiled.flags), regex_guard.SLICE_SECONDS
    )
    assert budget.run(compiled, "x ROUTE-1234 y") == [(2, 12)]  # predicted heavy: moved
    assert moved == [1]
    assert regex_guard._HEAVY[(compiled.pattern, compiled.flags)] < regex_guard.SLICE_SECONDS
    assert budget.run(compiled, "x ROUTE-1234 y") == [(2, 12)]  # measured light again: thread
    assert moved == [1]


def test_a_deny_list_of_turkish_i_variants_cannot_blow_up_the_trie() -> None:
    """The Turkish i's match each other without being equal, so their branches are copied; terms
    made only of them would copy at every level (3^40 here). The trie's construction is bounded
    and such a list is matched flat — quickly, with the same results."""
    rnd = random.Random(3)
    terms = sorted({"".join(rnd.choice("Iiıİ") for _ in range(40)) for _ in range(300)})
    started = time.perf_counter()
    compiled = compile_deny_list(terms)
    assert time.perf_counter() - started < 5.0
    assert compiled.flags & regex.IGNORECASE  # the flat form: the trie passed its bound
    turkish = ["Işık Yıldız", "İsmail Iğdır", "Ilgaz İnci", *terms[:3]][:3]
    assert not compile_deny_list(turkish).flags & regex.IGNORECASE  # a few such names: a trie
    text = " ".join([terms[0], terms[5].upper(), "".join(reversed(terms[7])), terms[9]])
    want = [m.span() for m in _flat_deny(terms).finditer(text)]
    assert [m.span() for m in compiled.finditer(text)] == want and want
