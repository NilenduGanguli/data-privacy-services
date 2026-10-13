"""BSON-aware helpers for the MongoDB connector (pure, offline).

Type labels, a path shape inferred from a ``$sample`` (one column per generalised leaf path,
``[*]`` for array elements), and type restoration of the leaves the engine wrote
(:func:`restore_leaf`: a transformed ``Int64``, ``Decimal128``, date or UUID leaf goes back to its
*original* leaf's BSON type after the engine turned it into text and back). Values no plan covers
are never converted: they reach the target as the driver decoded them (contract §6.4). Nothing here
logs or raises a value.
"""

from __future__ import annotations

import datetime as dt
import decimal
import re
import uuid
from collections import Counter
from typing import Any, Final, Literal

from bson import Binary, Decimal128, Int64, ObjectId

from dps.engine.jsonpath import flatten, parse_path
from dps.layers.connectors.base import ColumnInfo, ColumnKind, ObjectRef, TableShape
from dps.layers.connectors.docpaths import generalise

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
#: Labels :func:`restore_leaf` restores (a string, bool, null, binary or ObjectId leaf is never
#: converted back from another type).
_RESTORED_LABELS: Final = frozenset({"int", "long", "double", "decimal", "date", "uuid"})
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


def id_label(value: Any) -> str:
    """The key family of an ``_id`` value: its BSON label, ``int`` for int32 and int64 alike (one
    family: they compare and page together), ``document`` for an embedded document or array."""
    if isinstance(value, dict | list):
        return "document"
    label = bson_type(value)
    return "int" if label == "long" else label


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
    obj: ObjectRef,
    docs: list[dict[str, Any]],
    *,
    row_estimate: int | None,
    id_bounds: list[Any] | None = None,
) -> TableShape:
    """One column per generalised leaf path across ``docs`` (``_id`` first).

    ``key = ["_id"]`` (``primary_key``) when every sampled ``_id`` — and every value of
    ``id_bounds`` — has one range-pageable scalar BSON type (ints and longs count as one);
    otherwise no key (``none``): BSON type bracketing would drop documents of the other types from
    a range read, so the collection is one partition and ``in_place``/``append`` refuse it.

    ``id_bounds`` are the collection's smallest and largest ``_id`` (``MongoConnector.describe``
    reads both from the ``_id`` index). BSON orders values by type bracket first, so when both
    bounds share the sample's type every ``_id`` lies in that bracket: a minority type the sample
    missed can no longer make the collection look keyed (review G3 r1 B2).
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
    ids = {id_label(d.get("_id")) for d in docs} | {id_label(v) for v in id_bounds or []}
    keyed = bool(docs) and len(ids) == 1 and ids <= _RANGE_ID_LABELS
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


def restore_leaf(original: Any, value: Any) -> Any:
    """``value`` (what the engine wrote at a leaf) in ``original``'s BSON type (contract §6.4).

    Called by ``BatchTransformer`` for each document leaf a plan *wrote*, never for untouched
    leaves. A value whose type already matches the original is returned as is; an int32 original
    stays int32 unless the new value needs 64 bits; text that cannot be restored (a token in a
    numeric field) is returned unchanged.
    """
    label = bson_type(original)
    if label not in _RESTORED_LABELS or bson_type(value) == label:
        return value
    return _coerce(value, label)


def upsert_fields(doc: dict[str, Any]) -> dict[str, Any] | None:
    """``$set`` document of a ``table`` upsert: every field but ``_id`` at its dotted path through
    sub-documents, so a matched target document keeps the fields the row does not carry (contract
    §5.2: an upsert leaves the target's other columns untouched). Arrays, scalars and empty
    sub-documents are set whole (an array element cannot be merged by position). None when a field
    name cannot be dot-addressed (empty, with a ``.``, or starting with ``$``).
    """
    out: dict[str, Any] = {}

    def walk(node: dict[str, Any], prefix: str) -> bool:
        for key, value in node.items():
            if not prefix and key == "_id":
                continue
            if not isinstance(key, str) or not key or "." in key or key.startswith("$"):
                return False
            path = f"{prefix}{key}"
            if isinstance(value, dict) and value:
                if not walk(value, path + "."):
                    return False
            else:
                out[path] = value
        return True

    return out if walk(doc, "") else None


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
