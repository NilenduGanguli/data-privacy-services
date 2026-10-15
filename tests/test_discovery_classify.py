"""Discovery classification and method suggestion (offline, contract §5.4)."""

from __future__ import annotations

from typing import Any

import pytest

from dps.config import Settings
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.jobs_models import ColumnClassification, ObjectRef, SnapshotOverride
from dps.layers.connectors.base import ColumnInfo, TableShape
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


def test_bounded_columns_never_get_a_method_whose_output_cannot_fit() -> None:
    """M2 verify r2 issue 2: discovery suggested det_token for national_id varchar(18), whose
    worst case is 141 characters, so every In place run on the restored copy failed."""
    national = c("national_id", "text", data_type="character varying(18)", length=18)
    mixed = ["123456789012", "123-45-6789", "ABCD123456EFGHIJ12"]
    obf, _, note = suggest_methods(
        "NATIONAL_ID",
        national,
        policy=POLICY,
        fpe_enabled=True,
        min_digits=8,
        text_column=False,
        values=mixed,
    )
    # letters in the values: no reversible length-preserving method fits all of them, so the
    # column is a decision with the reason, not a silent overflow
    assert obf.method == "det_token" and note == "length_overflow"
    digits = ["123456789012", "123-45-6789", "987654321098"]
    kept, _, knote = suggest_methods(
        "NATIONAL_ID",
        national,
        policy=POLICY,
        fpe_enabled=True,
        min_digits=9,
        text_column=False,
        values=digits,
    )
    assert kept == MethodSpec(method="fpe_digits") and knote == "length_kept"
    no_fpe, _, fnote = suggest_methods(
        "NATIONAL_ID",
        national,
        policy=POLICY,
        fpe_enabled=False,
        min_digits=9,
        text_column=False,
        values=digits,
    )
    assert no_fpe.method == "det_token" and fnote == "length_overflow"
    # an unbounded column takes a token as before
    free, _, unote = suggest_methods(
        "NATIONAL_ID",
        c("national_id", "text"),
        policy=POLICY,
        fpe_enabled=True,
        min_digits=8,
        text_column=False,
        values=mixed,
    )
    assert free.method == "det_token" and unote is None


def test_fpe_values_that_fall_back_to_tokens_are_checked_against_the_column() -> None:
    pan = c("tax_id", "char", data_type="character(10)", length=10)
    obf, _, note = suggest_methods(
        "PAN_IN",
        pan,
        policy=POLICY,
        fpe_enabled=True,
        min_digits=4,
        text_column=False,
        values=["ABCDE1234F", "PQRST6789Z"],  # four digits: fpe_text runs as det_token
    )
    assert obf.method == "fpe_text" and note == "length_overflow"
    name = c("full_name", "text", data_type="character varying(120)", length=120)
    fits, _, nnote = suggest_methods(
        "PERSON_NAME",
        name,
        policy=POLICY,
        fpe_enabled=True,
        min_digits=None,
        text_column=False,
        values=["Ananya Krishnan", "Li Wu"],  # "Li Wu" falls back, and its token still fits
    )
    assert fits.method == "fpe_text" and nnote is None
    ssn = c("ssn", "text", data_type="character varying(11)", length=11)
    kept_suffix, _, snote = suggest_methods(
        "SSN",
        ssn,
        policy=POLICY,
        fpe_enabled=True,
        min_digits=9,
        text_column=False,
        values=["900-45-1000"],  # keep_suffix 4 leaves five digits: a token, 44 characters
    )
    # every digit encrypted instead (no kept suffix): reversible and the length kept
    assert kept_suffix == MethodSpec(method="fpe_digits") and snote == "length_kept"
    letters, _, lnote = suggest_methods(
        "SSN",
        ssn,
        policy=POLICY,
        fpe_enabled=True,
        min_digits=9,
        text_column=False,
        values=["900-45-1000", "AB-45-1000"],
    )
    assert letters.method == "fpe_digits" and lnote == "length_overflow"


def test_classify_columns_passes_the_sampled_values_to_the_length_check() -> None:
    from dps.jobs_models import ObjectRef
    from dps.layers.connectors.base import TableShape

    shape = TableShape(
        object=ObjectRef(owner="bank", name="people"),
        key=["id"],
        key_source="primary_key",
        columns=[
            c("id", "int", is_key=True, nullable=False),
            c("national_id", "text", data_type="character varying(18)", length=18),
        ],
    )
    rows = [{"id": i, "national_id": f"ABCD{100000 + i}EFGHIJ{i % 90 + 10}"} for i in range(40)]
    out = by_name(
        classify_columns(
            shape, rows, policy=POLICY, detector=ENGINE.detector(POLICY, "en_IN"), fpe_enabled=False
        )
    )
    assert out["national_id"].entity_type == "NATIONAL_ID"
    assert out["national_id"].type_note == "length_overflow"


@pytest.mark.parametrize("preset", ["synthetic-realistic", "synthetic-strict"])
def test_name_part_columns_are_names_with_ner_off(preset: str) -> None:
    # review r1 B2: a column that holds a part of a name is a PERSON_NAME on its key alone (the
    # default image has no NER); classified as nothing, a synthesize job kept the real names
    parts = (
        "given_names",
        "forenames",
        "first_names",
        "middle_names",
        "f_name",
        "l_name",
        "surName",
        # review r1 B3, all classified keep before: plural surnames and NAMEMIDDLE under both
        # presets, SAP's NAME_FIRST order and Siebel's short forms under synthetic-realistic
        # (where "name" alone, 0.55, is below the floor)
        "surnames",
        "last_names",
        "family_names",
        "NAME_FIRST",
        "NAME_MIDDLE",
        "NAME_LAST",
        "NAMEMIDDLE",
        "FST_NAME",
        "MID_NAME",
    )
    shape = TableShape(
        object=ObjectRef(owner="bank", name="kyc"),
        columns=[
            c("customer_id", "int", data_type="bigint", is_key=True, nullable=False),
            *(c(n, "text", data_type="varchar(80)", length=80) for n in parts),
        ],
        key=["customer_id"],
        key_source="primary_key",
    )
    rows = [
        {"customer_id": 1000 + i, **dict.fromkeys(parts, ("Kavya Devi", "Rohan")[i % 2])}
        for i in range(20)
    ]
    policy = load_preset(preset)
    cols = by_name(
        classify_columns(
            shape, rows, policy=policy, detector=ENGINE.detector(policy, "en_IN"), fpe_enabled=False
        )
    )
    for name in parts:
        col = cols[name]
        assert col.entity_type == "PERSON_NAME", name
        assert col.synthesize_method is not None and col.synthesize_method.method != "keep", name
