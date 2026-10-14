"""Tabular constraints (spec §5.6): keyed unique, ranges, positivity, dependent-field rules, and
their policy models (``synthesize.constraints``, ``synthesize.quasi_identifiers``)."""

from __future__ import annotations

import re
from datetime import date
from decimal import Decimal

import pytest

pytest.importorskip("scipy")

from dps.engine.policy import (  # noqa: E402
    Policy,
    SynthesizeConfig,
    TabularConstraint,
    load_preset,
    resolve_policy,
)
from dps.engine.synth.constraints import (  # noqa: E402
    ColumnSpec,
    ConstraintError,
    ConstraintSet,
    feistel_permute,
)

SEED = bytes(range(32))
KEYS = [b"k1" * 16, b"k2" * 16, b"k3" * 16, b"k4" * 16]
COLS = {
    "ACCOUNT_REF": ColumnSpec("ACCOUNT_REF", "int", precision=10),
    "CARD_REF": ColumnSpec("CARD_REF", "categorical", shape="XXX-99999999"),
    "BALANCE": ColumnSpec("BALANCE", "decimal", scale=2),
    "OPENED": ColumnSpec("OPENED", "date"),
    "STATUS": ColumnSpec("STATUS", "categorical"),
    "TENURE": ColumnSpec("TENURE", "int"),
}


def _set(*constraints: dict, fqn: str = "BANK.ACCOUNTS") -> ConstraintSet:
    return ConstraintSet(
        [TabularConstraint.model_validate(c) for c in constraints],
        COLS,
        seed=SEED,
        object_fqn=fqn,
    )


def test_feistel_is_a_bijection_and_keyed() -> None:
    assert sorted(feistel_permute(x, 1000, KEYS) for x in range(1000)) == list(range(1000))
    sample = {feistel_permute(x, 999_983, KEYS) for x in range(5000)}
    assert len(sample) == 5000 and max(sample) < 999_983
    assert feistel_permute(7, 1000, KEYS) == feistel_permute(7, 1000, KEYS)
    forward = [feistel_permute(x, 1000, KEYS) for x in range(20)]
    assert forward != [feistel_permute(x, 1000, KEYS[::-1]) for x in range(20)]
    with pytest.raises(ConstraintError):
        feistel_permute(1000, 1000, KEYS)


def test_unique_integer_column() -> None:
    cs = _set({"path": "$.ACCOUNT_REF", "unique": True})
    assert cs.needs_key_int
    values = []
    for k in range(10_000):
        row = {"ACCOUNT_REF": 5}
        cs.apply(row, key_int=k)
        values.append(row["ACCOUNT_REF"])
    assert len(set(values)) == 10_000 and all(0 <= v < 10**10 for v in values)
    again = {"ACCOUNT_REF": 0}
    cs.apply(again, key_int=42)
    assert again["ACCOUNT_REF"] == values[42]
    other_seed = ConstraintSet(
        [TabularConstraint(path="$.ACCOUNT_REF", unique=True)], COLS, seed=bytes(32), object_fqn="T"
    )
    row = {"ACCOUNT_REF": 0}
    other_seed.apply(row, key_int=42)
    assert row["ACCOUNT_REF"] != values[42]  # the seed keys the permutation


def test_unique_text_column_follows_the_shape() -> None:
    cs = _set({"path": "$.CARD_REF", "unique": True})
    seen = set()
    for k in range(5000):
        row: dict = {"CARD_REF": "x"}
        cs.apply(row, key_int=k)
        assert re.fullmatch(r"[A-Z]{3}-\d{8}", row["CARD_REF"])
        seen.add(row["CARD_REF"])
    assert len(seen) == 5000 and len({v[:3] for v in seen}) == 1


def test_unique_errors_are_value_free() -> None:
    with pytest.raises(ConstraintError, match="at least 6"):
        ConstraintSet(
            [TabularConstraint(path="$.CARD_REF", unique=True)],
            {"CARD_REF": ColumnSpec("CARD_REF", "categorical", shape="XX-999")},
            seed=SEED,
            object_fqn="T",
        )
    with pytest.raises(ConstraintError, match="at least 6"):
        ConstraintSet(
            [TabularConstraint(path="$.SMALL", unique=True)],
            {"SMALL": ColumnSpec("SMALL", "decimal", precision=4, scale=0)},
            seed=SEED,
            object_fqn="T",
        )
    with pytest.raises(ConstraintError, match="integer or text"):
        ConstraintSet(
            [TabularConstraint(path="$.OPENED", unique=True)], COLS, seed=SEED, object_fqn="T"
        )
    cs = _set({"path": "$.ACCOUNT_REF", "unique": True})
    with pytest.raises(ConstraintError, match="single integer key"):
        cs.apply({"ACCOUNT_REF": 1}, key_int=None)
    with pytest.raises(ConstraintError) as err:
        cs.apply({"ACCOUNT_REF": 1}, key_int=10**10 + 5)
    assert str(10**10 + 5) not in str(err.value)


def test_ranges_and_positivity() -> None:
    cs = _set(
        {"path": "$.BALANCE", "min": 0, "max": 1000},
        {"path": "$.OPENED", "min": "2020-01-01"},
        {"path": "$.TENURE", "positive": True},
    )
    row = {"BALANCE": Decimal("-5.10"), "OPENED": date(2019, 5, 1), "TENURE": 0}
    cs.apply(row, key_int=None)
    assert row == {"BALANCE": Decimal("0.00"), "OPENED": date(2020, 1, 1), "TENURE": 1}
    high = {"BALANCE": Decimal("1500.00"), "OPENED": None, "TENURE": 3}
    cs.apply(high, key_int=None)
    assert high == {"BALANCE": Decimal("1000.00"), "OPENED": None, "TENURE": 3}
    positive = _set({"path": "$.BALANCE", "positive": True})
    small = {"BALANCE": Decimal("-3.00")}
    positive.apply(small, key_int=None)
    assert small["BALANCE"] == Decimal("0.01")


def test_range_errors_name_the_rule_not_the_literal() -> None:
    with pytest.raises(ConstraintError, match="numeric or date"):
        _set({"path": "$.STATUS", "min": 0})
    with pytest.raises(ConstraintError, match=r"\$\.OPENED") as err:
        _set({"path": "$.OPENED", "min": "not-a-date-xyz"})
    assert "xyz" not in str(err.value)
    with pytest.raises(ConstraintError, match=r"\$\.TENURE") as err:
        _set(
            {
                "path": "$.TENURE",
                "when": [
                    {"if": {"path": "$.BALANCE", "op": "<", "value": 0}, "then": "zz-literal"}
                ],
            }
        )
    assert "zz-literal" not in str(err.value)


def test_dependent_rules_first_match_wins() -> None:
    cs = _set(
        {
            "path": "$.STATUS",
            "when": [
                {"if": {"path": "$.BALANCE", "op": "<", "value": 0}, "then": "OVERDRAWN"},
                {"if": {"path": "$.BALANCE", "op": ">=", "value": 100000}, "then": "PREMIUM"},
                {"if": {"path": "$.OPENED", "op": "is_null"}, "then": None},
                {"if": {"path": "$.TENURE", "op": "in", "value": [1, 2]}, "then": "NEW"},
            ],
        }
    )
    opened = date(2021, 1, 1)
    cases = [
        ({"BALANCE": Decimal("-1"), "OPENED": opened, "TENURE": 1, "STATUS": "OK"}, "OVERDRAWN"),
        ({"BALANCE": Decimal("250000"), "OPENED": None, "TENURE": 9, "STATUS": "OK"}, "PREMIUM"),
        ({"BALANCE": Decimal("10"), "OPENED": None, "TENURE": 9, "STATUS": "OK"}, None),
        ({"BALANCE": Decimal("10"), "OPENED": opened, "TENURE": 2, "STATUS": "OK"}, "NEW"),
        ({"BALANCE": Decimal("10"), "OPENED": opened, "TENURE": 9, "STATUS": "OK"}, "OK"),
    ]
    for row, expected in cases:
        cs.apply(row, key_int=None)
        assert row["STATUS"] == expected


def test_conditions_compare_dates_text_and_nulls() -> None:
    cs = _set(
        {
            "path": "$.STATUS",
            "when": [
                {"if": {"path": "$.OPENED", "op": "<", "value": "2018-01-01"}, "then": "LEGACY"},
                {"if": {"path": "$.TENURE", "op": "not_in", "value": [1, 2, 3]}, "then": "OLD"},
                {"if": {"path": "$.STATUS", "op": "==", "value": "OK"}, "then": "FINE"},
            ],
        }
    )
    rows = [
        ({"OPENED": date(2017, 6, 1), "TENURE": 1, "STATUS": "OK"}, "LEGACY"),
        ({"OPENED": date(2019, 6, 1), "TENURE": 7, "STATUS": "OK"}, "OLD"),
        ({"OPENED": date(2019, 6, 1), "TENURE": 2, "STATUS": "OK"}, "FINE"),
        ({"OPENED": None, "TENURE": None, "STATUS": "KO"}, "KO"),  # a null never compares
    ]
    for row, expected in rows:
        cs.apply(row, key_int=None)
        assert row["STATUS"] == expected


def test_object_filter_missing_columns_and_describe() -> None:
    cs = _set(
        {"path": "$.BALANCE", "min": 0, "object": "OTHER.TABLE"},
        {"path": "$.NOT_HERE", "positive": True},
        {"path": "$.TENURE", "positive": True},
        {"path": "$.STATUS", "when": [{"if": {"path": "$.GONE", "op": "is_null"}, "then": "X"}]},
        fqn="bank.accounts",
    )
    assert cs.describe() == [{"path": "$.TENURE", "rule": "positive"}]
    assert cs.skipped == [
        {"path": "$.BALANCE", "reason": "other_object"},
        {"path": "$.NOT_HERE", "reason": "no_column"},
        {"path": "$.STATUS", "reason": "no_condition_column"},
    ]
    assert not cs.needs_key_int and _set(fqn="x").describe() == []


def test_policy_validates_constraints_and_presets_still_load() -> None:
    with pytest.raises(ValueError, match="unique cannot be combined"):
        TabularConstraint(path="$.A", unique=True, min=1)
    with pytest.raises(ValueError, match="sets no rule"):
        TabularConstraint(path="$.A")
    with pytest.raises(ValueError):
        TabularConstraint(path="A", positive=True)
    cfg = SynthesizeConfig.model_validate(
        {
            "constraints": [
                {
                    "path": "$.S",
                    "when": [{"if": {"path": "$.B", "op": "<", "value": 0}, "then": "X"}],
                }
            ],
            "quasi_identifiers": ["$.GENDER"],
        }
    )
    assert cfg.constraints[0].when[0].if_.op == "<" and cfg.quasi_identifiers == ["$.GENDER"]
    assert load_preset("synthetic-realistic").synthesize.constraints == []
    with pytest.raises(ValueError):
        SynthesizeConfig.model_validate({"quasi_identifiers": ["GENDER"]})


def test_constraints_round_trip_and_change_the_policy_hash() -> None:
    rule = {"path": "$.S", "when": [{"if": {"path": "$.B", "op": "<", "value": 0}, "then": "X"}]}
    base = load_preset("synthetic-realistic")
    merged = resolve_policy(
        {"preset": "synthetic-realistic", "synthesize": {"constraints": [rule]}}, default_name="x"
    )
    dumped = merged.model_dump(mode="json")["synthesize"]["constraints"][0]
    assert dumped["when"][0]["if"] == {"path": "$.B", "op": "<", "value": 0}  # dumped by alias
    assert Policy.model_validate(merged.model_dump(mode="json")) == merged
    assert merged.config_hash() != base.config_hash()
    # empty M5 lists are left out of the hash: hashes recorded before M5 still match
    explicit = base.model_copy(deep=True)
    explicit.synthesize.quasi_identifiers = []
    assert explicit.config_hash() == base.config_hash()
