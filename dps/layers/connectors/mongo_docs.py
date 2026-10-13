"""BSON-aware helpers for the MongoDB connector (pure, offline).

Type labels, a path shape inferred from a ``$sample`` (one column per generalised leaf path,
``[*]`` for array elements), and type restoration before writes, so a masked copy keeps its
``Int64``, ``Decimal128``, date and ``ObjectId`` fields as their BSON types after the engine turned
values into text and back. Nothing here logs or raises a value.
"""

from __future__ import annotations

import datetime as dt
import decimal
import re
import uuid
from collections import Counter
from typing import Any, Final, Literal

from bson import Binary, Decimal128, Int64, ObjectId

from dps.engine.jsonpath import flatten, get_path, parse_path, set_path
from dps.layers.connectors.base import ColumnInfo, ColumnKind, ObjectRef, TableShape
from dps.layers.connectors.docpaths import expand_wildcards, generalise

#: BSON label → ``ColumnInfo.kind``.
KIND_OF: Final[dict[str, ColumnKind]] = {
    "string": "text",
    "int": "int",
    "long": "int",
    "double": "float",
    "decimal": "decimal",
    "bool": "bool",
    "date": "timestamp",
    "objectId": "object_id",
    "binData": "binary",
    "uuid": "uuid",
    "null": "other",
}
#: ``_id`` labels that page by range: the key codec turns them back into the same BSON value
#: (a double, Decimal128, bool or binary ``_id`` would not round-trip, so it reads as one partition).
_RANGE_ID_LABELS: Final = frozenset({"int", "long", "string", "objectId", "date", "uuid"})
_INT_TEXT: Final = re.compile(r"-?\d{1,19}")
_NUM_TEXT: Final = re.compile(r"-?\d+(\.\d+)?([eE][-+]?\d+)?")
_SKIP_LABELS: Final = frozenset({"string", "bool", "null", "other", "binData"})
_INT32 = range(-(2**31), 2**31)


def bson_type(value: Any) -> str:
    """The BSON type label of a decoded value (pymongo decodes int64 as ``Int64``).

    One of ``string int long double decimal bool date objectId binData uuid null other``.
    """
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, Int64):
        return "long"
    if isinstance(value, int):
        return "int" if value in _INT32 else "long"
    if isinstance(value, float):
        return "double"
    if isinstance(value, Decimal128):
        return "decimal"
    if isinstance(value, str):
        return "string"
    if isinstance(value, dt.datetime):
        return "date"
    if isinstance(value, ObjectId):
        return "objectId"
    if isinstance(value, uuid.UUID):
        return "uuid"
    if isinstance(value, bytes | Binary):
        return "binData"
    return "other"


def top_level(path: str) -> str:
    """The top-level field of a path: ``$.contact.addresses[*].line1`` → ``contact``."""
    return str(parse_path(path.replace("[*]", "[0]"))[0])


def _id_label(doc: dict[str, Any]) -> str:
    value = doc.get("_id")
    return "document" if isinstance(value, dict | list) else bson_type(value)


def _label(counts: Counter[str]) -> tuple[str, ColumnKind]:
    non_null = sorted(t for t in counts if t != "null")
    if not non_null:
        return "null", "other"
    if len(non_null) == 1:
        return non_null[0], KIND_OF.get(non_null[0], "other")
    if set(non_null) <= {"int", "long"}:
        return "long", "int"
    return "mixed(" + ",".join(non_null) + ")", "other"


def infer_shape(
    obj: ObjectRef, docs: list[dict[str, Any]], *, row_estimate: int | None
) -> TableShape:
    """One column per generalised leaf path across ``docs`` (``_id`` first).

    ``key = ["_id"]`` (``primary_key``) when every sampled ``_id`` has one range-pageable scalar
    BSON type (ints and longs count as one); otherwise no key (``none``): BSON type bracketing
    would drop documents of the other types from a range read, so the collection is one
    partition and ``in_place``/``append`` refuse it.
    """
    types: dict[str, Counter[str]] = {}
    present: Counter[str] = Counter()
    for doc in docs:
        seen: set[str] = set()
        for path, value in flatten(doc):
            g = generalise(path)
            types.setdefault(g, Counter())[bson_type(value)] += 1
            seen.add(g)
        present.update(seen)
    ids = {_id_label(d) for d in docs}
    uniform = bool(docs) and (len(ids) == 1 or ids <= {"int", "long"})
    keyed = uniform and ids <= _RANGE_ID_LABELS
    columns: list[ColumnInfo] = []
    for g in sorted(types, key=lambda p: (p != "$._id", p)):
        label, kind = _label(types[g])
        counts = types[g]
        columns.append(
            ColumnInfo(
                path=g,
                name=top_level(g),
                data_type=label,
                kind=kind,
                nullable=counts["null"] > 0 or present[g] < len(docs),
                is_key=keyed and g == "$._id",
            )
        )
    source: Literal["primary_key", "none"] = "primary_key" if keyed else "none"
    return TableShape(
        object=obj,
        columns=columns,
        key=["_id"] if keyed else [],
        key_source=source,
        row_estimate=row_estimate,
    )


def _coerce(value: Any, label: str) -> Any:
    """``value`` as the BSON type ``label``; unchanged when it cannot be restored."""
    if value is None or isinstance(value, bool):
        return value
    try:
        if label == "long":
            if isinstance(value, int) and not isinstance(value, Int64):
                return Int64(value)
            if isinstance(value, str) and _INT_TEXT.fullmatch(value):
                return Int64(int(value))
        elif label == "int":
            if isinstance(value, str) and _INT_TEXT.fullmatch(value):
                value = int(value)
            if isinstance(value, int):
                return int(value) if value in _INT32 else Int64(value)
        elif label == "double":
            if isinstance(value, int | decimal.Decimal | Decimal128):
                return float(str(value))
            if isinstance(value, str) and _NUM_TEXT.fullmatch(value):
                return float(value)
        elif label == "decimal":
            if isinstance(value, decimal.Decimal):
                return Decimal128(value)
            if isinstance(value, int | float):
                return Decimal128(str(value))
            if isinstance(value, str) and _NUM_TEXT.fullmatch(value):
                return Decimal128(value)
        elif label == "date":
            if isinstance(value, dt.datetime):
                return value
            if isinstance(value, dt.date):
                return dt.datetime(value.year, value.month, value.day)
            if isinstance(value, str):
                return dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        elif label == "objectId":
            if isinstance(value, str) and ObjectId.is_valid(value):
                return ObjectId(value)
        elif label == "uuid" and isinstance(value, str):
            return uuid.UUID(value)
    except (ValueError, ArithmeticError, decimal.InvalidOperation):
        return value
    return value


def coerce_bson(doc: dict[str, Any], shape: TableShape) -> dict[str, Any]:
    """Restore the shape's BSON type at every typed path of ``doc`` (in place); returns ``doc``."""
    for col in shape.columns:
        if col.data_type in _SKIP_LABELS or col.data_type.startswith("mixed"):
            continue
        for path in expand_wildcards(doc, col.path):
            old = get_path(doc, path)
            new = _coerce(old, col.data_type)
            if new is not old:
                set_path(doc, path, new)
    return doc


def make_bson_safe(value: Any) -> Any:
    """A copy of ``value`` where what pymongo cannot encode became its BSON equivalent
    (``decimal.Decimal`` → ``Decimal128``, ``datetime.date`` → ``datetime``), recursively."""
    if isinstance(value, dict):
        return {k: make_bson_safe(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [make_bson_safe(v) for v in value]
    if isinstance(value, decimal.Decimal):
        return Decimal128(value)
    if isinstance(value, dt.date) and not isinstance(value, dt.datetime):
        return dt.datetime(value.year, value.month, value.day)
    return value
