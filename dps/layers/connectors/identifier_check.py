"""Post-write identifier check of a synthesize run (D-A, programme §2.4; M5 verify r2).

The surrogates of government identifiers come from never-issued ranges (``unissued.py``), so by
construction no synthetic identifier is a real one. This check proves it on what was actually
written, over **every** row of the target (not a sample), for each identifier-family column the
run wrote:

* ``in_source`` — distinct target values found anywhere in the same source column, compared on
  the leakage sweep's normalised form through a keyed 64-bit hash (a fresh random key per check,
  never stored), so neither side's values are held, logged or returned;
* ``issuable`` — target values of a government identifier that are **not** in a range the issuer
  never allocates (:func:`~dps.engine.synth.unissued.never_issued`): every value of a type that
  has no such range (PASSPORT, DRIVER_LICENSE, ...) and of an unrecognised national-id shape.

A column with either count above 0 is a review column: the evidence pack's verification then
says ``review_required`` and does not pass. The result holds column names, entity types and
counts only.
"""

from __future__ import annotations

import hashlib
import secrets
from array import array
from collections.abc import AsyncIterator, Iterable, Sequence
from typing import Any

from dps.engine.batch import ColumnPlan
from dps.engine.synth.unissued import never_issued
from dps.engine.taxonomy import Family, entity_info
from dps.engine.verify import normalise
from dps.jobs_models import ClassificationSnapshot, Selection
from dps.layers.connectors import docpaths
from dps.layers.connectors.base import (
    ColumnInfo,
    Connector,
    KeyRange,
    Row,
    TableShape,
    to_engine_text,
)
from dps.layers.connectors.docpaths import column_key

#: Financial identifiers checked against the source too (governments' are found by family).
FINANCIAL_IDENTIFIERS = frozenset({"CARD_PAN", "IBAN", "ACCOUNT_NUMBER", "CRYPTO_ADDRESS"})
#: Rows per read page.
CHECK_BATCH = 5000


def is_identifier(entity_type: str | None) -> bool:
    """True for an entity type whose values identify one person or account."""
    if not entity_type:
        return False
    return (
        entity_type in FINANCIAL_IDENTIFIERS
        or entity_info(entity_type).family == Family.GOVERNMENT_ID
    )


def identifier_columns(
    planned: Sequence[ColumnPlan],
    snapshot: ClassificationSnapshot | None,
    written: Iterable[str],
) -> dict[str, str]:
    """``{column key: entity type}`` of the written identifier-family columns (not free text):
    the plan's entity type where the column is planned, else the snapshot's. ``written`` holds
    row-level names; a key is the name of a flat column and the path of a MongoDB leaf
    (:func:`~dps.layers.connectors.docpaths.column_key`, WP-I m3)."""
    out: dict[str, str] = {}
    field: dict[str, str] = {}
    for c in snapshot.columns if snapshot else []:
        if c.entity_type and not c.text_column:
            key = column_key(c.path, c.name)
            out[key], field[key] = c.entity_type, c.name
    for p in planned:
        key = column_key(p.path, p.name)
        if p.text_column:
            out.pop(key, None)
        else:
            out[key], field[key] = p.entity_type, p.name
    keep = set(written)
    return {k: e for k, e in sorted(out.items()) if field[k] in keep and is_identifier(e)}


class _Members:
    """A sorted, de-duplicated set of 64-bit hashes (numpy when installed: 8 bytes a value)."""

    def __init__(self, hashes: array[int]) -> None:
        try:
            import numpy as np
        except ImportError:  # pragma: no cover - the tabular extra ships numpy
            self._np: Any = None
            self._set = set(hashes)
            return
        self._np = np
        self._sorted = np.unique(np.frombuffer(hashes, dtype=np.uint64))

    def __len__(self) -> int:
        return len(self._set) if self._np is None else int(self._sorted.size)

    def hits(self, chunk: array[int]) -> set[int]:
        """The members found in ``chunk``."""
        if self._np is None:
            return self._set.intersection(chunk)
        np = self._np
        if not self._sorted.size or not len(chunk):
            return set()
        values = np.frombuffer(chunk, dtype=np.uint64)
        idx = np.minimum(np.searchsorted(self._sorted, values), self._sorted.size - 1)
        return {int(v) for v in values[self._sorted[idx] == values]}


class IdentifierTally:
    """Counts for one check: feed every target value, :meth:`seal`, then every source value."""

    def __init__(self, columns: dict[str, str]) -> None:
        self.columns = dict(columns)
        self._key = secrets.token_bytes(32)
        self._target: dict[str, array[int]] = {n: array("Q") for n in columns}
        self._members: dict[str, _Members] = {}
        self._matched: dict[str, set[int]] = {n: set() for n in columns}
        self.checked: dict[str, int] = dict.fromkeys(columns, 0)
        self.issuable: dict[str, int] = dict.fromkeys(columns, 0)

    def _hash(self, text: str) -> int | None:
        folded = normalise(text)
        if not folded:
            return None
        digest = hashlib.blake2b(folded.encode(), key=self._key, digest_size=8).digest()
        return int.from_bytes(digest, "big")

    def add_target(self, name: str, text: str | None) -> None:
        """One written value of ``name``."""
        if not text or name not in self._target:
            return
        self.checked[name] += 1
        entity = self.columns[name]
        if entity_info(entity).family == Family.GOVERNMENT_ID and not never_issued(entity, text):
            self.issuable[name] += 1
        h = self._hash(text)
        if h is not None:
            self._target[name].append(h)

    def seal(self) -> None:
        """Index the target's values; the raw hash lists are dropped."""
        self._members = {n: _Members(a) for n, a in self._target.items()}
        self._target.clear()

    def add_source(self, name: str, texts: Iterable[str | None]) -> None:
        """Source values of ``name`` (a batch)."""
        members = self._members.get(name)
        if members is None:
            return
        chunk = array("Q", (h for t in texts if t and (h := self._hash(t)) is not None))
        self._matched[name] |= members.hits(chunk)

    def result(self, *, source_compared: bool) -> dict[str, Any]:
        """Column names, entity types and counts only."""
        cols: dict[str, dict[str, Any]] = {}
        flagged: list[str] = []
        for name, entity in self.columns.items():
            in_source = len(self._matched[name]) if source_compared else None
            cols[name] = {
                "entity_type": entity,
                "checked": self.checked[name],
                "in_source": in_source,
                "issuable": self.issuable[name],
            }
            if (in_source or 0) > 0 or self.issuable[name] > 0:
                flagged.append(name)
        return {
            "columns": cols,
            "source_compared": source_compared,
            "review_columns": flagged,
        }


def _by_key(shape: TableShape) -> dict[str, ColumnInfo]:
    return {column_key(c.path, c.name): c for c in shape.columns}


async def _stream(
    connector: Connector,
    shape: TableShape,
    names: list[str],
    after_key: tuple[Any, ...] | None = None,
) -> AsyncIterator[list[Row]]:
    by_key = _by_key(shape)
    # what the connector reads: row-level fields (a MongoDB leaf's whole top-level field)
    present = list(dict.fromkeys(by_key[n].name for n in names if n in by_key))
    if not present:
        return
    pages = connector.read_partition(
        shape, Selection(columns=present), {}, KeyRange(), after_key=after_key, batch=CHECK_BATCH
    )
    async for rows in pages:
        yield rows


async def key_floor(connector: Connector, shape: TableShape) -> tuple[int] | None:
    """The largest key of ``shape`` before an append writes (``None``: no row, or not one
    integer key column). Rows the target generates after it carry larger keys, so
    :func:`check_identifiers` can read only what the run appended. Reads the key column only."""
    if len(shape.key) != 1:
        return None
    column = next((c for c in shape.columns if c.name == shape.key[0]), None)
    if column is None or column.kind != "int":
        return None
    largest: int | None = None
    pages = connector.read_partition(
        shape, Selection(columns=[column.name]), {}, KeyRange(), after_key=None, batch=CHECK_BATCH
    )
    async for rows in pages:
        for row in rows:
            value = row.get(column.name)
            if isinstance(value, int) and (largest is None or value > largest):
                largest = value
        rows.clear()
    return (largest,) if largest is not None else None


async def _tally_target(
    tally: IdentifierTally,
    names: list[str],
    target: Connector,
    target_shape: TableShape,
    after_key: tuple[Any, ...] | None,
) -> int:
    by_key = _by_key(target_shape)
    read = 0
    async for rows in _stream(target, target_shape, names, after_key):
        read += len(rows)
        for row in rows:
            for name in names:
                column = by_key.get(name)
                if column is not None:
                    # every value the column holds in this row (each element of a wildcard path)
                    for value in docpaths.column_values([row], column):
                        tally.add_target(name, to_engine_text(value, column))
        rows.clear()
    return read


async def check_identifiers(
    columns: dict[str, str],
    *,
    target: Connector,
    target_shape: TableShape,
    source: Connector | None,
    source_shape: TableShape | None,
    after_key: tuple[Any, ...] | None = None,
    expected_rows: int | None = None,
) -> dict[str, Any]:
    """Read every target row's identifier columns, then (unless ``source`` is None: in place, the
    target *is* the source) every source row's, and count (see the module docstring).

    ``after_key`` (WP-I, an append into a table that already held rows): read only the target
    rows after the pre-run key (:func:`key_floor`), the rows the run appended; the target's own
    rows are not the run's output. Fail closed: when fewer rows follow the floor than the run
    wrote (``expected_rows``), some written rows sit below it and every row is checked again.
    ``scope`` says which: ``written_rows`` or ``whole_table``.
    """
    names = list(columns)
    tally = IdentifierTally(columns)
    scope, reason = "whole_table", None
    if after_key is not None:
        read = await _tally_target(tally, names, target, target_shape, after_key)
        if expected_rows is not None and read >= expected_rows:
            scope = "written_rows"
        else:
            tally = IdentifierTally(columns)
            reason = "rows_below_floor" if expected_rows is not None else "written_rows_unknown"
    if scope == "whole_table":
        await _tally_target(tally, names, target, target_shape, None)
    tally.seal()
    out: dict[str, Any] = {"scope": scope}
    if reason:
        out["scope_reason"] = reason
    if source is None or source_shape is None:
        return {**tally.result(source_compared=False), **out}
    src_cols = _by_key(source_shape)
    async for rows in _stream(source, source_shape, names):
        for name in names:
            column = src_cols.get(name)
            if column is not None:
                tally.add_source(
                    name,
                    (to_engine_text(v, column) for v in docpaths.column_values(rows, column)),
                )
        rows.clear()
    return {**tally.result(source_compared=True), **out}
