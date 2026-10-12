"""Task 6 (M5): tabular synthesis at ``prepare`` — eligibility, holdout, fit, preview, measure, gate.

Offline: a fake connector hands back synthetic rows; the real ``BatchTransformer`` and
``GaussianCopula`` run on them. Sentinel names and category labels must never reach the metrics,
the gate errors or anything else the job row stores.
"""

from __future__ import annotations

import base64
import json
import random
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

pytest.importorskip("scipy")

from dps.config import Settings  # noqa: E402
from dps.engine.batch import BatchTransformer, ColumnPlan  # noqa: E402
from dps.engine.findings import hash_value  # noqa: E402
from dps.engine.policy import MethodSpec, Policy, TabularConstraint, load_preset  # noqa: E402
from dps.engine.service import PrivacyEngine  # noqa: E402
from dps.engine.synth.surrogates import generate_synthesis_seed, seed_bytes  # noqa: E402
from dps.jobs_models import ClassificationSnapshot, ColumnClassification  # noqa: E402
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape  # noqa: E402
from dps.layers.connectors.tabular import (  # noqa: E402
    TabularGateError,
    TabularPrepared,
    load_sampler,
    prepare_tabular,
    runtime_sampler,
    split_holdout,
    tabular_columns,
)

SENTINEL_NAMES = ["Zebulon Quixote", "Ottoline Varga"]
LABELS = ["AFFLUENT", "MASS"]


def _col(name: str, kind: str, **kw: Any) -> ColumnInfo:
    return ColumnInfo(path=f"$.{name}", name=name, data_type=kind, kind=kind, **kw)  # type: ignore[arg-type]


SHAPE = TableShape(
    object=ObjectRef(owner="bank", name="customers"),
    key=["ID"],
    key_source="primary_key",
    columns=[
        _col("ID", "int", is_key=True, nullable=False),
        _col("FULL_NAME", "text"),
        _col("BALANCE", "decimal", scale=2, precision=14),
        _col("SEGMENT", "text"),
        _col("NOTE", "text"),
        _col("PHOTO", "binary"),
    ],
)


def _cc(
    name: str, kind: str, entity: str | None, distinct: float = 0.01, text: bool = False
) -> ColumnClassification:
    method = MethodSpec(method="surrogate" if entity else "keep")
    return ColumnClassification(
        path=f"$.{name}",
        name=name,
        data_type=kind,
        kind=kind,
        is_key=name == "ID",
        entity_type=entity,
        confidence=0.9,
        sensitivity=None,
        detectors=[],
        value_hit_rate=0.0,
        null_rate=0.0,
        distinct_ratio=distinct,
        shapes=[],
        text_column=text,
        anchor=False,
        suggested=method,
        method=method,
    )


def _snapshot(shape: TableShape, columns: list[ColumnClassification]) -> ClassificationSnapshot:
    return ClassificationSnapshot.model_construct(
        id="snap_x",
        tenant_id="t",
        job_id=None,
        connection_id="c",
        engine="postgres",
        object_ref="bank.customers",
        status="accepted",
        policy_name="synthetic-realistic",
        policy_hash="h",
        shape=shape,
        sample_rows=600,
        overrides=[],
        content_hash="c",
        created_at=None,
        accepted_at=None,
        accepted_by=None,
        columns=columns,
    )


SNAPSHOT = _snapshot(
    SHAPE,
    [
        _cc("ID", "int", None, 1.0),
        _cc("FULL_NAME", "text", "PERSON_NAME", 0.9),
        _cc("BALANCE", "decimal", None, 0.95),
        _cc("SEGMENT", "text", None, 0.003),
        _cc("NOTE", "text", None, 0.9),
        _cc("PHOTO", "binary", None, 1.0),
    ],
)


def _rows(n: int) -> list[dict[str, Any]]:
    rng = np.random.default_rng(3)
    bal = rng.lognormal(8, 1, n)
    return [
        {
            "ID": i,
            "FULL_NAME": SENTINEL_NAMES[i % 2],
            "BALANCE": Decimal(f"{b:.2f}"),
            "SEGMENT": LABELS[0] if b > 3000 else LABELS[1],
            "NOTE": f"n{i}",
            "PHOTO": b"\x00",
        }
        for i, b in enumerate(bal)
    ]


class FakeConnector:
    engine = "postgres"

    def __init__(self, rows: list[dict[str, Any]]) -> None:
        self.rows = rows

    async def sample(self, shape: TableShape, selection: Any, params: Any, n: int) -> list[Any]:
        return [dict(r) for r in self.rows[:n]]


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None,
        llm_provider="stub",
        ner_enabled=False,
        copula_fit_rows=400,
        copula_holdout_rows=100,
        copula_metrics_rows=300,
    )
    base.update(kw)
    return Settings(**base)


def _anchor(row: dict[str, Any]) -> str:
    return f"row:{hash_value(str(row.get('ID', row.get('CODE'))))}"


PLANS = [
    ColumnPlan(
        path="$.FULL_NAME",
        name="FULL_NAME",
        entity_type="PERSON_NAME",
        method=MethodSpec(method="surrogate"),
        text_column=False,
        reversible=False,
        kind="text",
        length=120,
    )
]


async def _prepare(
    rows: list[dict[str, Any]],
    *,
    shape: TableShape = SHAPE,
    snapshot: ClassificationSnapshot = SNAPSHOT,
    plans: list[ColumnPlan] = PLANS,
    policy: Policy | None = None,
    seed: Any = None,
    engine_name: str = "copula",
    **kw: Any,
) -> tuple[TabularPrepared, Any]:
    settings = _settings(**kw)
    seed = seed or generate_synthesis_seed()
    policy = policy or load_preset("synthetic-realistic")

    def make(sampler: Any, constraints: Any) -> BatchTransformer:
        return BatchTransformer(
            PrivacyEngine(settings),
            policy=policy,
            mode="synthesize",
            columns=plans,
            shape=shape,
            key=None,
            seed=seed,
            locale="en_US",
            tabular=sampler,
            constraints=constraints,
        )

    out = await prepare_tabular(
        settings=settings,
        engine_name=engine_name,
        connector=FakeConnector(rows),  # type: ignore[arg-type]
        shape=shape,
        selection=None,
        params={},
        snapshot=snapshot,
        policy=policy,
        seed=seed_bytes(seed),
        object_fqn="bank.customers",
        make_transformer=make,
        anchor_of=_anchor,
    )
    return out, seed


def test_column_eligibility() -> None:
    cols, excluded = tabular_columns(SHAPE, SNAPSHOT)
    assert [c.path for c in cols] == ["$.BALANCE", "$.SEGMENT"]
    assert cols[0].kind == "decimal" and cols[0].scale == 2 and cols[1].kind == "categorical"
    assert excluded == [
        {"path": "$.NOTE", "reason": "high_cardinality"},
        {"path": "$.PHOTO", "reason": "kind:binary"},
    ]


def test_integer_columns_get_the_digits_their_type_holds() -> None:
    shape = TableShape(
        object=ObjectRef(owner="bank", name="t"),
        key=["ID"],
        key_source="primary_key",
        columns=[
            ColumnInfo(path="$.ID", name="ID", data_type="bigint", kind="int", is_key=True),
            ColumnInfo(path="$.S", name="S", data_type="smallint", kind="int"),
            ColumnInfo(path="$.I", name="I", data_type="integer", kind="int"),
            ColumnInfo(path="$.B", name="B", data_type="bigint", kind="int"),
            ColumnInfo(path="$.N", name="N", data_type="numeric(7,0)", kind="decimal", precision=7),
        ],
    )
    snap = _snapshot(shape, [_cc(n, "int", None) for n in ("ID", "S", "I", "B", "N")])
    cols = {c.name: c for c in tabular_columns(shape, snap)[0]}
    assert {n: c.precision for n, c in cols.items()} == {"B": 18, "I": 9, "N": 7, "S": 4}


def test_holdout_split_is_deterministic_and_bounded() -> None:
    rows = _rows(1000)
    anchors = [_anchor(r) for r in rows]
    train, holdout = split_holdout(rows, anchors, b"s" * 32, max_holdout=150)
    assert len(holdout) <= 150 and len(train) + len(holdout) == 1000
    assert split_holdout(rows, anchors, b"s" * 32, max_holdout=150)[1] == holdout
    assert {r["ID"] for r in train}.isdisjoint({r["ID"] for r in holdout})


async def test_prepare_returns_a_sealable_model_and_value_free_metrics() -> None:
    prepared, seed = await _prepare(_rows(600))
    m = prepared.metrics
    assert m["privacy"]["exact_row_matches"] == 0 and m["privacy"]["nndr"]["measured"] == 1.0
    assert m["fidelity"]["score"] > 0.8 and set(m["fidelity"]["ks"]) == {"$.BALANCE"}
    assert set(m["fidelity"]["tvd"]) == {"$.SEGMENT"}
    assert m["tabular"]["columns"] == ["$.BALANCE", "$.SEGMENT"]
    assert m["tabular"]["preview_rows"] == 300 and m["tabular"]["engine"] == "copula"
    # the holdout is ~20 % by a keyed hash of each anchor, capped at copula_holdout_rows
    assert m["tabular"]["fit_rows"] + m["tabular"]["holdout_rows"] == 500
    assert 50 <= m["tabular"]["holdout_rows"] <= 100
    assert m["privacy"]["membership_inference"]["run"] is True  # Task 6B
    assert m["utility"] == {"run": False, "reason": "no utility_label in the policy"}
    text = json.dumps(m)
    assert not any(s in text for s in [*SENTINEL_NAMES, *LABELS])
    sampler = load_sampler("copula", prepared.model_b64, seed_bytes(seed))
    assert sampler.columns == ["BALANCE", "SEGMENT"] and base64.b64decode(prepared.model_b64)
    assert load_sampler("copula", prepared.model_b64, seed_bytes(seed)) is sampler


async def test_prepare_is_deterministic_whatever_order_the_sample_comes_in() -> None:
    rows = _rows(450)  # fewer than fit + holdout: the sample is the whole table
    seed = generate_synthesis_seed()
    first, _ = await _prepare(rows, seed=seed)
    shuffled = list(rows)
    random.Random(7).shuffle(shuffled)
    again, _ = await _prepare(shuffled, seed=seed)
    assert again.model_b64 == first.model_b64
    assert again.metrics["fidelity"]["score"] == first.metrics["fidelity"]["score"]


async def test_runtime_sampler_rebuilds_the_constraints_over_the_sampled_columns() -> None:
    policy = load_preset("synthetic-realistic").model_copy(deep=True)
    policy.synthesize.constraints = [TabularConstraint(path="$.BALANCE", min=100, max=5000)]
    prepared, seed = await _prepare(_rows(600), policy=policy)
    assert prepared.metrics["tabular"]["constraints"] == [{"path": "$.BALANCE", "rule": "range"}]
    sampler, constraints = runtime_sampler(
        "copula",
        prepared.model_b64,
        seed_bytes(seed),
        policy=policy,
        shape=SHAPE,
        snapshot=SNAPSHOT,
        object_fqn="bank.customers",
    )
    assert constraints is not None and constraints.describe() == [
        {"path": "$.BALANCE", "rule": "range"}
    ]
    row = sampler.sample_row("row:x")
    constraints.apply(row, key_int=1)
    assert Decimal("100") <= row["BALANCE"] <= Decimal("5000")


@pytest.mark.parametrize(
    "rows,kw,code",
    [
        (40, {}, "tabular_invalid"),
        (600, {"synth_tabular_engine": ""}, "tabular_invalid"),
        (600, {"privacy_nndr_enforce": True, "privacy_nndr_target": 10.0}, "privacy_gate"),
    ],
)
async def test_prepare_gates(rows: int, kw: dict[str, Any], code: str) -> None:
    with pytest.raises(TabularGateError) as err:
        await _prepare(_rows(rows), **kw)
    assert err.value.code == code
    assert not any(s in err.value.detail for s in [*SENTINEL_NAMES, *LABELS])


async def test_a_table_whose_every_combination_is_real_fails_closed() -> None:
    shape = TableShape(
        object=ObjectRef(owner="bank", name="flags"),
        key=["ID"],
        key_source="primary_key",
        columns=[_col("ID", "int", is_key=True), _col("A", "text"), _col("B", "text")],
    )
    snap = _snapshot(
        shape, [_cc("ID", "int", None, 1.0), _cc("A", "text", None), _cc("B", "text", None)]
    )
    rows = [
        {"ID": i, "A": "yes" if i % 2 else "no", "B": "up" if i % 3 else "down"} for i in range(200)
    ]
    with pytest.raises(TabularGateError) as err:
        await _prepare(rows, shape=shape, snapshot=snap, plans=[])
    assert err.value.code == "leakage" and "yes" not in err.value.detail


async def test_a_unique_rule_needs_a_single_integer_key() -> None:
    shape = TableShape(
        object=ObjectRef(owner="bank", name="customers"),
        key=["CODE"],
        key_source="primary_key",
        columns=[
            _col("CODE", "text", is_key=True),
            ColumnInfo(path="$.REF", name="REF", data_type="bigint", kind="int"),
            _col("BALANCE", "decimal", scale=2, precision=14),
        ],
    )
    snap = _snapshot(
        shape,
        [
            _cc("CODE", "text", None, 1.0),
            _cc("REF", "int", None, 1.0),
            _cc("BALANCE", "decimal", None),
        ],
    )
    policy = load_preset("synthetic-realistic").model_copy(deep=True)
    policy.synthesize.constraints = [TabularConstraint(path="$.REF", unique=True)]
    rows = [
        {"CODE": f"C{r['ID']}", "REF": 1000 + r["ID"], "BALANCE": r["BALANCE"]} for r in _rows(300)
    ]
    with pytest.raises(TabularGateError) as err:
        await _prepare(rows, shape=shape, snapshot=snap, plans=[], policy=policy)
    assert err.value.code == "incompatible_method"
