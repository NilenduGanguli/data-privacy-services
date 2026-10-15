"""The L2 → engine bridge (contract §6.4): BatchTransformer, row anchors, per-row verification."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from typing import Any

import pytest
from faker.providers.person.en_IN import Provider as IndiaNames

from dps.config import Settings
from dps.engine.batch import (
    KEYLESS_METHODS,
    LOCALE_BY_COUNTRY,
    BatchTransformer,
    ColumnPlan,
    IncompatibleMethod,
    LeakageError,
    plans_digest,
    row_anchor,
    verify_row,
)
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import Payload, PrivacyEngine, RequestOptions
from dps.engine.synth.personas import ascii_fold
from dps.engine.synth.surrogates import generate_synthesis_seed
from dps.engine.transform.apply import ApplyOptions, reveal
from dps.engine.transform.crypto import KeyMaterial, generate_key_material
from dps.jobs_models import ObjectRef
from dps.layers.connectors.base import ColumnInfo, Row, TableShape


class RotCipher:
    def encrypt(self, alphabet: str, plaintext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) + 3) % len(alphabet)] for c in plaintext)

    def decrypt(self, alphabet: str, ciphertext: str) -> str:
        return "".join(alphabet[(alphabet.index(c) - 3) % len(alphabet)] for c in ciphertext)


ENGINE = PrivacyEngine(
    Settings(
        _env_file=None,
        llm_provider="stub",
        fpe_enabled=True,
        ner_enabled=False,
        default_locale="en_IN",
    ),
    cipher_factory=lambda k, t: RotCipher(),
)
COLS = [
    ColumnInfo(
        path="$.customer_id", name="customer_id", data_type="bigint", kind="int", is_key=True
    ),
    ColumnInfo(
        path="$.full_name", name="full_name", data_type="varchar(120)", kind="text", length=120
    ),
    ColumnInfo(path="$.email", name="email", data_type="varchar(254)", kind="text", length=254),
    ColumnInfo(path="$.dob", name="dob", data_type="date", kind="date"),
    ColumnInfo(
        path="$.balance",
        name="balance",
        data_type="numeric(14,2)",
        kind="decimal",
        precision=14,
        scale=2,
    ),
    ColumnInfo(path="$.country", name="country", data_type="char(2)", kind="char", length=2),
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
        "dob": date(1988, 3, 14),
        "balance": Decimal("1200.50"),
        "country": "IN",
        "notes": "Call ann.k@example-mail.in about the loan",
    },
    {
        "customer_id": 10_000_002,
        "full_name": None,
        "email": "",
        "dob": None,
        "balance": Decimal("1.00"),
        "country": "US",
        "notes": None,
    },
]


def plan(
    name: str, entity: str, method: str, kind: str, text: bool = False, **params: Any
) -> ColumnPlan:
    col = SHAPE.column(name)
    return ColumnPlan(
        path=col.path,
        name=name,
        entity_type=entity,
        method=MethodSpec(method=method, **params),
        text_column=text,
        reversible=method not in ("surrogate", "keep", "generalize"),
        kind=kind,
        length=col.length,
        precision=col.precision,
        scale=col.scale,
    )


OBF = [
    plan("customer_id", "CUSTOMER_ID", "fpe_digits", "int"),
    plan("full_name", "PERSON_NAME", "fpe_text", "text"),
    plan("email", "EMAIL", "email", "text"),
    plan("dob", "DATE_OF_BIRTH", "date_shift", "date", window_days=365),
    plan("notes", "FREE_TEXT", "det_token", "text", text=True),
]


def _obf(
    key: KeyMaterial | None = None, cols: list[ColumnPlan] = OBF, **kw: Any
) -> BatchTransformer:
    return BatchTransformer(
        ENGINE,
        policy=load_preset("gdpr-pseudonymise"),
        mode="obfuscate",
        columns=cols,
        shape=SHAPE,
        key=key or generate_key_material("key"),
        seed=None,
        locale="en_IN",
        anchor=("customer_id", "CUSTOMER_ID"),
        **kw,
    )


def test_obfuscate_keeps_types_nulls_and_untouched_columns() -> None:
    result = _obf().transform([dict(r) for r in ROWS])
    a, b = result.rows
    assert isinstance(a["customer_id"], int) and a["customer_id"] != ROWS[0]["customer_id"]
    assert len(str(a["customer_id"])) == 8 and isinstance(a["dob"], date)
    assert a["dob"] != ROWS[0]["dob"]
    assert a["email"].endswith("@example-mail.in") and a["full_name"] != ROWS[0]["full_name"]
    assert "[[PII:EMAIL:" in a["notes"] and "ann.k@" not in a["notes"]
    assert a["balance"] == ROWS[0]["balance"]  # not planned: unchanged
    assert b["full_name"] is None and b["email"] == "" and b["notes"] is None
    assert result.verification.passed and result.values_by_method["fpe_digits"] == 2
    assert result.values_by_column == {
        "customer_id": 2,
        "full_name": 1,
        "email": 1,
        "dob": 1,
        "notes": 1,
    }
    assert ROWS[0]["full_name"] == "Ananya Krishnan"  # the caller's rows are not mutated


def test_determinism_and_key_sensitivity() -> None:
    key = generate_key_material("key")
    one, two = _obf(key).transform([dict(ROWS[0])]), _obf(key).transform([dict(ROWS[0])])
    assert one.rows == two.rows and _obf().transform([dict(ROWS[0])]).rows != one.rows


def test_l1_and_l2_agree_on_the_anchor_driven_date_shift() -> None:
    key = generate_key_material("key")
    l2 = _obf(key).transform([dict(ROWS[0])]).rows[0]["dob"]
    l1 = ENGINE.obfuscate(
        Payload(data={"customer_id": "10000001", "dob": "1988-03-14"}),
        "gdpr-pseudonymise",
        RequestOptions(consistency={"key_material": key.model_dump(mode="json")}),
    ).result
    assert l2.isoformat() == l1["dob"]


def test_text_envelopes_reveal_without_hints() -> None:
    key = generate_key_material("key")
    out = _obf(key).transform([dict(ROWS[0])]).rows[0]["notes"]
    opts = ApplyOptions(
        policy=load_preset("gdpr-pseudonymise"),
        key=key,
        surrogates=None,
        fpe_enabled=True,
        cipher_factory=lambda k, t: RotCipher(),
    )
    assert reveal(output=out, key=key, hints=[], options=opts) == ROWS[0]["notes"]


def test_a_text_column_that_is_one_whole_value_is_tokenized_as_a_span() -> None:
    row = {**ROWS[0], "notes": "ann.k@example-mail.in"}
    out = _obf().transform([row]).rows[0]["notes"]
    assert out.startswith("[[PII:EMAIL:") and out.endswith("]]")


def test_synthesize_mode_needs_no_key_and_is_seeded() -> None:
    seed = generate_synthesis_seed()
    cols = [
        plan("full_name", "PERSON_NAME", "surrogate", "text"),
        plan("email", "EMAIL", "surrogate", "text"),
    ]

    def run(s: Any) -> Any:
        return BatchTransformer(
            ENGINE,
            policy=load_preset("synthetic-realistic"),
            mode="synthesize",
            columns=cols,
            shape=SHAPE,
            key=None,
            seed=s,
            locale="en_IN",
        ).transform([dict(ROWS[0])])

    first = run(seed)
    assert first.rows == run(seed).rows and first.rows[0]["full_name"] != ROWS[0]["full_name"]
    assert (
        first.values_by_method == {"surrogate": 2}
        and run(generate_synthesis_seed()).rows != first.rows
    )
    assert first.verification.passed


def test_mode_prerequisites() -> None:
    policy = load_preset("gdpr-pseudonymise")
    with pytest.raises(ValueError):
        BatchTransformer(
            ENGINE,
            policy=policy,
            mode="obfuscate",
            columns=OBF,
            shape=SHAPE,
            key=None,
            seed=None,
            locale="en_IN",
        )
    with pytest.raises(ValueError):
        BatchTransformer(
            ENGINE,
            policy=policy,
            mode="synthesize",
            columns=[],
            shape=SHAPE,
            key=None,
            seed=None,
            locale="en_IN",
        )
    with pytest.raises(IncompatibleMethod) as err:
        BatchTransformer(
            ENGINE,
            policy=policy,
            mode="synthesize",
            columns=OBF[:1],
            shape=SHAPE,
            key=None,
            seed=generate_synthesis_seed(),
            locale="en_IN",
        )
    assert err.value.code == "incompatible_method" and err.value.path == "$.customer_id"
    ghost = plan("full_name", "PERSON_NAME", "fpe_text", "text").model_copy(update={"name": "nope"})
    with pytest.raises(ValueError):
        _obf(cols=[ghost])
    assert {"surrogate", "keep", "null"} <= KEYLESS_METHODS and "det_token" not in KEYLESS_METHODS
    assert LOCALE_BY_COUNTRY["IN"] == "en_IN" and LOCALE_BY_COUNTRY["UK"] == "en_GB"


def test_incompatible_and_overflow_fail_with_codes() -> None:
    with pytest.raises(IncompatibleMethod) as err:
        _obf(cols=[plan("customer_id", "CUSTOMER_ID", "det_token", "int")]).transform(
            [dict(ROWS[0])]
        )
    assert err.value.code == "incompatible_method" and err.value.path == "$.customer_id"
    short = plan("full_name", "PERSON_NAME", "det_token", "text").model_copy(update={"length": 12})
    with pytest.raises(IncompatibleMethod) as err2:
        _obf(cols=[short]).transform([dict(ROWS[0])])
    assert err2.value.code == "length_overflow" and "Ananya" not in str(err2.value)


def test_leakage_strict_fails_closed_and_warn_counts() -> None:
    leaky = [plan("full_name", "PERSON_NAME", "dictionary", "text", values=["Ananya Krishnan"])]
    with pytest.raises(LeakageError) as err:
        _obf(cols=leaky).transform([dict(ROWS[0])])
    assert err.value.hits == 1 and "Ananya" not in str(err.value)
    warned = _obf(cols=leaky, verify="warn").transform([dict(ROWS[0])])
    assert warned.verification.passed is False and len(warned.verification.hits) == 1
    hit = warned.verification.hits[0]
    assert hit == {"path": "$.full_name", "entity_type": "PERSON_NAME", "rule": "fixed_point"}


def test_short_originals_inside_other_outputs_are_not_leaks() -> None:
    rows = [{**ROWS[0], "full_name": "Anna", "email": "hannah.anna@x.in"}]
    cols = [
        plan("full_name", "PERSON_NAME", "fpe_text", "text"),
        plan("email", "EMAIL", "dictionary", "text", values=["hannah@x.in"]),  # output holds "anna"
    ]
    result = _obf(cols=cols).transform(rows)
    assert result.verification.passed and result.verification.checked == 2


def test_an_undetected_copy_in_free_text_is_a_leak() -> None:
    # NER is off and the name is glued into one word: no detector finds it, and no row-value span
    # either (those match whole words), so the notes would keep the customer's name in clear.
    row = {**ROWS[0], "notes": "ref ananyakrishnan fees"}
    with pytest.raises(LeakageError):
        _obf().transform([row])
    warned = _obf(verify="warn").transform([row])
    assert warned.verification.hits == [
        {"path": "$.notes", "entity_type": "PERSON_NAME", "rule": "contained"}
    ]
    # S1-M3 r1 (B2): repeated as words — any case — the row's own name is a span and is replaced
    spaced = _obf().transform([{**ROWS[0], "notes": "ref ananya krishnan fees"}])
    assert spaced.verification.passed
    assert not {"ananya", "krishnan"} & set(spaced.rows[0]["notes"].lower().split())


def test_a_name_part_that_appears_from_nowhere_is_a_leak() -> None:
    cols = [
        plan("full_name", "PERSON_NAME", "fpe_text", "text"),
        plan("email", "EMAIL", "dictionary", "text", values=["krishnan.x@mail.in"]),
    ]
    row = {**ROWS[0], "email": "a.k@x.in"}
    result = _obf(cols=cols, verify="warn").transform([row])
    assert result.verification.hits == [
        {"path": "$.email", "entity_type": "PERSON_NAME", "rule": "name_part"}
    ]
    # the same part already in the input (outside the name) is not a hit
    ok = _obf(cols=cols).transform([{**ROWS[0], "email": "krishnan@x.in"}])
    assert ok.verification.passed


def test_kept_columns_are_not_searched() -> None:
    cols = [
        plan("full_name", "PERSON_NAME", "fpe_text", "text"),
        plan("email", "EMAIL", "keep", "text"),
    ]
    row = {**ROWS[0], "email": "ananya.krishnan@example-mail.in"}
    result = _obf(cols=cols).transform([row])
    assert result.verification.passed and result.rows[0]["email"] == row["email"]


def test_verify_row_rules() -> None:
    name = plan("full_name", "PERSON_NAME", "fpe_text", "text")
    email = plan("email", "EMAIL", "email", "text")
    assert (
        verify_row([(name, "Ann Lee", True)], {"$.full_name": "ann lee"})[0]["rule"]
        == "fixed_point"
    )
    assert verify_row([(email, "ab@cd.ef", True)], {"$.full_name": "x", "$.email": "zz"}) == []
    hits = verify_row(
        [(email, "abcd@efgh.ij", True)], {"$.full_name": "ABCD@efgh.ij!", "$.email": "q"}
    )
    assert hits == [{"path": "$.full_name", "entity_type": "EMAIL", "rule": "contained"}]
    # name parts need the inputs; without them only the value rules run
    parts = [(name, "Ananya Krishnan", True)]
    out = {"$.full_name": "Dqdqbd Nulvkqdq", "$.notes": "met krishnan today"}
    assert verify_row(parts, out) == []
    assert verify_row(parts, out, inputs={"$.full_name": "Ananya Krishnan", "$.notes": "met"}) == [
        {"path": "$.notes", "entity_type": "PERSON_NAME", "rule": "name_part"}
    ]


def test_a_chance_equal_low_cardinality_surrogate_is_not_a_fixed_point() -> None:
    """D-A B1: a persona-drawn gender (or nationality) surrogate equals the original by chance
    (M0 ``_IDENTITY_OK``); every other entity still fails on an unchanged whole value."""

    def low(name: str, entity: str) -> ColumnPlan:
        return ColumnPlan(
            path=f"$.{name}",
            name=name,
            entity_type=entity,
            method=MethodSpec(method="surrogate"),
            text_column=False,
            reversible=False,
            kind="text",
            length=None,
            precision=None,
            scale=None,
        )

    gender, nationality, city = (
        low("gender", "GENDER"),
        low("nationality", "NATIONALITY"),
        low("city", "CITY"),
    )
    assert verify_row([(gender, "female", True)], {"$.gender": "Female"}) == []
    assert verify_row([(nationality, "Indian", True)], {"$.nationality": "Indian"}) == []
    assert verify_row([(city, "Mumbai", True)], {"$.city": "mumbai"}) == [
        {"path": "$.city", "entity_type": "CITY", "rule": "fixed_point"}
    ]


def test_row_anchor_matches_the_l1_formula() -> None:
    from dps.engine.findings import hash_value

    assert (
        row_anchor(ROWS[0], SHAPE, ("customer_id", "CUSTOMER_ID"))
        == f"CUSTOMER_ID:{hash_value('10000001')}"
    )
    assert row_anchor(ROWS[0], SHAPE, None) == f"row:{hash_value('10000001')}"
    no_anchor_value = {**ROWS[0], "full_name": None}
    assert row_anchor(no_anchor_value, SHAPE, ("full_name", "PERSON_NAME")).startswith("row:")


def test_plans_digest_is_stable() -> None:
    assert plans_digest(OBF) == plans_digest([c.model_copy() for c in OBF])
    assert plans_digest(OBF) != plans_digest(OBF[:2])


def test_synthesize_free_text_keeps_low_sensitivity_spans_without_a_hit() -> None:
    cols = [plan("notes", "FREE_TEXT", "surrogate", "text", text=True)]
    row = {**ROWS[0], "notes": "met on 2024-01-05 re ann.k@example-mail.in"}
    result = BatchTransformer(
        ENGINE,
        policy=load_preset("synthetic-realistic"),
        mode="synthesize",
        columns=cols,
        shape=SHAPE,
        key=None,
        seed=generate_synthesis_seed(),
        locale="en_IN",
    ).transform([row])
    notes = result.rows[0]["notes"]
    assert "2024-01-05" in notes and "ann.k@" not in notes  # the DATE is kept by the policy
    assert result.verification.passed and result.values_by_method == {"keep": 1, "surrogate": 1}


# --- M2 review G4 r1 (B1): fpe_digits on int/decimal columns must stay revealable ------------
REAL_FF1 = PrivacyEngine(
    Settings(
        _env_file=None,
        llm_provider="stub",
        fpe_enabled=True,
        ner_enabled=False,
        default_locale="en_IN",
    )
)
AMOUNTS = TableShape(
    object=ObjectRef(owner="bank", name="accounts"),
    columns=[
        ColumnInfo(path="$.id", name="id", data_type="bigint", kind="int", is_key=True),
        ColumnInfo(
            path="$.amt",
            name="amt",
            data_type="numeric(14,2)",
            kind="decimal",
            precision=14,
            scale=2,
        ),
    ],
    key=["id"],
    key_source="primary_key",
)


def _numeric_plans(shape: TableShape, **params: Any) -> list[ColumnPlan]:
    out = []
    for name, entity in (("id", "CUSTOMER_ID"), ("amt", "AMOUNT")):
        col = shape.column(name)
        out.append(
            ColumnPlan(
                path=col.path,
                name=name,
                entity_type=entity,
                method=MethodSpec(method="fpe_digits", **params),
                text_column=False,
                reversible=True,
                kind=col.kind,
                length=None,
                precision=col.precision,
                scale=col.scale,
            )
        )
    return out


def _numeric_transformer(
    engine: PrivacyEngine, key: KeyMaterial, cols: list[ColumnPlan]
) -> BatchTransformer:
    return BatchTransformer(
        engine,
        policy=load_preset("gdpr-pseudonymise"),
        mode="obfuscate",
        columns=cols,
        shape=AMOUNTS,
        key=key,
        seed=None,
        locale="en_IN",
    )


def _reveal_column(
    engine: PrivacyEngine, key: KeyMaterial, col: ColumnPlan, rows: list[Row]
) -> list[str]:
    """Reveal one column the way Task 22's reveal_row will: per-path hints from the plan."""
    from dps.engine.transform.apply import RevealHint
    from dps.engine.transform.crypto import key_check_value
    from dps.layers.connectors.base import to_engine_text

    opts = ApplyOptions(
        policy=load_preset("gdpr-pseudonymise"),
        key=key,
        surrogates=None,
        fpe_enabled=True,
        cipher_factory=engine.cipher_factory,
    )
    hint = RevealHint(
        path=col.path,
        entity_type=col.entity_type,
        method=col.method.method,
        params=col.method.params,
        kid=key.kid,
        kcv=key_check_value(key),
    )
    info = AMOUNTS.column(col.name)
    out = []
    for row in rows:
        doc = {col.name: to_engine_text(row[col.name], info)}
        out.append(reveal(output=doc, key=key, hints=[hint], options=opts)[col.name])
    return out


def test_column_plans_default_fpe_digits_to_a_preserved_leading_zero() -> None:
    cols = _numeric_plans(AMOUNTS)
    assert all(c.method.params == {"leading_zero": "preserve"} for c in cols)
    keep4 = _numeric_plans(AMOUNTS, keep_suffix=4)[0]
    assert keep4.method.params == {"keep_suffix": 4, "leading_zero": "preserve"}
    text = plan("full_name", "PERSON_NAME", "fpe_digits", "text", leading_zero="any")
    assert text.method.params == {"leading_zero": "any"}  # a text column may keep v0.1.0 tokens


def test_fpe_digits_without_a_preserved_leading_zero_is_refused_on_numeric_columns() -> None:
    with pytest.raises(IncompatibleMethod) as err:
        _numeric_transformer(
            ENGINE, generate_key_material("key"), _numeric_plans(AMOUNTS, leading_zero="any")
        )
    assert err.value.code == "incompatible_method" and err.value.path == "$.id"
    # a plan copied around the validator would run with params its reveal map does not show
    bare = _numeric_plans(AMOUNTS)[0].model_copy(update={"method": MethodSpec(method="fpe_digits")})
    with pytest.raises(ValueError):
        _numeric_transformer(ENGINE, generate_key_material("key"), [bare])


def test_a_zero_led_fpe_output_keeps_every_digit_of_an_int_key() -> None:
    # RotCipher maps a leading 7 to 0: the plain token 03333334 would be stored as 3333334.
    key = generate_key_material("key")
    rows = [{"id": 70_000_001, "amt": Decimal("71234.56")}]
    out = _numeric_transformer(ENGINE, key, _numeric_plans(AMOUNTS)).transform(rows).rows[0]
    assert out["id"] == 36_666_667 and out["amt"] == Decimal("37890.12")
    cols = _numeric_plans(AMOUNTS)
    assert _reveal_column(ENGINE, key, cols[0], [out]) == ["70000001"]
    assert _reveal_column(ENGINE, key, cols[1], [out]) == ["71234.56"]


def test_a_reversible_output_the_column_would_change_is_refused() -> None:
    key = generate_key_material("key")
    # phone has no leading-zero rule: a zero-led token in an int column would lose its digit
    phone = _numeric_plans(AMOUNTS)[0].model_copy(
        update={"method": MethodSpec(method="phone"), "entity_type": "PHONE_NUMBER"}
    )
    with pytest.raises(IncompatibleMethod) as err:
        _numeric_transformer(ENGINE, key, [phone]).transform([{"id": 7_123_456_789, "amt": None}])
    assert err.value.code == "incompatible_method" and "7123456789" not in str(err.value)
    # a value with fewer decimals than the target scale would be re-scaled, changing its digits
    amt = _numeric_plans(AMOUNTS)[1]
    with pytest.raises(IncompatibleMethod) as err2:
        _numeric_transformer(ENGINE, key, [amt]).transform([{"id": 1, "amt": Decimal("12345.6")}])
    assert err2.value.code == "incompatible_method" and err2.value.path == "$.amt"


@pytest.mark.fpe
def test_real_ff1_int_and_decimal_columns_keep_their_digits_and_reveal() -> None:
    pytest.importorskip("fastfpe")
    import random

    rng = random.Random(20261012)
    ids = rng.sample(range(10_000_000, 100_000_000), 400)
    rows: list[Row] = [
        {"id": i, "amt": Decimal(rng.randrange(1_000_000, 100_000_000)) / 100} for i in ids
    ]
    for row in rows:
        row["amt"] = row["amt"].quantize(Decimal("0.01"))
    key = generate_key_material("key")
    cols = _numeric_plans(AMOUNTS)
    transformer = _numeric_transformer(REAL_FF1, key, cols)
    out = transformer.transform([dict(r) for r in rows[:150]]).rows
    out += transformer.transform([dict(r) for r in rows[150:]]).rows  # batch split is irrelevant
    assert all(len(str(o["id"])) == 8 for o in out)
    assert len({o["id"] for o in out}) == len(rows)
    assert all(
        len(format(o["amt"], "f")) == len(format(r["amt"], "f"))
        for o, r in zip(out, rows, strict=True)
    )
    assert _reveal_column(REAL_FF1, key, cols[0], out) == [str(i) for i in ids]
    assert _reveal_column(REAL_FF1, key, cols[1], out) == [format(r["amt"], "f") for r in rows]


# --- S8: a table's name columns are one person per row, the notes and the e-mail agree -----------
PEOPLE = TableShape(
    object=ObjectRef(owner="bank", name="people"),
    columns=[
        ColumnInfo(path="$.customer_id", name="customer_id", data_type="bigint", kind="int"),
        *(
            ColumnInfo(path=f"$.{n}", name=n, data_type="varchar(60)", kind="text", length=60)
            for n in ("first_name", "last_name", "email", "nominee_name")
        ),
        ColumnInfo(path="$.notes", name="notes", data_type="text", kind="text"),
    ],
    key=["customer_id"],
    key_source="primary_key",
)
#: Invented names; every customer is a "Mr." in the notes, whatever the first name.
GIVEN = ("Arjun", "Kavya", "Rohan", "Meera", "Vikram", "Ishita", "Karan", "Anjali")
FAMILY = ("Mehta", "Nair", "Kulkarni", "Iyer", "Rao", "Bose", "Malhotra", "Pillai")


def _people_rows() -> list[Row]:
    return [
        {
            "customer_id": 20_000_001 + i,
            "first_name": GIVEN[i % 8],
            "last_name": FAMILY[(i * 3) % 8],
            "email": f"{GIVEN[i % 8].lower()}.{FAMILY[(i * 3) % 8].lower()}@example-mail.in",
            "nominee_name": f"{GIVEN[(i + 3) % 8]} {FAMILY[(i * 3 + 5) % 8]}",
            "notes": f"KYC call with {GIVEN[i % 8]}; Mr. {FAMILY[(i * 3) % 8]} prefers e-mail.",
        }
        for i in range(24)
    ]


def _people_plan(name: str, entity: str, text: bool = False) -> ColumnPlan:
    return ColumnPlan(
        path=f"$.{name}",
        name=name,
        entity_type=entity,
        method=MethodSpec(method="surrogate"),
        text_column=text,
        reversible=False,
        kind="text",
        length=PEOPLE.column(name).length,
    )


def test_synthetic_rows_name_one_person_in_every_column() -> None:
    rows = _people_rows()
    result = BatchTransformer(
        ENGINE,
        policy=load_preset("synthetic-realistic"),
        mode="synthesize",
        columns=[
            _people_plan("first_name", "PERSON_NAME"),
            _people_plan("last_name", "PERSON_NAME"),
            _people_plan("email", "EMAIL"),
            _people_plan("nominee_name", "PERSON_NAME"),
            _people_plan("notes", "FREE_TEXT", text=True),
        ],
        shape=PEOPLE,
        key=None,
        seed=generate_synthesis_seed(),
        locale="en_IN",
    ).transform([dict(r) for r in rows])
    assert result.verification.passed
    for row, out in zip(rows, result.rows, strict=True):
        first, last = out["first_name"], out["last_name"]
        assert out["email"].startswith(f"{ascii_fold(first)}.{ascii_fold(last)}")
        assert out["notes"] == f"KYC call with {first}; Mr. {last} prefers e-mail."
        assert first in IndiaNames.first_names_male  # "Mr." in the row's notes
        assert out["nominee_name"].split(" ")[0] != first
        originals = {*row["nominee_name"].split(), row["first_name"], row["last_name"]}
        written = {first, last, *out["nominee_name"].split()}
        assert not originals & written
