"""Static ReDoS guard for policy-supplied regular expressions (security review S3).

Python's ``re`` has no timeout, so a pattern with nested unbounded quantifiers — ``(a+)+``,
``(\\w+\\s?)*``, ``(.*x){12}`` — can pin a CPU on a crafted input. Patterns are parsed with the
stdlib parser and refused when: longer than 512 characters; they use backreferences or
conditional groups; a repeat is bounded above 1000; an unbounded repeat sits inside another
repeat whose upper bound exceeds 3 (``(?:\\d+\\.){3}`` stays legal); or an alternation with an
empty branch sits inside such a repeat (the parser factors ``(a|aa)+`` into ``a(|a)``, the same
ambiguity). Error messages name the rule, never the pattern.

The guard is a heuristic: other ambiguous alternations can still backtrack, which the length
caps on what a pattern is matched against (``max_text_chars``) bound (security review residual).
"""

from __future__ import annotations

import re
import re._constants as C  # noqa: N812 — stdlib parser constants (Python ≥ 3.11)
import re._parser as P  # noqa: N812
from typing import Any

MAX_PATTERN = 512
MAX_BOUND = 1000
OUTER_LIMIT = 3
_REPEATS = (C.MAX_REPEAT, C.MIN_REPEAT, C.POSSESSIVE_REPEAT)


class UnsafePattern(ValueError):
    """The pattern could backtrack catastrophically or is otherwise refused."""


def check_pattern(pattern: str) -> None:
    """Refuse a pattern that could backtrack catastrophically (module docstring).

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
