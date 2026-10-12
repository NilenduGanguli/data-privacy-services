"""Discovery classification and method suggestion (offline, contract §5.4)."""

from __future__ import annotations

from typing import Any

import pytest

from dps.config import Settings
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.jobs_models import ColumnClassification, SnapshotOverride
from dps.layers.connectors.base import ColumnInfo
from dps.layers.connectors.discovery import (
    JOIN_ENTITIES,
    classify_columns,
    suggest_methods,
    value_shape,
)
from dps.snapshots import SnapshotStateError, apply_overrides, content_hash
from tests.connectors.factories import classified_columns, customers_rows, customers_shape

ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)
)
POLICY = load_preset("gdpr-pseudonymise")
SHAPE = customers_shape()
ROWS = customers_rows()


def c(name: str, kind: str, **kw: Any) -> ColumnInfo:
    return ColumnInfo(
        path=f"$.{name}", name=name, data_type=kw.pop("data_type", kind), kind=kind, **kw
    )


def by_name(cols: list[ColumnClassification]) -> dict[str, ColumnClassification]:
    return {col.name: col for col in cols}


def test_columns_are_classified_with_shapes_not_values() -> None:
    cols = by_name(
        classify_columns(
            SHAPE, ROWS, policy=POLICY, detector=ENGINE.detector(POLICY, "en_IN"), fpe_enabled=False
        )
    )
    assert (
        cols["full_name"].entity_type == "PERSON_NAME"
        and "structural" in cols["full_name"].detectors
    )
    assert cols["email"].entity_type == "EMAIL" and cols["email"].value_hit_rate > 0.9
    assert cols["dob"].entity_type == "DATE_OF_BIRTH" and cols["dob"].method.method == "date_shift"
    assert cols["notes"].text_column and cols["notes"].entity_type == "FREE_TEXT"
    assert cols["notes"].method.method == "det_token"
    assert cols["notes"].synthesize_method is not None
    assert cols["notes"].synthesize_method.method == "surrogate"
    assert cols["segment"].entity_type is None and cols["segment"].method.method == "keep"
    assert cols["customer_id"].anchor and sum(col.anchor for col in cols.values()) == 1
    assert cols["photo"].method.method == "keep"
    assert all(
        "Ananya" not in s and "example-mail" not in s for col in cols.values() for s in col.shapes
    )
    assert cols["email"].null_rate == 0.0 and cols["full_name"].distinct_ratio == 1.0
    assert (
        cols["full_name"].suggested == cols["full_name"].method and not cols["full_name"].override
    )
    assert [col.name for col in cols.values()] == [col.name for col in SHAPE.columns]


def test_empty_samples_and_nulls() -> None:
    cols = by_name(
        classify_columns(
            SHAPE, [], policy=POLICY, detector=ENGINE.detector(POLICY, "en_IN"), fpe_enabled=False
        )
    )
    assert cols["email"].null_rate == 0.0 and cols["email"].shapes == []
    rows = [{**r, "email": None} for r in ROWS[:10]]
    nulls = by_name(
        classify_columns(
            SHAPE, rows, policy=POLICY, detector=ENGINE.detector(POLICY, "en_IN"), fpe_enabled=False
        )
    )
    assert nulls["email"].null_rate == 1.0 and nulls["email"].value_hit_rate == 0.0


def test_numeric_and_key_rules() -> None:
    key = c("customer_id", "int", is_key=True, nullable=False)
    obf, syn, note = suggest_methods(
        "CUSTOMER_ID", key, policy=POLICY, fpe_enabled=True, min_digits=8, text_column=False
    )
    assert obf.method in ("fpe_digits", "det_token", "keep") and syn.method == "keep"
    assert note in (None, "synthesize_key_kept", "numeric_key_kept")
    small, _, note2 = suggest_methods(
        "CUSTOMER_ID", key, policy=POLICY, fpe_enabled=True, min_digits=4, text_column=False
    )
    assert small.method == "keep" and note2 in ("fpe_domain_too_small", "numeric_key_kept")
    dob, _, _ = suggest_methods(
        "DATE_OF_BIRTH",
        c("dob", "date"),
        policy=POLICY,
        fpe_enabled=False,
        min_digits=8,
        text_column=False,
    )
    assert dob.method == "date_shift"
    assert "CUSTOMER_ID" in JOIN_ENTITIES


def test_type_compatibility_rules() -> None:
    amount = c("salary", "decimal", nullable=True)
    obf, syn, note = suggest_methods(
        "PERSON_NAME", amount, policy=POLICY, fpe_enabled=True, min_digits=None, text_column=False
    )
    assert obf.method == "null" and note == "numeric_column" and syn.method in ("null", "keep")
    flag = c("verified", "bool")
    assert (
        suggest_methods(
            "EMAIL", flag, policy=POLICY, fpe_enabled=True, min_digits=None, text_column=False
        )[0].method
        == "keep"
    )
    date_col = c("opened", "date")
    shifted, _, dnote = suggest_methods(
        "EMAIL", date_col, policy=POLICY, fpe_enabled=True, min_digits=None, text_column=False
    )
    assert shifted.method == "date_shift" and dnote == "date_column"
    blob = c("scan", "binary")
    assert suggest_methods(
        "PASSPORT", blob, policy=POLICY, fpe_enabled=True, min_digits=None, text_column=False
    ) == (
        MethodSpec(method="keep"),
        MethodSpec(method="keep"),
        "binary_kept",
    )
    nothing = suggest_methods(
        None, c("x", "text"), policy=POLICY, fpe_enabled=True, min_digits=None, text_column=False
    )
    assert nothing == (MethodSpec(method="keep"), MethodSpec(method="keep"), None)
    no_fpe, _, fnote = suggest_methods(
        "PERSON_NAME",
        c("n", "text"),
        policy=POLICY,
        fpe_enabled=False,
        min_digits=None,
        text_column=False,
    )
    assert no_fpe.method == "det_token" and fnote == "fpe_disabled"


def test_value_shape() -> None:
    assert value_shape("Ananya K-12") == "Xxxxxx X-99"
    assert value_shape("4111 1111 1111 1111") == "9999 9999 9999 9999"
    assert value_shape("x" * 40) == "x" * 24 + "…"


def _cols() -> list[ColumnClassification]:
    return classified_columns()


def test_overrides_apply_to_suggestions() -> None:
    base = _cols()
    out = by_name(
        apply_overrides(
            base,
            [
                SnapshotOverride(path="$.email", method=MethodSpec(method="det_token")),
                SnapshotOverride(path="$.full_name", anchor=True),
                SnapshotOverride(path="$.segment", entity_type="CUSTOMER_ID"),
                SnapshotOverride(path="$.ssn", entity_type=None),
                SnapshotOverride(path="$.risk_score", text_column=False),
            ],
            policy=POLICY,
            fpe_enabled=False,
            shape=SHAPE,
        )
    )
    assert out["email"].method.method == "det_token" and out["email"].override
    assert out["email"].suggested.method != "det_token"  # the suggestion is kept for review
    assert [n for n, col in out.items() if col.anchor] == ["full_name"]
    assert (
        out["segment"].entity_type == "CUSTOMER_ID" and out["segment"].method.method == "det_token"
    )
    assert (
        out["ssn"].entity_type is None
        and out["ssn"].method.method == "keep"
        and out["ssn"].override
    )
    assert (
        not out["customer_id"].override
        and out["customer_id"].method == out["customer_id"].suggested
    )
    assert content_hash(list(out.values()), []) != content_hash(base, [])


def test_override_errors() -> None:
    with pytest.raises(SnapshotStateError) as err:
        apply_overrides(
            _cols(), [SnapshotOverride(path="$.nope")], policy=POLICY, fpe_enabled=False
        )
    assert (err.value.code, err.value.status) == ("override_unknown_path", 422)
    with pytest.raises(SnapshotStateError) as err2:
        apply_overrides(
            _cols(),
            [SnapshotOverride(path="$.segment", entity_type="EMAIL")],
            policy=None,
            fpe_enabled=False,
        )
    assert (err2.value.code, err2.value.status) == ("override_method_required", 422)
    ok = apply_overrides(
        _cols(),
        [
            SnapshotOverride(
                path="$.segment", entity_type="EMAIL", method=MethodSpec(method="redact")
            )
        ],
        policy=None,
        fpe_enabled=False,
    )
    seg = by_name(ok)["segment"]
    assert seg.entity_type == "EMAIL" and seg.method.method == "redact"
    assert seg.synthesize_method is not None and seg.synthesize_method.method == "surrogate"


def test_free_text_overrides() -> None:
    out = by_name(
        apply_overrides(
            _cols(),
            [
                SnapshotOverride(path="$.segment", text_column=True),
                SnapshotOverride(path="$.notes", text_column=False),
            ],
            policy=POLICY,
            fpe_enabled=False,
        )
    )
    assert out["segment"].text_column and out["segment"].entity_type == "FREE_TEXT"
    assert out["segment"].method.method == "det_token"
    assert not out["notes"].text_column and out["notes"].entity_type is None
    assert out["notes"].method.method == "keep"


def test_content_hash_is_canonical() -> None:
    cols = _cols()
    assert content_hash(cols, []) == content_hash([c.model_copy() for c in cols], [])
    assert len(content_hash(cols, [])) == 64
