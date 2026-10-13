"""Wildcard JSON paths over nested documents, and the discovery/transform seams that use them.

A ``TableShape`` column path may carry ``[*]`` (``$.contact.addresses[*].line1``): one column for
every element of an array. These helpers expand such a path against a concrete document, read
every value it addresses, and turn concrete paths into MongoDB dotted notation for ``$set``. SQL
rows are flat (``{column: value}``), so a top-level path reads the row by column name exactly as
M2 did. Nothing here logs or raises a value.
"""

from __future__ import annotations

import re
from typing import Any, Final

from dps.engine.jsonpath import flatten, get_path, parse_path
from dps.layers.connectors.base import NEVER_TRANSFORMED_KINDS, ColumnInfo, Row

_INDEX: Final = re.compile(r"\[\d+\]")
_WILDCARD: Final = "[*]"
_ABSENT: Final = (KeyError, IndexError, TypeError, ValueError)
_ROW_LOCATOR_TYPES: Final = frozenset({"ROWID", "UROWID"})


def generalise(path: str) -> str:
    """Replace every array index with ``[*]`` (``$.a[3].b`` → ``$.a[*].b``)."""
    return _INDEX.sub(_WILDCARD, path)


def expand_wildcards(doc: Any, path: str) -> list[str]:
    """The concrete paths ``path`` addresses in ``doc`` (missing branches yield nothing)."""
    head, sep, tail = path.partition(_WILDCARD)
    if not sep:
        try:
            get_path(doc, path)
        except _ABSENT:
            return []
        return [path]
    try:
        node = get_path(doc, head)
    except _ABSENT:
        return []
    if not isinstance(node, list):
        return []
    out: list[str] = []
    for i in range(len(node)):
        out.extend(expand_wildcards(doc, f"{head}[{i}]{tail}"))
    return out


def values_at(doc: Any, path: str) -> list[Any]:
    """Every value ``path`` (wildcards allowed) addresses in ``doc``, in document order."""
    return [get_path(doc, p) for p in expand_wildcards(doc, path)]


def top_level(column: ColumnInfo) -> bool:
    """True when the column is one key of a flat row (every SQL column, a Mongo top-level field)."""
    return column.path in (f"$.{column.name}", f"$['{column.name}']")


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
        parts = parse_path(path.replace(_WILDCARD, ""))
    except ValueError:
        return None
    out: list[str] = []
    for part in parts:
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
