"""ReDoS defences for policy-supplied regular expressions (security review S3, round 1 B1).

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
2. **A time budget** (:class:`PatternBudget`, the bound). Policy patterns run on the ``regex``
   module (``VERSION0``: the ``re`` syntax and semantics, plus a matching ``timeout``), never on
   ``re``, which cannot be interrupted. Every call — one API request, one worker handler task
   (:mod:`dps.engine.call_scope`) — gets :data:`BUDGET_BASE_SECONDS` of CPU for its policy
   patterns and earns :data:`BUDGET_PER_CALL_SECONDS` + :data:`BUDGET_PER_CHAR_SECONDS` per
   character for every pattern it runs on a text, so the work is linear in what is scanned
   (a linear pattern runs one to two orders of magnitude under that rate). A pattern that runs
   past what is left is stopped and the call is refused: :class:`PatternTooSlow`, 422
   ``custom_pattern_too_slow`` (value-free; a job fails with that code). Nothing is ever skipped
   silently — a pattern that cannot finish never leaves a value in clear.
"""

from __future__ import annotations

import re
import re._constants as C  # noqa: N812 — stdlib parser constants (Python ≥ 3.11)
import re._parser as P  # noqa: N812
import threading
import time
from contextvars import ContextVar, Token
from typing import Any

import regex

from dps.errors import CodedError

MAX_PATTERN = 512
MAX_BOUND = 1000
OUTER_LIMIT = 3
_REPEATS = (C.MAX_REPEAT, C.MIN_REPEAT, C.POSSESSIVE_REPEAT)

#: CPU seconds a call (request or worker handler) starts with for its policy patterns.
BUDGET_BASE_SECONDS = 1.0
#: CPU seconds a call earns per character, for each pattern it runs on a text.
BUDGET_PER_CHAR_SECONDS = 1e-6
#: CPU seconds a call earns per pattern run (the fixed cost of a call on a short text).
BUDGET_PER_CALL_SECONDS = 50e-6


class UnsafePattern(ValueError):
    """The pattern could backtrack catastrophically or is otherwise refused."""


class PatternTooSlow(CodedError):
    """A policy pattern ran past the call's time budget (round 1 B1); value-free."""

    status = 422

    def __init__(self) -> None:
        super().__init__(
            "custom_pattern_too_slow",
            "a custom recognizer pattern ran past its time budget on this input; simplify it "
            "(no repeat of a repeat, no stacked repeats over the same characters)",
            status=422,
        )


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
    try:
        compile_policy_pattern(pattern)
    except regex.error:
        raise UnsafePattern("pattern is not supported by the matching engine") from None


def compile_policy_pattern(pattern: str, flags: int = 0) -> regex.Pattern[str]:
    """Compile a policy-supplied (or policy-derived) pattern for :meth:`PatternBudget.run`:
    the ``regex`` module in ``VERSION0`` (``re`` semantics)."""
    return regex.compile(pattern, flags | regex.VERSION0)


class PatternBudget:
    """The CPU time policy patterns may still use in one call (module docstring, layer 2).

    Thread-safe: a request's engine work may run on several worker threads. Time is the calling
    thread's CPU time, so waiting for the GIL or another thread's work never counts.
    """

    def __init__(self, base_seconds: float = BUDGET_BASE_SECONDS) -> None:
        self._allowance = base_seconds
        self._spent = 0.0
        self._lock = threading.Lock()

    def earn(self, chars: int, patterns: int) -> None:
        """Credit ``patterns`` pattern runs over a text of ``chars`` characters."""
        if patterns <= 0:
            return
        credit = patterns * (BUDGET_PER_CALL_SECONDS + BUDGET_PER_CHAR_SECONDS * chars)
        with self._lock:
            self._allowance += credit

    @property
    def left(self) -> float:
        """Seconds still available (negative once overspent)."""
        with self._lock:
            return self._allowance - self._spent

    def run(self, pattern: regex.Pattern[str], text: str) -> list[regex.Match[str]]:
        """Every match of ``pattern`` in ``text`` (``finditer`` order), within what is left.

        Raises:
            PatternTooSlow: the budget is spent, or the pattern ran past it on ``text``.
        """
        left = self.left
        if left <= 0:
            raise PatternTooSlow()
        started = time.thread_time()
        try:
            # concurrent: the matcher releases the GIL (a str never changes while it runs)
            return list(pattern.finditer(text, concurrent=True, timeout=left))
        except TimeoutError:
            raise PatternTooSlow() from None
        finally:
            used = time.thread_time() - started
            with self._lock:
                self._spent += used


#: The budget of the current call (bound by :mod:`dps.engine.call_scope`); ``None`` outside a
#: request or a worker handler, where each detector keeps its own.
_CALL_BUDGET: ContextVar[PatternBudget | None] = ContextVar("dps_pattern_budget", default=None)


def bind_pattern_budget() -> Token[PatternBudget | None]:
    """Give the current call a fresh :class:`PatternBudget`; returns the token to reset it."""
    return _CALL_BUDGET.set(PatternBudget())


def reset_pattern_budget(token: Token[PatternBudget | None]) -> None:
    """Undo :func:`bind_pattern_budget` (same context)."""
    _CALL_BUDGET.reset(token)


def call_budget() -> PatternBudget | None:
    """The current call's budget, if one is bound."""
    return _CALL_BUDGET.get()


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
