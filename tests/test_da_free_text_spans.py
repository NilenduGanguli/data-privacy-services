"""WP-I (m3 merge): D-A in free text. A synthesize run never copies a LOW personal attribute into a
synthetic table — the CITY *column* is surrogated (M5 G2 B1) — yet the free-text pipeline kept the
same entity's spans by policy (``keep_non_sensitive``), so ``NOTES`` carried the source row's real
city. On Oracle's CRM demo (``NOTES``: "… Prefers branch in <city>") the leakage sweep caught it
and failed every synthesize run closed. A kept span is surrogated in a synthesize job now."""

from __future__ import annotations

import re
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.batch import BatchTransformer, ColumnPlan
from dps.engine.policy import MethodSpec, load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import generate_synthesis_seed
from dps.engine.transform.crypto import generate_key_material
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape

ENGINE = PrivacyEngine(
    Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)  # type: ignore[call-arg]
)
SHAPE = TableShape(
    object=ObjectRef(owner="CRM", name="CUSTOMERS"),
    key=["ID"],
    key_source="primary_key",
    columns=[
        ColumnInfo(path="$.ID", name="ID", data_type="NUMBER(12)", kind="int", is_key=True),
        ColumnInfo(path="$.CITY", name="CITY", data_type="VARCHAR2(80)", kind="text"),
        ColumnInfo(path="$.NOTES", name="NOTES", data_type="CLOB", kind="lob_text"),
    ],
)
CITIES = ["Pune", "Mumbai", "Chennai", "Kolkata", "Hyderabad"]


def _plan(name: str, entity: str, *, text: bool) -> ColumnPlan:
    return ColumnPlan(
        path=f"$.{name}",
        name=name,
        entity_type=entity,
        method=MethodSpec(method="surrogate"),
        text_column=text,
        reversible=False,
        kind="lob_text" if text else "text",
        length=None,
    )


def test_a_city_kept_in_free_text_is_surrogated_in_a_synthesize_job() -> None:
    rows: list[dict[str, Any]] = [
        {"ID": i, "CITY": CITIES[i % 5], "NOTES": f"Prefers branch in {CITIES[i % 5]}."}
        for i in range(50)
    ]
    transformer = BatchTransformer(
        ENGINE,
        policy=load_preset("gdpr-pseudonymise"),
        mode="synthesize",
        columns=[_plan("CITY", "CITY", text=False), _plan("NOTES", "FREE_TEXT", text=True)],
        shape=SHAPE,
        key=None,
        seed=generate_synthesis_seed(),
        locale="en_IN",
        unclassified="keep",
        verify="warn",
    )
    result = transformer.transform(rows)
    assert result.verification.passed, [
        (h["path"], h["entity_type"], h["rule"]) for h in result.verification.hits
    ]
    copied = sum(src["CITY"] in out["NOTES"] for src, out in zip(rows, result.rows, strict=True))
    assert copied == 0


# -- S1-M3 r1 (engines B2): the first name the CRM notes repeat ------------------------------------

ORACLE_CRM = TableShape(
    object=ObjectRef(owner="CRM", name="CUSTOMERS"),
    key=["CUSTOMER_ID"],
    key_source="primary_key",
    columns=[
        ColumnInfo(
            path="$.CUSTOMER_ID",
            name="CUSTOMER_ID",
            data_type="NUMBER(12)",
            kind="int",
            is_key=True,
        ),
        ColumnInfo(path="$.FULL_NAME", name="FULL_NAME", data_type="VARCHAR2(120)", kind="text"),
        ColumnInfo(path="$.EMAIL", name="EMAIL", data_type="VARCHAR2(254)", kind="text"),
        ColumnInfo(path="$.CITY", name="CITY", data_type="VARCHAR2(80)", kind="text"),
        ColumnInfo(path="$.NOTES", name="NOTES", data_type="CLOB", kind="lob_text"),
    ],
)
MONGO_PROFILES = TableShape(
    object=ObjectRef(name="profiles"),
    key=["_id"],
    key_source="primary_key",
    columns=[
        ColumnInfo(path="$._id", name="_id", data_type="int", kind="int", is_key=True),
        ColumnInfo(path="$.contact.email", name="contact", data_type="string", kind="text"),
        ColumnInfo(path="$.notes", name="notes", data_type="string", kind="text"),
        ColumnInfo(path="$.profile.fullName", name="profile", data_type="string", kind="text"),
    ],
)


def _named(path: str, name: str, entity: str, *, text: bool, method: str) -> ColumnPlan:
    return ColumnPlan(
        path=path,
        name=name,
        entity_type=entity,
        method=MethodSpec(method=method),
        text_column=text,
        reversible=method == "det_token",
        kind="text",
        length=None,
    )


def _demo(n: int) -> list[Any]:
    from tools.seed_model import seed_customers

    return list(seed_customers(n, 7))


def _first_name_kept(first: str, notes: str) -> bool:
    return (
        len(first) >= 3 and re.search(rf"(?<!\w){re.escape(first)}(?!\w)", notes, re.I) is not None
    )


def _transformer(mode: str, columns: list[ColumnPlan], shape: TableShape) -> BatchTransformer:
    return BatchTransformer(
        ENGINE,
        policy=load_preset("gdpr-pseudonymise"),
        mode=mode,  # type: ignore[arg-type]
        columns=columns,
        shape=shape,
        key=generate_key_material("key") if mode == "obfuscate" else None,
        seed=generate_synthesis_seed() if mode == "synthesize" else None,
        locale="en_IN",
        unclassified="keep",
        verify="strict",
    )


@pytest.mark.parametrize("mode", ["synthesize", "obfuscate"])
def test_the_first_name_the_crm_notes_repeat_is_replaced_on_the_oracle_shape(mode: str) -> None:
    """The M3 demo notes read "KYC call with <first name>; follow up at …": no detector finds a
    first name without NER, and the whole ``FULL_NAME`` never occurs, so every synthetic note kept
    its source row's first name (20 000 / 20 000). A word of a replaced name is a span now."""
    method = "surrogate" if mode == "synthesize" else "det_token"
    columns = [
        _named("$.FULL_NAME", "FULL_NAME", "PERSON_NAME", text=False, method=method),
        _named("$.EMAIL", "EMAIL", "EMAIL", text=False, method=method),
        _named("$.NOTES", "NOTES", "FREE_TEXT", text=True, method=method),
    ]
    customers = _demo(120)
    rows: list[dict[str, Any]] = [
        {
            "CUSTOMER_ID": c.customer_id,
            "FULL_NAME": c.full_name,
            "EMAIL": c.email,
            "CITY": c.city,
            "NOTES": c.notes,
        }
        for c in customers
    ]
    assert sum(_first_name_kept(c.first_name, c.notes) for c in customers) >= 100  # control
    result = _transformer(mode, columns, ORACLE_CRM).transform(rows)
    assert result.verification.passed
    kept = sum(
        _first_name_kept(c.first_name, out["NOTES"])
        for c, out in zip(customers, result.rows, strict=True)
    )
    assert kept == 0
    assert not any(c.email in out["NOTES"] for c, out in zip(customers, result.rows, strict=True))
    if mode == "synthesize":  # the note names the synthetic row's own first name
        own = sum(
            out["FULL_NAME"].split(" ")[0] in out["NOTES"]
            for out in result.rows
            if len(out["FULL_NAME"].split(" ")[0]) >= 3
        )
        assert own >= 100


@pytest.mark.parametrize("mode", ["synthesize", "obfuscate"])
def test_the_first_name_the_notes_repeat_is_replaced_in_a_mongo_document(mode: str) -> None:
    """The same on the ``crm.profiles`` shape: the name is a nested leaf (``$.profile.fullName``),
    the notes a top-level field."""
    method = "surrogate" if mode == "synthesize" else "det_token"
    columns = [
        _named("$.profile.fullName", "profile", "PERSON_NAME", text=False, method=method),
        _named("$.contact.email", "contact", "EMAIL", text=False, method=method),
        _named("$.notes", "notes", "FREE_TEXT", text=True, method=method),
    ]
    customers = _demo(120)
    rows: list[dict[str, Any]] = [
        {
            "_id": i,
            "profile": {"fullName": c.full_name},
            "contact": {"email": c.email},
            "notes": c.notes,
        }
        for i, c in enumerate(customers)
    ]
    result = _transformer(mode, columns, MONGO_PROFILES).transform(rows)
    assert result.verification.passed
    kept = sum(
        _first_name_kept(c.first_name, out["notes"])
        for c, out in zip(customers, result.rows, strict=True)
    )
    assert kept == 0


def test_a_name_word_is_matched_case_insensitively_and_as_a_whole_word_only() -> None:
    columns = [
        _named("$.FULL_NAME", "FULL_NAME", "PERSON_NAME", text=False, method="surrogate"),
        _named("$.NOTES", "NOTES", "FREE_TEXT", text=True, method="surrogate"),
    ]
    rows: list[dict[str, Any]] = [
        {
            "CUSTOMER_ID": 1,
            "FULL_NAME": "Asha Verma",
            "EMAIL": None,
            "CITY": None,
            "NOTES": "Called ASHA about the Vermatech card; Li and Ash were not reached.",
        }
    ]
    out = _transformer("synthesize", columns, ORACLE_CRM).transform(rows).rows[0]["NOTES"]
    assert "ASHA" not in out.upper().split() and "asha" not in out.lower().replace(".", " ").split()
    assert "Vermatech" in out  # a name word inside a longer word is not a match
    assert "Li and Ash" in out  # words of other names (and short ones) are left alone


def _caseless_reference(text: str, needle: str) -> list[tuple[int, int]]:
    """Brute force: left to right, the shortest ``text[i:j]`` caselessly equal to ``needle`` with
    no word character on either side; non-overlapping."""
    word = re.compile(r"\w")
    folded, out, i = needle.casefold(), [], 0
    while i < len(text):
        j = next((j for j in range(i + 1, len(text) + 1) if text[i:j].casefold() == folded), None)
        if (
            j is not None
            and not (i > 0 and word.match(text[i - 1]))
            and not (j < len(text) and word.match(text[j]))
        ):
            out.append((i, j))
            i = j
        else:
            i += 1
    return out


def test_the_whole_word_search_is_caseless_and_needs_no_regex_per_row() -> None:
    """``_whole_word_spans`` (a folded substring search; no regex compiled per row) finds every
    caseless whole-word occurrence (Unicode full case folding, ``ß`` → ``ss`` included); on ASCII
    text it answers exactly what ``(?<!\\w)needle(?!\\w)`` with ``re.IGNORECASE`` answered."""
    import random

    from dps.engine.batch import _whole_word_spans

    rng = random.Random(7)
    fixed = [
        ("KYC call with ASHA; asha. Ashamed of Asha_x", "Asha"),
        ("Grüße an STRASSE und Straße", "straße"),
        ("+91 98000 12345 or 9800012345", "+91 98000 12345"),
        ("a@b.in, x a@b.in.", "a@b.in"),
    ]
    for alphabet, regex_too in (("aAsS _-.@+1", True), ("aAsSßẞéÉ _-.@1ǅǆ", False)):
        cases = list(fixed)
        for _ in range(3000):
            text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 14)))
            needle = "".join(rng.choice(alphabet) for _ in range(rng.randint(1, 3)))
            cases.append((text, needle))
        for text, needle in cases:
            got = list(_whole_word_spans(text, text.casefold(), needle))
            assert got == _caseless_reference(text, needle), (text, needle)
            if regex_too and text.isascii() and needle.isascii():
                pattern = rf"(?<!\w){re.escape(needle)}(?!\w)"
                want = [m.span() for m in re.finditer(pattern, text, re.IGNORECASE)]
                assert got == want, (text, needle)


@pytest.mark.parametrize("mode", ["synthesize", "obfuscate"])
def test_a_first_name_inside_a_kept_city_word_is_not_a_leak(mode: str) -> None:
    """Found by the smoke ``--m3`` run (3 of 20 000 demo customers): a first name of 8+ letters that
    starts the row's city word ("Christopher" in "Christopherville"). The whole-word span replaces
    the name and leaves the city alone; a name word is not a sweep original (the sweep's rules are
    substring rules), so the job does not fail closed on the kept city — and the whole name is
    still swept (the glued copy of ``test_batch.py`` still fails closed)."""
    method = "surrogate" if mode == "synthesize" else "det_token"
    columns = [
        _named("$.FULL_NAME", "FULL_NAME", "PERSON_NAME", text=False, method=method),
        _named("$.EMAIL", "EMAIL", "EMAIL", text=False, method=method),
        _named("$.NOTES", "NOTES", "FREE_TEXT", text=True, method=method),
    ]
    rows: list[dict[str, Any]] = [
        {
            "CUSTOMER_ID": 1,
            "FULL_NAME": "Christopher Wells",
            "EMAIL": "christopher.wells@example.com",
            "CITY": "Christopherville",
            "NOTES": "KYC call with Christopher; follow up at christopher.wells@example.com. "
            "Prefers branch in Christopherville.",
        }
    ]
    result = _transformer(mode, columns, ORACLE_CRM).transform(rows)  # strict: no LeakageError
    notes = result.rows[0]["NOTES"]
    assert result.verification.passed
    assert not _first_name_kept("Christopher", notes.replace("Christopherville", ""))
    assert "christopher.wells@example.com" not in notes
