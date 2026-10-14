"""S3: policy-supplied regexes are refused when they could backtrack catastrophically (ReDoS)."""

from __future__ import annotations

import time

import pytest

from dps.engine.detect.custom import RecognizerSpec, custom_detector_from
from dps.engine.detect.regex_guard import UnsafePattern, check_pattern


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


def test_stacked_glob_stars_collapse() -> None:
    """Adjacent ``*`` in a declared-field glob mean the same as one; compiled as one repeat they
    backtracked polynomially on a long dot-free segment (S3 finder, refuted as a separate issue
    but kept as hardening)."""
    from dps.engine.detect.custom import path_glob

    glob = path_glob("$." + "*" * 25 + "x")
    assert glob.pattern.count("*") == 1
    started = time.monotonic()
    assert glob.fullmatch("$." + "a" * 60) is None
    assert time.monotonic() - started < 0.5
