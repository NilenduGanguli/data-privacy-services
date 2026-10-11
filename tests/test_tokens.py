from __future__ import annotations

import pytest

from dps.engine.transform.tokens import is_envelope, make_envelope, parse_envelopes


def test_make_and_parse_envelopes() -> None:
    text = (
        f"Contact {make_envelope('EMAIL', 'k7fq2wxb3n')} or {make_envelope('PERSON_NAME', 'abcd')}."
    )
    found = parse_envelopes(text)
    assert [(e, t) for _, _, e, t in found] == [("EMAIL", "k7fq2wxb3n"), ("PERSON_NAME", "abcd")]
    start, end, _, _ = found[0]
    assert text[start:end] == "[[PII:EMAIL:k7fq2wxb3n]]"


def test_is_envelope_is_strict() -> None:
    assert is_envelope("[[PII:SSN:abc234]]")
    assert not is_envelope("[[PII:ssn:abc]]") and not is_envelope("x[[PII:SSN:abc234]]")
    assert not is_envelope("[[PII:SSN:ABC1]]")  # upper case / digits 0,1,8,9 are not base32 here


# --- addition beyond the plan (see docs/superpowers/build/M0-DEVIATIONS.md, Task 13) -----------
@pytest.mark.parametrize(
    "entity,token",
    [
        ("ssn", "abcd"),
        ("SSN\n", "abcd"),
        ("SS N", "abcd"),
        ("SSN", "ABCD"),
        ("SSN", "a"),
        ("", "ab"),
    ],
)
def test_make_envelope_refuses_what_parse_cannot_read(entity: str, token: str) -> None:
    with pytest.raises(ValueError):
        make_envelope(entity, token)
