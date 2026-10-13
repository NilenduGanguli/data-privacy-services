"""MongoDB connector over pymongo's ``AsyncMongoClient`` (contract §1: Motor is deprecated).

Reads. A keyed collection (one range-pageable ``_id`` type, ``mongo_docs.infer_shape``) pages on
``_id`` with a fresh ``find`` per batch (``_id >= lower AND _id < upper AND _id > after``, sorted by
``_id`` in the server, ``limit = batch``), so no server cursor outlives a batch; a key-less
collection (mixed or document ``_id``s, where BSON type bracketing would drop documents from a
range) is one partition read through one cursor in ``_id`` order. Reads use ``readConcern:
majority`` on a replica set (contract §4.2). Documents travel whole: the engine transforms nested
paths and arrays (``[*]``), and ``mongo_docs.coerce_bson`` restores BSON types before writes.
``selection.sample`` keeps a document when ``HMAC-SHA256(seed, BSON({_id}))[:4] < percent·2³²`` —
a function of the document alone, so the count, every partition and a resumed page read one sample.

Every operation carries ``comment = "dps:<action>:<job id>"`` (the analogue of Oracle's
module/action/client_identifier; visible in the profiler and ``currentOp``).

Errors. Server messages can quote a document (``E11000 … dup key: { … }``) and client errors can
quote the URI, so only error *codes* leave this module (:func:`mongo_code`), raised ``from None``;
``repr`` names the database only. Filters are validated against the allow-list before any call
(``selection.compile_mongo_filter``): a ``$where`` never reaches the server.
"""

from __future__ import annotations

import hashlib
import hmac
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Mapping
from typing import Any, Final, Literal, TypeVar

import bson
from bson import ObjectId
from pymongo import AsyncMongoClient
from pymongo.errors import (
    BulkWriteError,
    ConfigurationError,
    ConnectionFailure,
    PyMongoError,
    ServerSelectionTimeoutError,
)
from pymongo.read_concern import ReadConcern
from pymongo.uri_parser import parse_uri

from dps.config import Settings
from dps.jobs_models import ConnectionParams, Selection
from dps.layers.connectors.base import (
    ColumnInfo,
    ConnectionTestResult,
    ConnectorError,
    Engine,
    JsonScalar,
    KeyRange,
    ObjectRef,
    Row,
    TableShape,
    WriteMode,
    decode_key,
)
from dps.layers.connectors.mongo_docs import infer_shape
from dps.layers.connectors.selection import compile_mongo_filter, validate_selection
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
_CAPS: Final[dict[str, bool]] = {
    "transactions": False,
    "merge": True,
    "identity": False,
    "sample": True,
    "rowid": False,
    "copy": False,
}


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
    """``columns`` as a find projection (``_id`` always); a path under another listed path is
    dropped, since MongoDB refuses a projection with both (path collision)."""
    if not selection.columns:
        return None
    kept: list[str] = []
    for path in sorted(set(selection.columns), key=lambda p: (p.count("."), p)):
        if path == "_id" or path.startswith("_id."):
            continue
        if any(path.startswith(k + ".") for k in kept):
            continue
        kept.append(path)
    return {**{p: 1 for p in kept}, "_id": 1}


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
        pipeline: list[dict[str, Any]] = [{"$match": flt}] if flt else []
        pipeline.append({"$sample": {"size": max(1, int(n))}})
        if project:
            pipeline.append({"$project": project})
        pipeline += then or []

        async def run() -> list[dict[str, Any]]:
            cursor = await self.read_collection(name).aggregate(
                pipeline, comment=self.comment, maxTimeMS=self.max_time_ms
            )
            return list(await cursor.to_list(length=max(1, int(n))))

        return await self._guard("sample", run())

    async def describe(self, obj: ObjectRef) -> TableShape:
        """The path shape of ``obj`` from a ``$sample`` (``mongo_docs.infer_shape``).

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
        return infer_shape(ObjectRef(name=name), docs, row_estimate=int(estimate))

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
            after = docs[-1]["_id"]
            out = [d for d in docs if keep(d["_id"])] if keep else docs
            if out:
                yield out
            if len(docs) < batch:
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
                out = [d for d in docs if keep(d["_id"])] if keep else docs
                if out:
                    yield out
                if len(docs) < batch:
                    return
        finally:
            await cursor.close()

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
