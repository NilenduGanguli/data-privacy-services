"""D-A (programme plan §3): synthetic tables never carry linkable real identifiers.

A sensitive key or join identifier in a synthesize job is replaced by a **keyed pseudonym**: a
permutation keyed by the job (group) synthesis seed and the entity type, so the same customer id
maps to the same pseudonym in every table of the group (joins and foreign keys hold), different
ids never collide (keys stay unique), the value keeps its column's shape, and no value ever maps
to itself.
"""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.batch import BatchTransformer, ColumnPlan, PseudonymColumn
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.synth.keys import KeyPseudonyms, PseudonymError, feistel_invert, feistel_permute
from dps.engine.synth.surrogates import SynthesisSeed, generate_synthesis_seed, seed_bytes
from dps.engine.transform.crypto import generate_key_material
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape

SEED = b"\x07" * 32


def test_the_feistel_permutation_is_a_keyed_bijection_and_inverts() -> None:
    keys = [bytes([i]) * 32 for i in range(4)]
    for domain in (2, 7, 1000, 10**9, 26**4 * 10**6, 2**300 + 17):
        xs = range(min(domain, 400)) if domain < 10**4 else [0, 1, 5, domain // 2, domain - 1]
        for x in xs:
            y = feistel_permute(x, domain, keys)
            assert 0 <= y < domain and feistel_invert(y, domain, keys) == x
    assert sorted(feistel_permute(x, 1000, keys) for x in range(1000)) == list(range(1000))


def test_integer_pseudonyms_are_injective_fixed_point_free_and_in_tier() -> None:
    p = KeyPseudonyms(SEED)
    sources = list(range(10_000_001, 10_005_001)) + list(range(0, 2000))
    out = [p.integer("CUSTOMER_ID", v) for v in sources]
    assert len(set(out)) == len(sources)  # unique keys stay unique
    assert all(a != b for a, b in zip(sources, out, strict=True))  # never the value itself
    assert all(0 <= o < 10**9 for o in out)  # an int4 column still holds them
    assert not set(out) & set(sources)  # no pseudonym is another source key here
    assert out == [KeyPseudonyms(SEED).integer("CUSTOMER_ID", v) for v in sources]
    assert p.integer("EMPLOYEE_ID", 10_000_001) != out[0]  # entity-scoped
    assert KeyPseudonyms(b"\x08" * 32).integer("CUSTOMER_ID", 10_000_001) != out[0]
    big = 4_123_456_789_012
    assert len(str(p.integer("CUSTOMER_ID", big))) == len(str(big))  # 13 digits stay 13
    assert p.integer("CUSTOMER_ID", -42) < 0 and p.integer("CUSTOMER_ID", -42) != -42
    with pytest.raises(PseudonymError):
        p.integer("CUSTOMER_ID", 10**18)


def test_text_pseudonyms_keep_the_shape_and_never_collide() -> None:
    p = KeyPseudonyms(SEED)
    sources = [f"CUST-{i:06d}" for i in range(3000)]
    out = [p.text("CUSTOMER_ID", s) for s in sources]
    assert len(set(out)) == len(out) and not set(out) & set(sources)
    for s, o in zip(sources, out, strict=True):
        assert len(o) == len(s) and o[4] == "-"
        assert o[:4].isupper() and o[:4].isalpha() and o[5:].isdigit()
    mixed = "ab-12 Xy"
    shaped = p.text("ACCOUNT_NUMBER", mixed)
    assert shaped != mixed and len(shaped) == len(mixed) and shaped[2] == "-" and shaped[5] == " "
    assert shaped[0].islower() and shaped[3].isdigit() and shaped[6].isupper()
    assert p.text("ACCOUNT_NUMBER", mixed) == shaped
    with pytest.raises(PseudonymError):
        p.text("CUSTOMER_ID", "--")  # nothing to permute


def test_uuid_pseudonyms() -> None:
    p = KeyPseudonyms(SEED)
    u = "3f2b8c1e-9d4a-4e7b-8f0a-1c2d3e4f5a6b"
    out = p.text("CUSTOMER_ID", u, kind="uuid")
    assert out != u and str(uuid.UUID(out)) == out and p.text("CUSTOMER_ID", u, kind="uuid") == out


SHAPE = TableShape(
    object=ObjectRef(owner="bank", name="accounts"),
    key=["account_id"],
    key_source="primary_key",
    columns=[
        ColumnInfo(
            path="$.account_id", name="account_id", data_type="bigint", kind="int", is_key=True
        ),
        ColumnInfo(path="$.customer_id", name="customer_id", data_type="bigint", kind="int"),
        ColumnInfo(path="$.holder", name="holder", data_type="text", kind="text"),
        ColumnInfo(path="$.branch", name="branch", data_type="text", kind="text"),
    ],
)
PSEUDONYMS = [
    PseudonymColumn(
        path="$.account_id", name="account_id", entity_type="ACCOUNT_NUMBER", kind="int", digits=18
    ),
    PseudonymColumn(
        path="$.customer_id", name="customer_id", entity_type="CUSTOMER_ID", kind="int", digits=18
    ),
]
HOLDER = ColumnPlan(
    path="$.holder",
    name="holder",
    entity_type="PERSON_NAME",
    method=MethodSpec(method="surrogate"),
    text_column=False,
    reversible=False,
    kind="text",
    length=None,
)


def _engine() -> PrivacyEngine:
    return PrivacyEngine(
        Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)
    )


def _transformer(seed: SynthesisSeed, **kw: Any) -> BatchTransformer:
    return BatchTransformer(
        _engine(),
        policy=load_preset("synthetic-realistic"),
        mode=kw.pop("mode", "synthesize"),
        columns=kw.pop("columns", [HOLDER]),
        shape=SHAPE,
        key=kw.pop("key", None),
        seed=seed,
        locale="en_US",
        pseudonyms=kw.pop("pseudonyms", PSEUDONYMS),
        **kw,
    )


def test_the_batch_transformer_pseudonymises_keys_consistently_across_tables() -> None:
    seed = generate_synthesis_seed()
    rows = [
        {
            "account_id": 900_000 + i,
            "customer_id": 10_000_001 + i % 7,
            "holder": "Ann Lee",
            "branch": "B1",
        }
        for i in range(50)
    ]
    result = _transformer(seed).transform([dict(r) for r in rows])
    out = result.rows
    sources = {r["account_id"] for r in rows} | {r["customer_id"] for r in rows}
    assert not {r["account_id"] for r in out} & sources
    assert not {r["customer_id"] for r in out} & sources
    assert len({r["account_id"] for r in out}) == 50  # still a key
    assert len({r["customer_id"] for r in out}) == 7  # the same customer, the same pseudonym
    assert all(r["branch"] == "B1" for r in out)  # non-personal, kept
    assert result.values_by_method["key_pseudonym"] == 100
    assert result.values_by_column["customer_id"] == 50
    # another table of the same job group: the same customer ids map to the same pseudonyms
    keys = KeyPseudonyms(seed_bytes(seed))
    assert {r["customer_id"] for r in out} == {
        keys.integer("CUSTOMER_ID", 10_000_001 + i) for i in range(7)
    }
    again = _transformer(SynthesisSeed(seed=seed.seed)).transform([dict(r) for r in rows]).rows
    assert [r["customer_id"] for r in again] == [r["customer_id"] for r in out]


def test_pseudonyms_are_refused_outside_synthesize_mode_and_on_planned_columns() -> None:
    seed = generate_synthesis_seed()
    with pytest.raises(ValueError, match="synthesize"):
        _transformer(seed, mode="obfuscate", key=generate_key_material("key"), columns=[])
    planned = PseudonymColumn(
        path="$.holder", name="holder", entity_type="PERSON_NAME", kind="text"
    )
    with pytest.raises(ValueError, match="planned"):
        _transformer(seed, pseudonyms=[planned])
    ghost = PseudonymColumn(path="$.x", name="x", entity_type="CUSTOMER_ID", kind="int")
    with pytest.raises(ValueError, match="does not have"):
        _transformer(seed, pseudonyms=[ghost])


def test_a_pseudonym_the_column_cannot_hold_fails_the_batch_closed() -> None:
    from dps.engine.batch import IncompatibleMethod

    narrow = [
        PseudonymColumn(
            path="$.customer_id",
            name="customer_id",
            entity_type="CUSTOMER_ID",
            kind="int",
            digits=9,
        )
    ]
    t = _transformer(generate_synthesis_seed(), pseudonyms=narrow)
    with pytest.raises(IncompatibleMethod) as err:
        t.transform([{"account_id": 1, "customer_id": 4_000_000_000, "holder": "A", "branch": "B"}])
    assert err.value.code == "incompatible_method" and "4000000000" not in str(err.value)
