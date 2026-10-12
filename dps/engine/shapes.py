"""Value shapes (contract §5.4): letters → x/X, digits → 9, everything else kept, ≤ 24 chars.

A shape says what a value looks like (``XX-9999``) without saying what it is; it is the only
form in which sample values may appear in a profile, a snapshot or a prompt.
"""

from __future__ import annotations

MAX_SHAPE_CHARS = 24


def value_shape(value: str, *, max_chars: int = MAX_SHAPE_CHARS) -> str:
    """``"AB-1234"`` → ``"XX-9999"``; ``"ann.k@x.in"`` → ``"xxx.x@x.xx"``."""
    out: list[str] = []
    for ch in value[:max_chars]:
        if ch.isdigit():
            out.append("9")
        elif ch.isalpha():
            out.append("X" if ch.isupper() else "x")
        else:
            out.append(ch)
    return "".join(out)
