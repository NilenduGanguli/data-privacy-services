"""MongoDB connector over pymongo's ``AsyncMongoClient`` (contract §1: Motor is deprecated).

Reads. A keyed collection (one range-pageable ``_id`` type in the ``$sample`` *and* at both ends of
the ``_id`` index, ``describe``) pages on ``_id`` with a fresh ``find`` per batch (``_id >= lower
AND _id < upper AND _id > after``, sorted by ``_id`` in the server, ``limit = batch``), so no server
cursor outlives a batch; a document whose ``_id`` has another type inside the same BSON bracket (a
double among ints) fails the read closed (``key_type_mixed``). A key-less collection (mixed or
document ``_id``s, where BSON type bracketing would drop documents from a range) is one partition
read through one cursor in ``_id`` order. Reads use ``readConcern: majority`` on a replica set
(contract §4.2). ``selection.columns`` project top-level fields (a dotted column reads its whole
top-level field, as ``read_projection`` plans it). Documents travel whole: the engine transforms nested paths and arrays (``[*]``)
and restores each leaf it wrote to the original leaf's BSON type (``mongo_docs.restore_leaf``);
every other value reaches the writer as the driver decoded it.
``selection.sample`` keeps a document when ``HMAC-SHA256(seed, BSON({_id}))[:4] < percent·2³²`` —
a function of the document alone, so the count, every partition and a resumed page read one sample.
``describe`` and the discovery/key samples use ``$sample``; when the server's pseudo-random cursor
fails with 28799 (a collection that is mostly deleted records, such as a ``table`` target emptied
and refilled by every run) twice, the sample is taken by a collection scan (:func:`sample_pipeline`).

Writes (:class:`MongoWriter`). Every document goes through ``make_bson_safe`` (a copy; only
``decimal.Decimal`` and ``datetime.date``, which the driver cannot encode, change). A ``table`` load
into a created or emptied collection replaces whole documents by ``_id``
(``ReplaceOne(upsert=True)``); ``table_load: upsert`` sets the row's fields through sub-documents
(``UpdateOne({_id}, mongo_docs.upsert_update(doc), upsert=True)``: ``$set`` through sub-documents,
empty sub-documents only ``$setOnInsert``), so a matched target document keeps every field the row
does not carry (arrays are set whole). Both are unordered, idempotent and unledgered. ``in_place``
(``$set`` of the document's leaf paths; a projected read holds whole top-level fields, so every
array position is the stored one and no field is dropped) and ``append`` (``InsertOne``; ``generate`` lets the driver assign a new
ObjectId) run in one multi-document transaction with the partition's ledger document in
``dps_write_ledger``: the batch is applied only when the ledger holds ``mark.batch_no - 1`` (the
caller's own mark, contract §4.2), else :class:`LedgerConflict` and nothing is written. A standalone
server has no transactions and is refused at ``prepare`` (``transactions_unsupported``). A
duplicate key in the rows is :class:`PkCollision`; server messages are never read.

Every operation carries ``comment = "dps:<action>:<job id>"`` (the analogue of Oracle's
module/action/client_identifier; visible in the profiler and ``currentOp``).

Errors. Server messages can quote a document (``E11000 … dup key: { … }``) and client errors can
quote the URI, so only error *codes* leave this module (:func:`mongo_code`), raised ``from None``;
``repr`` names the database only. Filters are validated against the allow-list before any call
(``selection.compile_mongo_filter``): a ``$where`` never reaches the server.
"""

from __future__ import annotations

import asyncio
import datetime as dt
import hashlib
import hmac
import re
import time
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, Final, Literal, TypeVar

import bson
from bson import ObjectId
from pymongo import AsyncMongoClient, InsertOne, ReplaceOne, UpdateOne
from pymongo.errors import (
    BulkWriteError,
    CollectionInvalid,
    ConfigurationError,
    ConnectionFailure,
    DuplicateKeyError,
    OperationFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
)
from pymongo.read_concern import ReadConcern
from pymongo.uri_parser import parse_uri
from pymongo.write_concern import WriteConcern

from dps.config import Settings
from dps.jobs_models import ConnectionParams, Selection
from dps.layers.connectors.base import (
    ColumnInfo,
    ConnectionTestResult,
    ConnectorError,
    Engine,
    JsonScalar,
    KeyRange,
    LedgerConflict,
    LedgerMark,
    ObjectRef,
    PrepareReport,
    ResolvedTarget,
    Row,
    TableShape,
    WriteMode,
    decode_key,
    encode_key,
)
from dps.layers.connectors.docpaths import dotted, leaf_updates
from dps.layers.connectors.mongo_docs import id_label, infer_shape, make_bson_safe, upsert_update
from dps.layers.connectors.postgres import PkCollision
from dps.layers.connectors.selection import compile_mongo_filter, validate_selection
from dps.layers.connectors.targets import PrepareFailure
from dps.observability import CONNECTOR_CALLS, get_logger

logger = get_logger(__name__)
T = TypeVar("T")

#: The write ledger collection (contract §4.2), created in the target database.
LEDGER_COLLECTION: Final = "dps_write_ledger"
#: Documents ``describe`` samples (``$sample``) to infer the path shape.
DESCRIBE_SAMPLE_MAX: Final = 500
_DB: Final = re.compile(r"[A-Za-z0-9_-]{1,63}")
_HOST: Final = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9.-]{0,251}[A-Za-z0-9])?")
_AUTH_FAILED: Final = 18  # AuthenticationFailed: a retry cannot fix a refused login
_TIME_LIMIT: Final = 50  # MaxTimeMSExpired
_INTERNAL: Final = frozenset({LEDGER_COLLECTION})
_SCAN_BATCH: Final = 1000
_DUPLICATE_KEY: Final = 11000
#: How long ``prepare`` waits for the clock to pass the newest ObjectId's second (same-collection
#: ``generate``); a collection whose ids are further ahead was written by a skewed clock.
_CLOCK_WAIT_MAX_S: Final = 5.0
_CAPS: Final[dict[str, bool]] = {
    "transactions": False,
    "merge": True,
    "identity": False,
    "sample": True,
    "rowid": False,
    "copy": False,
}


#: ``$sample``'s pseudo-random cursor (``$sample`` first, N < 5 % of a collection of > 100
#: documents) gives up after 100 duplicate documents in a row: "likely a sporadic failure". It is
#: not sporadic on a collection that is mostly deleted records — a ``table`` target emptied and
#: refilled by every run: the cursor's random descent lands among them and walks to the same live
#: document, every time (review S6b r1 B1).
_RANDOM_CURSOR_DUPLICATES: Final = 28799
#: Tries of the pseudo-random cursor before a sample is taken without it.
_RANDOM_CURSOR_TRIES: Final = 2
#: Leads a ``$sample`` pipeline that must not use the pseudo-random cursor: every document passes
#: it, but unlike an empty ``$match`` (optimised away) it keeps ``$sample`` off the first stage, so
#: the server samples by a random sort of what the scan returns — as it does anyway for N ≥ 5 %.
ALL_DOCUMENTS: Final[dict[str, Any]] = {"$match": {"_id": {"$exists": True}}}
#: The hint of that pipeline: a collection scan (the ``_id`` index would fetch every document in
#: key order, at random places on disk).
NATURAL_ORDER: Final[dict[str, Any]] = {"$natural": 1}


def sample_pipeline(
    flt: dict[str, Any],
    n: int,
    *,
    project: dict[str, Any] | None = None,
    then: list[dict[str, Any]] | None = None,
    random_cursor: bool = True,
) -> list[dict[str, Any]]:
    """The ``$sample`` aggregation of up to ``n`` documents of ``flt``, projected, then ``then``.

    ``random_cursor=False`` leads with :data:`ALL_DOCUMENTS` when there is no filter (run it with
    the :data:`NATURAL_ORDER` hint); a filter already keeps ``$sample`` off the first stage.
    """
    head = [{"$match": flt}] if flt else ([] if random_cursor else [ALL_DOCUMENTS])
    tail = [{"$project": project}] if project else []
    return [*head, {"$sample": {"size": max(1, int(n))}}, *tail, *(then or [])]


def mongo_code(exc: BaseException) -> str:
    """``mongo:<server code>`` or ``mongo:<ExceptionClass>`` — never ``str(exc)`` or ``details``."""
    if isinstance(exc, BulkWriteError):
        errors = (exc.details or {}).get("writeErrors") or []
        code = errors[0].get("code") if errors else None
        return f"mongo:{code}" if isinstance(code, int) else "mongo:bulk"
    code = getattr(exc, "code", None)
    return f"mongo:{code}" if isinstance(code, int) else f"mongo:{type(exc).__name__}"


def connector_error(code: str, exc: BaseException) -> ConnectorError:
    """A value-free :class:`ConnectorError` for a driver error (raise it ``from None``).

    Unreachable servers are ``connection_failed`` (retryable); a refused login is
    ``connection_failed`` (not retryable: a retried password can lock the account); an operation
    over its time limit is ``statement_timeout``; a bad URI or client option is
    ``connection_invalid``; anything else keeps ``code`` with the driver code as detail.
    """
    detail = mongo_code(exc)
    if isinstance(exc, ServerSelectionTimeoutError):
        return ConnectorError("connection_failed", detail, retryable=True)
    if getattr(exc, "code", None) == _TIME_LIMIT or (
        getattr(exc, "timeout", False) and not isinstance(exc, BulkWriteError)
    ):
        return ConnectorError("statement_timeout", detail)
    if isinstance(exc, ConnectionFailure):
        return ConnectorError("connection_failed", detail, retryable=True)
    if getattr(exc, "code", None) == _AUTH_FAILED:
        return ConnectorError("connection_failed", detail)
    if isinstance(exc, ConfigurationError):
        return ConnectorError("connection_invalid", detail)
    return ConnectorError(code, detail)


def _check(name: str, ok: bool, severity: str = "required", **extra: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, "severity": severity, **extra}


def collection_name(obj: ObjectRef) -> str:
    """The collection of ``obj``: no owner (the database is on the connection), no ``$``,
    never a ``system.`` collection (``object_invalid``)."""
    if obj.owner is not None or "$" in obj.name or obj.name.startswith("system."):
        raise ConnectorError("object_invalid", "a MongoDB object is a collection name")
    return obj.name


def bson_id(value: Any, shape: TableShape) -> Any:
    """A decoded key back to its BSON type (M2's ``decode_key`` returns ObjectIds as hex text)."""
    col = next((c for c in shape.columns if c.path == "$._id"), None)
    if col is not None and col.kind == "object_id" and isinstance(value, str):
        if ObjectId.is_valid(value):
            return ObjectId(value)
    return value


def _projection(selection: Selection) -> dict[str, int] | None:
    """``columns`` as a find projection of **top-level fields** (``_id`` always): a dotted column
    reads its whole top-level field (``accounts.iban`` → ``accounts``), as ``read_projection``
    plans it (contract §5.2).

    A dotted inclusion projection is not position-exact: MongoDB drops the non-document elements
    of an array it crosses (``[{iban: A}, "note", {iban: B}]`` reads ``[{iban: A}, {iban: B}]``),
    so an in-place ``$set`` of ``accounts.1.iban`` would land on the wrong element and leave the
    source value in clear (review G3 r2 B1); a sparse sub-document would read ``{}``.
    """
    if not selection.columns:
        return None
    fields = {path.split(".", 1)[0] for path in selection.columns}
    return {**{f: 1 for f in sorted(fields) if f != "_id"}, "_id": 1}


def _key_family(shape: TableShape) -> str:
    """The ``id_label`` every ``_id`` of a keyed shape has (``int`` covers int32 and int64)."""
    col = next((c for c in shape.columns if c.path == "$._id"), None)
    label = col.data_type if col is not None else "other"
    return "int" if label == "long" else label


def _sampler(selection: Selection) -> Callable[[Any], bool] | None:
    """The deterministic ``selection.sample`` test of one ``_id`` (None without a sample)."""
    if selection.sample is None:
        return None
    threshold = int(selection.sample.percent / 100 * 2**32)
    key = str(selection.sample.seed or 0).encode()

    def keep(_id: Any) -> bool:
        digest = hmac.new(key, bson.encode({"_id": _id}), hashlib.sha256).digest()
        return int.from_bytes(digest[:4], "big") < threshold

    return keep


class MongoConnector:
    """Contract §4.2 ``Connector`` for MongoDB (whole documents are rows)."""

    engine: Engine = "mongo"

    def __init__(
        self,
        *,
        client_kwargs: dict[str, Any],
        database: str,
        settings: Settings,
        action: str,
        client_id: str | None,
    ) -> None:
        self._kwargs, self._db_name, self._settings = client_kwargs, database, settings
        self._action, self._client_id = action, client_id or ""
        self._client: AsyncMongoClient[dict[str, Any]] | None = None
        self._replica = False
        self._version: str | None = None

    def __repr__(self) -> str:
        return f"MongoConnector(database={self._db_name!r})"

    @property
    def comment(self) -> str:
        """``dps:<action>:<job id>`` — attached to every operation."""
        return f"dps:{self._action}:{self._client_id}"

    @property
    def max_time_ms(self) -> int:
        """Server-side time limit of every read (``DPS_CONNECTOR_STATEMENT_TIMEOUT_SECONDS``)."""
        return int(self._settings.connector_statement_timeout_seconds * 1000)

    @property
    def database(self) -> str:
        """The connection's database."""
        return self._db_name

    @property
    def replica_set(self) -> bool:
        """True when the server is a replica-set member or a mongos (transactions available)."""
        return self._replica

    @classmethod
    def from_secret(
        cls,
        secret: Mapping[str, str],
        params: ConnectionParams,
        settings: Settings,
        *,
        action: str,
        client_id: str | None,
    ) -> MongoConnector:
        """Build from a resolved secret: a ``uri`` (production: credentials and TLS live in the
        URI file), else ``host``/``port`` from ``params`` with ``username``/``password``.

        Allowed options (contract §5.1): ``authSource``, ``replicaSet``, ``directConnection``,
        ``tls``. The database is ``params.database``, else the URI's path.

        Raises:
            ConnectorError: ``connection_invalid`` (a URI that does not parse, a host or option
                that is not a plain name), ``database_required``.
        """
        kwargs: dict[str, Any] = {
            "serverSelectionTimeoutMS": settings.mongo_server_selection_timeout_ms,
            "connectTimeoutMS": int(settings.connector_connect_timeout_seconds * 1000),
            # not ``timeoutMS``: under client-side operation timeouts pymongo waits the whole
            # timeout for server selection (measured: an unreachable server took timeoutMS, not
            # serverSelectionTimeoutMS); the socket timeout bounds a hung call instead and every
            # read also carries ``maxTimeMS`` so the server stops the work
            "socketTimeoutMS": int(settings.connector_statement_timeout_seconds * 1000) + 5000,
            "appname": "dps",
            "tz_aware": False,
            "uuidRepresentation": "standard",
        }
        uri = (secret.get("uri") or "").strip()
        database = params.database
        if uri:
            try:
                parsed = parse_uri(uri)
            except (PyMongoError, ValueError):
                raise ConnectorError(
                    "connection_invalid", "the MongoDB URI does not parse"
                ) from None
            kwargs["host"] = uri
            database = database or parsed.get("database")
        else:
            if not params.host or not _HOST.fullmatch(params.host):
                raise ConnectorError("connection_invalid", "the host must be a plain name")
            kwargs.update(host=params.host, port=params.port or 27017)
        if secret.get("username"):
            kwargs.update(username=secret["username"], password=secret.get("password", ""))
        opts = params.options or {}
        for name in ("authSource", "replicaSet"):
            if name in opts:
                if not _DB.fullmatch(opts[name]):
                    raise ConnectorError("connection_invalid", f"{name} must be a plain name")
                kwargs[name] = opts[name]
        for flag in ("directConnection", "tls"):
            if flag in opts:
                kwargs[flag] = opts[flag].strip().lower() == "true"
        if not database:
            raise ConnectorError("database_required", "the connection names no database")
        if not _DB.fullmatch(database):
            raise ConnectorError("connection_invalid", "the database must be a plain name")
        return cls(
            client_kwargs=kwargs,
            database=database,
            settings=settings,
            action=action,
            client_id=client_id,
        )

    # -- plumbing ---------------------------------------------------------------------------------
    async def _guard(self, op: str, call: Awaitable[T], code: str = "source_error") -> T:
        """Await one driver call; a driver error becomes a value-free ConnectorError."""
        try:
            result = await call
        except PyMongoError as exc:
            CONNECTOR_CALLS.labels("mongo", op, "error").inc()
            err = connector_error(code, exc)
            logger.warning("mongo_call_failed", op=op, driver_code=err.detail)
            raise err from None
        CONNECTOR_CALLS.labels("mongo", op, "ok").inc()
        return result

    def client(self) -> AsyncMongoClient[dict[str, Any]]:
        """The open client (``connection_failed`` before :meth:`open`)."""
        if self._client is None:
            raise ConnectorError("connection_failed", "the connector is not open")
        return self._client

    @property
    def db(self) -> Any:
        """The connection's database handle."""
        return self.client()[self._db_name]

    def read_collection(self, name: str) -> Any:
        """A collection handle that reads with ``majority`` on a replica set."""
        concern = ReadConcern("majority") if self._replica else ReadConcern()
        return self.db.get_collection(name, read_concern=concern)

    async def open(self) -> None:
        """Create the client and learn the topology (``hello``) and version (``buildInfo``).

        Raises:
            ConnectorError: ``connection_failed`` / ``connection_invalid`` (value-free).
        """
        if self._client is not None:
            return
        client: AsyncMongoClient[dict[str, Any]] | None = None
        try:
            client = AsyncMongoClient(**self._kwargs)
            hello = await client.admin.command("hello", comment=self.comment)
            info = await client.admin.command("buildInfo", comment=self.comment)
        except PyMongoError as exc:
            CONNECTOR_CALLS.labels("mongo", "connect", "error").inc()
            err = connector_error("connection_failed", exc)
            logger.warning("mongo_call_failed", op="connect", driver_code=err.detail)
            if client is not None:
                await client.close()
            raise err from None
        CONNECTOR_CALLS.labels("mongo", "connect", "ok").inc()
        self._client = client
        self._replica = "setName" in hello or hello.get("msg") == "isdbgrid"
        self._version = str(info.get("version", "")) or None

    async def close(self) -> None:
        """Close the client (idempotent)."""
        client, self._client = self._client, None
        if client is not None:
            await client.close()

    # -- metadata ---------------------------------------------------------------------------------
    async def collections(self) -> set[str]:
        """Names of the database's collections (views excluded)."""
        await self.open()
        names = await self._guard(
            "list",
            self.db.list_collection_names(filter={"type": "collection"}, comment=self.comment),
        )
        return set(names)

    async def list_objects(self, owner: str | None = None, *, limit: int = 500) -> list[ObjectRef]:
        """Collections of the connection's database, sorted (``owner`` does not apply: the
        database is on the connection); internal and ``system.`` collections are left out."""
        limit = max(1, min(int(limit), 5000))
        out: list[ObjectRef] = []
        for name in sorted(await self.collections()):
            if name.startswith("system.") or name in _INTERNAL or "$" in name:
                continue
            try:
                out.append(ObjectRef(name=name))
            except ValueError:
                continue  # a name ObjectRef cannot carry (e.g. with a dot) is not offered
            if len(out) >= limit:
                break
        return out

    async def _sample_docs(
        self,
        name: str,
        flt: dict[str, Any],
        n: int,
        *,
        project: dict[str, Any] | None = None,
        then: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        """Up to ``n`` random documents (:func:`sample_pipeline`). When the server's pseudo-random
        cursor fails (:data:`_RANDOM_CURSOR_DUPLICATES`) :data:`_RANDOM_CURSOR_TRIES` times, the
        sample is taken without it (a collection scan), under the same ``maxTimeMS``."""
        size = max(1, int(n))
        coll = self.read_collection(name)

        async def run(pipeline: list[dict[str, Any]], **extra: Any) -> list[dict[str, Any]]:
            cursor = await coll.aggregate(
                pipeline, comment=self.comment, maxTimeMS=self.max_time_ms, **extra
            )
            return list(await cursor.to_list(length=size))

        async def sample() -> list[dict[str, Any]]:
            for _ in range(_RANDOM_CURSOR_TRIES):
                try:
                    return await run(sample_pipeline(flt, size, project=project, then=then))
                except OperationFailure as exc:  # raised by the aggregate or a getMore
                    if exc.code != _RANDOM_CURSOR_DUPLICATES:
                        raise
                    logger.info("mongo_sample_retry", driver_code=mongo_code(exc))
            logger.warning("mongo_sample_scan", reason="random_cursor_duplicates")
            pipeline = sample_pipeline(flt, size, project=project, then=then, random_cursor=False)
            return await run(pipeline, hint=NATURAL_ORDER)

        return await self._guard("sample", sample())

    async def _id_bounds(self, name: str) -> list[Any]:
        """The smallest and largest ``_id`` of the collection (two ``_id`` index lookups)."""
        coll = self.read_collection(name)
        out: list[Any] = []
        for direction in (1, -1):
            cursor = coll.find(
                {},
                projection={"_id": 1},
                sort=[("_id", direction)],
                limit=1,
                comment=self.comment,
                max_time_ms=self.max_time_ms,
            )
            docs = await self._guard("describe", cursor.to_list(length=1))
            out += [d["_id"] for d in docs]
        return out

    async def describe(self, obj: ObjectRef) -> TableShape:
        """The path shape of ``obj`` from a ``$sample`` (``mongo_docs.infer_shape``); the ``_id``
        is the key only when the smallest and largest ``_id`` share the sample's type too.

        Raises:
            ConnectorError: ``object_not_found``, ``object_invalid``.
        """
        name = collection_name(obj)
        if name not in await self.collections():
            raise ConnectorError("object_not_found", "the collection does not exist")
        estimate = await self._guard(
            "describe", self.read_collection(name).estimated_document_count(comment=self.comment)
        )
        n = min(DESCRIBE_SAMPLE_MAX, self._settings.discovery_sample_rows)
        docs = await self._sample_docs(name, {}, n)
        bounds = await self._id_bounds(name)
        return infer_shape(ObjectRef(name=name), docs, row_estimate=int(estimate), id_bounds=bounds)

    # -- selection --------------------------------------------------------------------------------
    @staticmethod
    def _filter(selection: Selection) -> dict[str, Any]:
        validate_selection(selection, "mongo", None)
        return compile_mongo_filter(selection.mongo_filter)

    async def _scan_ids(
        self, name: str, flt: dict[str, Any], keep: Callable[[Any], bool]
    ) -> AsyncIterator[Any]:
        """Every kept ``_id`` of the filter in ``_id`` order (keyset pages of ``_id`` only)."""
        after: Any = None
        coll = self.read_collection(name)
        while True:
            query = flt if after is None else {"$and": [flt, {"_id": {"$gt": after}}]}
            cursor = coll.find(
                query,
                projection={"_id": 1},
                sort=[("_id", 1)],
                limit=_SCAN_BATCH,
                batch_size=_SCAN_BATCH,
                comment=self.comment,
                max_time_ms=self.max_time_ms,
            )
            docs = await self._guard("scan", cursor.to_list(length=_SCAN_BATCH))
            for doc in docs:
                if keep(doc["_id"]):
                    yield doc["_id"]
            if len(docs) < _SCAN_BATCH:
                return
            after = docs[-1]["_id"]

    # -- reads ------------------------------------------------------------------------------------
    async def count(
        self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar]
    ) -> int:
        """Documents the selection matches (``max_rows`` caps the answer)."""
        flt = self._filter(selection)
        await self.open()
        name = collection_name(shape.object)
        keep = _sampler(selection)
        if keep is None:
            extra = {"limit": int(selection.max_rows)} if selection.max_rows else {}
            n = await self._guard(
                "count",
                self.read_collection(name).count_documents(
                    flt, comment=self.comment, maxTimeMS=self.max_time_ms, **extra
                ),
            )
            return int(n)
        total = 0
        async for _ in self._scan_ids(name, flt, keep):
            total += 1
            if selection.max_rows and total >= selection.max_rows:
                break
        return total

    async def sample(
        self, shape: TableShape, selection: Selection, params: dict[str, JsonScalar], n: int
    ) -> list[Row]:
        """Up to ``n`` documents from ``$sample`` (discovery), projected like the read."""
        flt = self._filter(selection)
        await self.open()
        name = collection_name(shape.object)
        return await self._sample_docs(name, flt, n, project=_projection(selection))

    async def sample_keys(
        self,
        shape: TableShape,
        selection: Selection,
        params: dict[str, JsonScalar],
        n: int,
        *,
        upper: tuple[Any, ...] | None = None,
    ) -> list[tuple[Any, ...]]:
        """Up to ``n`` random ``_id``s below ``upper``, sorted by the server (``[]`` when the
        collection has no range key: one partition)."""
        if not shape.key:
            return []
        flt = self._filter(selection)
        await self.open()
        if upper is not None:
            bound = {"_id": {"$lt": bson_id(upper[0], shape)}}
            flt = {"$and": [flt, bound]} if flt else bound
        docs = await self._sample_docs(
            collection_name(shape.object),
            flt,
            n,
            project={"_id": 1},
            then=[{"$sort": {"_id": 1}}],
        )
        return [(d["_id"],) for d in docs]

    async def key_at_offset(
        self,
        shape: TableShape,
        selection: Selection,
        params: dict[str, JsonScalar],
        offset: int,
    ) -> tuple[Any, ...] | None:
        """The ``_id`` at ``offset`` in ``_id`` order among the selected documents, or None."""
        if not shape.key:
            return None
        flt = self._filter(selection)
        await self.open()
        name = collection_name(shape.object)
        keep = _sampler(selection)
        if keep is not None:
            seen = 0
            async for _id in self._scan_ids(name, flt, keep):
                if seen == offset:
                    return (_id,)
                seen += 1
            return None
        cursor = self.read_collection(name).find(
            flt,
            projection={"_id": 1},
            sort=[("_id", 1)],
            skip=int(offset),
            limit=1,
            comment=self.comment,
            max_time_ms=self.max_time_ms,
        )
        docs = await self._guard("key_at_offset", cursor.to_list(length=1))
        return (docs[0]["_id"],) if docs else None

    async def read_partition(
        self,
        shape: TableShape,
        selection: Selection,
        params: dict[str, JsonScalar],
        key_range: KeyRange,
        *,
        after_key: tuple[Any, ...] | None,
        batch: int,
    ) -> AsyncIterator[list[Row]]:
        """Batches of documents in ``key_range`` after ``after_key``, in ``_id`` order."""
        flt = self._filter(selection)
        await self.open()
        coll = self.read_collection(collection_name(shape.object))
        proj, keep = _projection(selection), _sampler(selection)
        batch = max(1, int(batch))
        if not shape.key:
            async for page in self._read_unkeyed(coll, flt, proj, keep, batch):
                yield page
            return
        keys: list[ColumnInfo] = shape.key_columns()
        family = _key_family(shape)
        lower = bson_id(decode_key(key_range.lower, keys)[0], shape) if key_range.lower else None
        upper = bson_id(decode_key(key_range.upper, keys)[0], shape) if key_range.upper else None
        after = bson_id(after_key[0], shape) if after_key else None
        while True:
            rng: dict[str, Any] = {}
            if lower is not None:
                rng["$gte"] = lower
            if upper is not None:
                rng["$lt"] = upper
            if after is not None:
                rng["$gt"] = after
            if rng:
                query = {"$and": [flt, {"_id": rng}]} if flt else {"_id": rng}
            else:
                query = flt
            cursor = coll.find(
                query,
                projection=proj,
                sort=[("_id", 1)],
                limit=batch,
                batch_size=batch,
                comment=self.comment,
                max_time_ms=self.max_time_ms,
            )
            docs: list[dict[str, Any]] = await self._guard("read", cursor.to_list(length=batch))
            if not docs:
                return
            if any(id_label(d["_id"]) != family for d in docs):
                raise ConnectorError(
                    "key_type_mixed",
                    "a document _id has another BSON type than the collection key: discover again",
                )
            # the page's length and the next bound are taken before the page is handed on: the
            # consumer owns the list it gets (``docs`` itself without a sample) and may empty it
            # (S1-M3 engines r3 B1: read again afterwards, an emptied page ended the read)
            after = docs[-1]["_id"]
            last_page = len(docs) < batch
            out = [d for d in docs if keep(d["_id"])] if keep else docs
            if out:
                yield out
            if last_page:
                return

    async def _read_unkeyed(
        self,
        coll: Any,
        flt: dict[str, Any],
        proj: dict[str, int] | None,
        keep: Callable[[Any], bool] | None,
        batch: int,
    ) -> AsyncIterator[list[Row]]:
        """One cursor over a key-less collection in ``_id`` order (a range would drop documents
        of the other ``_id`` types); closed when the caller stops."""
        cursor = coll.find(
            flt,
            projection=proj,
            sort=[("_id", 1)],
            batch_size=batch,
            comment=self.comment,
            max_time_ms=self.max_time_ms,
        )
        try:
            while True:
                docs: list[dict[str, Any]] = await self._guard("read", cursor.to_list(length=batch))
                if not docs:
                    return
                last_page = len(docs) < batch  # before the consumer gets (and may empty) it
                out = [d for d in docs if keep(d["_id"])] if keep else docs
                if out:
                    yield out
                if last_page:
                    return
        finally:
            await cursor.close()

    async def lookup_values(
        self, shape: TableShape, join: ColumnInfo, values: list[Any], attributes: list[ColumnInfo]
    ) -> list[Row]:
        """Documents whose ``join`` path holds one of ``values`` (``$in``; consistency check),
        projected to the top-level fields of ``join`` and ``attributes`` (whole fields, as a read
        projects them: array positions stay exact).

        Raises:
            ConnectorError: ``path_unsupported`` when a path cannot be dot-addressed.
        """
        field_ = dotted(join.path)
        paths = [dotted(a.path) for a in attributes]
        if field_ is None or any(p is None for p in paths):
            raise ConnectorError("path_unsupported", "a path cannot be dot-addressed")
        tops = {p.split(".", 1)[0] for p in [field_, *[p for p in paths if p]]}
        projection = {**{t: 1 for t in sorted(tops) if t != "_id"}, "_id": 1}
        await self.open()
        coll = self.read_collection(collection_name(shape.object))

        async def run() -> list[dict[str, Any]]:
            cursor = coll.find(
                {field_: {"$in": list(values)}},
                projection=projection,
                comment=self.comment,
                max_time_ms=self.max_time_ms,
            )
            try:
                return list(await cursor.to_list(length=None))
            finally:
                await cursor.close()

        return await self._guard("lookup", run())

    async def writer(self, target: ResolvedTarget, source_shape: TableShape) -> MongoWriter:
        """A :class:`MongoWriter` for an ``in_place``, ``append`` or ``table`` target in the
        connection's database.

        Raises:
            ConnectorError: ``target_invalid`` for a ``files`` target or one without an object.
        """
        if target.write_mode == "files" or target.object is None:
            raise ConnectorError("target_invalid", "a MongoDB writer needs a collection target")
        await self.open()
        return MongoWriter(self, target, source_shape)

    # -- connection test --------------------------------------------------------------------------
    async def _privileges(self) -> list[dict[str, Any]] | None:
        """The session's privileges, or None on a server without authentication."""
        status = await self._guard(
            "test",
            self.client().admin.command(
                {"connectionStatus": 1, "showPrivileges": True}, comment=self.comment
            ),
        )
        info = status.get("authInfo", {})
        if not info.get("authenticatedUsers"):
            return None
        return list(info.get("authenticatedUserPrivileges", []))

    def _allowed(self, privs: list[dict[str, Any]] | None, coll: str, action: str) -> bool:
        if privs is None:
            return True
        for p in privs:
            res = p.get("resource", {})
            hit = res.get("anyResource") or (
                res.get("db") in ("", self._db_name) and res.get("collection") in ("", coll)
            )
            if hit and action in p.get("actions", []):
                return True
        return False

    async def test(
        self,
        *,
        role: Literal["source", "target"],
        obj: ObjectRef | None = None,
        write_mode: WriteMode | None = None,
    ) -> ConnectionTestResult:
        """Value-free connectivity and privilege checks; never raises for a refused login."""
        try:
            await self.open()
        except ConnectorError as err:
            return ConnectionTestResult(
                ok=False,
                engine="mongo",
                checks=[_check("connect", False, code=err.code, detail=err.detail)],
                capabilities=dict(_CAPS),
                error_code="connection_failed",
            )
        caps = {**_CAPS, "transactions": self._replica}
        checks = [_check("connect", True), _check("session_tagged", True, "advisory")]
        try:
            checks += await self._object_checks(role, obj, write_mode)
        except ConnectorError as err:
            checks.append(_check("privileges", False, code=err.code, detail=err.detail))
        ok = all(c["ok"] for c in checks if c["severity"] == "required")
        return ConnectionTestResult(
            ok=ok, engine="mongo", server_version=self._version, checks=checks, capabilities=caps
        )

    async def _object_checks(
        self, role: str, obj: ObjectRef | None, write_mode: str | None
    ) -> list[dict[str, Any]]:
        if obj is None:
            return []
        name = collection_name(obj)
        privs = await self._privileges()
        present = name in await self.collections()
        allowed = self._allowed
        if role == "source":
            return [
                _check("select_source", present and allowed(privs, name, "find")),
                _check(
                    "no_dml_on_source",
                    not any(allowed(privs, name, a) for a in ("insert", "update", "remove")),
                    "advisory",
                ),
            ]
        checks: list[dict[str, Any]] = []
        if write_mode == "table" and not present:
            checks.append(_check("create_target", allowed(privs, name, "createCollection")))
        elif write_mode in ("table", "append"):
            checks.append(_check("insert_target", present and allowed(privs, name, "insert")))
            if write_mode == "table":
                checks.append(_check("update_target", allowed(privs, name, "update")))
        elif write_mode == "in_place":
            checks.append(_check("select_target", present and allowed(privs, name, "find")))
            checks.append(_check("update_target", present and allowed(privs, name, "update")))
        if write_mode in ("in_place", "append"):
            checks.append(_check("transactions", self._replica))
            checks.append(
                _check(
                    "create_ledger",
                    allowed(privs, LEDGER_COLLECTION, "insert")
                    and allowed(privs, LEDGER_COLLECTION, "update"),
                )
            )
        return checks


# --- the writer (contract §4.2) ---------------------------------------------------------------------


def _ledger_id(job_id: str, partition_no: int) -> str:
    return f"{job_id}:{partition_no}"


def _oid_floor(seconds: int) -> ObjectId:
    """The smallest ObjectId of a second (timestamp, zero tail)."""
    return ObjectId(seconds.to_bytes(4, "big") + bytes(8))


class MongoWriter:
    """Contract §4.2 ``BatchWriter`` for MongoDB targets (module docstring: atomicity, resume)."""

    def __init__(
        self, connector: MongoConnector, target: ResolvedTarget, source_shape: TableShape
    ) -> None:
        if target.object is None:
            raise ConnectorError("target_invalid", "a MongoDB writer needs a collection target")
        self._c, self._t, self._src = connector, target, source_shape
        self._name = collection_name(target.object)
        self._ledgered = target.write_mode in ("in_place", "append")

    def __repr__(self) -> str:
        return f"MongoWriter(collection={self._name!r}, mode={self._t.write_mode!r})"

    # -- handles ----------------------------------------------------------------------------------
    def target_collection(self) -> Any:
        """The target collection with the default write concern (operations inside a
        transaction take the transaction's)."""
        return self._c.db.get_collection(self._name)

    def _durable(self) -> Any:
        """The target collection acknowledging by majority on a replica set (``table`` loads)."""
        concern = WriteConcern("majority") if self._c.replica_set else WriteConcern()
        return self.target_collection().with_options(write_concern=concern)

    def _ledger(self) -> Any:
        return self._c.db.get_collection(LEDGER_COLLECTION)

    def _id_column(self) -> ColumnInfo | None:
        return next((c for c in self._src.columns if c.path == "$._id"), None)

    def _id_kind(self) -> str:
        col = self._id_column()
        return col.kind if col is not None else "other"

    async def _call(self, op: str, call: Awaitable[T]) -> T:
        return await self._c._guard(op, call, "target_error")

    # -- prepare ----------------------------------------------------------------------------------
    async def prepare(self, *, dry_run: bool) -> PrepareReport:
        """Create (or empty) a ``table`` target, or check an ``in_place``/``append`` target and
        create the ledger collection; ``dry_run`` only checks.

        Raises:
            PrepareFailure: ``transactions_unsupported`` (no replica set for a ledgered mode),
                ``no_key`` (in place without a range key), ``target_missing``,
                ``pk_strategy_invalid`` (module docstring of the key rules in :meth:`_check_append`).
            ConnectorError: ``target_error`` / ``connection_failed`` from the server.
        """
        await self._c.open()
        mode = self._t.write_mode
        if mode not in ("table", "in_place", "append"):
            raise PrepareFailure("target_invalid", "a MongoDB target is a collection")
        if self._ledgered and not self._c.replica_set:
            raise PrepareFailure(
                "transactions_unsupported",
                "in_place and append need a replica set: the batch and its ledger mark commit "
                "together in one transaction",
            )
        if mode == "in_place" and self._src.key_source != "primary_key":
            raise PrepareFailure("no_key", "in place needs one _id type to page and update by")
        report = PrepareReport()
        present = self._name in await self._collections()
        if mode == "table":
            await self._prepare_table(report, present=present, dry_run=dry_run)
            return report
        if not present:
            raise PrepareFailure("target_missing", "the target collection does not exist")
        if mode == "append":
            await self._check_append(report)
        if not dry_run:
            await self._create(LEDGER_COLLECTION)
            report.ledger = True
        report.checks.append({"name": "write_ledger", "ok": True, "dry_run": dry_run})
        return report

    async def _collections(self) -> set[str]:
        names = await self._call(
            "prepare",
            self._c.db.list_collection_names(
                filter={"type": "collection"}, comment=self._c.comment
            ),
        )
        return set(names)

    async def _create(self, name: str) -> None:
        try:
            await self._c.db.create_collection(name, comment=self._c.comment)
        except CollectionInvalid:
            pass  # created concurrently: fine
        except PyMongoError as exc:
            raise connector_error("target_error", exc) from None

    async def _prepare_table(self, report: PrepareReport, *, present: bool, dry_run: bool) -> None:
        if not present:
            if not self._t.create_if_missing:
                raise PrepareFailure("target_missing", "the target collection does not exist")
            if not dry_run:
                await self._create(self._name)
                report.created = True
            report.checks.append({"name": "create_target", "ok": True, "dry_run": dry_run})
            # a created collection holds no secondary index of the source's
            report.checks.append({"name": "indexes_not_copied", "ok": True, "severity": "info"})
            return
        report.existing_rows = int(
            await self._call(
                "prepare",
                self.target_collection().estimated_document_count(comment=self._c.comment),
            )
        )
        if self._t.table_load == "truncate_insert" and not dry_run:
            await self._call("prepare", self._durable().delete_many({}, comment=self._c.comment))
            report.truncated = True
        report.checks.append({"name": "existing_target", "ok": True})

    async def _top_id(self) -> Any:
        doc = await self._call(
            "prepare",
            self.target_collection().find_one(
                {}, projection={"_id": 1}, sort=[("_id", -1)], comment=self._c.comment
            ),
        )
        return None if doc is None else doc["_id"]

    async def _check_append(self, report: PrepareReport) -> None:
        """Key rules of ``append`` (``pk_strategy_invalid`` otherwise) and the read bound of a
        same-collection append (every existing ``_id`` below it, every appended one above it).

        * ``generate``: the source ``_id`` is an ObjectId and the target holds only ObjectIds
          (new ids would otherwise mix types and leave the collection without a range key); on
          the same collection the bound is the first ObjectId of a second after the newest id,
          and ``prepare`` returns only once the clock has passed it by a second.
        * ``offset``: an ``offset`` needs integer ids (bound ``max + 1``), a ``prefix`` text ids
          (on the same collection the prefix must sort above every id; it is the bound).
        * ``token``: never ObjectIds, and not on the same collection (a token can land anywhere
          in the read range and be read and appended again).
        """
        pk, kind, same = self._t.pk_strategy, self._id_kind(), self._t.same_as_source
        col = self._id_column()
        invalid = PrepareFailure
        if pk is None:
            return
        if pk.kind == "generate":
            if kind != "object_id":
                raise invalid(
                    "pk_strategy_invalid", "generate assigns ObjectIds: the _id is not one"
                )
            other = await self._call(
                "prepare",
                self.target_collection().find_one(
                    {"_id": {"$not": {"$type": "objectId"}}},
                    projection={"_id": 1},
                    comment=self._c.comment,
                ),
            )
            if other is not None:
                raise invalid("pk_strategy_invalid", "the target holds _ids that are not ObjectIds")
            if same and col is not None:
                bound = await self._clock_bound(await self._top_id())
                report.append_upper_key = encode_key((bound,), [col])
            return
        if pk.kind == "offset":
            if pk.offset is not None and kind != "int":
                raise invalid("pk_strategy_invalid", "an offset needs integer _ids")
            if pk.prefix is not None and kind != "text":
                raise invalid("pk_strategy_invalid", "a prefix needs text _ids")
            if not same or col is None:
                return
            top = await self._top_id()
            if pk.offset is not None:
                report.append_upper = int(top) + 1 if top is not None else None
            elif pk.prefix is not None:
                if top is not None and not pk.prefix > str(top):
                    raise invalid(
                        "pk_strategy_invalid",
                        "a same-collection prefix must sort above every existing _id",
                    )
                report.append_upper_key = encode_key((pk.prefix,), [col])
            return
        if kind == "object_id":
            raise invalid("pk_strategy_invalid", "ObjectIds cannot be tokenized")
        if same:
            raise invalid(
                "pk_strategy_invalid", "a same-collection token append would read its own output"
            )

    async def _clock_bound(self, top: Any) -> ObjectId:
        """The first ObjectId of a second after ``top``'s, once the clock is a second past it."""
        floor = int(top.generation_time.timestamp()) if isinstance(top, ObjectId) else 0
        bound_ts = max(floor + 1, int(time.time()))
        started = time.monotonic()
        while time.time() < bound_ts + 1:  # a second of margin for the other workers' clocks
            if time.monotonic() - started > _CLOCK_WAIT_MAX_S:
                raise PrepareFailure(
                    "pk_strategy_invalid",
                    "the collection holds ObjectIds from a clock ahead of ours",
                )
            await asyncio.sleep(0.05)
        return _oid_floor(bound_ts)

    # -- collisions and the ledger ----------------------------------------------------------------
    async def precheck_collisions(self, keys: AsyncIterator[list[tuple[Any, ...]]]) -> int:
        """How many of the streamed (transformed) ``_id``s already exist in the target."""
        total = 0
        async for batch in keys:
            ids = [k[0] for k in batch]
            for start in range(0, len(ids), _SCAN_BATCH):
                chunk = ids[start : start + _SCAN_BATCH]
                total += int(
                    await self._call(
                        "precheck",
                        self.target_collection().count_documents(
                            {"_id": {"$in": chunk}}, comment=self._c.comment
                        ),
                    )
                )
        return total

    async def read_ledger(self, job_id: str, partition_no: int) -> LedgerMark | None:
        """The partition's last committed mark, or None before its first batch."""
        doc = await self._call(
            "ledger",
            self._ledger().find_one(
                {"_id": _ledger_id(job_id, partition_no)}, comment=self._c.comment
            ),
        )
        if doc is None or int(doc.get("batch_no", 0)) == 0:
            return None
        return LedgerMark(
            job_id=job_id,
            partition_no=partition_no,
            batch_no=int(doc["batch_no"]),
            last_key=doc["last_key"],
            rows=int(doc.get("rows", 0)),
        )

    async def clear_ledger(self, job_id: str) -> int:
        """Finalize: delete the job's ledger documents (the collection stays)."""
        res = await self._call(
            "ledger", self._ledger().delete_many({"job_id": job_id}, comment=self._c.comment)
        )
        return int(res.deleted_count)

    async def _advance(self, session: Any, mark: LedgerMark) -> None:
        """Move the partition's ledger from ``mark.batch_no - 1`` to ``mark.batch_no`` in the
        batch's transaction; anything else is another holder's work (:class:`LedgerConflict`).

        The first batch creates the document (upsert on ``batch_no: 0``); a concurrent first batch
        conflicts on the document and, once the other commits, finds it taken (duplicate key).
        """
        fields = {
            "job_id": mark.job_id,
            "partition_no": mark.partition_no,
            "batch_no": mark.batch_no,
            "last_key": mark.last_key,
            "rows": mark.rows,
            "updated_at": dt.datetime.now(dt.UTC),
        }
        try:
            res = await self._ledger().update_one(
                {"_id": _ledger_id(mark.job_id, mark.partition_no), "batch_no": mark.batch_no - 1},
                {"$set": fields},
                upsert=mark.batch_no == 1,
                session=session,
                comment=self._c.comment,
            )
        except DuplicateKeyError:
            raise LedgerConflict() from None
        if res.matched_count != 1 and res.upserted_id is None:
            raise LedgerConflict()

    # -- writes -----------------------------------------------------------------------------------
    def _documents(self, rows: list[Row]) -> list[dict[str, Any]]:
        """BSON-safe copies of ``rows`` (input unchanged). No other value is converted: the engine
        restored the leaves it wrote, and every other leaf is the driver's own value."""
        return [make_bson_safe(dict(r)) for r in rows]

    def _table_ops(self, docs: list[dict[str, Any]]) -> list[Any]:
        """Whole documents for a created or emptied target; ``$set`` of the row's fields for an
        upsert, so matched documents keep what the row does not carry (contract §5.2)."""
        if self._t.table_load != "upsert":
            return [ReplaceOne({"_id": d["_id"]}, d, upsert=True) for d in docs]
        ops: list[Any] = []
        for doc in docs:
            update = upsert_update(doc)
            if update is None:
                raise PrepareFailure(
                    "path_unsupported", "a field name cannot be addressed with dot notation"
                )
            ops.append(UpdateOne({"_id": doc["_id"]}, update, upsert=True))
        return ops

    def _in_place_ops(self, docs: list[dict[str, Any]]) -> list[UpdateOne]:
        writable = set(self._t.write_columns)
        ops: list[UpdateOne] = []
        for doc in docs:
            updates = leaf_updates(doc)
            if updates is None:
                raise PrepareFailure(
                    "path_unsupported", "a field name cannot be addressed with dot notation"
                )
            if writable:
                updates = {k: v for k, v in updates.items() if k.split(".")[0] in writable}
            if updates:
                ops.append(UpdateOne({"_id": doc["_id"]}, {"$set": updates}))
        return ops

    def _append_doc(self, doc: dict[str, Any]) -> dict[str, Any]:
        if self._t.generated_key:
            return {k: v for k, v in doc.items() if k != "_id"}  # the driver assigns an ObjectId
        return doc

    async def write_batch(self, rows: list[Row], *, mark: LedgerMark) -> int:
        """Write ``rows`` (and, for in_place/append, the ledger ``mark``) — one transaction.

        Raises:
            LedgerConflict: the ledger does not hold ``mark.batch_no - 1`` (nothing written).
            PkCollision: a duplicate key in the rows (the batch rolled back).
            PrepareFailure: ``path_unsupported`` (in place or upsert, a field name with a dot).
            ConnectorError: ``target_error`` / ``connection_failed`` / ``statement_timeout``.
        """
        if not rows and not self._ledgered:
            return 0
        docs = self._documents(rows)
        try:
            if self._t.write_mode == "table":
                ops = self._table_ops(docs)
                res = await self._durable().bulk_write(ops, ordered=False, comment=self._c.comment)
                written = int(res.upserted_count + res.matched_count)
            else:
                written = await self._transaction(docs, mark)
        except (LedgerConflict, PrepareFailure, ConnectorError):
            raise
        except (BulkWriteError, DuplicateKeyError) as exc:
            CONNECTOR_CALLS.labels("mongo", "write", "error").inc()
            if mongo_code(exc) == f"mongo:{_DUPLICATE_KEY}":
                raise PkCollision() from None
            raise connector_error("target_error", exc) from None
        except PyMongoError as exc:
            CONNECTOR_CALLS.labels("mongo", "write", "error").inc()
            err = connector_error("target_error", exc)
            logger.warning("mongo_call_failed", op="write", driver_code=err.detail)
            raise err from None
        CONNECTOR_CALLS.labels("mongo", "write", "ok").inc()
        return written

    async def _transaction(self, docs: list[dict[str, Any]], mark: LedgerMark) -> int:
        mode = self._t.write_mode
        ops: list[Any] = (
            self._in_place_ops(docs)
            if mode == "in_place"
            else [InsertOne(self._append_doc(d)) for d in docs]
        )

        async def apply(session: Any) -> int:
            await self._advance(session, mark)
            if not ops:
                return 0
            res = await self.target_collection().bulk_write(
                ops, ordered=False, session=session, comment=self._c.comment
            )
            return int(res.matched_count if mode == "in_place" else res.inserted_count)

        concern = WriteConcern("majority")
        async with self._c.client().start_session() as session:
            return int(await session.with_transaction(apply, write_concern=concern))

    async def finish_partition(
        self, job_id: str, partition_no: int, *, batch_no: int | None = None
    ) -> dict[str, Any]:
        """Nothing to upload for a database target."""
        return {}

    async def close(self) -> None:
        """The connector owns the client; nothing to release here."""
        return None
