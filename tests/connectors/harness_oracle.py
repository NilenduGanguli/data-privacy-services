"""The conformance suite's engine environment on Oracle (``ConformanceEnv``, contract §12.3).

Tables live in the session's test owner (``tests/fixtures/m3_services.py``); SYSTEM (``admin``)
creates them, inserts and reads them back for assertions; the connector under test reads as the
read-only login (``SELECT`` granted per table) and writes as the owner. Oracle folds unquoted names
to upper case, so the suite's ``id``/``name``/``note`` columns are ``ID``/``NAME``/``NOTE``; a
key-less table is paged on ``ROWID``.
"""

from __future__ import annotations

from typing import Any

from dps.config import Settings
from dps.jobs_models import ConnectionParams, Selection
from dps.layers.connectors.base import ObjectRef, ResolvedTarget, Row, TableShape, WriteMode
from dps.layers.connectors.oracle import OracleConnector
from tests.connectors.conformance import KeyKind, sql_rejected_selections
from tests.fixtures.m3_services import OracleTestSchema

_TEXT = "VARCHAR2(200 CHAR)"
_DDL: dict[str, str] = {
    "int": f"ID NUMBER(12) PRIMARY KEY, NAME {_TEXT}, NOTE {_TEXT}",
    "composite": f"A NUMBER(5), B VARCHAR2(10), NAME {_TEXT}, NOTE {_TEXT}, PRIMARY KEY (A, B)",
    "text": f"CODE VARCHAR2(20) PRIMARY KEY, NAME {_TEXT}, NOTE {_TEXT}",
    "none": f"ID NUMBER(12), NAME {_TEXT}, NOTE {_TEXT}",
}
_KEYS: dict[str, list[str]] = {"int": ["ID"], "composite": ["A", "B"], "text": ["CODE"]}


def _key_values(key: KeyKind, i: int) -> tuple[Any, ...]:
    if key == "composite":
        return (i % 7, f"b{i:03d}")
    if key == "text":
        return (f"{'aBcD'[i % 4]}{i}",)  # binary order differs from a linguistic one
    return (i,)


class OracleEnv:
    """:class:`~tests.connectors.conformance.ConformanceEnv` on the lane's Oracle Free."""

    engine = "oracle"
    names = {"id": "ID", "a": "A", "b": "B", "code": "CODE", "name": "NAME", "note": "NOTE"}
    keyless = (["ROWID"], "rowid")
    composite_keys = True

    def __init__(
        self, schema: OracleTestSchema, admin: Any, source: OracleConnector, rw: OracleConnector
    ) -> None:
        self.schema, self.admin, self.source, self.rw = schema, admin, source, rw
        self._keys: dict[str, list[str]] = {}
        with admin.cursor() as cur:  # read_all orders like the connector's sessions (BINARY)
            cur.execute("ALTER SESSION SET NLS_SORT = BINARY NLS_COMP = BINARY")

    def _fqn(self, obj: ObjectRef) -> str:
        return f'"{self.schema.owner}"."{obj.name.upper()}"'

    async def make_table(self, name: str, *, key: KeyKind, rows: int) -> ObjectRef:
        obj = ObjectRef(owner=self.schema.owner, name=name.upper())
        cols = (["ID"] if key in ("int", "none") else _KEYS[key]) + ["NAME", "NOTE"]
        records = [
            (*_key_values(key, i), f"Name {i}", f"note-{i}@example.test")
            for i in range(1, rows + 1)
        ]
        with self.admin.cursor() as cur:
            cur.execute(f"CREATE TABLE {self._fqn(obj)} ({_DDL[key]})")
            binds = ", ".join(f":{n + 1}" for n in range(len(cols)))
            cur.executemany(
                f"INSERT INTO {self._fqn(obj)} ({', '.join(cols)}) VALUES ({binds})", records
            )
        self.schema.grant_select(self.admin, obj.name)
        self._keys[obj.name] = _KEYS.get(key, ["ROWID"])
        return obj

    async def insert(self, obj: ObjectRef, rows: list[Row]) -> None:
        columns = list(rows[0])
        binds = ", ".join(f":{n + 1}" for n in range(len(columns)))
        with self.admin.cursor() as cur:
            cur.executemany(
                f"INSERT INTO {self._fqn(obj)} ({', '.join(columns)}) VALUES ({binds})",
                [tuple(r[c] for c in columns) for r in rows],
            )

    async def read_all(self, obj: ObjectRef) -> list[Row]:
        name = obj.name.upper()
        keys = self._keys.get(name) or self._keys[name.replace("_DEST", "_SRC")]
        order = ", ".join(keys)
        with self.admin.cursor() as cur:
            cur.execute(f"SELECT * FROM {self._fqn(obj)} ORDER BY {order}")
            names = [d[0] for d in cur.description]
            return [dict(zip(names, row, strict=True)) for row in cur.fetchall()]

    def target(
        self, obj: ObjectRef, shape: TableShape, write_mode: WriteMode, **kw: Any
    ) -> ResolvedTarget:
        base: dict[str, Any] = {
            "job_id": "j_conf",
            "write_mode": write_mode,
            "connection_id": "c_conf",
            "engine": "oracle",
            "object": obj,
            "key": [k for k in shape.key if not shape.column(k).pseudo],
            "write_columns": [c.name for c in shape.columns if not c.pseudo],
            "target_shape": shape if write_mode in ("in_place", "append") else None,
            "column_types": {c.name: c.data_type for c in shape.columns if not c.pseudo},
        }
        base.update(kw)
        if write_mode == "table":
            self._keys.setdefault(obj.name.upper(), list(shape.key))
        return ResolvedTarget(**base)

    def rejected_selections(self) -> list[tuple[Selection, str]]:
        return sql_rejected_selections()


def oracle_connector(user: str, password: str, dsn: str) -> OracleConnector:
    """A connector for the suite (action ``conformance``, job ``j_conf``)."""
    return OracleConnector.from_secret(
        {"username": user, "password": password, "dsn": dsn},
        ConnectionParams(),
        Settings(_env_file=None),
        action="conformance",
        client_id="j_conf",
    )
