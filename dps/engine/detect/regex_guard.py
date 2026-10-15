"""ReDoS defences for policy-supplied regular expressions (security review S3, rounds 1 to 3).

Two layers:

1. **A static first filter** (:func:`check_pattern`, run when a policy is validated). Patterns are
   parsed with the stdlib parser and refused when: longer than 512 characters; they use
   backreferences or conditional groups; a repeat is bounded above 1000; an unbounded repeat sits
   inside another repeat whose upper bound exceeds 3 (``(?:\\d+\\.){3}`` stays legal); or an
   alternation with an empty branch sits inside such a repeat (the parser factors ``(a|aa)+`` into
   ``a(|a)``, the same ambiguity). Error messages name the rule, never the pattern. This catches
   the classic shapes early, with a clear message, but it is a heuristic: stacked repeats over the
   same characters (``\\w*\\w*\\w*!``, ``.*a.*a.*b``, ``\\d+\\d+\\d+x``) backtrack polynomially and
   pass it.
2. **A CPU budget** (:class:`PatternBudget`, the bound). Policy patterns run on the ``regex``
   module (``VERSION0``: the ``re`` syntax, plus a matching ``timeout``; a few Unicode details
   differ from ``re`` — the security review's §5), never on ``re``, which cannot be interrupted.
   A run is charged **its own CPU time** and stopped on it alone (review round 2, R2-B1 — the
   ``regex`` timeout counts the CPU of the whole process, so in a busy server it fired on other
   requests' work, and a matcher that released the GIL was starved by any thread running Python):

   * it starts in the calling thread **holding the GIL** for at most :data:`SLICE_SECONDS` —
     while it holds the GIL no other Python thread runs, so the process clock is the run's own,
     and no other thread waits longer than the slice;
   * a run that needs longer (a long text, or a pattern that backtracks) continues in a matcher
     process (:mod:`dps.engine.detect.pattern_process`): one run at a time on one thread, so there
     the ``regex`` timeout is exactly the run's CPU, and the server's threads stay free. Such runs
     occupy at most ``DPS_PATTERN_PROCESSES`` CPUs of a server process, however many arrive; a run
     waits at most ``pattern_process.ACQUIRE_TIMEOUT_SECONDS`` for one, then the call is refused
     with 503 ``pattern_matcher_busy`` (:class:`PatternMatcherBusy`, retryable). A pattern whose
     last such run predicts it would outgrow the slice again starts there directly.
   * a run never happens on the event loop that serves the request (review round 3, R3-B1): the
     routes run their engine work on worker threads, and a run (or a deny-list build) on the
     loop's own thread is refused as a server defect (:func:`ensure_off_serving_loop`, a
     value-free 500) — a pattern can never freeze the server, ``/health`` included.

   A deny list's build — the trie's text and its compile, cached by its terms — is charged to the
   call that makes it (review round 3, R3-B2): its CPU counts toward the call's ceiling (never
   toward the run allowance, which stays what the call's texts earn), and no list is built once
   the call has spent its budget. A build is linear in the list (about 1.3 µs per term character
   on the build Mac), so a request builds at most a ceiling's worth. A matcher process charges its
   own compile of a pattern it has not seen the same way.

   The allowance: every call — one API request, one NDJSON item, one worker handler task
   (:mod:`dps.engine.call_scope`) — starts with :data:`BUDGET_BASE_SECONDS` and earns, for every
   pattern it runs on a text, :data:`BUDGET_PER_RUN_SECONDS` + :data:`BUDGET_PER_CHAR_SECONDS` per
   character (legitimate patterns measure 0.002–0.1 µs per character, a 1000-name deny list about
   0.05 µs — it is matched as a trie). An API request, and each NDJSON item, may spend at most
   ``DPS_PATTERN_CPU_SECONDS`` in total; an NDJSON item spends only what it earned itself (plus the
   stream's base), never what earlier items earned; and no single run anywhere — L2 jobs included
   — runs longer than ``DPS_PATTERN_CPU_SECONDS``. A pattern that runs past what is left is stopped
   and the call is refused: :class:`PatternTooSlow`, 422 ``custom_pattern_too_slow`` (value-free; a
   job fails with that code). Nothing is ever skipped silently — a pattern that cannot finish never
   leaves a value in clear.
"""

from __future__ import annotations

import re
import re._constants as C  # noqa: N812 — stdlib parser constants (Python ≥ 3.11)
import re._parser as P  # noqa: N812
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from typing import Any

import regex

from dps.engine.detect.pattern_process import POOL, MatcherBusy, MatcherFailed
from dps.errors import CodedError
from dps.observability import get_logger

logger = get_logger(__name__)

MAX_PATTERN = 512
MAX_BOUND = 1000
OUTER_LIMIT = 3
_REPEATS = (C.MAX_REPEAT, C.MIN_REPEAT, C.POSSESSIVE_REPEAT)

#: CPU seconds a call (request, NDJSON item, worker handler) starts with for its policy patterns.
BUDGET_BASE_SECONDS = 0.01
#: CPU seconds a call earns per pattern run (the fixed cost of a run on a short text).
BUDGET_PER_RUN_SECONDS = 20e-6
#: CPU seconds a call earns per character, for each pattern it runs on a text.
BUDGET_PER_CHAR_SECONDS = 0.5e-6
#: CPU seconds a run may take in the calling thread (holding the GIL) before it moves to a
#: matcher process.
SLICE_SECONDS = 0.005
#: a run with less than this left after its slice is refused without a matcher-process round trip
MIN_ESCALATION_SECONDS = 0.001
#: ``DPS_PATTERN_CPU_SECONDS``'s default: the most an API request (or NDJSON item) may spend in
#: total, and the most any single run may take.
DEFAULT_CPU_SECONDS = 5.0
#: CPU per character of each pattern's last run that went to a matcher process, by (source,
#: flags): a later run that would outgrow the slice again goes there directly (no slice spent,
#: no GIL held). Routing only — every run is charged what it took, wherever it ran.
_HEAVY: dict[tuple[str, int], float] = {}
_HEAVY_MAX = 4096


class UnsafePattern(ValueError):
    """The pattern could backtrack catastrophically or is otherwise refused."""


class PatternTooSlow(CodedError):
    """A policy pattern ran past the call's CPU budget (review rounds 1 and 2); value-free."""

    status = 422

    def __init__(self) -> None:
        super().__init__(
            "custom_pattern_too_slow",
            "a custom recognizer pattern ran past its CPU budget on this input; simplify it "
            "(no repeat of a repeat, no stacked repeats over the same characters) or send less "
            "text per call",
            status=422,
        )


class PatternMatcherBusy(CodedError):
    """Every matcher process stayed busy for the wait's bound (review round 3); value-free and
    retryable — the call did nothing wrong."""

    status = 503

    def __init__(self) -> None:
        super().__init__(
            "pattern_matcher_busy",
            "every matcher process for custom recognizer patterns stayed busy; retry later",
            status=503,
        )


class PatternOnEventLoop(RuntimeError):
    """A policy pattern was about to run, or a deny list to be built, on the event loop that
    serves the request (review round 3, R3-B1). A server defect: the call is refused (a value-free
    500) rather than the server frozen for every caller."""


#: The thread that runs the event loop serving the current request (bound by the app's call
#: scope); ``None`` outside a request (tools, the worker, the browser-worker).
_SERVING_THREAD: ContextVar[int | None] = ContextVar("dps_serving_thread", default=None)


def bind_serving_thread() -> Token[int | None]:
    """Record the current thread as the one running the serving event loop (the app's call
    scope, entered on that loop); returns the token to reset it."""
    return _SERVING_THREAD.set(threading.get_ident())


def reset_serving_thread(token: Token[int | None]) -> None:
    """Undo :func:`bind_serving_thread` (same context)."""
    _SERVING_THREAD.reset(token)


def ensure_off_serving_loop() -> None:
    """Refuse to run a policy pattern (or build a deny list) on the serving event loop's thread:
    there it would freeze every request, liveness checks included (R3-B1).

    Raises:
        PatternOnEventLoop: the current thread runs the loop serving this request.
    """
    if _SERVING_THREAD.get() == threading.get_ident():
        logger.error("pattern_on_event_loop")
        raise PatternOnEventLoop("policy patterns never run on the serving event loop")


def check_pattern(pattern: str) -> None:
    """Refuse a pattern that could backtrack catastrophically (module docstring, layer 1) or
    that the matching engine cannot run.

    Raises:
        UnsafePattern: the rule the pattern breaks (value-free message).
    """
    if len(pattern) > MAX_PATTERN:
        raise UnsafePattern(f"pattern is longer than {MAX_PATTERN} characters")
    try:
        tree = P.parse(pattern)
    except re.error as exc:
        raise UnsafePattern(f"pattern does not compile: {exc.msg}") from None
    _walk(tree, outer_bound=1)
    rule = _engine_only_syntax(pattern)
    if rule is not None:
        raise UnsafePattern(rule)
    try:
        compile_policy_pattern(pattern)
    except regex.error:
        raise UnsafePattern("pattern is not supported by the matching engine") from None


#: What ``re`` reads as a repeat count: ``{3}``, ``{3,}``, ``{3,5}``, ``{,5}``, ``{,}``.
_REPEAT_COUNT = re.compile(r"\{(?:\d+(?:,\d*)?|,\d*)\}")


def _engine_only_syntax(pattern: str) -> str | None:
    """The rule a pattern breaks if the matching engine would read it differently from ``re``
    (review round 2, N-r2-1), else ``None``: an unescaped ``{`` that is not a repeat count — ``re``
    matches it literally, the ``regex`` module may read a fuzzy-match constraint
    (``(?:ACCT1234){e<=2}``) — and an unescaped ``[`` inside a set (``[[:digit:]]`` is a POSIX
    class there, a set followed by ``]`` in ``re``)."""
    i, size, in_set = 0, len(pattern), False
    while i < size:
        char = pattern[i]
        if char == "\\":
            i += 2
            continue
        if in_set:
            if char == "]":
                in_set = False
            elif char == "[":
                return "a [ inside a set must be escaped as \\["
            i += 1
            continue
        if char == "[":
            in_set = True
            i += 1
            if i < size and pattern[i] == "^":
                i += 1
            if i < size and pattern[i] == "]":
                i += 1  # a ] first in a set is literal
            continue
        if char == "{" and not _REPEAT_COUNT.match(pattern, i):
            return "a { that is not a repeat count must be escaped as \\{"
        i += 1
    return None


def compile_policy_pattern(pattern: str, flags: int = 0) -> regex.Pattern[str]:
    """Compile a policy-supplied (or policy-derived) pattern for :meth:`PatternBudget.run`:
    the ``regex`` module in ``VERSION0`` (``re`` syntax)."""
    return regex.compile(pattern, flags | regex.VERSION0)


class PatternBudget:
    """The CPU time policy patterns may still use in one call (module docstring, layer 2).

    Thread-safe: a call's engine work may run on several threads. Time is each run's own CPU:
    the calling thread's while it holds the GIL, then the matcher process's.

    Args:
        base_seconds: what the call starts with.
        ceiling: the most the call may spend in total (an API request or an NDJSON item), or
            ``None`` (a worker handler task: its allowance grows with what it scans).
        run_cap: the most any single run may take.
        parent: the stream an NDJSON item belongs to: the item also draws on (and is limited by)
            what the stream has left, so it cannot spend what earlier items earned.
        item_ceiling: the ``ceiling`` of the items of a stream built by :meth:`for_stream`.
    """

    def __init__(
        self,
        base_seconds: float = BUDGET_BASE_SECONDS,
        *,
        ceiling: float | None = None,
        run_cap: float = DEFAULT_CPU_SECONDS,
        parent: PatternBudget | None = None,
        item_ceiling: float | None = None,
    ) -> None:
        self.base = base_seconds
        self.ceiling = ceiling
        self.run_cap = run_cap
        self.parent = parent
        self.item_ceiling = item_ceiling
        self._earned = 0.0
        self._spent = 0.0
        self._lock = threading.Lock()

    def for_stream(self) -> PatternBudget:
        """A budget for a stream of items (NDJSON) started inside this call: no total ceiling,
        its items get this call's ceiling (:meth:`item`)."""
        return PatternBudget(run_cap=self.run_cap, item_ceiling=self.ceiling)

    def item(self) -> PatternBudget:
        """The budget of one item of this stream."""
        return PatternBudget(ceiling=self.item_ceiling, run_cap=self.run_cap, parent=self)

    def earn(self, chars: int, patterns: int) -> None:
        """Credit ``patterns`` pattern runs over a text of ``chars`` characters."""
        if patterns <= 0:
            return
        self._credit(patterns * (BUDGET_PER_RUN_SECONDS + BUDGET_PER_CHAR_SECONDS * chars))

    def _credit(self, seconds: float) -> None:
        with self._lock:
            self._earned += seconds
        if self.parent is not None:
            self.parent._credit(seconds)

    def _charge(self, seconds: float) -> None:
        with self._lock:
            self._spent += seconds
        if self.parent is not None:
            self.parent._charge(seconds)

    def charge_build(self, used: float) -> None:
        """Charge a deny-list build (or a matcher process's compile) that took ``used`` CPU seconds
        (R3-B2): it counts toward the ceiling, never toward the run allowance — a build is linear
        in the list and its timing includes whatever the interpreter did meanwhile (a garbage
        collection), so it is bounded by the ceiling, not judged against the texts' earnings."""
        self._credit(used)
        self._charge(used)

    @property
    def spent(self) -> float:
        """CPU seconds this call's runs and deny-list builds have taken."""
        with self._lock:
            return self._spent

    @property
    def left(self) -> float:
        """Seconds still available (zero or negative once spent)."""
        with self._lock:
            own = self.base + self._earned - self._spent
            if self.ceiling is not None:
                own = min(own, self.ceiling - self._spent)
        if self.parent is not None:
            own = min(own, self.parent.left)
        return own

    def run(self, pattern: regex.Pattern[str], text: str) -> list[tuple[int, int]]:
        """The span of every match of ``pattern`` in ``text`` (``finditer`` order), within what is
        left (module docstring, layer 2).

        Raises:
            PatternTooSlow: the budget is spent, or the pattern ran past it on ``text``.
            PatternMatcherBusy: the run needed a matcher process and none became free in time.
            PatternOnEventLoop: called on the event loop serving the request (a server defect).
        """
        ensure_off_serving_loop()
        left = min(self.left, self.run_cap)
        if left <= 0:
            raise PatternTooSlow()
        key = (pattern.pattern, pattern.flags)
        heavy = _HEAVY.get(key)
        used = 0.0
        if heavy is None or heavy * len(text) < SLICE_SECONDS / 2:
            started = time.thread_time()
            matches: list[regex.Match[str]] | None
            try:
                # holding the GIL: the process clock of the timeout is this run's own CPU
                matches = list(
                    pattern.finditer(text, concurrent=False, timeout=min(SLICE_SECONDS, left))
                )
            except TimeoutError:
                matches = None
            used = time.thread_time() - started
            self._charge(used)
            if matches is not None:
                return [m.span() for m in matches]
            # the slice ran out (or a thread outside the GIL used the process clock)
            left = min(self.left, self.run_cap - used)
            if left < MIN_ESCALATION_SECONDS:
                raise PatternTooSlow()
        # the run goes on in a matcher process, where the clock is its own
        try:
            result = POOL.run(pattern.pattern, pattern.flags, text, left)
        except MatcherBusy:
            raise PatternMatcherBusy() from None
        except MatcherFailed:
            logger.error("pattern_matcher_failed")
            raise PatternTooSlow() from None
        if result.compile_cpu:  # a pattern the matcher had not seen: charged like a build
            self.charge_build(result.compile_cpu)
        self._charge(result.cpu)
        if len(_HEAVY) >= _HEAVY_MAX:
            _HEAVY.clear()
        _HEAVY[key] = (used + result.cpu) / max(1, len(text))
        if result.spans is None:
            raise PatternTooSlow()
        return result.spans


#: The budget of the current call (bound by :mod:`dps.engine.call_scope`); ``None`` outside a
#: request or a worker handler, where each detector keeps its own.
_CALL_BUDGET: ContextVar[PatternBudget | None] = ContextVar("dps_pattern_budget", default=None)


def bind_pattern_budget(
    *, ceiling: float | None = None, run_cap: float = DEFAULT_CPU_SECONDS
) -> Token[PatternBudget | None]:
    """Give the current call a fresh :class:`PatternBudget`; returns the token to reset it."""
    return _CALL_BUDGET.set(PatternBudget(ceiling=ceiling, run_cap=run_cap))


def reset_pattern_budget(token: Token[PatternBudget | None]) -> None:
    """Undo :func:`bind_pattern_budget` (same context)."""
    _CALL_BUDGET.reset(token)


def call_budget() -> PatternBudget | None:
    """The current call's budget, if one is bound."""
    return _CALL_BUDGET.get()


@contextmanager
def budget_scope(budget: PatternBudget) -> Iterator[PatternBudget]:
    """Make ``budget`` the current call's for the ``with`` block (an NDJSON item: ``with
    budget_scope(stream.item()):``), then restore the previous one."""
    token = _CALL_BUDGET.set(budget)
    try:
        yield budget
    finally:
        _CALL_BUDGET.reset(token)


def _children(op: Any, av: Any) -> list[Any]:
    if op in _REPEATS:
        return [av[2]]
    if op is C.SUBPATTERN:
        return [av[3]]
    if op is C.BRANCH:
        return list(av[1])
    if op in (C.ASSERT, C.ASSERT_NOT):
        return [av[1]]
    if op is C.ATOMIC_GROUP:
        return [av]
    return []


def _walk(sub: Any, *, outer_bound: int) -> None:
    for op, av in sub:
        if op in (C.GROUPREF, C.GROUPREF_EXISTS):
            raise UnsafePattern("backreferences and conditional groups are not allowed")
        if op in _REPEATS:
            _lo, hi, body = av
            unbounded = hi == C.MAXREPEAT
            if not unbounded and hi > MAX_BOUND:
                raise UnsafePattern(f"repeat bounds above {MAX_BOUND} are not allowed")
            if unbounded and outer_bound > OUTER_LIMIT:
                raise UnsafePattern(
                    "nested quantifiers (an unbounded repeat inside a repeat) are not allowed"
                )
            _walk(body, outer_bound=max(outer_bound, C.MAXREPEAT if unbounded else hi))
            continue
        if op is C.BRANCH and outer_bound > OUTER_LIMIT and any(not alt for alt in av[1]):
            raise UnsafePattern(
                "an alternation with an empty branch inside a repeat is not allowed "
                "(write the alternatives without a shared prefix)"
            )
        for child in _children(op, av):
            _walk(child, outer_bound=outer_bound)
