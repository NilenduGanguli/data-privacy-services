from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Any

import pytest
from pydantic import ValidationError
from stdnum import verhoeff

from dps.engine.detect.checksums import validate
from dps.engine.synth.schema_gen import (
    ENTITY_GENERATORS,
    ENTITY_TEMPLATES,
    GeneratorSpec,
    SchemaError,
    execute_spec,
    rows_to_csv,
)
from dps.engine.synth.surrogates import REDACTED

SEED = bytes(range(32))
FIELDS: list[dict[str, Any]] = [
    {
        "name": "customer_id",
        "generator": "sequence",
        "params": {"prefix": "CUST-", "width": 6},
        "unique": True,
    },
    {"name": "full_name", "generator": "entity", "params": {"entity_type": "PERSON_NAME"}},
    {"name": "email", "generator": "entity", "params": {"entity_type": "EMAIL"}},
    {"name": "card", "generator": "entity", "params": {"entity_type": "CARD_PAN"}},
    {"name": "aadhaar", "generator": "entity", "params": {"entity_type": "AADHAAR"}},
    {"name": "iban", "generator": "entity", "params": {"entity_type": "IBAN"}},
    {"name": "ssn", "generator": "entity", "params": {"entity_type": "SSN"}},
    {"name": "score", "generator": "integer", "params": {"min": 300, "max": 900}},
    {"name": "balance", "generator": "decimal", "params": {"min": -500, "max": 5000, "scale": 2}},
    {
        "name": "status",
        "generator": "choice",
        "params": {"values": ["ACTIVE", "CLOSED"], "weights": [9, 1]},
    },
    {
        "name": "opened_on",
        "generator": "date",
        "params": {"start": "2015-01-01", "end": "2020-12-31"},
    },
    {
        "name": "closed_on",
        "generator": "date",
        "params": {"start": "2015-01-01", "end": "2026-10-01"},
    },
    {"name": "ref", "generator": "pattern", "params": {"template": "AA-9999"}},
    {"name": "address.city", "generator": "entity", "params": {"entity_type": "CITY"}},
    {"name": "vip", "generator": "boolean", "params": {"p_true": 0.3}},
    {"name": "memo", "generator": "text", "params": {"words_min": 3, "words_max": 6}},
]
CONSTRAINTS: list[dict[str, Any]] = [
    {"kind": "order", "fields": ["opened_on", "closed_on"]},
    {
        "kind": "rule",
        "field": "status",
        "when": [{"field": "balance", "op": "lt", "value": 0, "then": "OVERDRAWN"}],
        "else": "ACTIVE",
    },
]


def spec(**overrides: Any) -> GeneratorSpec:
    return GeneratorSpec.model_validate(
        {"name": "customers", "fields": FIELDS, "constraints": CONSTRAINTS, **overrides}
    )


def test_execution_is_deterministic() -> None:
    first = execute_spec(spec(), rows=50, seed=SEED)
    assert first.rows == execute_spec(spec(), rows=50, seed=SEED).rows
    assert first.rows != execute_spec(spec(), rows=50, seed=bytes(32)).rows
    assert first.spec_hash == spec().spec_hash() and first.generators_used["entity"] == 50 * 7


def test_generators_respect_their_params() -> None:
    rows = execute_spec(spec(), rows=500, seed=SEED).rows
    assert [r["customer_id"] for r in rows[:3]] == ["CUST-000001", "CUST-000002", "CUST-000003"]
    assert all(300 <= r["score"] <= 900 for r in rows)
    assert all(
        round(r["balance"], 2) == r["balance"] and -500 <= r["balance"] <= 5000 for r in rows
    )
    assert all(re.fullmatch(r"[A-Z]{2}-\d{4}", r["ref"]) for r in rows)
    assert 0.24 <= sum(r["vip"] for r in rows) / len(rows) <= 0.36
    assert all(3 <= len(r["memo"].rstrip(".").split()) <= 6 for r in rows)
    assert all(isinstance(r["address"]["city"], str) and r["address"]["city"] for r in rows)


def test_entity_values_are_rule_valid_and_coherent() -> None:
    rows = execute_spec(spec(), rows=200, seed=SEED).rows
    assert all(validate("CARD_PAN", r["card"]) is True for r in rows)
    # Aadhaar surrogates are Verhoeff-valid but start with 1: UIDAI never issues 0/1, so stdnum's
    # validator (which knows that) refuses them — never-issued by design (M0 surrogates)
    aadhaar = [r["aadhaar"].replace(" ", "") for r in rows]
    assert all(len(a) == 12 and a[0] == "1" and verhoeff.is_valid(a) for a in aadhaar)
    assert all(validate("IBAN", r["iban"]) is True for r in rows)
    assert all(r["ssn"].startswith("9") for r in rows)  # never-issued area
    assert all(
        r["email"].split("@")[1] in ("example.com", "example.net", "example.org") for r in rows
    )
    coherent = sum(r["full_name"].split()[0].lower() in r["email"] for r in rows)
    assert coherent >= 0.95 * len(rows)  # one persona per row


def test_constraints_hold() -> None:
    rows = execute_spec(spec(), rows=300, seed=SEED).rows
    assert all(r["opened_on"] <= r["closed_on"] for r in rows)
    assert all(r["status"] == ("OVERDRAWN" if r["balance"] < 0 else "ACTIVE") for r in rows)


def test_csv_flattens_nested_fields() -> None:
    s = spec()
    text = rows_to_csv(execute_spec(s, rows=5, seed=SEED).rows, s)
    table = list(csv.reader(io.StringIO(text)))
    assert "address.city" in table[0] and len(table) == 6


def test_unique_and_null_rate() -> None:
    tiny = GeneratorSpec.model_validate(
        {
            "fields": [
                {"name": "d", "generator": "pattern", "params": {"template": "9"}, "unique": True}
            ]
        }
    )
    with pytest.raises(SchemaError) as exc:
        execute_spec(tiny, rows=11, seed=SEED)
    assert exc.value.code == "unique_exhausted"
    sparse = GeneratorSpec.model_validate(
        {
            "fields": [
                {
                    "name": "x",
                    "generator": "integer",
                    "params": {"min": 1, "max": 9},
                    "null_rate": 0.5,
                }
            ]
        }
    )
    result = execute_spec(sparse, rows=1000, seed=SEED)
    assert 400 <= result.nulls <= 600 and sum(r["x"] is None for r in result.rows) == result.nulls


DATES = {"start": "2015-01-01", "end": "2020-12-31"}


@pytest.mark.parametrize("locale", ["en_US", "en_IN", "en_GB", "es_MX", "en_CA"])
def test_every_entity_generator_produces_a_value(locale: str) -> None:
    assert set(ENTITY_TEMPLATES) >= ENTITY_GENERATORS
    every = GeneratorSpec.model_validate(
        {
            "locale": locale,
            "fields": [
                {"name": e.lower(), "generator": "entity", "params": {"entity_type": e}}
                for e in sorted(ENTITY_GENERATORS)
            ],
        }
    )
    rows = execute_spec(every, rows=20, seed=SEED).rows
    for row in rows:
        assert all(isinstance(v, str) and v.strip() and v != REDACTED for v in row.values()), row
        assert validate("IBAN", row["iban"]) is True and validate("CARD_PAN", row["card_pan"])
        assert re.fullmatch(r"\d{4}-\d{2}-\d{2}", row["date_of_birth"])
    if locale == "en_IN":
        assert all(r["phone"].startswith("+91 ") for r in rows)  # the persona's country


def test_ordered_datetimes_compare_as_instants() -> None:
    timed = GeneratorSpec.model_validate(
        {
            "fields": [
                {
                    "name": "a",
                    "generator": "datetime",
                    "params": {"start": "2020-01-01T00:00:00+05:30", "end": "2020-01-02T00:00:00"},
                },
                {
                    "name": "b",
                    "generator": "datetime",
                    "params": {"start": "2020-01-01T00:00:00-08:00", "end": "2020-01-02T00:00:00"},
                },
            ],
            "constraints": [{"kind": "order", "fields": ["a", "b"]}],
        }
    )
    for r in execute_spec(timed, rows=200, seed=SEED).rows:
        assert datetime.fromisoformat(r["a"]) <= datetime.fromisoformat(r["b"])


@pytest.mark.parametrize(
    "bad",
    [
        {"fields": [{"name": "a", "generator": "teleport"}]},
        # sorting moves values between fields: one type and one format only
        {
            "fields": [
                {"name": "a", "generator": "date", "params": DATES},
                {"name": "b", "generator": "date", "params": {**DATES, "format": "%Y%m%d"}},
            ],
            "constraints": [{"kind": "order", "fields": ["a", "b"]}],
        },
        {
            "fields": [
                {"name": "a", "generator": "integer", "params": {"min": 1, "max": 9}},
                {"name": "b", "generator": "decimal", "params": {"min": 1, "max": 9}},
            ],
            "constraints": [{"kind": "order", "fields": ["a", "b"]}],
        },
        {
            "fields": [
                {"name": "a", "generator": "sequence", "params": {"prefix": "C-"}},
                {"name": "b", "generator": "integer", "params": {"min": 1, "max": 9}},
            ],
            "constraints": [{"kind": "order", "fields": ["a", "b"]}],
        },
        # a constraint must not rewrite a unique field
        {
            "fields": [
                {"name": "a", "generator": "integer", "params": {"min": 1, "max": 99}},
                {
                    "name": "b",
                    "generator": "integer",
                    "params": {"min": 1, "max": 99},
                    "unique": True,
                },
            ],
            "constraints": [{"kind": "order", "fields": ["a", "b"]}],
        },
        {
            "fields": [{"name": "a", "generator": "uuid", "unique": True}],
            "constraints": [
                {
                    "kind": "rule",
                    "field": "a",
                    "when": [{"field": "a", "op": "eq", "value": 1, "then": 2}],
                }
            ],
        },
        # nothing that could fail or produce NaN/infinity during execution
        {
            "fields": [
                {"name": "a", "generator": "decimal", "params": {"min": 0, "max": 1, "scale": "x"}}
            ]
        },
        {
            "fields": [
                {"name": "a", "generator": "decimal", "params": {"min": 0, "max": 1, "scale": 99}}
            ]
        },
        {
            "fields": [
                {"name": "a", "generator": "decimal", "params": {"min": float("nan"), "max": 1}}
            ]
        },
        {
            "fields": [
                {"name": "a", "generator": "decimal", "params": {"min": -1e300, "max": 1e300}}
            ]
        },
        {"fields": [{"name": "a", "generator": "lognormal", "params": {"mu": 1000, "sigma": 1}}]},
        {
            "fields": [
                {
                    "name": "a",
                    "generator": "normal",
                    "params": {"mean": 0, "std": 1, "min": 5, "max": 1},
                }
            ]
        },
        {"fields": [{"name": "a", "generator": "choice", "params": {"values": [float("inf")]}}]},
        {"fields": [{"name": "a", "generator": "constant", "params": {"value": float("nan")}}]},
        {
            "fields": [
                {
                    "name": "a",
                    "generator": "entity",
                    "params": {"entity_type": "SSN", "format": "  "},
                }
            ]
        },
        {"fields": [{"name": "a", "generator": "integer", "params": {"min": 5, "max": 1}}]},
        {
            "fields": [
                {
                    "name": "a",
                    "generator": "choice",
                    "params": {"values": ["x", "y"], "weights": [1]},
                }
            ]
        },
        {"fields": [{"name": "a", "generator": "uuid"}, {"name": "a", "generator": "uuid"}]},
        {"fields": [{"name": "a", "generator": "entity", "params": {"entity_type": "PASSWORD"}}]},
        {"fields": [{"name": "1bad name", "generator": "uuid"}]},
        {"fields": [{"name": "a", "generator": "uuid"}, {"name": "a.b", "generator": "uuid"}]},
        {
            "fields": [{"name": "t", "generator": "text"}, {"name": "u", "generator": "uuid"}],
            "constraints": [{"kind": "order", "fields": ["t", "u"]}],
        },
        {
            "fields": [
                {
                    "name": "a",
                    "generator": "integer",
                    "params": {"min": 1, "max": 2, "colour": "red"},
                }
            ]
        },
    ],
)
def test_invalid_specs_are_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        GeneratorSpec.model_validate(bad)


def test_spec_hash_ignores_notes_and_source() -> None:
    assert spec(notes=["x"], source="llm").spec_hash() == spec().spec_hash()
    assert spec(locale="en_IN").spec_hash() != spec().spec_hash()


def test_reversed_date_ranges_name_the_problem() -> None:
    with pytest.raises(ValidationError, match="start must be"):
        GeneratorSpec.model_validate(
            {
                "fields": [
                    {
                        "name": "d",
                        "generator": "date",
                        "params": {"start": "2020-01-02", "end": "2020-01-01"},
                    }
                ]
            }
        )
