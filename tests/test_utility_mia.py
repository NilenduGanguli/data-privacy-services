"""M5 Task 6B: utility (train on synthetic, test on real) and the membership-inference proxy.

Offline. Both are computed from the rows ``measure`` already holds — the fit sample (members), the
holdout (non-members, never fitted) and the deterministic preview of synthetic rows — and only
numbers are stored.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

import numpy as np
import pytest

pytest.importorskip("scipy")

from dps.engine.batch import BatchTransformer  # noqa: E402
from dps.engine.findings import hash_value  # noqa: E402
from dps.engine.metrics import membership_inference_proxy, utility_tstr  # noqa: E402
from dps.engine.policy import MethodSpec, Policy, load_preset  # noqa: E402
from dps.engine.service import PrivacyEngine  # noqa: E402
from dps.engine.synth.surrogates import generate_synthesis_seed, seed_bytes  # noqa: E402
from dps.jobs_models import ClassificationSnapshot, ColumnClassification  # noqa: E402
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape  # noqa: E402
from dps.layers.connectors.evidence import build_pack  # noqa: E402
from dps.layers.connectors.tabular import TabularGateError, prepare_tabular  # noqa: E402
from tests.connectors.factories import evidence_inputs  # noqa: E402
from tests.test_tabular_prepare import FakeConnector, _settings  # noqa: E402

SHAPE = TableShape(
    object=ObjectRef(owner="bank", name="accounts"),
    key=["ID"],
    key_source="primary_key",
    columns=[
        ColumnInfo(path="$.ID", name="ID", data_type="bigint", kind="int", is_key=True),
        ColumnInfo(path="$.BALANCE", name="BALANCE", data_type="numeric", kind="decimal", scale=2),
        ColumnInfo(path="$.AGE_BAND", name="AGE_BAND", data_type="integer", kind="int"),
        ColumnInfo(path="$.STATUS", name="STATUS", data_type="varchar(12)", kind="text"),
    ],
)
STATUSES = ["GOLDLEAF", "SILVERLINE"]


def _cc(name: str, kind: str, distinct: float) -> ColumnClassification:
    keep = MethodSpec(method="keep")
    return ColumnClassification(
        path=f"$.{name}",
        name=name,
        data_type=kind,
        kind=kind,
        is_key=name == "ID",
        entity_type=None,
        confidence=0.0,
        sensitivity=None,
        detectors=[],
        value_hit_rate=0.0,
        null_rate=0.0,
        distinct_ratio=distinct,
        shapes=[],
        text_column=False,
        anchor=False,
        suggested=keep,
        method=keep,
    )


SNAPSHOT = ClassificationSnapshot.model_construct(
    id="snap_u",
    tenant_id="t",
    job_id=None,
    connection_id="c",
    engine="postgres",
    object_ref="bank.accounts",
    status="accepted",
    policy_name="synthetic-realistic",
    policy_hash="h",
    shape=SHAPE,
    sample_rows=600,
    overrides=[],
    content_hash="c",
    created_at=None,
    accepted_at=None,
    accepted_by=None,
    columns=[
        _cc("ID", "int", 1.0),
        _cc("BALANCE", "decimal", 0.95),
        _cc("AGE_BAND", "int", 0.02),
        _cc("STATUS", "text", 0.004),
    ],
)


def _rows(n: int) -> list[dict[str, Any]]:
    rng = np.random.default_rng(11)
    out = []
    for i in range(n):
        balance = float(rng.lognormal(8, 1))
        out.append(
            {
                "ID": 7_000_000 + i,
                "BALANCE": Decimal(f"{balance:.2f}"),
                "AGE_BAND": int(rng.integers(2, 8)) * 10,
                "STATUS": STATUSES[0] if balance > 3000 else STATUSES[1],
            }
        )
    return out


async def _measure(policy: Policy, **kw: Any) -> dict[str, Any]:
    settings = _settings(**kw)
    seed = generate_synthesis_seed()

    def make(sampler: Any, constraints: Any) -> BatchTransformer:
        return BatchTransformer(
            PrivacyEngine(settings),
            policy=policy,
            mode="synthesize",
            columns=[],
            shape=SHAPE,
            key=None,
            seed=seed,
            locale="en_US",
            tabular=sampler,
            constraints=constraints,
        )

    prepared = await prepare_tabular(
        settings=settings,
        engine_name="copula",
        connector=FakeConnector(_rows(600)),  # type: ignore[arg-type]
        shape=SHAPE,
        selection=None,
        params={},
        snapshot=SNAPSHOT,
        policy=policy,
        seed=seed_bytes(seed),
        object_fqn="bank.accounts",
        make_transformer=make,
        anchor_of=lambda r: f"row:{hash_value(str(r['ID']))}",
    )
    return prepared.metrics


def _labelled(label: str | None) -> Policy:
    policy = load_preset("synthetic-realistic").model_copy(deep=True)
    policy.synthesize.utility_label = label
    return policy


def test_mia_proxy_detects_copies_and_accepts_fresh_draws() -> None:
    rng = np.random.default_rng(1)
    train = rng.normal(size=(400, 3))
    holdout = rng.normal(size=(400, 3))
    copies = train + rng.normal(scale=1e-3, size=train.shape)
    leaky = membership_inference_proxy(train, holdout, copies)
    assert leaky["run"] is True and leaky["method"] == "dcr_auc" and leaky["auc"] >= 0.9
    fresh = membership_inference_proxy(train, holdout, rng.normal(size=(400, 3)))
    assert 0.4 <= fresh["auc"] <= 0.6
    assert fresh["members"] == fresh["non_members"] == 400


def test_mia_proxy_is_deterministic_and_balanced() -> None:
    rng = np.random.default_rng(2)
    train, holdout, synth = (
        rng.normal(size=(1000, 2)),
        rng.normal(size=(80, 2)),
        rng.normal(size=(300, 2)),
    )
    first = membership_inference_proxy(train, holdout, synth)
    assert first == membership_inference_proxy(train, holdout, synth)
    assert first["members"] == first["non_members"] == 80
    assert membership_inference_proxy(train, holdout[:0], synth)["run"] is False


def test_utility_classification() -> None:
    rng = np.random.default_rng(3)
    x = rng.normal(size=(600, 2))
    y = list(x[:, 0] > 0)
    x2 = rng.normal(size=(400, 2))
    y2 = list(x2[:, 0] > 0)
    good = utility_tstr(x[:400], y[:400], x2, y2, x[400:], y[400:], task="classification")
    assert good["run"] is True and good["model"] == "knn5" and good["metric"] == "accuracy"
    assert good["train_on_real"] >= 0.9 and good["ratio"] >= 0.9 and good["test_rows"] == 200
    shuffled = list(np.random.default_rng(4).permutation(y2))
    bad = utility_tstr(x[:400], y[:400], x2, shuffled, x[400:], y[400:], task="classification")
    assert bad["ratio"] <= 0.7


def test_utility_regression() -> None:
    rng = np.random.default_rng(5)
    x = rng.normal(size=(600, 2))
    y = list(2 * x[:, 0] + rng.normal(scale=0.1, size=600))
    out = utility_tstr(x[:400], y[:400], x[:400], y[:400], x[400:], y[400:], task="regression")
    assert out["metric"] == "r2" and out["train_on_real"] >= 0.8
    assert out["ratio"] == 1.0  # the same training rows on both sides


async def test_measure_reports_utility_and_mia() -> None:
    out = await _measure(_labelled("$.STATUS"))
    assert out["utility"]["run"] is True and out["utility"]["label"] == "$.STATUS"
    assert out["utility"]["metric"] == "accuracy" and out["utility"]["task"] == "classification"
    mia = out["privacy"]["membership_inference"]
    assert mia["run"] is True and 0.0 <= mia["auc"] <= 1.0
    assert mia["target"] == 0.60 and mia["passed"] == (mia["auc"] <= 0.60)
    regression = await _measure(_labelled("$.BALANCE"))
    assert regression["utility"]["metric"] == "r2"
    small = await _measure(_labelled("$.STATUS"), mia_min_holdout=10_000)
    assert small["privacy"]["membership_inference"]["run"] is False
    assert "holdout has" in small["privacy"]["membership_inference"]["reason"]
    assert small["utility"]["run"] is False and "holdout has" in small["utility"]["reason"]
    none = await _measure(_labelled(None))
    assert none["utility"] == {"run": False, "reason": "no utility_label in the policy"}
    other = await _measure(_labelled("$.ID"))
    assert other["utility"] == {"run": False, "reason": "utility_label is not a modelled column"}
    text = json.dumps(out)
    sources = _rows(600)[:5]
    assert not any(s in text for s in STATUSES)
    assert not any(str(r["BALANCE"]) in text or str(r["ID"]) in text for r in sources)


async def test_an_enforced_privacy_gate_also_checks_the_mia_proxy() -> None:
    with pytest.raises(TabularGateError) as err:
        await _measure(
            _labelled("$.STATUS"),
            privacy_nndr_enforce=True,
            privacy_nndr_target=0.01,  # NNDR passes ...
            mia_auc_target=0.01,  # ... the membership-inference proxy cannot
        )
    assert err.value.code == "privacy_gate" and "membership" in err.value.detail


def test_utility_label_is_hashed_only_when_set() -> None:
    plain = load_preset("synthetic-realistic")
    assert _labelled(None).config_hash() == plain.config_hash()
    assert _labelled("$.STATUS").config_hash() != plain.config_hash()
    with pytest.raises(ValueError):
        Policy.model_validate({"synthesize": {"utility_label": "STATUS; drop"}})


def test_evidence_carries_utility() -> None:
    inputs = evidence_inputs(mode="synthesize")
    inputs.job.spec["options"]["tabular"] = "copula"
    utility = {"run": True, "metric": "accuracy", "train_on_real": 0.9, "label": "$.segment"}
    inputs.job.progress.metrics = {"utility": utility, "privacy": {"exact_row_matches": 0}}
    pack = build_pack(inputs)
    assert pack.utility is not None and pack.utility["metric"] == "accuracy"
