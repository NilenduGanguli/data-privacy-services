"""Wildcard JSON paths over nested documents, and the discovery/transform seams that use them.

A ``TableShape`` column path may carry ``[*]`` (``$.contact.addresses[*].line1``): one column for
every element of an array. These helpers expand such a path against a concrete document, read
every value it addresses, and turn concrete paths into MongoDB dotted notation for ``$set``. SQL
rows are flat (``{column: value}``), so a top-level path reads the row by column name exactly as
M2 did. Nothing here logs or raises a value.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any, Final

from dps.engine.jsonpath import child_path, flatten, get_path
from dps.layers.connectors.base import NEVER_TRANSFORMED_KINDS, ColumnInfo, Row

_INDEX: Final = re.compile(r"\[\d+\]")
_WILDCARD: Final = "[*]"
_ABSENT: Final = (KeyError, IndexError, TypeError, ValueError)
_ROW_LOCATOR_TYPES: Final = frozenset({"ROWID", "UROWID"})
#: One path segment: ``.name``, ``[3]``, ``[*]`` or ``['quoted key']`` (``[*]`` / ``[3]`` inside a
#: quoted key are part of the key — a MongoDB field may be called ``iban[1]``).
_SEGMENT: Final = re.compile(
    r"\.([A-Za-z_][A-Za-z0-9_]*)|\[([0-9]+)\]|\[(\*)\]|\['((?:[^'\\]|\\.)*)'\]"
)
#: A ``[*]`` segment in :func:`path_parts`.
WILDCARD = None
Part = str | int | None


def path_parts(path: str) -> list[Part]:
    """``$.a[*]['b[2]'][0]`` → ``["a", None, "b[2]", 0]`` (``None`` = a ``[*]`` segment).

    Raises:
        ValueError: a malformed path (the message names the offset only).
    """
    if not path.startswith("$"):
        raise ValueError("a path starts with '$'")
    out: list[Part] = []
    pos, rest = 0, path[1:]
    while pos < len(rest):
        match = _SEGMENT.match(rest, pos)
        if not match:
            raise ValueError(f"malformed path at offset {pos + 1}")
        ident, index, star, quoted = match.groups()
        if ident is not None:
            out.append(ident)
        elif index is not None:
            out.append(int(index))
        elif star is not None:
            out.append(WILDCARD)
        else:
            out.append(quoted.replace("\\'", "'").replace("\\\\", "\\"))
        pos = match.end()
    return out


def render(parts: list[Part]) -> str:
    """The canonical path of ``parts`` (keys as :func:`~dps.engine.jsonpath.flatten` spells them)."""
    path = "$"
    for part in parts:
        if part is WILDCARD:
            path += _WILDCARD
        elif isinstance(part, int):
            path += f"[{part}]"
        else:
            path = child_path(path, part)
    return path


def generalise(path: str) -> str:
    """Replace every array index **segment** with ``[*]`` (``$.a[3].b`` → ``$.a[*].b``); text inside
    a quoted key is never an index (``$['iban[1]']`` stays)."""
    try:
        parts = path_parts(path)
    except ValueError:
        return _INDEX.sub(_WILDCARD, path)
    if not any(isinstance(p, int) for p in parts):
        return path
    return render([WILDCARD if isinstance(p, int) else p for p in parts])


def expand_wildcards(doc: Any, path: str) -> list[str]:
    """The concrete paths ``path`` addresses in ``doc`` (missing branches yield nothing)."""
    try:
        parts = path_parts(path)
    except ValueError:
        return []
    if WILDCARD not in parts:
        try:
            get_path(doc, path)
        except _ABSENT:
            return []
        return [path]
    out: list[str] = []

    def walk(node: Any, i: int, at: str) -> None:
        if i == len(parts):
            out.append(at)
            return
        part = parts[i]
        if part is WILDCARD or isinstance(part, int):
            if not isinstance(node, list):
                return
            indexes = range(len(node)) if part is WILDCARD else [part]
            for k in indexes:
                if k < len(node):
                    walk(node[k], i + 1, f"{at}[{k}]")
        elif isinstance(node, Mapping) and part in node:
            walk(node[part], i + 1, child_path(at, part))

    walk(doc, 0, "$")
    return out


def values_at(doc: Any, path: str) -> list[Any]:
    """Every value ``path`` (wildcards allowed) addresses in ``doc``, in document order."""
    return [get_path(doc, p) for p in expand_wildcards(doc, path)]


def top_level(column: ColumnInfo) -> bool:
    """True when the column is one key of a flat row (every SQL column, a Mongo top-level field)."""
    return column.path in (f"$.{column.name}", f"$['{column.name}']")


def column_key(path: str, name: str) -> str:
    """A column's key in the post-write checks (WP-I m3): its name for a column of a flat row
    (every SQL column, a top-level MongoDB field), its (wildcard) path for a nested document
    leaf — MongoDB leaves share their top-level field's name, so a name cannot tell them apart."""
    return name if path in (f"$.{name}", f"$['{name}']") else path


def column_values(rows: list[Row], column: ColumnInfo) -> list[Any]:
    """Every sampled value of ``column``: SQL rows by column name, documents by (wildcard) path.

    A row without the column contributes nothing (a missing field is not a NULL value).
    """
    out: list[Any] = []
    if top_level(column):
        for row in rows:
            if column.name in row:
                out.append(row[column.name])
        return out
    for row in rows:
        out.extend(values_at(row, column.path))
    return out


def row_locator(column: ColumnInfo) -> bool:
    """True for a column that addresses rows instead of holding data: the ``ROWID`` pseudo key of
    a key-less table and real ``ROWID``/``UROWID`` columns. Never classified, not even by name."""
    return column.pseudo or column.data_type.split("(")[0].strip().upper() in _ROW_LOCATOR_TYPES


def forced_keep(column: ColumnInfo) -> bool:
    """Columns classified ``keep`` without value detectors: row locators (a packed ROWID can pass
    a Luhn check), binary data and ObjectIds (contract §4.1: never transformed)."""
    return row_locator(column) or column.kind in NEVER_TRANSFORMED_KINDS


def dotted(path: str) -> str | None:
    """``$.a[0].b`` → ``a.0.b``; ``[*]`` is dropped (MongoDB matches array elements implicitly).

    ``None`` when a segment cannot be dot-addressed (contains ``.``, starts with ``$``, is empty)
    or the path is the root.
    """
    try:
        parts = path_parts(path)
    except ValueError:
        return None
    out: list[str] = []
    for part in parts:
        if part is WILDCARD:
            continue
        if isinstance(part, int):
            out.append(str(part))
        elif not part or "." in part or part.startswith("$"):
            return None
        else:
            out.append(part)
    return ".".join(out) if out else None


def leaf_updates(doc: dict[str, Any]) -> dict[str, Any] | None:
    """``$set`` document of every scalar leaf except ``_id``; None if a leaf is not dot-addressable."""
    updates: dict[str, Any] = {}
    for path, value in flatten(doc):
        if path == "$._id" or path.startswith(("$._id.", "$._id[")):
            continue
        key = dotted(path)
        if key is None:
            return None
        updates[key] = value
    return updates
