"""Revealing an L2 output row with the caller's key and the evidence reveal map (offline)."""

from __future__ import annotations

from datetime import date
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.batch import BatchTransformer, ColumnPlan, reveal_row
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.transform.crypto import generate_key_material
from dps.engine.transform.methods import MethodError
from dps.jobs_models import ObjectRef
from dps.layers.connectors.base import ColumnInfo, Row, TableShape


class RotCipher:
    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)


ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", fpe_enabled=True, ner_enabled=False),
    cipher_factory=lambda k, t: RotCipher(),
)
POLICY = load_preset("gdpr-pseudonymise")
COLS = [
    ColumnInfo(
        path="$.customer_id", name="customer_id", data_type="bigint", kind="int", is_key=True
    ),
    ColumnInfo(
        path="$.full_name", name="full_name", data_type="varchar(120)", kind="text", length=120
    ),
    ColumnInfo(path="$.email", name="email", data_type="varchar(254)", kind="text", length=254),
    ColumnInfo(path="$.mobile", name="mobile", data_type="varchar(20)", kind="text", length=20),
    ColumnInfo(path="$.dob", name="dob", data_type="date", kind="date"),
    ColumnInfo(path="$.tax_id", name="tax_id", data_type="character(14)", kind="char", length=14),
    ColumnInfo(path="$.notes", name="notes", data_type="text", kind="text"),
]
SHAPE = TableShape(
    object=ObjectRef(owner="bank", name="customers"),
    columns=COLS,
    key=["customer_id"],
    key_source="primary_key",
)
ROWS: list[Row] = [
    {
        "customer_id": 10_000_001,
        "full_name": "Ananya Krishnan",
        "email": "ananya.k@example-mail.in",
        "mobile": "+91 98765 43210",
        "dob": date(1988, 3, 14),
        "tax_id": "ABCPE123456F  ",  # char(14): the padding comes back
        "notes": "Call ann.k@example-mail.in about the loan",
    },
    {
        "customer_id": 10_000_002,
        "full_name": "Al",  # too short for FPE: det_token fallback
        "email": "a@x.in",  # a one-character local part: det_token local part
        "mobile": None,
        "dob": None,
        "tax_id": None,
        "notes": "",
    },
]
ANCHOR = ("customer_id", "CUSTOMER_ID")


def _plan(name: str, entity: str, method: str, *, text: bool = False, **params: Any) -> ColumnPlan:
    col = SHAPE.column(name)
    return ColumnPlan(
        path=col.path,
        name=name,
        entity_type=entity,
        method=MethodSpec(method=method, params=params),
        text_column=text,
        reversible=True,
        kind=col.kind,
        length=col.length,
    )


PLANS = [
    _plan("full_name", "PERSON_NAME", "fpe_text"),
    _plan("email", "EMAIL", "email", domain="keep"),
    _plan("mobile", "PHONE", "phone", keep_country_code=True),
    _plan("dob", "DATE_OF_BIRTH", "date_shift", window_days=365),
    _plan("tax_id", "PAN_IN", "fpe_text"),
    _plan("notes", "FREE_TEXT", "det_token", text=True),
]


def _reveal_map(plans: list[ColumnPlan], **overrides: Any) -> list[dict[str, Any]]:
    return [
        {
            "path": p.path,
            "entity_type": p.entity_type,
            "method": p.method.method,
            "params": p.method.params,
            "anchor_path": "$.customer_id",
            "reveal": True,
            "text_column": p.text_column,
            **overrides.get(p.name, {}),
        }
        for p in plans
    ]


def _transform(plans: list[ColumnPlan], key: Any) -> list[Row]:
    t = BatchTransformer(
        ENGINE,
        policy=POLICY,
        mode="obfuscate",
        columns=plans,
        shape=SHAPE,
        key=key,
        seed=None,
        locale="en_IN",
        anchor=ANCHOR,
    )
    return t.transform([dict(r) for r in ROWS]).rows


def test_reveal_row_restores_every_reversible_column() -> None:
    key = generate_key_material("key")
    out = _transform(PLANS, key)
    assert out[0]["full_name"] != ROWS[0]["full_name"] and out[0]["dob"] != ROWS[0]["dob"]
    assert "[[PII:EMAIL:" in out[0]["notes"]
    for original, row in zip(ROWS, out, strict=True):
        restored = reveal_row(
            row,
            reveal_map=_reveal_map(PLANS),
            shape=SHAPE,
            key=key,
            engine=ENGINE,
            policy=POLICY,
            anchor=ANCHOR,
        )
        assert restored == original


def test_a_reversibly_transformed_anchor_is_revealed_first() -> None:
    key = generate_key_material("key")
    plans = [_plan("customer_id", "CUSTOMER_ID", "fpe_digits"), *PLANS]
    out = _transform(plans, key)
    assert out[0]["customer_id"] != ROWS[0]["customer_id"]
    restored = reveal_row(
        out[0],
        reveal_map=_reveal_map(plans),
        shape=SHAPE,
        key=key,
        engine=ENGINE,
        policy=POLICY,
        anchor=ANCHOR,
    )
    assert restored == ROWS[0]


def test_entries_marked_not_revealable_are_left_as_they_are() -> None:
    key = generate_key_material("key")
    out = _transform(PLANS, key)
    partial = reveal_row(
        out[0],
        reveal_map=_reveal_map(PLANS, dob={"reveal": False}),
        shape=SHAPE,
        key=key,
        engine=ENGINE,
        policy=POLICY,
        anchor=ANCHOR,
    )
    assert partial["dob"] == out[0]["dob"] and partial["full_name"] == ROWS[0]["full_name"]
    assert (
        reveal_row(
            out[0], reveal_map=[], shape=SHAPE, key=key, engine=ENGINE, policy=POLICY, anchor=ANCHOR
        )
        == out[0]
    )


def test_a_wrong_key_reveals_nothing() -> None:
    out = _transform(PLANS, generate_key_material("key"))
    with pytest.raises(MethodError) as err:
        reveal_row(
            out[0],
            reveal_map=_reveal_map(PLANS),
            shape=SHAPE,
            key=generate_key_material("key"),
            engine=ENGINE,
            policy=POLICY,
            anchor=ANCHOR,
        )
    assert "Ananya" not in str(err.value)


def test_another_key_is_refused_by_the_pack_key_id() -> None:
    """FPE tokens decrypt to well-formed values under any key (and map back under it), so the
    pack's key id is what refuses another key for rows without an authenticated column."""
    plans = [p for p in PLANS if p.method.method in ("fpe_digits", "fpe_text", "date_shift")]
    right = generate_key_material("key")
    out = _transform(plans, right)
    wrong = generate_key_material("key")
    with pytest.raises(MethodError) as err:
        reveal_row(
            out[0],
            reveal_map=_reveal_map(plans),
            shape=SHAPE,
            key=wrong,
            engine=ENGINE,
            policy=POLICY,
            anchor=ANCHOR,
            kid=right.kid,
        )
    assert "key id" in str(err.value) and "Ananya" not in str(err.value)
    same = reveal_row(
        out[0],
        reveal_map=_reveal_map(plans),
        shape=SHAPE,
        key=right,
        engine=ENGINE,
        policy=POLICY,
        anchor=ANCHOR,
        kid=right.kid,
    )
    assert same["full_name"] == ROWS[0]["full_name"]
