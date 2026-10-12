"""``fpe_digits`` on integer, decimal and text columns of the lane's source-pg (M2 review G4 r1, B1).

Real FF1 tokens are written to a table, read back as the driver returns them and revealed the way
Task 22's ``reveal_row`` will (per-path hints from the column plans). Plain FF1 starts about one
token in ten with ``0``, which a ``bigint`` or ``numeric`` column drops; the plans' default
``leading_zero: preserve`` keeps every digit, and the same digits get the same token in a text
column (contract §6.5). Rows are synthetic.
"""

from __future__ import annotations

import random
from decimal import Decimal
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.batch import BatchTransformer, ColumnPlan
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.transform.apply import ApplyOptions, RevealHint, reveal
from dps.engine.transform.crypto import KeyMaterial, generate_key_material, key_check_value
from dps.jobs_models import Selection
from dps.layers.connectors.base import KeyRange, Row, TableShape, to_engine_text
from dps.layers.connectors.postgres import PostgresConnector
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.integration, pytest.mark.pg_source, pytest.mark.fpe]

DDL = (
    "seq int PRIMARY KEY, id bigint NOT NULL, ref varchar(20) NOT NULL, amt numeric(14,2) NOT NULL"
)
ROWS = 300


def _records() -> list[tuple[Any, ...]]:
    rng = random.Random(20261012)
    ids = rng.sample(range(10_000_000, 100_000_000), ROWS)
    return [
        (
            seq,
            i,
            str(i),
            (Decimal(rng.randrange(1_000_000, 100_000_000)) / 100).quantize(Decimal("0.01")),
        )
        for seq, i in enumerate(ids)
    ]


def _plans(shape: TableShape, *, ref_params: dict[str, Any] | None = None) -> list[ColumnPlan]:
    entities = {"id": "CUSTOMER_ID", "ref": "CUSTOMER_ID", "amt": "AMOUNT"}
    plans = []
    for name, entity in entities.items():
        col = shape.column(name)
        params = (ref_params or {}) if name == "ref" else {}
        plans.append(
            ColumnPlan(
                path=col.path,
                name=name,
                entity_type=entity,
                method=MethodSpec(method="fpe_digits", **params),
                text_column=False,
                reversible=True,
                kind=col.kind,
                length=col.length,
                precision=col.precision,
                scale=col.scale,
            )
        )
    return plans


async def _read_all(c: PostgresConnector, shape: TableShape) -> list[Row]:
    return [
        r
        async for batch in c.read_partition(
            shape, Selection(), {}, KeyRange(), after_key=None, batch=64
        )
        for r in batch
    ]


def _reveal(
    engine: PrivacyEngine, key: KeyMaterial, plan: ColumnPlan, shape: TableShape, rows: list[Row]
) -> list[str | None]:
    opts = ApplyOptions(
        policy=load_preset("gdpr-pseudonymise"), key=key, surrogates=None, fpe_enabled=True
    )
    hint = RevealHint(
        path=plan.path,
        entity_type=plan.entity_type,
        method=plan.method.method,
        params=plan.method.params,  # what the evidence pack's reveal_map carries
        kid=key.kid,
        kcv=key_check_value(key),
    )
    info = shape.column(plan.name)
    return [
        reveal(
            output={plan.name: to_engine_text(r[plan.name], info)},
            key=key,
            hints=[hint],
            options=opts,
        )[plan.name]
        for r in rows
    ]


async def test_fpe_digits_tokens_survive_bigint_and_numeric_columns_and_reveal(
    source_pg: SourcePg, settings: Settings
) -> None:
    pytest.importorskip("fastfpe")
    engine = PrivacyEngine(
        Settings(_env_file=None, llm_provider="stub", fpe_enabled=True, ner_enabled=False)
    )
    obj = await source_pg.table("accounts", DDL, _records())
    masked_obj = await source_pg.table("accounts_masked", DDL, [])
    c = PostgresConnector(
        source_pg.connection(), source_pg.credentials(), settings, action="test", job_id="j_test"
    )
    await c.open()
    try:
        shape = await c.describe(obj)
        source = await _read_all(c, shape)
        key = generate_key_material("key")
        plans = _plans(shape)
        assert all(p.method.params == {"leading_zero": "preserve"} for p in plans)
        transformer = BatchTransformer(
            engine,
            policy=load_preset("gdpr-pseudonymise"),
            mode="obfuscate",
            columns=plans,
            shape=shape,
            key=key,
            seed=None,
            locale="en_IN",
        )
        result = transformer.transform([dict(r) for r in source])
        assert result.verification.passed and result.values_by_method == {"fpe_digits": 3 * ROWS}
        names = [col.name for col in shape.columns]
        await source_pg.admin.copy_records_to_table(
            "accounts_masked",
            records=[tuple(r[n] for n in names) for r in result.rows],
            schema_name=source_pg.schema,
        )
        stored = await _read_all(c, await c.describe(masked_obj))
        assert [r["seq"] for r in stored] == [r["seq"] for r in source]
        # every digit kept: 8-digit ids stay 8 digits, amounts keep their length and scale
        assert all(len(str(r["id"])) == 8 for r in stored)
        assert len({r["id"] for r in stored}) == ROWS
        assert all(
            len(format(m["amt"], "f")) == len(format(o["amt"], "f"))
            for m, o in zip(stored, source, strict=True)
        )
        # contract §6.5: the same digits get the same token in a bigint and a text column
        assert all(str(r["id"]) == r["ref"] for r in stored)
        by_name = {p.name: p for p in plans}
        assert _reveal(engine, key, by_name["id"], shape, stored) == [str(r["id"]) for r in source]
        assert _reveal(engine, key, by_name["ref"], shape, stored) == [r["ref"] for r in source]
        assert _reveal(engine, key, by_name["amt"], shape, stored) == [
            format(r["amt"], "f") for r in source
        ]
        # the data set does exercise the defect: plain FF1 (v0.1.0 tokens, allowed on a text
        # column) starts some of these same tokens with 0
        legacy = BatchTransformer(
            engine,
            policy=load_preset("gdpr-pseudonymise"),
            mode="obfuscate",
            columns=[
                p for p in _plans(shape, ref_params={"leading_zero": "any"}) if p.name == "ref"
            ],
            shape=shape,
            key=key,
            seed=None,
            locale="en_IN",
        ).transform([dict(r) for r in source])
        zero_led = sum(1 for r in legacy.rows if r["ref"].startswith("0"))
        assert zero_led >= 10
        assert (
            sum(1 for a, b in zip(legacy.rows, result.rows, strict=True) if a["ref"] != b["ref"])
            == zero_led
        )  # only the zero-led tokens changed
    finally:
        await c.close()
