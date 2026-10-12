"""The connector conformance suite bound to PostgreSQL (the lane's ``source-pg``): reads as the
read-only ``dps_reader``, writes as ``dps_writer``, in a fresh ``t_<hex>`` schema per test."""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio

from dps.config import Settings
from dps.jobs_models import Selection
from dps.layers.connectors.base import (
    Connector,
    ObjectRef,
    ResolvedTarget,
    Row,
    TableShape,
    WriteMode,
)
from dps.layers.connectors.postgres import PostgresConnector
from tests.connectors.conformance import (
    ConnectorConformance,
    KeyKind,
    sql_rejected_selections,
)
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.integration, pytest.mark.pg_source]

_DDL: dict[str, str] = {
    "int": "id bigint PRIMARY KEY, name text, note text",
    "composite": "a int, b varchar(10), name text, note text, PRIMARY KEY (a, b)",
    "text": "code varchar(20) PRIMARY KEY, name text, note text",
    "none": "id bigint, name text, note text",
}


def _key_values(key: KeyKind, i: int) -> tuple[Any, ...]:
    if key == "composite":
        return (i % 7, f"b{i:03d}")
    if key == "text":
        # mixed case and digits: a collation's order differs from Python's code-point order
        return (f"{'aBcD'[i % 4]}{i}",)
    return (i,)


class PgEnv:
    """:class:`~tests.connectors.conformance.ConformanceEnv` on the lane's source-pg."""

    engine = "postgres"

    def __init__(self, pg: SourcePg, source: Connector, rw: Connector) -> None:
        self.pg, self.source, self.rw = pg, source, rw
        self._keys: dict[str, list[str]] = {}

    async def make_table(self, name: str, *, key: KeyKind, rows: int) -> ObjectRef:
        records = [
            (*_key_values(key, i), f"Name {i}", f"note-{i}@example.test")
            for i in range(1, rows + 1)
        ]
        self._keys[name] = {
            "int": ["id"],
            "composite": ["a", "b"],
            "text": ["code"],
            "none": ["id"],
        }[key]
        return await self.pg.table(name, _DDL[key], records)

    async def insert(self, obj: ObjectRef, rows: list[Row]) -> None:
        columns = list(rows[0])
        await self.pg.admin.copy_records_to_table(
            obj.name,
            records=[tuple(r[c] for c in columns) for r in rows],
            columns=columns,
            schema_name=self.pg.schema,
        )

    async def read_all(self, obj: ObjectRef) -> list[Row]:
        keys = self._keys.get(obj.name) or self._keys[obj.name.replace("_dest", "_src")]
        order = ", ".join(f'"{k}"' for k in keys)
        records = await self.pg.admin.fetch(
            f'SELECT * FROM "{self.pg.schema}"."{obj.name}" ORDER BY {order}'
        )
        return [dict(r) for r in records]

    def target(
        self, obj: ObjectRef, shape: TableShape, write_mode: WriteMode, **kw: Any
    ) -> ResolvedTarget:
        base: dict[str, Any] = {
            "job_id": "j_conf",
            "write_mode": write_mode,
            "connection_id": "c_conf",
            "engine": "postgres",
            "object": obj,
            "key": list(shape.key),
            "write_columns": [c.name for c in shape.columns],
            "target_shape": shape if write_mode in ("in_place", "append") else None,
            "column_types": {c.name: c.data_type for c in shape.columns},
        }
        base.update(kw)
        if write_mode == "table":
            self._keys.setdefault(obj.name, list(shape.key))
        return ResolvedTarget(**base)

    def rejected_selections(self) -> list[tuple[Selection, str]]:
        return sql_rejected_selections()


async def _open(pg: SourcePg, settings: Settings, user: str, roles: tuple[str, ...]) -> Connector:
    connector = PostgresConnector(
        pg.connection(user=user, roles=roles),
        pg.credentials(user),
        settings,
        action="conformance",
        job_id="j_conf",
    )
    await connector.open()
    return connector


class TestPostgresConformance(ConnectorConformance):
    """Contract §12.3 on PostgreSQL."""

    @pytest_asyncio.fixture
    async def env(self, source_pg: SourcePg, settings: Settings) -> AsyncIterator[PgEnv]:
        source = await _open(source_pg, settings, "dps_reader", ("source",))
        rw = await _open(source_pg, settings, "dps_writer", ("source", "target"))
        try:
            yield PgEnv(source_pg, source, rw)
        finally:
            await rw.close()
            await source.close()
