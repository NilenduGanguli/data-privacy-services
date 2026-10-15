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

**Every row** (S1-M3 engines r3 B1). The check pages through each side itself: every read starts
after the last key it has read (``after_key``), and a side is done only when a read after that
key returns no row — how a connector pages inside one read is never taken as the end of the
object (Oracle and MongoDB once stopped after the first 5 000-row page while the result said
``whole_table``). A key-less object (a MongoDB collection whose ``_id``s cannot be range-paged) is
read in one pass and held to its row count. ``rows_read`` says how many rows of each side were
read; ``checked`` counts the values read. A MongoDB shape comes from a sample of documents, so a
written leaf the sample missed is read as the other side describes it, else as its key names it.
A read that cannot be completed — one that does not advance past its start key, a row without
its key, fewer rows than a key-less object holds, a SQL column the object does not have or a
SQL read does not return — raises :class:`IncompleteCheck` (``identifier_check_incomplete``):
the run's evidence then records ``{"error": ...}`` and every written identifier column is a
review column. A partial read never reports a scope.

**As the object spells it** (S8-idcheck review r1). A column list is a person's selection:
validated by name and folded to upper case on Oracle (the Wega rule), so a quoted mixed-case
Oracle column was never read (``checked`` 0, passed) and a name with a space, or a MongoDB field
with a hyphen, was refused and failed the run's finalize. A SQL read lists no column: it reads
the shape narrowed to its key and the identifier columns, which the connector quotes as the
catalogue spells them. A MongoDB read projects plain top-level field names only; a field a
projection cannot name (a hyphen, a dot) is read from whole documents.
"""

from __future__ import annotations

import hashlib
import secrets
from array import array
from collections.abc import AsyncIterator, Iterable, Sequence
from typing import Any

from dps.engine.batch import ColumnPlan
from dps.engine.jsonpath import child_path
from dps.engine.synth.unissued import never_issued
from dps.engine.taxonomy import Family, entity_info
from dps.engine.verify import normalise
from dps.jobs_models import ClassificationSnapshot, Selection
from dps.layers.connectors import docpaths
from dps.layers.connectors.base import (
    ColumnInfo,
    Connector,
    ConnectorError,
    KeyRange,
    Row,
    TableShape,
    key_tuple,
    to_engine_text,
)
from dps.layers.connectors.docpaths import column_key
from dps.layers.connectors.selection import MONGO_PATH

#: Financial identifiers checked against the source too (governments' are found by family).
FINANCIAL_IDENTIFIERS = frozenset({"CARD_PAN", "IBAN", "ACCOUNT_NUMBER", "CRYPTO_ADDRESS"})
#: Rows per read page.
CHECK_BATCH = 5000


class IncompleteCheck(ConnectorError):
    """The check could not read every row it covers (fails closed: the run's evidence records
    the code and reviews every written identifier column). ``detail`` is value-free."""

    def __init__(self, detail: str) -> None:
        super().__init__("identifier_check_incomplete", detail)


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


def _sampled(connector: Connector) -> bool:
    """True when ``describe`` infers the shape from a sample of documents (MongoDB): a field the
    sample missed still exists. SQL shapes come from the catalogue."""
    return getattr(connector, "engine", None) == "mongo"


def _leaf_column(key: str) -> ColumnInfo | None:
    """The document field a column key names (``$.kyc.taxId``, or a top-level field), read as
    text — what ``to_engine_text`` makes of every non-``char`` kind."""
    path = key if key.startswith("$") else child_path("$", key)
    try:
        parts = docpaths.path_parts(path)
    except ValueError:
        return None
    if not parts or not isinstance(parts[0], str):
        return None
    return ColumnInfo(path=path, name=parts[0], data_type="string", kind="text")


def _columns_for(
    names: list[str], shape: TableShape, other: TableShape | None, *, sampled: bool
) -> dict[str, ColumnInfo]:
    """The column of each key as ``shape`` describes it.

    A sampled (MongoDB) shape may miss a leaf the run wrote: it is then read as ``other`` (the
    other side's shape) describes it, else as its key names it. A SQL shape lists every column
    of the object, and a read projects only those: a column it does not list cannot be read.

    Raises:
        IncompleteCheck: a column that cannot be read — never counted as clean.
    """
    own = _by_key(shape)
    alt = _by_key(other) if sampled and other is not None else {}
    out: dict[str, ColumnInfo] = {}
    for name in names:
        column = own.get(name) or alt.get(name) or (_leaf_column(name) if sampled else None)
        if column is None:
            raise IncompleteCheck("an identifier column is not in the object read")
        out[name] = column
    return out


def _fields(columns: dict[str, ColumnInfo]) -> list[str]:
    """What the connector reads: row-level fields (a MongoDB leaf's whole top-level field)."""
    return list(dict.fromkeys(c.name for c in columns.values()))


def _plain_field(name: str) -> bool:
    """A MongoDB top-level field a find projection names as it is: one segment of the selection
    validator's path syntax (no dot — a projection would read the field before it — and no
    hyphen, space or leading ``$``, which the validator refuses)."""
    return "." not in name and MONGO_PATH.fullmatch(name) is not None


def _read_request(
    connector: Connector, shape: TableShape, fields: list[str]
) -> tuple[TableShape, Selection]:
    """The shape and selection a read of ``fields`` passes to ``read_partition``.

    A column list is a person's selection (validated by name, folded to upper case on Oracle),
    so a SQL read lists none: ``shape`` is narrowed to its key and ``fields``, every one a name
    the catalogue gave, and the connector projects and quotes them as it spells them. MongoDB
    projects by the list: plain top-level names are projected, anything else reads whole
    documents.
    """
    if _sampled(connector):
        if all(_plain_field(f) for f in fields):
            return shape, Selection(columns=fields)
        return shape, Selection()
    keep = {*fields, *shape.key}
    columns = [c for c in shape.columns if c.name in keep]
    return shape.model_copy(update={"columns": columns}), Selection()


def _page_end(page: list[Row], shape: TableShape) -> tuple[Any, ...]:
    try:
        return key_tuple(page[-1], shape)
    except KeyError:
        raise IncompleteCheck("a row was read without its key") from None


def _projected(page: list[Row], fields: list[str]) -> None:
    """A SQL row holds every column the read projects (a NULL is a value of the row): a row
    without one was not read as asked.

    Raises:
        IncompleteCheck: a row without one of ``fields``.
    """
    if any(f not in row for row in page for f in fields):
        raise IncompleteCheck("a read did not return an identifier column")


async def _every_row(
    connector: Connector,
    shape: TableShape,
    fields: list[str],
    after_key: tuple[Any, ...] | None = None,
) -> AsyncIterator[list[Row]]:
    """Every row of ``shape`` after ``after_key`` (``None``: every row), ``fields`` read.

    Keyset pages driven by the check: each read starts after the last key read so far, and the
    scan ends only when a read after that key returns no row, so a read the connector ends early
    cannot shorten it. A page's last key is taken before the page is handed on. A key-less
    shape is read in one pass, and its rows are counted against the object's. Columns are read
    as the object spells them (:func:`_read_request`).

    Raises:
        IncompleteCheck: a read that does not advance past its start key, a row without its
            key, fewer rows than a key-less object holds, a SQL row without one of ``fields``.
    """
    read_shape, selection = _read_request(connector, shape, fields)
    sql = not _sampled(connector)  # a MongoDB document may lack a field; a SQL row may not
    if not shape.key:
        if after_key is not None:
            raise IncompleteCheck("a key-less object has no rows after a key")
        read = 0
        async for page in connector.read_partition(
            read_shape, selection, {}, KeyRange(), after_key=None, batch=CHECK_BATCH
        ):
            if sql:
                _projected(page, fields)
            read += len(page)
            yield page
        if read < await connector.count(read_shape, Selection(), {}):
            raise IncompleteCheck("fewer rows were read than the key-less object holds")
        return
    cursor = after_key
    while True:
        got, end = 0, cursor
        async for page in connector.read_partition(
            read_shape, selection, {}, KeyRange(), after_key=cursor, batch=CHECK_BATCH
        ):
            if not page:
                continue
            if sql:
                _projected(page, fields)
            got += len(page)
            end = _page_end(page, shape)
            yield page
        if not got:
            return  # nothing after the last key read: every row was read
        if end == cursor:
            raise IncompleteCheck("a read did not advance past its start key")
        cursor = end


async def key_floor(connector: Connector, shape: TableShape) -> tuple[int] | None:
    """The largest key of ``shape`` before an append writes (``None``: no row, or not one
    integer key column). Rows the target generates after it carry larger keys, so
    :func:`check_identifiers` can read only what the run appended. Reads the key column only,
    every page of it.

    Raises:
        IncompleteCheck: the key column could not be read to its end.
    """
    if len(shape.key) != 1:
        return None
    column = next((c for c in shape.columns if c.name == shape.key[0]), None)
    if column is None or column.kind != "int":
        return None
    largest: int | None = None
    async for rows in _every_row(connector, shape, [column.name]):
        for row in rows:
            value = row.get(column.name)
            if isinstance(value, int) and (largest is None or value > largest):
                largest = value
    return (largest,) if largest is not None else None


async def _tally_target(
    tally: IdentifierTally,
    columns: dict[str, ColumnInfo],
    target: Connector,
    target_shape: TableShape,
    after_key: tuple[Any, ...] | None,
) -> int:
    """Feed every target row after ``after_key`` to ``tally``; the rows read."""
    read = 0
    async for rows in _every_row(target, target_shape, _fields(columns), after_key):
        read += len(rows)
        for row in rows:
            for name, column in columns.items():
                # every value the column holds in this row (each element of a wildcard path)
                for value in docpaths.column_values([row], column):
                    tally.add_target(name, to_engine_text(value, column))
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
    ``scope`` says which: ``written_rows`` or ``whole_table``; ``rows_read`` how many rows of
    each side were read (``source`` None in place).

    Raises:
        IncompleteCheck: a side could not be read to its end (no scope is reported then).
    """
    names = list(columns)
    tally = IdentifierTally(columns)
    target_columns = _columns_for(names, target_shape, source_shape, sampled=_sampled(target))
    scope, reason = "whole_table", None
    read = 0
    if after_key is not None:
        read = await _tally_target(tally, target_columns, target, target_shape, after_key)
        if expected_rows is not None and read >= expected_rows:
            scope = "written_rows"
        else:
            tally = IdentifierTally(columns)
            reason = "rows_below_floor" if expected_rows is not None else "written_rows_unknown"
    if scope == "whole_table":
        read = await _tally_target(tally, target_columns, target, target_shape, None)
    tally.seal()
    out: dict[str, Any] = {"scope": scope}
    if reason:
        out["scope_reason"] = reason
    if source is None or source_shape is None:
        rows_read = {"target": read, "source": None}
        return {**tally.result(source_compared=False), **out, "rows_read": rows_read}
    source_columns = _columns_for(names, source_shape, target_shape, sampled=_sampled(source))
    source_read = 0
    async for rows in _every_row(source, source_shape, _fields(source_columns)):
        source_read += len(rows)
        for name, column in source_columns.items():
            tally.add_source(
                name,
                (to_engine_text(v, column) for v in docpaths.column_values(rows, column)),
            )
    rows_read = {"target": read, "source": source_read}
    return {**tally.result(source_compared=True), **out, "rows_read": rows_read}
