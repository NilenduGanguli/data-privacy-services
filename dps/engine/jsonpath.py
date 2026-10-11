"""Minimal JSON path helpers: flatten to ``(path, leaf)`` pairs, get and set by path.

Paths look like ``$.customer.card.pan``, ``$.tags[0]`` and ``$['odd key']`` — enough for records,
never a query language.
"""

from __future__ import annotations

import re
from typing import Any

# Used with ``fullmatch``: ``re.match`` with a ``$`` anchor also accepts a trailing newline
# ("name\n"), which would emit a bare ``.name\n`` segment that ``parse_path`` cannot read back.
_IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")
_TOKEN = re.compile(r"\.([A-Za-z_][A-Za-z0-9_]*)|\[([0-9]+)\]|\['((?:[^'\\]|\\.)*)'\]")

Scalar = str | int | float | bool | None


def _segment(key: str) -> str:
    if _IDENT.fullmatch(key):
        return f".{key}"
    escaped = key.replace("\\", "\\\\").replace("'", "\\'")
    return f"['{escaped}']"


def flatten(obj: Any, prefix: str = "$") -> list[tuple[str, Scalar]]:
    """Every scalar leaf of ``obj`` with its path, in document order."""
    out: list[tuple[str, Scalar]] = []
    if isinstance(obj, dict):
        for key, value in obj.items():
            out.extend(flatten(value, prefix + _segment(str(key))))
    elif isinstance(obj, list):
        for index, value in enumerate(obj):
            out.extend(flatten(value, f"{prefix}[{index}]"))
    else:
        out.append((prefix, obj))
    return out


def parse_path(path: str) -> list[str | int]:
    """``"$.a.b[1]['k']"`` → ``["a", "b", 1, "k"]``. Raises ``ValueError`` on malformed paths."""
    if not path.startswith("$"):
        raise ValueError(f"path must start with '$': {path!r}")
    rest = path[1:]
    out: list[str | int] = []
    pos = 0
    while pos < len(rest):
        match = _TOKEN.match(rest, pos)
        if not match:
            raise ValueError(f"malformed path at offset {pos + 1}: {path!r}")
        ident, index, quoted = match.groups()
        if ident is not None:
            out.append(ident)
        elif index is not None:
            out.append(int(index))
        else:
            out.append(quoted.replace("\\'", "'").replace("\\\\", "\\"))
        pos = match.end()
    return out


def get_path(obj: Any, path: str) -> Any:
    """Return the value at ``path``; ``KeyError``/``IndexError`` when absent."""
    node = obj
    for part in parse_path(path):
        node = node[part]
    return node


def set_path(obj: Any, path: str, value: Any) -> None:
    """Replace the value at ``path`` in place. The root (``$``) of a scalar document cannot be
    reassigned in place; callers handle ``path == "$"`` themselves."""
    parts = parse_path(path)
    if not parts:
        raise ValueError("cannot set the root path in place")
    node = obj
    for part in parts[:-1]:
        node = node[part]
    node[parts[-1]] = value
