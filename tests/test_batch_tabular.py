"""Tabular sampling inside ``BatchTransformer`` (contract §6.4, §6.6): sampled columns, constraints
after sampling, in-flight exact-row rejection and fail-closed redraws."""

from __future__ import annotations

from collections.abc import Mapping, Sequence, Set
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("scipy")

from dps.config import Settings  # noqa: E402
from dps.engine.batch import BatchTransformer, ColumnPlan, LeakageError  # noqa: E402
from dps.engine.findings import hash_value  # noqa: E402
from dps.engine.metrics import row_digest  # noqa: E402
from dps.engine.policy import MethodSpec, TabularConstraint, load_preset  # noqa: E402
from dps.engine.service import PrivacyEngine  # noqa: E402
from dps.engine.synth.constraints import ColumnSpec, ConstraintSet  # noqa: E402
from dps.engine.synth.copula import GaussianCopula  # noqa: E402
from dps.engine.synth.surrogates import generate_synthesis_seed  # noqa: E402
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape  # noqa: E402

KEY = b"d" * 32
ENGINE = PrivacyEngine(Settings(_env_file=None, llm_provider="stub", ner_enabled=False))


def _anchor(i: int) -> str:
    """Contract §6.2: no anchor column → ``row:<hash_value(canonical key text)>``."""
    return f"row:{hash_value(str(i))}"


class FakeSampler:
    engine = "copula"

    def __init__(
        self,
        *,
        echo: Sequence[dict[str, Any]] = (),
        always_echo: bool = False,
        reject_rows: Sequence[Mapping[str, Any]] = (),
    ) -> None:
        self._echo, self._always = {_anchor(i): r for i, r in enumerate(echo)}, always_echo
        self.reject_columns = ["BALANCE", "SEGMENT"]
        self._reject = {row_digest(r, self.reject_columns, KEY) for r in reject_rows}
        self.calls: list[tuple[int, int]] = []  # (anchors, attempt)

    @property
    def columns(self) -> list[str]:
        return ["BALANCE", "SEGMENT"]

    def sample_rows(self, anchors: Sequence[str], *, attempt: int = 0) -> list[dict[str, Any]]:
        self.calls.append((len(anchors), attempt))
        out = []
        for a in anchors:
            if a in self._echo and (attempt == 0 or self._always):
                out.append({k: self._echo[a][k] for k in self.columns})
            else:
                out.append({"BALANCE": Decimal(len(a) * 10 + attempt), "SEGMENT": "SYN"})
        return out

    def sample_row(self, anchor: str) -> dict[str, Any]:
        return self.sample_rows([anchor])[0]

    def row_digest(self, row: Mapping[str, Any]) -> int:
        return row_digest(row, self.reject_columns, KEY)

    def is_rejected(self, row: Mapping[str, Any], *, extra: Set[int] = frozenset()) -> bool:
        d = self.row_digest(row)
        return d in self._reject or d in extra

    def to_bytes(self) -> bytes:
        return b""


def _shape(with_name: bool) -> TableShape:
    cols = [
        ColumnInfo(
            path="$.ID", name="ID", data_type="integer", kind="int", nullable=False, is_key=True
        ),
        ColumnInfo(
            path="$.BALANCE",
            name="BALANCE",
            data_type="numeric(12,2)",
            kind="decimal",
            precision=12,
            scale=2,
        ),
        ColumnInfo(path="$.SEGMENT", name="SEGMENT", data_type="text", kind="text"),
    ]
    if with_name:
        cols.insert(
            1,
            ColumnInfo(
                path="$.FULL_NAME", name="FULL_NAME", data_type="text", kind="text", length=120
            ),
        )
    return TableShape(
        object=ObjectRef(owner="bank", name="customers"),
        columns=cols,
        key=["ID"],
        key_source="primary_key",
    )


def _name_plan() -> ColumnPlan:
    return ColumnPlan(
        path="$.FULL_NAME",
        name="FULL_NAME",
        entity_type="PERSON_NAME",
        method=MethodSpec(method="surrogate"),
        text_column=False,
        reversible=False,
        kind="text",
        length=120,
    )


def _transformer(
    sampler: Any,
    *,
    with_name: bool = True,
    constraints: ConstraintSet | None = None,
    max_resample: int = 8,
    shape: TableShape | None = None,
    anchor: tuple[str, str] | None = None,
) -> BatchTransformer:
    return BatchTransformer(
        ENGINE,
        policy=load_preset("synthetic-realistic"),
        mode="synthesize",
        columns=[_name_plan()] if with_name else [],
        shape=shape or _shape(with_name),
        key=None,
        seed=generate_synthesis_seed(),
        locale="en_US",
        tabular=sampler,
        constraints=constraints,
        max_resample=max_resample,
        anchor=anchor,
    )


def _rows(n: int, with_name: bool = True) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = [
        {"ID": i, "BALANCE": Decimal(f"{100 + i}.50"), "SEGMENT": "MASS"} for i in range(n)
    ]
    if with_name:
        for r in rows:
            r["FULL_NAME"] = "Ananya Krishnan"
    return rows


def test_sampler_writes_tabular_columns_and_surrogates_the_rest() -> None:
    sampler = FakeSampler()
    result = _transformer(sampler).transform(_rows(50))
    assert all(r["SEGMENT"] == "SYN" and r["FULL_NAME"] != "Ananya Krishnan" for r in result.rows)
    assert [r["ID"] for r in result.rows] == list(range(50))
    assert result.values_by_method["copula"] == 100
    assert result.values_by_method.get("copula_resample", 0) == 0
    assert result.values_by_column["BALANCE"] == result.values_by_column["SEGMENT"] == 50
    assert sampler.calls == [(50, 0)]  # one vectorised draw per batch


def test_output_is_deterministic_across_batch_boundaries() -> None:
    t = _transformer(FakeSampler())
    whole = t.transform(_rows(20)).rows
    split = t.transform(_rows(20)[:7]).rows + t.transform(_rows(20)[7:]).rows
    pairs = [(r["BALANCE"], r["SEGMENT"]) for r in whole]
    assert pairs == [(r["BALANCE"], r["SEGMENT"]) for r in split]


def test_a_row_equal_to_its_source_is_redrawn() -> None:
    source = _rows(3, with_name=False)
    result = _transformer(FakeSampler(echo=source), with_name=False).transform(source)
    for r, s in zip(result.rows, source, strict=True):
        assert not (r["BALANCE"] == s["BALANCE"] and r["SEGMENT"] == s["SEGMENT"])
    assert result.values_by_method["copula_resample"] == 3
    assert source[0]["SEGMENT"] == "MASS"  # the input rows are not mutated


def test_a_row_equal_to_a_fit_or_holdout_row_is_redrawn() -> None:
    real = {"BALANCE": Decimal("200.00"), "SEGMENT": "AFFLUENT"}
    sampler = FakeSampler(echo=[real], reject_rows=[real])
    result = _transformer(sampler, with_name=False).transform(
        [{"ID": 0, "BALANCE": Decimal("1.00"), "SEGMENT": "MASS"}]
    )
    assert result.rows[0]["SEGMENT"] == "SYN" and result.values_by_method["copula_resample"] == 1


def test_fails_closed_after_max_resample() -> None:
    source = _rows(3, with_name=False)
    sampler = FakeSampler(echo=source, always_echo=True)
    with pytest.raises(LeakageError) as err:
        _transformer(sampler, with_name=False, max_resample=2).transform(source)
    assert str(err.value) == "exact_row_match" and err.value.code == "leakage"


def test_constraints_run_after_sampling() -> None:
    cs = ConstraintSet(
        [
            TabularConstraint.model_validate(
                {
                    "path": "$.SEGMENT",
                    "when": [
                        {"if": {"path": "$.BALANCE", "op": ">=", "value": 0}, "then": "RULED"}
                    ],
                }
            )
        ],
        {
            "SEGMENT": ColumnSpec("SEGMENT", "categorical"),
            "BALANCE": ColumnSpec("BALANCE", "decimal", scale=2),
        },
        seed=bytes(32),
        object_fqn="bank.customers",
    )
    result = _transformer(FakeSampler(), constraints=cs).transform(_rows(5))
    assert {r["SEGMENT"] for r in result.rows} == {"RULED"}


def test_unique_constraints_receive_the_integer_key() -> None:
    cs = ConstraintSet(
        [TabularConstraint(path="$.SEGMENT", unique=True)],
        {"SEGMENT": ColumnSpec("SEGMENT", "categorical", shape="XX-99999999")},
        seed=bytes(32),
        object_fqn="bank.customers",
    )
    rows = _transformer(FakeSampler(), constraints=cs).transform(_rows(40)).rows
    assert len({r["SEGMENT"] for r in rows}) == 40


def test_without_a_sampler_nothing_changes() -> None:
    result = _transformer(None).transform(_rows(5))
    assert [r["BALANCE"] for r in result.rows] == [r["BALANCE"] for r in _rows(5)]
    assert "copula" not in result.values_by_method


def test_a_sampler_may_not_write_keys_planned_or_unknown_columns() -> None:
    class Writes(FakeSampler):
        def __init__(self, cols: list[str]) -> None:
            super().__init__()
            self._cols = cols

        @property
        def columns(self) -> list[str]:
            return self._cols

    for cols in (["ID"], ["FULL_NAME"], ["NOPE"]):
        with pytest.raises(ValueError, match="tabular sampler"):
            _transformer(Writes(cols))
    with pytest.raises(ValueError, match="synthesize"):
        BatchTransformer(
            ENGINE,
            policy=load_preset("gdpr-pseudonymise"),
            mode="obfuscate",
            columns=[],
            shape=_shape(False),
            key=__import__(
                "dps.engine.transform.crypto", fromlist=["generate_key_material"]
            ).generate_key_material("job"),
            seed=None,
            locale="en_US",
            tabular=FakeSampler(),
        )


def test_rows_sharing_an_entity_anchor_still_get_their_own_draws() -> None:
    # the sampler is keyed by the row key (unique per row), not by the persona anchor that every
    # row of one customer shares — those rows would otherwise get identical synthetic values
    class Recording(FakeSampler):
        def __init__(self) -> None:
            super().__init__()
            self.anchors: list[str] = []

        def sample_rows(self, anchors: Sequence[str], *, attempt: int = 0) -> list[dict[str, Any]]:
            self.anchors += list(anchors)
            return super().sample_rows(anchors, attempt=attempt)

    sampler = Recording()
    _transformer(sampler, anchor=("FULL_NAME", "PERSON_NAME")).transform(_rows(4))
    assert sampler.anchors == [_anchor(i) for i in range(4)]


def _real_rows(n: int, distinct: int | None = None) -> list[dict[str, Any]]:
    np = pytest.importorskip("numpy")
    rng = np.random.default_rng(5)
    balances = rng.lognormal(mean=7, sigma=1.0, size=n)
    return [
        {
            "ID": i,
            "BALANCE": Decimal(f"{(i % distinct) * 100 if distinct else balances[i]:.2f}"),
            "SEGMENT": ("MASS", "AFFLUENT")[i % 2],
        }
        for i in range(n)
    ]


def _fit(real: list[dict[str, Any]], reject: list[dict[str, Any]]) -> GaussianCopula:
    return GaussianCopula.fit(
        {"$.BALANCE": [r["BALANCE"] for r in real], "$.SEGMENT": [r["SEGMENT"] for r in real]},
        {"$.BALANCE": "decimal", "$.SEGMENT": "categorical"},
        rare_floor=0.01,
        seed=bytes(range(32)),
        scales={"$.BALANCE": 2},
        reject_rows=reject,
        reject_columns=["BALANCE", "SEGMENT"],
    )


def test_with_the_real_copula_no_output_row_equals_a_real_row() -> None:
    real = _real_rows(400)
    t = _transformer(_fit(real, real), with_name=False)
    result = t.transform(real)
    seen = {(r["BALANCE"], r["SEGMENT"]) for r in real}
    assert not any((r["BALANCE"], r["SEGMENT"]) in seen for r in result.rows)
    assert result.values_by_method["copula"] == 800
    assert [r["ID"] for r in result.rows] == list(range(400))
    assert t.transform(real).rows == result.rows  # deterministic, resumable


def test_the_real_copula_redraws_rows_its_reject_set_holds() -> None:
    # model B is model A (same sample, same seed) whose reject set holds A's own first draws:
    # every first draw is rejected and redrawn with the attempt-salted stream
    real = _real_rows(200)
    first = _transformer(_fit(real, []), with_name=False).transform(real).rows
    drawn = [{"BALANCE": r["BALANCE"], "SEGMENT": r["SEGMENT"]} for r in first]
    result = _transformer(_fit(real, drawn), with_name=False).transform(real)
    assert result.values_by_method["copula_resample"] >= 200  # a redraw may hit another draw
    again = {(r["BALANCE"], r["SEGMENT"]) for r in drawn}
    assert not any((r["BALANCE"], r["SEGMENT"]) in again for r in result.rows)


def test_a_table_whose_every_combination_is_real_fails_closed() -> None:
    # few distinct values on every column: almost every draw is some real row, so the batch is
    # refused rather than written (prepare's preview meets this first, Task 6)
    real = _real_rows(400, distinct=7)
    with pytest.raises(LeakageError, match="^exact_row_match$"):
        _transformer(_fit(real, real), with_name=False).transform(real)
