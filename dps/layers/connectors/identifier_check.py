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
from dps.layers.connectors.base import Connector, KeyRange, Row, TableShape, to_engine_text

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
    """``{column: entity type}`` of the written identifier-family columns (not free text): the
    plan's entity type where the column is planned, else the snapshot's."""
    out: dict[str, str] = {}
    for c in snapshot.columns if snapshot else []:
        if c.entity_type and not c.text_column:
            out[c.name] = c.entity_type
    for p in planned:
        if p.text_column:
            out.pop(p.name, None)
        else:
            out[p.name] = p.entity_type
    keep = set(written)
    return {n: e for n, e in sorted(out.items()) if n in keep and is_identifier(e)}


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


async def _stream(
    connector: Connector, shape: TableShape, names: list[str]
) -> AsyncIterator[list[Row]]:
    present = [n for n in names if any(c.name == n for c in shape.columns)]
    if not present:
        return
    pages = connector.read_partition(
        shape, Selection(columns=present), {}, KeyRange(), after_key=None, batch=CHECK_BATCH
    )
    async for rows in pages:
        yield rows


async def check_identifiers(
    columns: dict[str, str],
    *,
    target: Connector,
    target_shape: TableShape,
    source: Connector | None,
    source_shape: TableShape | None,
) -> dict[str, Any]:
    """Read every target row's identifier columns, then (unless ``source`` is None: in place, the
    target *is* the source) every source row's, and count (see the module docstring)."""
    tally = IdentifierTally(columns)
    names = list(columns)
    by_name = {c.name: c for c in target_shape.columns}
    async for rows in _stream(target, target_shape, names):
        for row in rows:
            for name in names:
                column = by_name.get(name)
                if column is not None:
                    tally.add_target(name, to_engine_text(row.get(name), column))
        rows.clear()
    tally.seal()
    if source is None or source_shape is None:
        return tally.result(source_compared=False)
    src_cols = {c.name: c for c in source_shape.columns}
    async for rows in _stream(source, source_shape, names):
        for name in names:
            column = src_cols.get(name)
            if column is not None:
                tally.add_source(name, (to_engine_text(r.get(name), column) for r in rows))
        rows.clear()
    return tally.result(source_compared=True)
