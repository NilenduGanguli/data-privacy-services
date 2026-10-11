"""Connector types, key codec and value codec (contract §4.1).

Keys and values of source rows are customer data. Encoded keys are stored only in partition rows
and the target's write ledger; nothing here logs, and every error message names a kind or a code,
never a value. Every model hides its input in validation errors (``hide_input_in_errors``), since
a key range or ledger mark that fails validation would otherwise echo a key.
"""

from __future__ import annotations

import hashlib
import json
import re
import uuid
from collections.abc import AsyncIterator
from datetime import date, datetime
from decimal import Context, Decimal, InvalidOperation
from typing import TYPE_CHECKING, Annotated, Any, Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator

if TYPE_CHECKING:
    from dps.jobs_models import Selection

Engine = Literal["postgres", "oracle", "mongo"]
ConnEngine = Literal["postgres", "oracle", "mongo", "s3", "pvc"]
WriteMode = Literal["in_place", "append", "table", "files"]
ColumnKind = Literal[
    "int",
    "decimal",
    "float",
    "text",
    "char",
    "date",
    "timestamp",
    "bool",
    "binary",
    "json",
    "lob_text",
    "lob_binary",
    "uuid",
    "object_id",
    "other",
]
JsonScalar = str | int | float | bool | None
Row = dict[str, Any]
#: In memory a key is a tuple of driver values (never persisted as such).
Key = tuple[Any, ...]
#: Persisted form of a key: ``[{"t": "int|dec|str|char|date|ts|bytes|uuid|oid", "v": "<text>"}]``.
EncodedKey = list[dict[str, str]]

NEVER_TRANSFORMED_KINDS = frozenset({"binary", "lob_binary", "object_id"})
KEYABLE_KINDS = frozenset(
    {"int", "decimal", "text", "char", "date", "timestamp", "uuid", "binary", "object_id"}
)
_KEY_TAG = {
    "int": "int",
    "decimal": "dec",
    "text": "str",
    "char": "char",
    "date": "date",
    "timestamp": "ts",
    "uuid": "uuid",
    "binary": "bytes",
    "object_id": "oid",
}
_REF_PART = re.compile(r"^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")
_INT = re.compile(r"-?\d+")
_DEC = re.compile(r"-?\d+(\.\d+)?")
_HIDDEN = ConfigDict(hide_input_in_errors=True)


class ObjectRef(BaseModel):
    """A table or collection: ``owner`` is the PG schema / Oracle owner, ``None`` for Mongo."""

    model_config = ConfigDict(frozen=True, hide_input_in_errors=True)
    owner: str | None = None
    name: str

    @model_validator(mode="after")
    def _parts(self) -> ObjectRef:
        for part in (self.owner, self.name):
            if part is not None and not _REF_PART.fullmatch(part):
                raise ValueError("object names must match ^[A-Za-z_][A-Za-z0-9_$#]{0,127}$")
        return self

    def fqn(self) -> str:
        """``OWNER.NAME`` / ``schema.name`` / ``name`` — the ``object_ref`` text."""
        return f"{self.owner}.{self.name}" if self.owner else self.name

    @classmethod
    def parse(cls, text: str) -> ObjectRef:
        """Inverse of :meth:`fqn`."""
        parts = text.split(".")
        if len(parts) == 1:
            return cls(name=parts[0])
        if len(parts) == 2:
            return cls(owner=parts[0], name=parts[1])
        raise ValueError("object reference must be NAME or OWNER.NAME")


class ColumnInfo(BaseModel):
    """One column (SQL) or field path (Mongo) as the connector describes it (contract §4.1)."""

    model_config = _HIDDEN
    path: str
    name: str
    data_type: str
    kind: ColumnKind
    length: int | None = None
    precision: int | None = None
    scale: int | None = None
    ts_precision: int | None = None
    nullable: bool = True
    is_key: bool = False
    has_default: bool = False
    is_identity: bool = False


class TableShape(BaseModel):
    """Columns and paging key of one object (contract §4.1)."""

    model_config = _HIDDEN
    object: ObjectRef
    columns: list[ColumnInfo]
    key: list[str]
    key_source: Literal["primary_key", "unique_index", "rowid", "none"]
    row_estimate: int | None = None

    def column(self, name: str) -> ColumnInfo:
        """The column called ``name`` (``KeyError`` when absent)."""
        for c in self.columns:
            if c.name == name:
                return c
        raise KeyError(name)

    def key_columns(self) -> list[ColumnInfo]:
        """Key columns in key order."""
        return [self.column(k) for k in self.key]


class KeyRange(BaseModel):
    """A partition's key range ``[lower, upper)``, encoded; ``None`` is open."""

    model_config = _HIDDEN
    lower: EncodedKey | None = None  # inclusive
    upper: EncodedKey | None = None  # exclusive


class PkGenerate(BaseModel):
    """Append: the target generates the key (identity, default or sequence)."""

    model_config = _HIDDEN
    kind: Literal["generate"] = "generate"
    sequence: str | None = None  # Oracle sequence name (M3)


class PkOffset(BaseModel):
    """Append: shift integer keys by ``offset`` or prefix text keys with ``prefix`` (exactly one)."""

    model_config = _HIDDEN
    kind: Literal["offset"] = "offset"
    offset: int | None = Field(default=None, gt=0)
    prefix: str | None = Field(
        default=None, min_length=1, max_length=16, pattern=r"^[A-Za-z0-9_-]+$"
    )

    @model_validator(mode="after")
    def _exactly_one(self) -> PkOffset:
        if (self.offset is None) == (self.prefix is None):
            raise ValueError("pk_strategy offset needs exactly one of offset or prefix")
        return self


class PkToken(BaseModel):
    """Append: keys go through the column's (reversible) token method."""

    model_config = _HIDDEN
    kind: Literal["token"] = "token"


PkStrategy = Annotated[PkGenerate | PkOffset | PkToken, Field(discriminator="kind")]


class FilesSpec(BaseModel):
    """``files`` targets: format, object key prefix, compression and rows per part file."""

    model_config = _HIDDEN
    format: Literal["csv", "parquet", "jsonl"] = "parquet"
    prefix: str = Field(default="", max_length=200, pattern=r"^[A-Za-z0-9/_.-]*$")
    compression: Literal["none", "gzip", "snappy", "zstd"] = "snappy"
    rows_per_file: int = Field(default=1_000_000, ge=1000)


class IncompatibleValue(ValueError):
    """A transformed value cannot be stored in its column (never carries the value)."""

    def __init__(
        self,
        kind: str,
        code: Literal["incompatible_method", "length_overflow"] = "incompatible_method",
    ) -> None:
        super().__init__(f"{code}: output does not fit a {kind} column")
        self.code = code


class UnsupportedKey(ValueError):
    """A key column kind (or a NULL key) that keyset paging cannot handle."""


class ConnectorError(Exception):
    """A source/target failure with a stable code (``source_error``, ``target_error``, …).

    ``detail`` is a driver *code* or a value-free phrase, never a driver message.
    """

    def __init__(self, code: str, detail: str = "", *, retryable: bool = False) -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code, self.detail, self.retryable = code, detail, retryable


class ConnectionTestResult(BaseModel):
    """Outcome of ``Connector.test`` (contract §4.2); checks and capabilities are value-free."""

    model_config = _HIDDEN
    ok: bool
    engine: ConnEngine
    server_version: str | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)  # {name, ok, severity, detail?}
    capabilities: dict[str, bool] = Field(default_factory=dict)
    error_code: str | None = None


class PrepareReport(BaseModel):
    """What ``BatchWriter.prepare`` did (or would do under ``dry_run``)."""

    model_config = _HIDDEN
    created: bool = False
    truncated: bool = False
    ledger: bool = False
    existing_rows: int | None = None
    checks: list[dict[str, Any]] = Field(default_factory=list)
    append_upper: int | None = None  # same-table append: exclusive upper bound = max(key) + 1


class LedgerMark(BaseModel):
    """The write-ledger row of one partition, written in the same transaction as its batch.

    ``rows`` is the cumulative number of rows the partition has written (added: a resume
    reconciles the partition counters with the ledger).
    """

    model_config = _HIDDEN
    job_id: str
    partition_no: int
    batch_no: int
    last_key: EncodedKey
    rows: int = 0


def encode_key(values: tuple[Any, ...], columns: list[ColumnInfo]) -> EncodedKey:
    """Tagged, lossless text form of a key (ts keeps full precision, char keeps padding)."""
    out: EncodedKey = []
    for value, column in zip(values, columns, strict=True):
        tag = _KEY_TAG.get(column.kind)
        if tag is None:
            raise UnsupportedKey(f"key columns of kind {column.kind} cannot be paged")
        if value is None:
            raise UnsupportedKey("key columns cannot be NULL")
        if isinstance(value, bytes | bytearray | memoryview):
            text = bytes(value).hex()
        elif isinstance(value, Decimal):
            text = format(value, "f")
        elif isinstance(value, date | datetime):
            text = value.isoformat()
        else:
            text = str(value)
        out.append({"t": tag, "v": text})
    return out


def decode_key(encoded: EncodedKey, columns: list[ColumnInfo]) -> tuple[Any, ...]:
    """Inverse of :func:`encode_key`: driver values for bind parameters."""
    values: list[Any] = []
    for item, column in zip(encoded, columns, strict=True):
        tag, text = item["t"], item["v"]
        if tag == "int":
            values.append(int(text))
        elif tag == "dec":
            values.append(Decimal(text))
        elif tag in ("str", "char", "oid"):
            values.append(text)
        elif tag == "date":
            values.append(date.fromisoformat(text))
        elif tag == "ts":
            # Oracle TIMESTAMP(7-9) keys travel as text (contract §4.2); PG keys are microseconds.
            values.append(text if (column.ts_precision or 6) > 6 else datetime.fromisoformat(text))
        elif tag == "uuid":
            values.append(uuid.UUID(text))
        elif tag == "bytes":
            values.append(bytes.fromhex(text))
        else:
            raise UnsupportedKey("unknown key tag")
    return tuple(values)


def key_tuple(row: Row, shape: TableShape) -> tuple[Any, ...]:
    """The key values of ``row`` in key order."""
    return tuple(row[k] for k in shape.key)


def key_fingerprint(encoded: EncodedKey | None) -> str | None:
    """A short, non-reversible handle for a checkpoint (shown in the console instead of the key)."""
    if encoded is None:
        return None
    digest = hashlib.sha256(json.dumps(encoded, sort_keys=True).encode("utf-8")).hexdigest()
    return digest[:8]


def to_engine_text(value: Any, column: ColumnInfo) -> str | None:
    """Canonical text the engine sees (contract §4.1)."""
    if value is None:
        return None
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, dict | list):
        return json.dumps(value, sort_keys=True, ensure_ascii=False)
    if column.kind == "char":
        return str(value).rstrip(" ")
    return str(value)


def _check_length(text: str, column: ColumnInfo) -> None:
    if column.length is not None and len(text) > column.length:
        raise IncompatibleValue(column.kind, "length_overflow")


def _decimal(text: str, column: ColumnInfo) -> Decimal:
    if not _DEC.fullmatch(text):
        raise IncompatibleValue(column.kind)
    try:
        number = Decimal(text)
    except InvalidOperation:
        raise IncompatibleValue(column.kind) from None
    if column.precision is None:
        return number
    scale = column.scale or 0
    whole = len(text.lstrip("-").split(".")[0].lstrip("0"))
    if whole > column.precision - scale:
        raise IncompatibleValue(column.kind, "length_overflow")
    # A context wide enough for the declared precision (the default context holds 28 digits).
    ctx = Context(prec=max(28, column.precision + 2))
    try:
        return number.quantize(Decimal(1).scaleb(-scale), context=ctx)
    except InvalidOperation:
        raise IncompatibleValue(column.kind, "length_overflow") from None


def from_engine_text(text: str | None, column: ColumnInfo) -> Any:
    """Driver value for ``text`` in ``column``; :class:`IncompatibleValue` when it cannot fit."""
    if text is None:
        return None
    kind = column.kind
    if kind in NEVER_TRANSFORMED_KINDS:
        raise IncompatibleValue(kind)
    if kind == "int":
        if not _INT.fullmatch(text):
            raise IncompatibleValue(kind)
        return int(text)
    if kind == "decimal":
        return _decimal(text, column)
    if kind == "float":
        try:
            return float(text)
        except ValueError:
            raise IncompatibleValue(kind) from None
    if kind == "bool":
        if text.lower() not in ("true", "false"):
            raise IncompatibleValue(kind)
        return text.lower() == "true"
    if kind == "date":
        try:
            return date.fromisoformat(text)
        except ValueError:
            raise IncompatibleValue(kind) from None
    if kind == "timestamp":
        try:
            return datetime.fromisoformat(text)
        except ValueError:
            raise IncompatibleValue(kind) from None
    if kind == "uuid":
        try:
            return uuid.UUID(text)
        except ValueError:
            raise IncompatibleValue(kind) from None
    if kind == "json":
        try:
            json.loads(text)
        except ValueError:
            raise IncompatibleValue(kind) from None
        return text
    if kind == "char":
        _check_length(text, column)
        return text.ljust(column.length) if column.length else text
    _check_length(text, column)
    return text


# --- write side and protocols (contract §4.2) ------------------------------------------------


class ResolvedTarget(BaseModel):
    """A run's target after validation and prepare (built by ``targets.py``, used by writers)."""

    model_config = _HIDDEN
    job_id: str
    write_mode: WriteMode
    connection_id: str
    engine: ConnEngine
    object: ObjectRef | None = None  # None for files
    same_as_source: bool = False
    create_if_missing: bool = True
    table_load: Literal["truncate_insert", "upsert"] = "truncate_insert"
    pk_strategy: PkStrategy | None = None
    files: FilesSpec | None = None
    ledger_owner: str | None = None
    key: list[str] = Field(default_factory=list)  # key column names (source = target names)
    write_columns: list[str] = Field(
        default_factory=list
    )  # in_place: SET list; else written columns
    generated_key: bool = False  # append+generate: key columns omitted from INSERT
    target_shape: TableShape | None = None  # existing table, or the table prepare will create
    column_types: dict[str, str] = Field(default_factory=dict)  # DDL type per column (create)


class Connector(Protocol):
    """A source (and, for databases, target) engine (contract §4.2).

    Rules every implementation obeys (``tests/connectors/conformance.py``): source reads are
    read-only (PG ``BEGIN READ ONLY`` + ``statement_timeout``; never ``FOR UPDATE``); partitions
    are read in key order with the keyset predicate ``key >= lower AND key < upper AND key >
    after_key``; identifiers are interpolated only after ``selection.quote_ident``; nothing logs or
    raises a row value or a key.
    """

    engine: Engine

    async def open(self) -> None:
        """Open the driver pool/client."""
        ...

    async def close(self) -> None:
        """Close the driver pool/client."""
        ...

    async def test(
        self,
        *,
        role: Literal["source", "target"],
        obj: ObjectRef | None = None,
        write_mode: WriteMode | None = None,
    ) -> ConnectionTestResult:
        """Value-free connectivity and privilege checks for ``role``."""
        ...

    async def list_objects(self, owner: str | None = None, *, limit: int = 500) -> list[ObjectRef]:
        """Tables/collections visible to the connection (optionally of one owner)."""
        ...

    async def describe(self, obj: ObjectRef) -> TableShape:
        """Columns, kinds and the paging key of ``obj``."""
        ...

    async def count(
        self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar]
    ) -> int:
        """Rows the selection matches."""
        ...

    async def sample(
        self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar], n: int
    ) -> list[Row]:
        """Up to ``n`` sampled rows (discovery)."""
        ...

    async def sample_keys(
        self,
        shape: TableShape,
        selection: Selection,
        params: dict[str, JsonScalar],
        n: int,
        *,
        upper: tuple[Any, ...] | None = None,
    ) -> list[tuple[Any, ...]]:
        """Up to ``n`` keys ordered by the database; only keys ``< upper`` (the read bound)."""
        ...

    async def key_at_offset(
        self,
        shape: TableShape,
        selection: Selection,
        params: dict[str, JsonScalar],
        offset: int,
    ) -> tuple[Any, ...] | None:
        """The key at ``offset`` in key order (``max_rows`` → the global upper bound), or None."""
        ...

    def read_partition(
        self,
        shape: TableShape,
        selection: Selection,
        params: dict[str, JsonScalar],
        key_range: KeyRange,
        *,
        after_key: tuple[Any, ...] | None,
        batch: int,
    ) -> AsyncIterator[list[Row]]:
        """Batches of rows in ``key_range`` after ``after_key``, ordered by key, keyset paged."""
        ...

    async def writer(self, target: ResolvedTarget, source_shape: TableShape) -> BatchWriter:
        """A writer for ``target`` (in_place, append, table) on this engine."""
        ...


class BatchWriter(Protocol):
    """Writes transformed batches to a target (contract §4.2).

    ``write_batch`` is ONE target transaction holding the rows and the partition's ledger mark
    (``in_place``/``append``), so a resumed partition never applies a batch twice; on resume the
    ledger mark is authoritative over the queue's checkpoint. ``table`` loads upsert on the key;
    ``files`` writes one object per partition, uploaded by ``finish_partition``.
    """

    async def prepare(self, *, dry_run: bool) -> PrepareReport:
        """Create-if-missing, truncate (table), ledger table, capability checks."""
        ...

    async def precheck_collisions(self, keys: AsyncIterator[list[tuple[Any, ...]]]) -> int:
        """Append offset/token: how many transformed keys already exist (a count only)."""
        ...

    async def read_ledger(self, job_id: str, partition_no: int) -> LedgerMark | None:
        """The partition's last committed mark, or None."""
        ...

    async def write_batch(self, rows: list[Row], *, mark: LedgerMark) -> int:
        """Write ``rows`` and ``mark`` in one transaction; returns rows written."""
        ...

    async def finish_partition(self, job_id: str, partition_no: int) -> dict[str, Any]:
        """Files: upload the part object and return its metadata; ``{}`` otherwise."""
        ...

    async def clear_ledger(self, job_id: str) -> int:
        """Finalize: delete the job's ledger rows; returns rows deleted."""
        ...

    async def close(self) -> None:
        """Release the writer's connections."""
        ...
