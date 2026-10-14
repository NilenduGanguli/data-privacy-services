"""MOSTLY AI linked generation: a parent table and a child table linked to it by a foreign key,
optionally ordered into sequences (spec §5.6 "multi-table and sequential data"; M5 Task 7B).

Unlike the 1:1 row path (contract §6.6), linked generation **generates a new dataset**: ``subjects``
synthetic parents with their child sequences. The trainer sees only what the accepted snapshots
call non-sensitive, non-key columns of both tables, plus the link — parent keys re-numbered
``1..n`` over the sample in a seed-keyed order (so neither a key value nor the order of real
customers reaches it) and the child's foreign key mapped onto those numbers — and the ``order_by``
column the child's sequences are sorted by. The output carries **new** keys (``1..n`` for integer
keys, ``S00000001…``/``C00000001…`` for text keys); the child's foreign key references the
generated parent keys, so referential integrity holds inside the output and no source key value is
written. Sensitive columns are filled with surrogates from the job's synthesis seed, anchored on
the generated subject (``linked:parent:<generated id>``) and computed from a value-shaped
placeholder — never from a real value — so a subject's persona is coherent across both tables.
Columns that are neither modelled nor sensitive (free text, high-cardinality, unsupported kinds)
are written as null: a generated dataset has no source row to copy them from.

The pool (the generated rows) is derived from real data and is sealed with the job KEK like any
tabular model; it is never logged and is purged with the job key. Errors never quote a value.
"""

from __future__ import annotations

import json
import tempfile
import zlib
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any

from pydantic import BaseModel, ConfigDict

from dps.engine.metrics import canonical, to_float
from dps.engine.synth.mostly import (
    DpConfig,
    TabularUnavailable,
    _decode,
    _encode,
    coerce_rows,
    dp_report,
    model_configuration,
)
from dps.engine.synth.personas import seed_int
from dps.engine.synth.surrogates import SurrogateEngine

FORMAT = "dps.mostly.linked.v1"
PARENT_INDEX = "__parent"
_TEXT_KINDS = frozenset({"text", "char", "lob_text", "json", "other", "uuid"})
#: ``(parent columns, child columns, layout, subjects, minutes, dp) → (parent rows, child rows)``;
#: every child row carries ``"__parent"``, the index of its generated parent.
LinkedTrainer = Callable[
    [dict[str, list[Any]], dict[str, list[Any]], "LinkedLayout", int, int, DpConfig | None],
    tuple[list[dict[str, Any]], list[dict[str, Any]]],
]


class LinkedLayout(BaseModel):
    """How the two tables link: the parent's key, the child's foreign key, the sequence order."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)
    parent_key: str
    foreign_key: str
    order_by: str | None = None


@dataclass(frozen=True)
class SensitiveColumn:
    """A column the snapshot classifies sensitive: filled with a surrogate in the output."""

    entity_type: str
    kind: str
    shape: str | None = None


@dataclass
class LinkedTable:
    """One table's training sample (in memory only) and how its output columns are produced.

    ``columns`` holds the sampled values of the modelled columns (``kinds``) **and** of the link
    columns (the parent key, or the child's foreign key and ``order_by``); ``output`` is every
    column of the written table, in order. ``key`` is the generated key column (``None`` when the
    table has no single-column key), ``key_kind`` ``int`` or ``text``.
    """

    role: str  # "parent" | "child"
    columns: dict[str, list[Any]]
    kinds: dict[str, str]
    output: list[str]
    key: str | None
    key_kind: str = "int"
    scales: dict[str, int] = field(default_factory=dict)
    sensitive: dict[str, SensitiveColumn] = field(default_factory=dict)


def _sort_value(value: Any) -> tuple[int, float, str]:
    if value is None:
        return (2, 0.0, "")
    number = to_float(value)
    if number is not None:
        return (0, number, "")
    return (1, 0.0, str(canonical(value)))


def new_key(kind: str, n: int, prefix: str) -> int | str:
    """The ``n``-th generated key: ``n`` for integer keys, ``<prefix>00000001…`` for text keys."""
    return n if kind == "int" else f"{prefix}{n:08d}"


def sequence_lengths(
    child_rows: Iterable[Mapping[str, Any]], fk: str, keys: Sequence[Any] | None = None
) -> list[int]:
    """Children per parent: per ``keys`` (zeros included) when given, else per distinct ``fk``."""
    counts = Counter(canonical(r.get(fk)) for r in child_rows if r.get(fk) is not None)
    if keys is None:
        return list(counts.values())
    return [counts.get(canonical(k), 0) for k in keys]


def _pseudo_original(spec: SensitiveColumn, n: int) -> str:
    """A value-shaped placeholder for the surrogate engine (no real value is ever used)."""
    if spec.kind in ("date", "timestamp"):
        return (date(1950, 1, 1) + timedelta(days=n % 20_000)).isoformat()
    if spec.shape:
        digits = str(n).zfill(24)
        out: list[str] = []
        d = 0
        for ch in spec.shape:
            if ch == "9":
                out.append(digits[-1 - d % len(digits)])
                d += 1
            elif ch in "Xx":
                letter = chr(ord("A") + (n >> (d % 32)) % 26)
                out.append(letter if ch == "X" else letter.lower())
                d += 1
            else:
                out.append(ch)
        return "".join(out)
    return f"{spec.entity_type.lower()}-{n % 10**9}"


def _typed_surrogate(value: str, kind: str) -> Any:
    """A surrogate as its column's type; ``None`` when it does not read as that type."""
    try:
        if kind in _TEXT_KINDS:
            return value
        if kind == "date":
            return date.fromisoformat(value[:10])
        if kind == "timestamp":
            return datetime.fromisoformat(value)
        if kind == "int":
            digits = "".join(ch for ch in value if ch.isdigit())
            return int(digits[:18]) if digits else None
        if kind == "decimal":
            return Decimal(value)
        if kind == "float":
            return float(value)
    except (ValueError, TypeError, ArithmeticError, InvalidOperation):
        return None
    return None


def _sdk_linked_trainer(
    parent: dict[str, list[Any]],
    child: dict[str, list[Any]],
    layout: LinkedLayout,
    subjects: int,
    minutes: int,
    dp: DpConfig | None,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Train one MOSTLY generator on both tables (the child as the parent's context-linked
    sequence table) and generate ``subjects`` parents with their children.

    The only function that touches the SDK for two tables (verify the config keys with ``help()``
    in the ``ml`` run). Child rows come back with ``"__parent"`` = their parent's row index.

    Raises:
        TabularUnavailable: the ``synth-ml`` extra is not installed or the output cannot be linked.
    """
    try:
        import pandas as pd
        from mostlyai.sdk import MostlyAI
    except ImportError as exc:
        raise TabularUnavailable("the synth-ml extra (mostlyai, pandas) is not installed") from exc
    configuration = model_configuration(minutes, dp)
    with tempfile.TemporaryDirectory(prefix="dps-mostly-linked-") as workdir:
        mostly = MostlyAI(local=True, local_dir=workdir)
        generator = mostly.train(
            config={
                "name": "dps-linked",
                "tables": [
                    {
                        "name": "parent",
                        "data": pd.DataFrame(parent),
                        "primary_key": layout.parent_key,
                        "tabular_model_configuration": configuration,
                    },
                    {
                        "name": "child",
                        "data": pd.DataFrame(child),
                        "foreign_keys": [
                            {
                                "column": layout.foreign_key,
                                "referenced_table": "parent",
                                "is_context": True,
                            }
                        ],
                        "tabular_model_configuration": configuration,
                    },
                ],
            },
            start=True,
            wait=True,
        )
        data = mostly.generate(generator, size=subjects, start=True, wait=True).data()
    parents = [dict(r) for r in data["parent"].to_dict(orient="records")]
    index = {canonical(p.get(layout.parent_key)): i for i, p in enumerate(parents)}
    children: list[dict[str, Any]] = []
    for row in data["child"].to_dict(orient="records"):
        position = index.get(canonical(row.get(layout.foreign_key)))
        if position is not None:
            children.append({**row, PARENT_INDEX: position})
    return parents, children


class LinkedPool:
    """The generated parent and child rows of one linked job (sealed like a tabular model)."""

    def __init__(
        self,
        parent_rows: list[dict[str, Any]],
        child_rows: list[dict[str, Any]],
        *,
        layout: LinkedLayout,
        parent_columns: list[str],
        child_columns: list[str],
        dp: dict[str, Any],
    ) -> None:
        self.parent_rows, self.child_rows = parent_rows, child_rows
        self.layout = layout
        self.parent_columns, self.child_columns = parent_columns, child_columns
        self.dp = dict(dp)

    @classmethod
    def fit(
        cls,
        parent: LinkedTable,
        child: LinkedTable,
        *,
        layout: LinkedLayout,
        subjects: int,
        seed: bytes,
        max_training_minutes: int,
        dp: DpConfig | None,
        trainer: LinkedTrainer | None = None,
        locale: str = "en_US",
    ) -> LinkedPool:
        """Re-number the link, train, generate, assign new keys, fill surrogates.

        Raises:
            TabularUnavailable: the extra is missing, the generator returned no parent, a child
                without a valid parent, or a value its column cannot hold (value-free).
        """
        number = _renumber(parent.columns[layout.parent_key], seed)
        parent_rows = [i for i, v in enumerate(parent.columns[layout.parent_key]) if v is not None]
        parent_train: dict[str, list[Any]] = {
            layout.parent_key: [
                number[canonical(parent.columns[layout.parent_key][i])] for i in parent_rows
            ],
            **{c: [parent.columns[c][i] for i in parent_rows] for c in sorted(parent.kinds)},
        }
        fks = child.columns[layout.foreign_key]
        order_values = child.columns.get(layout.order_by) if layout.order_by else None
        kept = [j for j, v in enumerate(fks) if v is not None and canonical(v) in number]
        kept.sort(
            key=lambda j: (
                number[canonical(fks[j])],
                _sort_value(order_values[j]) if order_values is not None else (0, 0.0, ""),
                j,
            )
        )
        child_names = sorted(set(child.kinds) | ({layout.order_by} if layout.order_by else set()))
        child_train: dict[str, list[Any]] = {
            layout.foreign_key: [number[canonical(fks[j])] for j in kept],
            **{c: [child.columns[c][j] for j in kept] for c in child_names if c in child.columns},
        }
        raw_parents, raw_children = (trainer or _sdk_linked_trainer)(
            parent_train, child_train, layout, subjects, max_training_minutes, dp
        )
        if not raw_parents:
            raise TabularUnavailable("the MOSTLY AI generator returned no parent rows")
        parent_kinds = sorted(parent.kinds)
        typed_parents = coerce_rows(
            raw_parents,
            parent_kinds,
            {f"$.{c}": k for c, k in parent.kinds.items()},
            {f"$.{c}": s for c, s in parent.scales.items()},
        )
        child_kinds = sorted(child.kinds)
        try:
            positions = [int(r[PARENT_INDEX]) for r in raw_children]
        except (KeyError, TypeError, ValueError):
            raise TabularUnavailable("the generator returned a child without its parent") from None
        if any(not 0 <= p < len(raw_parents) for p in positions):
            raise TabularUnavailable("the generator returned a child without its parent")
        typed_children = coerce_rows(
            raw_children,
            child_kinds,
            {f"$.{c}": k for c, k in child.kinds.items()},
            {f"$.{c}": s for c, s in child.scales.items()},
        )
        order_at = child_kinds.index(layout.order_by) if layout.order_by in child_kinds else None
        sequence = sorted(
            range(len(typed_children)),
            key=lambda j: (
                positions[j],
                _sort_value(typed_children[j][order_at]) if order_at is not None else (0, 0.0, ""),
                j,
            ),
        )
        surrogates = SurrogateEngine(seed, default_locale=locale)
        parent_keys = [new_key(parent.key_kind, i + 1, "S") for i in range(len(typed_parents))]
        out_parents: list[dict[str, Any]] = []
        for i, values in enumerate(typed_parents):
            row: dict[str, Any] = dict.fromkeys(parent.output)
            row.update(zip(parent_kinds, values, strict=True))
            row[layout.parent_key] = parent_keys[i]
            _fill(row, parent, surrogates, seed, subject=parent_keys[i], index=i, locale=locale)
            out_parents.append(row)
        out_children: list[dict[str, Any]] = []
        for n, j in enumerate(sequence):
            row = dict.fromkeys(child.output)
            row.update(zip(child_kinds, typed_children[j], strict=True))
            subject = parent_keys[positions[j]]
            row[layout.foreign_key] = subject
            if child.key is not None:
                row[child.key] = new_key(child.key_kind, n + 1, "C")
            _fill(row, child, surrogates, seed, subject=subject, index=n, locale=locale)
            out_children.append(row)
        return cls(
            out_parents,
            out_children,
            layout=layout,
            parent_columns=list(parent.output),
            child_columns=list(child.output),
            dp=dp_report(dp),
        )

    def to_bytes(self) -> bytes:
        """zlib-compressed JSON; the seed is NOT included."""
        body = {
            "format": FORMAT,
            "layout": self.layout.model_dump(),
            "parent_columns": self.parent_columns,
            "child_columns": self.child_columns,
            "parent": [[_encode(r.get(c)) for c in self.parent_columns] for r in self.parent_rows],
            "child": [[_encode(r.get(c)) for c in self.child_columns] for r in self.child_rows],
            "dp": self.dp,
        }
        return zlib.compress(json.dumps(body, separators=(",", ":")).encode("utf-8"), 6)

    @classmethod
    def from_bytes(cls, data: bytes, seed: bytes) -> LinkedPool:
        """Rebuild the pool (``seed`` is unused: the pool is already generated; kept for the
        sampler-style signature).

        Raises:
            TabularUnavailable: the bytes are not a linked pool of this format (no detail).
        """
        try:
            body = json.loads(zlib.decompress(data).decode("utf-8"))
            if not isinstance(body, dict) or body.get("format") != FORMAT:
                raise TabularUnavailable("unsupported pool format")
            pcols = [str(c) for c in body["parent_columns"]]
            ccols = [str(c) for c in body["child_columns"]]
            parents = [dict(zip(pcols, map(_decode, r), strict=True)) for r in body["parent"]]
            children = [dict(zip(ccols, map(_decode, r), strict=True)) for r in body["child"]]
            dp = body.get("dp")
            return cls(
                parents,
                children,
                layout=LinkedLayout.model_validate(body["layout"]),
                parent_columns=pcols,
                child_columns=ccols,
                dp=dp if isinstance(dp, dict) else {"enabled": False},
            )
        except TabularUnavailable:
            raise
        except (zlib.error, ValueError, KeyError, TypeError, ArithmeticError):
            raise TabularUnavailable("unsupported pool format") from None


def _renumber(keys: Sequence[Any], seed: bytes) -> dict[Any, int]:
    """Parent key (canonical) → ``1..n`` in a seed-keyed order (not the order of real keys)."""
    distinct = list(dict.fromkeys(canonical(v) for v in keys if v is not None))
    ordered = sorted(distinct, key=lambda c: (seed_int(seed, f"linked.renumber|{c}"), str(c)))
    return {c: i + 1 for i, c in enumerate(ordered)}


def _fill(
    row: dict[str, Any],
    table: LinkedTable,
    surrogates: SurrogateEngine,
    seed: bytes,
    *,
    subject: int | str,
    index: int,
    locale: str,
) -> None:
    """Surrogates for the sensitive columns, anchored on the generated subject."""
    anchor = f"linked:parent:{subject}"
    for name, spec in table.sensitive.items():
        n = seed_int(seed, f"{anchor}|{table.role}|{name}|{index}")
        value = surrogates.surrogate(spec.entity_type, _pseudo_original(spec, n), locale, anchor)
        row[name] = _typed_surrogate(value, spec.kind)
