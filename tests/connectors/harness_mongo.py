"""The conformance suite's engine environment on MongoDB (``ConformanceEnv``, contract §12.3).

One fresh database per test (``mongo_db``); the synchronous ``mongo_client`` inserts and reads back
for assertions. MongoDB's only key is ``_id`` (contract §4.2), so the suite's key column (``id`` or
``code``) is ``_id``; a key-less "table" is a collection whose ``_id``s are documents (they cannot be
range-paged); there are no composite keys.
"""

from __future__ import annotations

from typing import Any

from dps.jobs_models import Selection
from dps.layers.connectors.base import ObjectRef, ResolvedTarget, Row, TableShape, WriteMode
from dps.layers.connectors.mongo import MongoConnector
from tests.connectors.conformance import KeyKind


def _key_value(key: KeyKind, i: int) -> Any:
    if key == "text":
        return f"{'aBcD'[i % 4]}{i}"  # binary order differs from a linguistic one
    if key == "none":
        return {"n": i}  # a document _id: no range key
    return i


class MongoEnv:
    """:class:`~tests.connectors.conformance.ConformanceEnv` on the lane's MongoDB replica set."""

    engine = "mongo"
    names = {"id": "_id", "code": "_id"}
    keyless = ([], "none")
    composite_keys = False

    def __init__(
        self, client: Any, database: str, source: MongoConnector, rw: MongoConnector
    ) -> None:
        self.client, self.database, self.source, self.rw = client, database, source, rw

    def _coll(self, obj: ObjectRef) -> Any:
        return self.client[self.database][obj.name]

    async def make_table(self, name: str, *, key: KeyKind, rows: int) -> ObjectRef:
        if key == "composite":
            raise NotImplementedError("MongoDB has no composite keys")
        obj = ObjectRef(name=name)
        self._coll(obj).insert_many(
            [
                {"_id": _key_value(key, i), "name": f"Name {i}", "note": f"note-{i}@example.test"}
                for i in range(1, rows + 1)
            ]
        )
        return obj

    async def insert(self, obj: ObjectRef, rows: list[Row]) -> None:
        self._coll(obj).insert_many([dict(r) for r in rows])

    async def read_all(self, obj: ObjectRef) -> list[Row]:
        return list(self._coll(obj).find().sort("_id", 1))  # the server's order

    def target(
        self, obj: ObjectRef, shape: TableShape, write_mode: WriteMode, **kw: Any
    ) -> ResolvedTarget:
        base: dict[str, Any] = {
            "job_id": "j_conf",
            "write_mode": write_mode,
            "connection_id": "c_conf",
            "engine": "mongo",
            "object": obj,
            "key": list(shape.key),
            "write_columns": list(dict.fromkeys(c.name for c in shape.columns)),
        }
        base.update(kw)
        return ResolvedTarget(**base)

    def rejected_selections(self) -> list[tuple[Selection, str]]:
        return [
            (Selection(mongo_filter={"$where": "sleep(1)"}), "selection_operator_denied"),
            (Selection(mongo_filter={"name": {"$function": {}}}), "selection_operator_denied"),
            (Selection(where="name = 'x'"), "selection_engine_mismatch"),
            (Selection(mongo_filter={"bad-field": 1}), "selection_identifier_invalid"),
            (Selection(mongo_filter={"$and": []}), "selection_invalid"),
        ]

    def equals_selection(self, column: str, value: str) -> Selection:
        return Selection(mongo_filter={column: value})
