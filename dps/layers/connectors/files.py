"""``files`` targets: CSV / Parquet / JSONL part files per partition and a manifest (contract §13).

Key layout: ``<base_prefix>/<files.prefix>/<job_id>/part-<partition:05d>-<file:03d>.<ext>`` and
``…/<job_id>/manifest.json`` (``base_prefix`` is the connection's prefix; empty parts are left
out). A partition's rows are spooled to local files (rolled every ``rows_per_file`` rows) and
uploaded by :meth:`FilesWriter.finish_partition`, which returns ``{"files": [{key, rows, bytes,
sha256}]}`` for ``dps_job_partition.meta``. A resumed partition calls
:meth:`FilesWriter.reset_partition` and rewrites its objects under the same keys: the content is
deterministic (gzip headers carry no timestamp), so a rewrite produces the same bytes. The
manifest is written **last**, at finalize, so a loader that sees it sees complete files.

One cached writer serves every holder of a job in a process (a zombie whose lease was reclaimed
and the re-claimed holder alike), so a spool takes each batch number exactly once and in order
(``mark.batch_no`` must follow the spool's last batch, under the spool's lock; anything else is
:class:`~dps.layers.connectors.base.LedgerConflict` and nothing is spooled), and every spool has
its own folder. Batch ``n`` of a partition is the same rows whoever reads it, so a spool of
batches ``1..n`` is complete whichever holder wrote each one.

Values: CSV — a header row, ``None`` → empty, ``Decimal`` → plain digits, dates ISO-8601, bytes →
hex; JSONL — one compact object per row with the same scalar rules (numbers stay numbers,
``Decimal`` becomes a string); Parquet — a ``pyarrow`` schema from the column kinds. A MongoDB
field holding a sub-document or an array is one column (:func:`file_columns`): a nested JSON value
in JSONL, its JSON text in CSV and Parquet, every nested leaf by the same scalar rules (``datetime``
ISO-8601, ``Decimal128`` its digits, ``ObjectId`` its hex, ``Int64`` an int). An Oracle
``TIMESTAMP(7-9)`` or ``TIMESTAMP WITH TIME ZONE`` arrives as exact text and stays text in Parquet;
a row locator (``ROWID``) is never written. ``snappy`` and ``zstd`` apply to Parquet only; text
formats take ``gzip`` or nothing. Errors carry codes; nothing here logs a value.
"""

from __future__ import annotations

import asyncio
import csv
import gzip
import io
import json
import math
import shutil
import tempfile
from collections.abc import AsyncIterator, Mapping
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from uuid import UUID

import pyarrow as pa
import pyarrow.parquet as pq

from dps.layers.connectors import docpaths
from dps.layers.connectors.base import (
    ColumnInfo,
    FilesSpec,
    LedgerConflict,
    LedgerMark,
    PrepareReport,
    Row,
    TableShape,
)
from dps.layers.connectors.targets import PrepareFailure
from dps.storage import ObjectStore, StorageError, StoredObject

#: File extension per format (gzip-compressed text formats add ``.gz``).
EXTENSIONS: dict[str, str] = {"csv": "csv", "jsonl": "jsonl", "parquet": "parquet"}
CONTENT_TYPES: dict[str, str] = {
    "csv": "text/csv",
    "jsonl": "application/x-ndjson",
    "parquet": "application/vnd.apache.parquet",
}
GZIP_CONTENT_TYPE = "application/gzip"
_STRING_KINDS = frozenset({"text", "char", "lob_text", "json", "uuid", "object_id", "other"})
_CHUNK = 1 << 20


def effective_compression(spec: FilesSpec) -> str:
    """The compression actually applied (``snappy``/``zstd`` are Parquet codecs only)."""
    if spec.format == "parquet":
        return spec.compression
    return "gzip" if spec.compression == "gzip" else "none"


def file_columns(shape: TableShape, names: list[str]) -> list[ColumnInfo]:
    """The columns a files target writes for the projected ``names``: a document field is one
    JSON-text column (:func:`~dps.layers.connectors.docpaths.field_columns`), and a row locator
    (the ``ROWID`` pseudo key of a key-less Oracle table) is never written — it is not table data,
    and in a synthetic file it would point back at the source row (S1-M3 r1 B1)."""
    return [c for c in docpaths.field_columns(shape, names) if not c.pseudo]


def _text_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value).hex()
    if isinstance(value, Mapping | list | tuple):
        return json.dumps(_plain(value), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    to_decimal = getattr(value, "to_decimal", None)  # BSON Decimal128 (no driver import here)
    if callable(to_decimal):
        return format(to_decimal(), "f")
    return str(value)  # ObjectId → its hex, UUID → its text, other driver types → their text


def _plain(value: Any) -> Any:
    """``value`` as plain JSON data (S1-M3 r1 B3): containers recursively, with the scalar rules
    of the module docstring for every leaf — a nested ``datetime`` is ISO-8601, a ``Decimal`` or
    ``Decimal128`` its digits, an ``ObjectId`` its hex, an ``Int64`` an int, bytes hex."""
    if value is None or isinstance(value, bool | str):
        return value
    if isinstance(value, int):
        return int(value)  # an Int64 is an int
    if isinstance(value, float):
        return value if math.isfinite(value) else str(value)
    if isinstance(value, Mapping):
        return {str(k): _plain(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_plain(v) for v in value]
    return _text_value(value)


def _json_value(value: Any) -> Any:
    return _plain(value)


def _text_timestamp(column: ColumnInfo) -> bool:
    """A timestamp the connector carries as text: Oracle ``TIMESTAMP(7-9)`` (FF9) and ``TIMESTAMP
    WITH TIME ZONE`` (FF9 and the offset), as ``oracle_sql.is_ts9`` / ``is_tz`` read them. A
    microsecond Parquet timestamp would lose the digits, so the file keeps the exact text (S1-M3
    r1 B4)."""
    return "TIME ZONE" in column.data_type or (column.ts_precision or 0) > 6


def _arrow_type(column: ColumnInfo) -> pa.DataType:
    kind = column.kind
    if kind == "int":
        return pa.int64()
    if kind == "decimal":
        if column.precision and column.precision <= 38:
            return pa.decimal128(column.precision, column.scale or 0)
        return pa.string()  # unconstrained or wider than decimal128: exact text
    if kind == "float":
        return pa.float64()
    if kind == "date":
        return pa.date32()
    if kind == "timestamp":
        if _text_timestamp(column):
            return pa.string()
        aware = "with time zone" in column.data_type
        return pa.timestamp("us", tz="UTC") if aware else pa.timestamp("us")
    if kind == "bool":
        return pa.bool_()
    if kind in ("binary", "lob_binary"):
        return pa.binary()
    return pa.string()


def arrow_schema(columns: list[ColumnInfo]) -> pa.Schema:
    """Parquet schema from the output column kinds (module docstring)."""
    return pa.schema([pa.field(c.name, _arrow_type(c)) for c in columns])


def _arrow_value(value: Any, arrow_type: pa.DataType) -> Any:
    if value is None:
        return None
    if pa.types.is_string(arrow_type) and not isinstance(value, str):
        return _text_value(value)
    if isinstance(value, UUID):
        return str(value)
    return value


class _Spool:
    """Part files of one partition being written (rolled every ``rows_per_file`` rows)."""

    def __init__(
        self, root: Path, partition: int, spec: FilesSpec, columns: list[ColumnInfo]
    ) -> None:
        self.root, self.partition, self.spec, self.columns = root, partition, spec, columns
        self.names = [c.name for c in columns]
        self.schema = arrow_schema(columns)
        self.types = [self.schema.field(n).type for n in self.names]
        self.files: list[tuple[Path, int]] = []  # (path, rows)
        self.gzip = effective_compression(spec) == "gzip"
        self.batch = 0  # the last batch number spooled
        self.lock = asyncio.Lock()  # one batch, reset or upload at a time
        self._raw: Any = None
        self._fh: Any = None
        self._writer: Any = None
        self._rows = 0

    def suffix(self) -> str:
        """``.<ext>`` plus ``.gz`` for gzip text formats."""
        return f".{EXTENSIONS[self.spec.format]}" + (".gz" if self.gzip else "")

    def _open(self) -> None:
        path = self.root / f"part-{self.partition:05d}-{len(self.files):03d}{self.suffix()}"
        path.parent.mkdir(parents=True, exist_ok=True)
        if self.spec.format == "parquet":
            codec = self.spec.compression
            self._writer = pq.ParquetWriter(
                str(path), self.schema, compression="NONE" if codec == "none" else codec
            )
        else:
            self._raw = path.open("wb")
            binary: Any = (
                gzip.GzipFile(filename="", mode="wb", fileobj=self._raw, mtime=0)
                if self.gzip
                else self._raw
            )
            self._fh = io.TextIOWrapper(binary, encoding="utf-8", newline="")
            if self.spec.format == "csv":
                self._writer = csv.writer(self._fh)
                self._writer.writerow(self.names)
        self.files.append((path, 0))
        self._rows = 0

    def write(self, rows: list[Row]) -> None:
        """Append ``rows``, rolling to a new part file every ``rows_per_file`` rows."""
        index = 0
        while index < len(rows):
            if self._fh is None and self._writer is None:
                self._open()
            room = self.spec.rows_per_file - self._rows
            chunk = rows[index : index + room]
            self._emit(chunk)
            self._rows += len(chunk)
            path, count = self.files[-1]
            self.files[-1] = (path, count + len(chunk))
            index += len(chunk)
            if self._rows >= self.spec.rows_per_file:
                self.close()

    def _emit(self, rows: list[Row]) -> None:
        if self.spec.format == "parquet":
            columns = {
                name: [_arrow_value(r.get(name), t) for r in rows]
                for name, t in zip(self.names, self.types, strict=True)
            }
            self._writer.write_table(pa.Table.from_pydict(columns, schema=self.schema))
        elif self.spec.format == "csv":
            self._writer.writerows([[_text_value(r.get(n)) for n in self.names] for r in rows])
        else:
            for r in rows:
                doc = {n: _json_value(r.get(n)) for n in self.names}
                self._fh.write(json.dumps(doc, ensure_ascii=False, separators=(",", ":")) + "\n")

    def close(self) -> None:
        """Close the open part file (if any)."""
        if self.spec.format == "parquet" and self._writer is not None:
            self._writer.close()
        if self._fh is not None:
            self._fh.close()  # closes the gzip stream and the raw file too
        if self._raw is not None and not self._raw.closed:
            self._raw.close()
        self._raw = self._fh = self._writer = None

    def discard(self) -> None:
        """Close and delete every part file of this partition."""
        self.close()
        for path, _ in self.files:
            path.unlink(missing_ok=True)
        self.files = []


async def _chunks(path: Path) -> AsyncIterator[bytes]:
    fh = await asyncio.to_thread(path.open, "rb")
    try:
        while part := await asyncio.to_thread(fh.read, _CHUNK):
            yield part
    finally:
        fh.close()


class FilesWriter:
    """``BatchWriter`` for ``files`` targets over an :class:`~dps.storage.ObjectStore`."""

    def __init__(
        self,
        store: ObjectStore,
        spec: FilesSpec,
        *,
        job_id: str,
        base_prefix: str,
        columns: list[ColumnInfo],
        spool_root: Path | None = None,
    ) -> None:
        self.store, self.spec, self.job_id, self.columns = store, spec, job_id, columns
        self.base_prefix = base_prefix.strip("/")
        root = spool_root or Path(tempfile.gettempdir()) / "dps-spool" / job_id
        self.spool_root = Path(root)
        self._spools: dict[int, _Spool] = {}
        self._made = 0  # spools created (each gets its own folder)

    def base_key(self) -> str:
        """``<base_prefix>/<files.prefix>/<job_id>`` (empty parts left out)."""
        parts = [self.base_prefix, self.spec.prefix.strip("/"), self.job_id]
        return "/".join(p for p in parts if p)

    def content_type(self) -> str:
        """Content type of the part objects."""
        return GZIP_CONTENT_TYPE if self._gzip() else CONTENT_TYPES[self.spec.format]

    def _gzip(self) -> bool:
        return effective_compression(self.spec) == "gzip"

    def _new_spool(self, partition_no: int) -> _Spool:
        self._made += 1
        folder = self.spool_root / f"p{partition_no:05d}-{self._made:04d}"
        spool = _Spool(folder, partition_no, self.spec, self.columns)
        self._spools[partition_no] = spool
        return spool

    async def prepare(self, *, dry_run: bool) -> PrepareReport:
        """Probe the store with a put and a delete (no customer data) unless ``dry_run``.

        Raises:
            PrepareFailure: ``target_invalid`` when the store refuses the probe.
        """
        report = PrepareReport()
        if dry_run:
            return report
        key = f"{self.base_key()}/dps-probe.txt"
        try:
            await self.store.put(key, b"dps write probe", content_type="text/plain")
            await self.store.delete(key)
        except StorageError as exc:
            raise PrepareFailure(
                "target_invalid", f"the target store refused a probe object ({exc.code})"
            ) from None
        report.checks.append({"name": "put_object", "ok": True})
        return report

    async def precheck_collisions(self, keys: AsyncIterator[list[tuple[Any, ...]]]) -> int:
        """Files have no keys to collide with."""
        return 0

    async def read_ledger(self, job_id: str, partition_no: int) -> LedgerMark | None:
        """Files keep no ledger: a resumed partition restarts from its lower bound."""
        return None

    async def write_batch(self, rows: list[Row], *, mark: LedgerMark) -> int:
        """Spool ``rows`` for ``mark.partition_no`` (uploaded by :meth:`finish_partition`).

        Raises:
            LedgerConflict: ``mark.batch_no`` does not follow the spool's last batch (another
                holder of the partition spooled it, or reset or uploaded the spool meanwhile).
        """
        spool = self._spools.get(mark.partition_no)
        if spool is None:
            if mark.batch_no != 1:
                raise LedgerConflict()
            spool = self._new_spool(mark.partition_no)
        async with spool.lock:
            if self._spools.get(mark.partition_no) is not spool or mark.batch_no != spool.batch + 1:
                raise LedgerConflict()
            if rows:
                await asyncio.to_thread(spool.write, rows)
            spool.batch = mark.batch_no
        return len(rows)

    async def reset_partition(self, partition_no: int) -> None:
        """Drop what a partition spooled so far (a resumed partition starts again); waits for a
        batch another holder is spooling into it."""
        spool = self._spools.pop(partition_no, None)
        if spool is not None:
            async with spool.lock:
                await asyncio.to_thread(spool.discard)
                await asyncio.to_thread(shutil.rmtree, spool.root, True)

    async def finish_partition(
        self, job_id: str, partition_no: int, *, batch_no: int | None = None
    ) -> dict[str, Any]:
        """Upload the partition's part files; ``{"files": [{key, rows, bytes, sha256}]}``.

        ``batch_no`` is the caller's last batch (0: it spooled none): the spool is uploaded only
        when it ends with that batch, so a holder never uploads a spool that another holder of
        the partition reset and restarted.

        Raises:
            LedgerConflict: the spool does not end with ``batch_no`` (nothing uploaded).
            StorageError: the store refused an upload (the run handler retries the partition).
        """
        spool = self._spools.get(partition_no)
        if spool is None:
            if batch_no:
                raise LedgerConflict()
            return {"files": []}
        async with spool.lock:
            if self._spools.get(partition_no) is not spool or (
                batch_no is not None and spool.batch != batch_no
            ):
                raise LedgerConflict()
            del self._spools[partition_no]
            await asyncio.to_thread(spool.close)
        out: list[dict[str, Any]] = []
        try:
            for path, rows in spool.files:
                key = f"{self.base_key()}/{path.name}"
                stored = await self.store.put(key, _chunks(path), content_type=self.content_type())
                out.append(
                    {
                        "key": stored.key,
                        "rows": rows,
                        "bytes": stored.bytes,
                        "sha256": stored.sha256,
                    }
                )
        finally:
            await asyncio.to_thread(spool.discard)
            await asyncio.to_thread(shutil.rmtree, spool.root, True)
        return {"files": out}

    async def clear_ledger(self, job_id: str) -> int:
        """Files keep no ledger."""
        return 0

    async def close(self) -> None:
        """Discard every unfinished spool."""
        for partition_no in list(self._spools):
            await self.reset_partition(partition_no)
        await asyncio.to_thread(shutil.rmtree, self.spool_root, True)


def build_manifest(
    *,
    job_id: str,
    spec: FilesSpec,
    columns: list[ColumnInfo],
    parts: list[dict[str, Any]],
    created_at: datetime,
) -> dict[str, Any]:
    """The manifest a loader reads (value-free: names, types, keys, counts, hashes)."""
    objects = sorted(
        (
            {
                "key": p["key"],
                "rows": int(p["rows"]),
                "bytes": int(p["bytes"]),
                "sha256": p["sha256"],
            }
            for p in parts
        ),
        key=lambda o: o["key"],
    )
    return {
        "manifest_version": "dps.files.v1",
        "job_id": job_id,
        "format": spec.format,
        "compression": effective_compression(spec),
        "columns": [{"name": c.name, "type": c.data_type or c.kind} for c in columns],
        "objects": objects,
        "rows_total": sum(o["rows"] for o in objects),
        "created_at": created_at.isoformat(),
    }


def manifest_bytes(manifest: dict[str, Any]) -> bytes:
    """The manifest as written (sorted keys, indented UTF-8 JSON)."""
    return json.dumps(manifest, sort_keys=True, indent=2, ensure_ascii=False).encode("utf-8")


async def write_manifest(
    store: ObjectStore, base_key: str, manifest: dict[str, Any]
) -> StoredObject:
    """Write ``<base_key>/manifest.json`` (call it last, after every part is uploaded)."""
    return await store.put(
        f"{base_key}/manifest.json", manifest_bytes(manifest), content_type="application/json"
    )
