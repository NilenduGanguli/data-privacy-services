"""BatchTransformer on document rows (M3 Task 13, offline): nested and ``[*]`` plan paths, values
outside the plan untouched, unclassified leaves (``detect`` / ``keep`` / ``redact``)."""

from __future__ import annotations

import copy
import datetime as dt
from decimal import Decimal
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.batch import BatchTransformer, ColumnPlan, IncompatibleMethod, LeakageError
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.transform.crypto import generate_key_material
from dps.jobs_models import ObjectRef
from dps.layers.connectors.base import ColumnInfo, TableShape

ENGINE = PrivacyEngine(Settings(_env_file=None, llm_provider="stub", ner_enabled=False))
KEY = generate_key_material("key")


def _col(path: str, name: str, kind: Any, data_type: str = "string") -> ColumnInfo:
    return ColumnInfo(path=path, name=name, data_type=data_type, kind=kind)


SHAPE = TableShape(
    object=ObjectRef(name="profiles"),
    columns=[
        _col("$._id", "_id", "int", "int"),
        _col("$.customerId", "customerId", "text"),
        _col("$.profile.fullName", "profile", "text"),
        _col("$.profile.dob", "profile", "timestamp", "date"),
        _col("$.accounts[*].iban", "accounts", "text"),
        _col("$.accounts[*].balance", "accounts", "decimal", "decimal"),
        _col("$.notes", "notes", "text"),
        _col("$.tags[*]", "tags", "text"),
    ],
    key=["_id"],
    key_source="primary_key",
)


def _doc(i: int, **extra: Any) -> dict[str, Any]:
    return {
        "_id": i,
        "customerId": f"CUST-{10_000_000 + i}",
        "profile": {"fullName": f"Ananya Krishnan {i:03d}", "dob": dt.datetime(1980, 1, 1)},
        "accounts": [
            {"iban": f"GB00TEST{i:06d}{j:04d}", "balance": Decimal(f"{j}.50")} for j in range(3)
        ],
        "notes": "kept note",
        "tags": ["salary"],
        **extra,
    }


def _plan(path: str, entity: str, method: str = "det_token", **kw: Any) -> ColumnPlan:
    col = next(c for c in SHAPE.columns if c.path == path)
    return ColumnPlan(
        path=path,
        name=col.name,
        entity_type=entity,
        method=MethodSpec(method=method),
        text_column=kw.pop("text", False),
        reversible=True,
        kind=col.kind,
        length=None,
        **kw,
    )


def _run(plans: list[ColumnPlan], rows: list[dict[str, Any]], **kw: Any) -> Any:
    return BatchTransformer(
        ENGINE,
        policy=load_preset("gdpr-pseudonymise"),
        mode="obfuscate",
        columns=plans,
        shape=SHAPE,
        key=KEY,
        seed=None,
        locale="en_IN",
        **kw,
    ).transform(rows)


def test_a_nested_path_changes_only_its_leaf() -> None:
    rows = [_doc(i) for i in range(4)]
    before = copy.deepcopy(rows)
    result = _run([_plan("$.profile.fullName", "PERSON_NAME")], rows, unclassified="keep")
    assert rows == before  # the input is not mutated
    for old, new in zip(rows, result.rows, strict=True):
        assert new["profile"]["fullName"] != old["profile"]["fullName"]
        assert new["profile"]["dob"] is old["profile"]["dob"]  # untouched leaves by reference
        assert new["accounts"] is old["accounts"]  # untouched sub-documents are not copied
        assert {k: v for k, v in new.items() if k != "profile"} == {
            k: v for k, v in old.items() if k != "profile"
        }
    assert result.values_by_column == {"$.profile.fullName": 4}


def test_a_wildcard_path_transforms_every_element() -> None:
    rows = [
        _doc(1),
        {**_doc(2), "accounts": []},
        {k: v for k, v in _doc(3).items() if k != "accounts"},
    ]
    result = _run([_plan("$.accounts[*].iban", "IBAN")], rows, unclassified="keep")
    first = result.rows[0]["accounts"]
    assert len(first) == 3 and all(
        a["iban"] != b["iban"] for a, b in zip(first, rows[0]["accounts"], strict=True)
    )
    assert [a["balance"] for a in first] == [a["balance"] for a in rows[0]["accounts"]]
    assert result.rows[1]["accounts"] == [] and "accounts" not in result.rows[2]
    assert result.values_by_method == {"det_token": 3}
    assert len({a["iban"] for a in first}) == 3  # each element its own token


def test_a_flat_document_field_is_a_column() -> None:
    result = _run([_plan("$.customerId", "CUSTOMER_ID")], [_doc(1)], unclassified="keep")
    assert result.rows[0]["customerId"] != "CUST-10000001"
    assert result.rows[0]["profile"] == _doc(1)["profile"]


def test_unclassified_leaves_detect_keep_redact() -> None:
    extra = {"extra": {"note": "write to sentinel.x@example.com today", "n": 7}}
    rows = [_doc(1, **extra), _doc(2)]
    detected = _run([], rows, unclassified="detect")
    note = detected.rows[0]["extra"]["note"]
    assert "sentinel.x@example.com" not in note and note.startswith("write to ")
    assert detected.rows[0]["extra"]["n"] == 7 and detected.rows[1] == rows[1]
    assert detected.rows[0]["notes"] == "kept note"  # a shape path: reviewed, never re-detected
    assert detected.values_by_column == {"$.extra.note": 1}
    kept = _run([], rows, unclassified="keep")
    assert kept.rows == rows
    redacted = _run([], rows, unclassified="redact")
    assert redacted.rows[0]["extra"] == {"note": "[REDACTED]", "n": "[REDACTED]"}
    assert redacted.rows[1] == rows[1]


def test_an_unknown_array_element_field_is_unclassified() -> None:
    row = _doc(1)
    row["accounts"][0]["holder"] = "mail ananya.k@example.com"
    out = _run([], [row], unclassified="detect").rows[0]
    assert "ananya.k@example.com" not in out["accounts"][0]["holder"]
    assert out["accounts"][0]["iban"] == row["accounts"][0]["iban"]  # in the shape: kept


def test_plans_must_name_shape_paths() -> None:
    bad = ColumnPlan(
        path="$.profile.nickname",
        name="profile",
        entity_type="PERSON_NAME",
        method=MethodSpec(method="det_token"),
        text_column=False,
        reversible=True,
        kind="text",
        length=None,
    )
    with pytest.raises(ValueError):
        _run([bad], [_doc(1)])


def test_leakage_in_documents_fails_closed() -> None:
    row = _doc(1, tags=["met Ananya Krishnan 001 at the branch"])
    plans = [
        _plan("$.profile.fullName", "PERSON_NAME"),
        _plan("$.tags[*]", "FREE_TEXT", text=True),  # no detector finds the name (NER off)
    ]
    with pytest.raises(LeakageError):  # the name survives in an array element: fail closed
        _run(plans, [row], unclassified="keep")
    kept_copy = _run(plans[:1], [row], unclassified="keep")  # kept leaves are not searched
    assert kept_copy.verification.passed


def test_a_timestamp9_date_shift_keeps_its_nanoseconds() -> None:
    """Oracle TIMESTAMP(7-9) values travel as FF9 text; the plan's ``ts_precision`` keeps them so."""
    shape = TableShape(
        object=ObjectRef(owner="CRM", name="EVENTS"),
        columns=[
            ColumnInfo(path="$.ID", name="ID", data_type="NUMBER(12)", kind="int"),
            ColumnInfo(
                path="$.AT", name="AT", data_type="TIMESTAMP(9)", kind="timestamp", ts_precision=9
            ),
        ],
        key=["ID"],
        key_source="primary_key",
    )

    def plan(ts_precision: int | None) -> ColumnPlan:
        return ColumnPlan(
            path="$.AT",
            name="AT",
            entity_type="DATE_TIME",
            method=MethodSpec(method="date_shift", window_days=30),
            text_column=False,
            reversible=True,
            kind="timestamp",
            length=None,
            ts_precision=ts_precision,
        )

    def run(p: ColumnPlan) -> Any:
        return BatchTransformer(
            ENGINE,
            policy=load_preset("gdpr-pseudonymise"),
            mode="obfuscate",
            columns=[p],
            shape=shape,
            key=KEY,
            seed=None,
            locale="en_IN",
        ).transform([{"ID": 1, "AT": "2025-02-01T10:00:00.123456789"}])

    out = run(plan(9)).rows[0]["AT"]
    assert out != "2025-02-01T10:00:00.123456789" and out.endswith("T10:00:00.123456789")
    with pytest.raises(IncompatibleMethod):  # without it, the nanoseconds would be lost
        run(plan(None))


def test_leaf_restore_sees_only_written_leaves_and_text_leaves_stay_text() -> None:
    """Review G3 r1 B1: the restore hook runs for the leaves a plan wrote, with their originals;
    an out-of-sample string in a numeric field keeps the engine's text (no number, no lost zero)."""
    calls: list[tuple[Any, Any]] = []

    def restore(original: Any, value: Any) -> Any:
        calls.append((original, value))
        return value

    shape = TableShape(
        object=ObjectRef(name="c"),
        columns=[
            _col("$._id", "_id", "int", "int"),
            _col("$.acct", "acct", "int", "long"),
            _col("$.score", "score", "int", "long"),
        ],
        key=["_id"],
        key_source="primary_key",
    )
    plan = ColumnPlan(
        path="$.acct",
        name="acct",
        entity_type="ACCOUNT_NUMBER",
        method=MethodSpec(method="det_token"),
        text_column=False,
        reversible=True,
        kind="text",
        length=None,
    )
    rows = [{"_id": 1, "acct": 12345678, "score": 7}, {"_id": 2, "acct": "00412345", "score": 8}]
    out = BatchTransformer(
        ENGINE,
        policy=load_preset("gdpr-pseudonymise"),
        mode="obfuscate",
        columns=[plan],
        shape=shape,
        key=KEY,
        seed=None,
        locale="en_IN",
        unclassified="keep",
        leaf_restore=restore,
    ).transform(rows)
    assert [original for original, _ in calls] == [12345678]  # the text leaf is not converted
    first, second = out.rows
    assert calls[0][1] == first["acct"] != 12345678  # the hook's result is what is written
    assert first["score"] == 7  # untouched: no hook call
    assert isinstance(second["acct"], str) and second["acct"] != "00412345"
    assert second["score"] == 8


def test_a_scalar_sampled_field_holding_documents_is_searched() -> None:
    """Review G3 r3 B1: ``$.notes`` was sampled as text; a drift document holding a
    sub-document or an array there must not pass its nested values in clear."""
    rows = [
        _doc(
            1, notes={"text": "mail sentinel.a@example.com now", "iban": "GB82WEST12345698765432"}
        ),
        _doc(2, notes=["mail sentinel.b@example.com now"]),
        _doc(3),
    ]
    for mode in ("detect", "redact"):
        out = _run([], rows, unclassified=mode).rows
        text = repr(out)
        assert "sentinel.a@example.com" not in text and "sentinel.b@example.com" not in text, mode
        assert out[2] == rows[2], mode  # a scalar under the reviewed name stays the review's
    redacted = _run([], rows, unclassified="redact").rows
    assert "GB82WEST12345698765432" not in repr(redacted)


def test_a_planned_field_name_with_brackets_is_transformed() -> None:
    """Review G3 r3 B2: plans on ``$['iban[1]']`` and ``$.acct['no[2]']`` reach their values."""
    from dps.layers.connectors.mongo_docs import infer_shape

    rows = [
        {"_id": i, "iban[1]": f"GB82WEST1234569876{i:04d}", "acct": {"no[2]": f"ACCT-{i:06d}"}}
        for i in range(20)
    ]
    shape = infer_shape(ObjectRef(name="forms"), rows, row_estimate=20)  # as discovery sees it
    assert [c.path for c in shape.columns] == ["$._id", "$.acct['no[2]']", "$['iban[1]']"]
    plans = [
        ColumnPlan(
            path=c.path, name=c.name, entity_type="IBAN", method=MethodSpec(method="det_token"),
            text_column=False, reversible=True, kind="text", length=None,
        )
        for c in shape.columns[1:]
    ]  # fmt: skip
    out = BatchTransformer(
        ENGINE, policy=load_preset("gdpr-pseudonymise"), mode="obfuscate", columns=plans,
        shape=shape, key=KEY, seed=None, locale="en_IN", unclassified="detect",
    ).transform(rows)  # fmt: skip
    unchanged = sum(
        1
        for old, new in zip(rows, out.rows, strict=True)
        for a, b in ((old["iban[1]"], new["iban[1]"]), (old["acct"]["no[2]"], new["acct"]["no[2]"]))
        if a == b
    )
    assert unchanged == 0 and out.values_by_column == {"iban[1]": 20, "$.acct['no[2]']": 20}
