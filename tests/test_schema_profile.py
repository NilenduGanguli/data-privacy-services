from __future__ import annotations

import hashlib
import itertools
import json
import math
import random
import re
import time
import unicodedata
from collections.abc import Sequence
from decimal import Decimal, localcontext
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.detect.ner_presidio import PresidioNer
from dps.engine.detect.patterns import Hit
from dps.engine.shapes import value_shape
from dps.engine.synth import schema_gen, schema_profile
from dps.engine.synth.personas import ascii_fold
from dps.engine.synth.schema_gen import (
    FieldSpec,
    GeneratorSpec,
    RuleConstraint,
    SchemaError,
    execute_spec,
)
from dps.engine.synth.schema_profile import (
    ProfileResult,
    heuristic_spec,
    parse_ddl,
    profile_from_ddl,
    profile_from_json_schema,
    profile_from_sample,
    propose_spec,
    render_prompt,
    sample_guard,
    sample_ids,
    verify_against_sample,
)
from dps.engine.verify import (
    SWEEP_MIN_LEN,
    Verification,
    normalise,
    output_strings,
    verify_output,
)
from dps.llm.gateway import LlmGateway, LlmResult

DETECTOR = Detector(DetectorConfig())
SAMPLE: list[dict[str, Any]] = [
    {
        "customer_id": f"CUST-{884120 + i}",
        "full_name": name,
        "email": email,
        "ssn": ssn,
        "status": status,
        "balance": balance,
        "opened": opened,
        "vip": i % 3 == 0,
    }
    for i, (name, email, ssn, status, balance, opened) in enumerate(
        [
            (
                "Ananya Krishnan",
                "ananya.k@example-mail.in",
                "123-45-6789",
                "ACTIVE",
                1200.50,
                "2019-03-14",
            ),
            (
                "Rohan Desai",
                "rohan.d@example-mail.in",
                "234-56-7890",
                "ACTIVE",
                85000.00,
                "2020-07-01",
            ),
            ("Priya Nair", "priya.n@example-mail.in", "345-67-8901", "CLOSED", 15.75, "2018-11-30"),
            (
                "Rajesh Iyer",
                "rajesh.i@example-mail.in",
                "456-78-9012",
                "CLOSED",
                430.00,
                "2021-01-15",
            ),
            ("Meera Rao", "meera.r@example-mail.in", "567-89-0123", "ACTIVE", 999.99, "2017-05-05"),
            (
                "Arjun Mehta",
                "arjun.m@example-mail.in",
                "678-90-1234",
                "FROZEN",
                77.10,
                "2022-09-09",
            ),
        ]
    )
]
DDL = """
-- customers
CREATE TABLE IF NOT EXISTS crm."CUSTOMERS" (
  "CUSTOMER_ID" NUMBER(12,0) NOT NULL,
  full_name VARCHAR2(120 CHAR),
  email varchar(254),
  dob DATE,
  balance NUMBER(12,2),
  status CHAR(1) DEFAULT 'A', /* single-letter code, commas, inside (a, b) */
  created_at TIMESTAMP(6) WITH TIME ZONE,
  CONSTRAINT pk_customers PRIMARY KEY ("CUSTOMER_ID")
);
"""


class RecordingGateway(LlmGateway):
    """A configured provider whose replies are scripted and whose prompts are kept."""

    def __init__(self, reply: str) -> None:
        super().__init__(
            Settings(
                _env_file=None,
                llm_provider="stellar",
                stellar_base_url="https://stellar.test",
                coin_url="https://coin.test",
            )
        )
        self.reply = reply
        self.messages: list[list[dict[str, str]]] = []

    async def complete(
        self,
        messages: list[dict[str, str]],
        *,
        temperature: float = 0.2,
        max_tokens: int = 512,
        purpose: str = "rewrite",
        protected: Sequence[str] = (),  # M5: the gateway's egress-verifier argument
    ) -> LlmResult:
        self.messages.append(messages)
        return LlmResult(text=self.reply, provider="stellar", model="test")


def test_value_shape() -> None:
    assert value_shape("AB-1234") == "XX-9999"
    assert value_shape("ananya.k@x.in") == "xxxxxx.x@x.xx"
    assert len(value_shape("x" * 40)) == 24


def test_json_schema_profile() -> None:
    schema = {
        "type": "object",
        "required": ["id"],
        "properties": {
            "id": {"type": "integer", "minimum": 1, "maximum": 99999},
            "email": {"type": "string", "format": "email"},
            "dob": {"type": "string", "format": "date"},
            "status": {"type": "string", "enum": ["ACTIVE", "CLOSED"]},
            "amount": {"type": ["number", "null"], "minimum": 0, "maximum": 25000},
            "address": {
                "type": "object",
                "properties": {"city": {"type": "string", "maxLength": 40}},
            },
            "tags": {"type": "array"},
        },
    }
    result = profile_from_json_schema(schema)
    by = {f.name: f for f in result.profile.fields}
    assert set(by) == {"id", "email", "dob", "status", "amount", "address.city"}
    assert by["email"].entity_type == "EMAIL" and by["dob"].entity_type == "DATE_OF_BIRTH"
    assert (
        by["status"].enum == ["ACTIVE", "CLOSED"]
        and by["id"].nullable is False
        and by["amount"].nullable
    )
    assert by["amount"].magnitude == {
        "min_order": 0,
        "max_order": 4,
        "negative": False,
        "integer": False,
    }
    with pytest.raises(SchemaError):
        profile_from_json_schema({"type": "array"})


def test_ddl_parsing_and_profile() -> None:
    table, columns = parse_ddl(DDL)
    assert table == "CUSTOMERS"
    by = {c.name: c for c in columns}
    assert list(by) == [
        "CUSTOMER_ID",
        "full_name",
        "email",
        "dob",
        "balance",
        "status",
        "created_at",
    ]
    assert by["CUSTOMER_ID"].primary_key and not by["CUSTOMER_ID"].nullable
    assert (by["full_name"].length, by["balance"].scale, by["created_at"].type_name) == (
        120,
        2,
        "timestamp",
    )
    profile = {f.name: f for f in profile_from_ddl(DDL).profile.fields}
    assert profile["CUSTOMER_ID"].type == "integer" and profile["balance"].type == "number"
    assert profile["dob"].type == "date" and profile["created_at"].type == "datetime"
    assert profile["full_name"].entity_type == "PERSON_NAME"
    for bad in ("DROP TABLE x", "CREATE TABLE t (a int"):
        with pytest.raises(SchemaError) as exc:
            parse_ddl(bad)
        assert exc.value.code == "ddl_unparsed"


def test_sample_profile_is_llm_safe() -> None:
    result = profile_from_sample(SAMPLE, DETECTOR)
    by = {f.name: f for f in result.profile.fields}
    assert by["full_name"].entity_type == "PERSON_NAME" and by["email"].entity_type == "EMAIL"
    assert by["ssn"].entity_type == "SSN" and by["ssn"].entity_source in ("structural", "values")
    assert by["status"].enum is None  # FROZEN occurs once: not a safe label set
    assert by["customer_id"].shapes == ["XXXX-999999"]
    prompt = normalise(render_prompt(result.profile)[1]["content"])
    for row in SAMPLE:
        for key in ("full_name", "email", "ssn", "customer_id"):
            assert normalise(str(row[key])) not in prompt
    assert "120050" not in prompt and "85000" not in prompt


def test_safe_enums_are_kept() -> None:
    rows = [dict(r, status=("ACTIVE" if i % 2 else "CLOSED")) for i, r in enumerate(SAMPLE)]
    by = {f.name: f for f in profile_from_sample(rows, DETECTOR).profile.fields}
    assert by["status"].enum == ["ACTIVE", "CLOSED"]


def test_sensitive_field_names_are_aliased_in_the_prompt_only() -> None:
    result = profile_from_sample(
        [{"ananya.k@example-mail.in": "x1"}, {"ananya.k@example-mail.in": "x2"}], DETECTOR
    )
    assert [f.name for f in result.profile.fields] == ["ananya.k_example_mail.in"]
    sent = render_prompt(result.profile)[1]["content"]
    assert [f["name"] for f in json.loads(sent)["fields"]] == ["field_1"]
    assert "ananya" not in sent and "example" not in sent


async def test_heuristic_spec_from_sample_executes_without_leaks() -> None:
    result = profile_from_sample(SAMPLE, DETECTOR)
    spec, info = await propose_spec(result, None, locale="en_IN")
    assert (
        info == {"llm_calls": 0, "source": "heuristic", "fallback": None}
        and spec.source == "heuristic"
    )
    by = {f.name: f for f in spec.fields}
    assert by["full_name"].generator == "entity" and by["ssn"].params == {"entity_type": "SSN"}
    assert (
        by["customer_id"].generator == "pattern"
        and by["customer_id"].params["template"] == "AAAA-999999"
    )
    assert by["balance"].generator in ("decimal", "lognormal") and by["vip"].generator == "boolean"
    assert by["opened"].generator == "date"
    rows = execute_spec(spec, rows=50, seed=bytes(32)).rows
    assert verify_against_sample(rows, result).passed


async def test_llm_proposal_sees_the_profile_only() -> None:
    reply = json.dumps(
        {
            "fields": [
                {
                    "name": "balance",
                    "generator": "lognormal",
                    "params": {"mu": 6.5, "sigma": 1.2, "min": 0, "scale": 2},
                },
                {"name": "ssn", "generator": "integer", "params": {"min": 1, "max": 9}},
            ],
            "constraints": [],
            "notes": ["balances are right-skewed"],
        }
    )
    fence = "`" * 3  # a fenced reply, as models often send
    gateway = RecordingGateway(f"{fence}json\n{reply}\n{fence}")
    result = profile_from_sample(SAMPLE, DETECTOR)
    spec, info = await propose_spec(result, gateway, locale="en_IN")
    assert info["source"] == "llm" and info["llm_calls"] == 1 and spec.source == "llm"
    by = {f.name: f for f in spec.fields}
    assert by["balance"].generator == "lognormal"
    assert by["ssn"].generator == "entity"  # a model cannot downgrade a detected SSN
    sent = normalise(json.dumps(gateway.messages))
    assert all(
        normalise(r["email"]) not in sent and normalise(r["ssn"]) not in sent for r in SAMPLE
    )


async def test_invalid_llm_replies_fall_back() -> None:
    result = profile_from_sample(SAMPLE, DETECTOR)
    spec, info = await propose_spec(result, RecordingGateway("not json at all"), locale="en_US")
    assert spec.source == "heuristic" and info["fallback"] == "llm_invalid_spec"


async def test_stub_provider_never_calls() -> None:
    gateway = LlmGateway(Settings(_env_file=None, llm_provider="stub"))
    _, info = await propose_spec(profile_from_sample(SAMPLE, DETECTOR), gateway, locale="en_US")
    assert info["llm_calls"] == 0 and info["source"] == "heuristic"


async def test_leak_guard_blocks_the_call() -> None:
    rows = [
        {"full_name": "Status", "status": "ACTIVE"},
        {"full_name": "Status", "status": "CLOSED"},
    ]
    gateway = RecordingGateway("{}")
    spec, info = await propose_spec(profile_from_sample(rows, DETECTOR), gateway, locale="en_US")
    assert info["fallback"] == "leak_guard" and info["llm_calls"] == 0 and gateway.messages == []
    assert spec.source == "heuristic"


# -- every profile yields a spec that validates and executes (no 500s) ----------------------------
def _sample(rows: list[dict[str, Any]]) -> ProfileResult:
    return profile_from_sample(rows, DETECTOR)


def _schema(properties: dict[str, Any]) -> ProfileResult:
    return profile_from_json_schema({"type": "object", "properties": properties})


@pytest.mark.parametrize(
    "make",
    [
        # names that collide after cleaning, nest under a scalar, or nest too deep
        lambda: _sample([{"a b": "x", "a_b": "y"}]),
        lambda: _sample([{"a": "x"}, {"a": {"b": "y"}}]),
        lambda: _sample([{"a.b.c.d.e": "x"}]),
        lambda: _schema({"a-b": {"type": "string"}, "a_b": {"type": "string"}}),
        # numbers no generator accepts
        lambda: _sample([{"a": 10**400}]),
        lambda: _sample([{"a": float("nan")}, {"a": 1.5}]),
        lambda: _sample([{"a": 1e-300}, {"a": 1e-300}, {"a": 1.0}, {"a": 1.0}, {"a": 1e300}]),
        lambda: _schema({"a": {"type": "number", "minimum": 0, "maximum": 1e308}}),
        lambda: profile_from_ddl("create table t (a numeric(1000,0), b numeric(20,12))"),
        # dates and lengths outside what the generators accept
        lambda: _sample([{"d": "0000-01-01"}, {"d": "0005-02-02"}]),
        lambda: _schema({"a": {"type": "string", "maxLength": 5, "minLength": "x"}}),
        lambda: _schema({"o": {"type": "object", "required": [{}], "properties": {"a": {}}}}),
        # quoted text that looks like a comment or a parenthesis
        lambda: profile_from_ddl("create table t (a varchar(5) default '--', b int)"),
        lambda: profile_from_ddl("create table t (a varchar(5) default '(', b int) /* ) */"),
    ],
)
def test_profiles_always_yield_valid_specs(make: Any) -> None:
    result = make()
    spec = heuristic_spec(result, locale="en_US")
    names = [f.name for f in spec.fields]
    assert len(names) == len(set(names)) == len(result.profile.fields)
    assert len(execute_spec(spec, rows=5, seed=bytes(32)).rows) == 5


def test_ddl_comments_and_quotes() -> None:
    _, columns = parse_ddl(
        "create table t (a varchar(5) default '--', /* b int, */ c int -- d int,\n, e int)"
    )
    assert [c.name for c in columns] == ["a", "c", "e"]


def test_unterminated_comments_parse_in_linear_time() -> None:
    started = time.perf_counter()
    with pytest.raises(SchemaError):
        parse_ddl("create table t (a int " + "/*a" * 30_000)  # 90 000 characters
    assert time.perf_counter() - started < 1.0  # the regex strip took ~4.6 s on this input


@pytest.mark.parametrize(
    "make",
    [
        lambda: profile_from_ddl(
            "create table t (" + ", ".join(f"c{i} int" for i in range(201)) + ")"
        ),
        lambda: _sample([{f"c{i}": i for i in range(201)}]),
        lambda: _schema({f"c{i}": {"type": "integer"} for i in range(201)}),
    ],
)
def test_too_many_fields_is_a_coded_refusal(make: Any) -> None:
    with pytest.raises(SchemaError) as exc:
        make()
    assert exc.value.code == "schema_unsupported"


@pytest.mark.parametrize(
    "reply",
    [
        '{"fields": 5}',
        '{"fields": [], "notes": 7}',
        "{}",
        "[1, 2]",
        '{"fields": [{"name": "not_a_field", "generator": "uuid"}]}',
        '{"a": ' * 50_000 + "1" + "}" * 50_000,  # json.loads raises RecursionError
    ],
    ids=["fields-int", "notes-int", "empty", "array", "unknown-field", "deep"],
)
async def test_unusable_llm_replies_fall_back(reply: str) -> None:
    spec, info = await propose_spec(
        profile_from_sample(SAMPLE, DETECTOR), RecordingGateway(reply), locale="en_US"
    )
    assert spec.source == "heuristic" and info["fallback"] == "llm_invalid_spec"


async def test_llm_notes_of_the_wrong_type_are_ignored() -> None:
    reply = json.dumps(
        {"fields": [{"name": "vip", "generator": "boolean", "params": {"p_true": 0.1}}], "notes": 7}
    )
    spec, info = await propose_spec(
        profile_from_sample(SAMPLE, DETECTOR), RecordingGateway(reply), locale="en_US"
    )
    assert info["source"] == "llm" and spec.notes == []
    assert {f.name: f for f in spec.fields}["vip"].params == {"p_true": 0.1}


def test_gender_and_nationality_are_not_swept() -> None:
    rows = [
        dict(r, gender=("Female" if i % 2 else "Male"), nationality="Indian")
        for i, r in enumerate(SAMPLE)
    ]
    result = profile_from_sample(rows, DETECTOR)
    by = {f.name: f for f in result.profile.fields}
    assert (by["gender"].entity_type, by["nationality"].entity_type) == ("GENDER", "NATIONALITY")
    spec = heuristic_spec(result, locale="en_IN")
    generated = execute_spec(spec, rows=50, seed=bytes(32)).rows
    assert {r["gender"] for r in generated} <= {"Female", "Male"}
    assert any(r["nationality"] == "Indian" for r in generated)
    assert verify_against_sample(generated, result).passed


# -- fix round 1 (review M1-G4-review-r1) ---------------------------------------------------------
B1_ROWS: list[dict[str, Any]] = [
    {
        "ssn": ssn,
        "card": card,
        "opened": opened,
        "closed": closed,
        "balance": balance,
        "fee": fee,
        "lo": lo,
        "hi": hi,
    }
    for ssn, card, opened, closed, balance, fee, lo, hi in [
        ("123-45-6789", "4111 1111 1111 1111", "2019-03-14", "2021-05-01", 10.5, 15, 120, 560),
        ("234-56-7890", "5500 0000 0000 0004", "2020-07-01", "2022-01-09", 20.0, 25, 310, 870),
        ("345-67-8901", "4012 8888 8888 1881", "2018-11-30", "2023-08-15", 30.25, 30, 240, 990),
    ]
]


async def test_llm_constraints_cannot_write_or_reorder_sensitive_fields() -> None:
    """B1: a model rule that writes a detected field, or an order that moves one, is dropped.

    Before the fix both rules were kept and every row held ``ssn = 078-05-1120`` (an issued SSN)
    and ``card = 4111111111111111``.
    """
    result = profile_from_sample(B1_ROWS, DETECTOR)
    entities = {f.name: f.entity_type for f in result.profile.fields}
    assert (entities["ssn"], entities["card"], entities["opened"]) == ("SSN", "CARD_PAN", "DATE")
    assert {entities[n] for n in ("balance", "fee", "lo", "hi")} == {None}
    always = [{"field": "balance", "op": "ge", "value": 0, "then": "078-05-1120"}]
    reply = json.dumps(
        {
            "fields": [
                {
                    "name": "balance",
                    "generator": "decimal",
                    "params": {"min": 0, "max": 9, "scale": 2},
                }
            ],
            "constraints": [
                {"kind": "rule", "field": "ssn", "when": always, "else": "078-05-1120"},
                {"kind": "rule", "field": "card", "when": always, "else": "4111111111111111"},
                {"kind": "order", "fields": ["opened", "closed"]},
                {"kind": "rule", "field": "fee", "when": [dict(always[0], then=0)], "else": 5},
                {"kind": "order", "fields": ["lo", "hi"]},
            ],
        }
    )
    spec, info = await propose_spec(result, RecordingGateway(reply), locale="en_US")
    assert info["source"] == "llm" and spec.source == "llm"
    kept = [
        ("rule", c.field) if isinstance(c, RuleConstraint) else ("order", tuple(c.fields))
        for c in spec.constraints
    ]
    assert kept == [("rule", "fee"), ("order", ("lo", "hi"))]
    assert any(note.startswith("3 llm constraint(s) dropped") for note in spec.notes)
    rows = execute_spec(spec, rows=40, seed=bytes(32)).rows
    ssns = {r["ssn"] for r in rows}
    assert len(ssns) == 40 and "078-05-1120" not in ssns
    assert "4111111111111111" not in {str(r["card"]).replace(" ", "") for r in rows}
    assert all(r["fee"] == 0 and r["lo"] <= r["hi"] for r in rows)
    assert verify_against_sample(rows, result).passed


async def test_sample_labels_and_data_keys_never_reach_the_model() -> None:
    """B2: without NER a low-cardinality name column is categorical, and a key can be a name.

    Before the fix the prompt carried the labels (``Ananya`` … ``Krishnan``) and the key
    ``Ananya_Krishnan.x``, while the leak guard had nothing to check them against.
    """
    first = ["Ananya", "Rohan", "Priya", "Rajesh"]
    rows = [
        {
            "owner": first[i % 4],
            "rm": f"{first[i % 4]} Krishnan",
            "segment": "WEALTH" if i % 2 else "RETAIL",
            "Ananya Krishnan": {"x": i},
            "score": 10 * i + 7,
        }
        for i in range(8)
    ]
    result = profile_from_sample(rows, DETECTOR)  # the default detector: NER off
    by = {f.name: f for f in result.profile.fields}
    assert by["owner"].entity_type is None and by["owner"].enum == sorted(first)
    assert by["rm"].enum is not None and by["segment"].enum == ["RETAIL", "WEALTH"]
    assert by["score"].entity_type is None and by["Ananya_Krishnan.x"].entity_type is None
    reply = json.dumps(
        {
            "fields": [
                {"name": "owner", "generator": "choice", "params": {"values": ["Zed"]}},
                {"name": "field_4", "generator": "integer", "params": {"min": 1, "max": 9}},
            ],
            "constraints": [
                {
                    "kind": "rule",
                    "field": "segment",
                    "when": [{"field": "score", "op": "ge", "value": 50, "then": "VIP"}],
                    "else": "STD",
                },
                {
                    "kind": "rule",
                    "field": "score",
                    "when": [{"field": "field_4", "op": "ge", "value": 5, "then": 0}],
                    "else": 1,
                },
            ],
        }
    )
    gateway = RecordingGateway(reply)
    spec, info = await propose_spec(result, gateway, locale="en_IN")
    assert info["source"] == "llm" and len(gateway.messages) == 1
    sent = normalise(json.dumps(gateway.messages))
    assert [w for w in (*first, "Krishnan", "RETAIL", "WEALTH") if normalise(w) in sent] == []
    prompt = {f["name"]: f for f in json.loads(gateway.messages[0][1]["content"])["fields"]}
    assert list(prompt) == ["owner", "rm", "segment", "field_4", "score"]
    assert prompt["owner"]["enum_size"] == 4 and "enum" not in prompt["owner"]
    fields = {f.name: f for f in spec.fields}
    assert list(fields) == ["owner", "rm", "segment", "Ananya_Krishnan.x", "score"]
    # categorical fields keep DPS's choice over the sampled labels; the model's alias maps back
    assert fields["owner"].generator == "choice" and fields["owner"].params["values"] == sorted(
        first
    )
    assert fields["Ananya_Krishnan.x"].params == {"min": 1, "max": 9}
    assert [c.model_dump(by_alias=True) for c in spec.constraints] == [
        {
            "kind": "rule",
            "field": "score",
            "when": [{"field": "Ananya_Krishnan.x", "op": "ge", "value": 5, "then": 0}],
            "else": 1,
        }
    ]
    assert any(note.startswith("1 llm constraint(s) dropped") for note in spec.notes)
    generated = execute_spec(spec, rows=20, seed=bytes(32)).rows
    assert {r["segment"] for r in generated} <= {"RETAIL", "WEALTH"}


def test_json_schema_labels_are_counted_not_sent() -> None:
    result = profile_from_json_schema(
        {"type": "object", "properties": {"tier": {"type": "string", "enum": ["Gold", "Platinum"]}}}
    )
    sent = render_prompt(result.profile)[1]["content"]
    assert json.loads(sent)["fields"][0]["enum_size"] == 2
    assert "Gold" not in sent and "Platinum" not in sent


# -- live run with NER and Gemini (generator spec from a sample) ----------------------------------
LIVE_SAMPLE: list[dict[str, Any]] = [
    {
        "cust_id": 1001 + i,
        "full_name": name,
        "email": email,
        "city": city,
        "segment": segment,
        "balance": balance,
        "opened": opened,
    }
    for i, (name, email, city, segment, balance, opened) in enumerate(
        [
            ("Arjun Mehta", "arjun.mehta@example.in", "Pune", "retail", 48250.50, "2019-04-11"),
            ("Priya Nair", "priya.nair@example.in", "Kochi", "premier", 812300.00, "2016-09-02"),
            (
                "Kavya Raghunathan",
                "kavya.r@example.in",
                "Chennai",
                "retail",
                1520.75,
                "2022-01-19",
            ),
            (
                "Rohan Kulkarni",
                "rohan.k@example.in",
                "Mumbai",
                "business",
                2390000.00,
                "2014-06-30",
            ),
        ]
    )
]


class SpacyLikeNer:
    """Presidio over spaCy as the live run saw it, without the model: a bare run of 4 to 12 digits
    or an ISO date is a DATE (spaCy reads ``1001`` as a year), and ``places`` are LOCATIONs (it
    read the key ``cust_id`` as one)."""

    languages = ("en",)

    def __init__(self, places: Sequence[str] = ()) -> None:
        self.places = set(places)

    def analyze(self, text: str, language: str) -> list[Hit]:
        if re.fullmatch(r"\d{4,12}|\d{4}-\d{2}-\d{2}", text):
            return [Hit("DATE", 0, len(text), 0.85, "ner:presidio", "ner:DATE_TIME:0.85")]
        if text in self.places:
            return [Hit("LOCATION", 0, len(text), 0.85, "ner:presidio", "ner:LOCATION:0.85")]
        return []


NER_DETECTOR = Detector(
    DetectorConfig(locale="en_IN", ner_enabled=True), ner=SpacyLikeNer(places=["cust_id"])
)


class ProviderSeamGateway(LlmGateway):
    """The real ``complete`` — availability, the strict egress verifier, counters — with only the
    provider call scripted; ``sent`` keeps every prompt the verifier let out."""

    def __init__(self, reply: str) -> None:
        super().__init__(
            Settings(
                _env_file=None,
                llm_provider="stellar",
                stellar_base_url="https://stellar.test",
                coin_url="https://coin.test",
                llm_egress_check="strict",
            )
        )
        self.reply = reply
        self.sent: list[list[dict[str, str]]] = []

    async def _stellar(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> LlmResult:
        self.sent.append(messages)
        return LlmResult(text=self.reply, provider="stellar", model="test")


def test_ner_does_not_read_json_numbers_as_dates() -> None:
    """Live run: Presidio's DATE_TIME read the integer IDs 1001..1004 as years, so ``cust_id``
    was profiled ``DATE`` from value votes. A JSON number gets no NER-only vote unless its digits
    spell a calendar date; pattern hits on numbers and NER on date strings still count."""
    cards = [4111111111111111, 5500000000000004, 4012888888881881, 4000056655665556]
    rows = [
        {
            "ref": 1001 + i,
            "code": 12345678 + i,
            "posted": 20190410 + i,
            "opened": f"2019-04-1{i}",
            "ref_b": card,
        }
        for i, card in enumerate(cards)
    ]
    by = {f.name: f for f in profile_from_sample(rows, NER_DETECTOR).profile.fields}
    assert (by["ref"].type, by["ref"].entity_type) == ("integer", None)
    assert by["code"].entity_type is None  # eight digits, but no calendar date
    assert (by["posted"].type, by["posted"].entity_type) == ("integer", "DATE")  # yyyymmdd
    assert (by["opened"].entity_type, by["opened"].entity_source) == ("DATE", "values")
    assert by["ref_b"].entity_type == "CARD_PAN"  # the pattern tier still reads numbers


async def test_flagged_keys_are_aliased_in_the_prompt_only() -> None:
    """Live run: Presidio read the key ``cust_id`` as a LOCATION, so the profile, the spec and the
    generated rows named the column ``field_1``. The alias belongs in the prompt only: here the
    flagged key and a key that is data (an e-mail address) reach the model as ``field_N``, the
    reply's aliases map back, and the caller's names come out."""
    email_key = "arjun.mehta@example.in"
    rows = [{"cust_id": 1001 + i, email_key: i % 3, "score": 10 * i + 7} for i in range(6)]
    result = profile_from_sample(rows, NER_DETECTOR)
    names = ["cust_id", "arjun.mehta_example.in", "score"]
    assert [f.name for f in result.profile.fields] == names
    prompt = json.loads(render_prompt(result.profile)[1]["content"])
    alias = {real: f["name"] for real, f in zip(names, prompt["fields"], strict=True)}
    assert list(alias.values()) == ["field_1", "field_2", "score"]
    reply = json.dumps(
        {
            "fields": [
                {"name": alias["cust_id"], "generator": "integer", "params": {"min": 1, "max": 99}},
                {"name": alias[names[1]], "generator": "integer", "params": {"min": 0, "max": 2}},
            ],
            "constraints": [{"kind": "order", "fields": [alias[names[1]], "score"]}],
        }
    )
    gateway = ProviderSeamGateway(reply)
    spec, info = await propose_spec(result, gateway, locale="en_IN")
    assert info["source"] == "llm" and len(gateway.sent) == 1
    sent = normalise(json.dumps(gateway.sent))
    assert [w for w in ("custid", "arjun", "mehta", "example") if w in sent] == []
    assert [f.name for f in spec.fields] == names
    assert spec.fields[0].params == {"min": 1, "max": 99}
    assert [c.model_dump(by_alias=True)["fields"] for c in spec.constraints] == [names[1:]]
    generated = execute_spec(spec, rows=20, seed=bytes(32)).rows
    assert all(set(r) == {"cust_id", "arjun", "score"} for r in generated)
    assert all(r["arjun"]["mehta_example"]["in"] <= r["score"] for r in generated)
    assert verify_against_sample(generated, result).passed


def test_collapsed_ranges_span_orders_of_magnitude() -> None:
    """Live run: 1001..1004 rounded to two significant figures gave ``integer{min: 1000, max:
    1000}`` and every row was 1000. A sampled range that rounding collapses spans its orders of
    magnitude (no sampled value in the spec); declared bounds stay as declared."""
    rows = [
        {"ref": 1001 + i, "debit": -1001 - i, "amount": 1001.5 + i, "score": 553 + 88 * i}
        for i in range(4)
    ]
    spec = heuristic_spec(profile_from_sample(rows, DETECTOR), locale="en_US")
    by = {f.name: f for f in spec.fields}
    assert by["ref"].params == {"min": 1000, "max": 9999}
    assert by["debit"].params == {"min": -9999, "max": -1000}
    assert by["amount"].generator == "decimal"
    assert (by["amount"].params["min"], by["amount"].params["max"]) == (1000.0, 10000.0)
    assert by["score"].params == {"min": 550, "max": 820}  # a range survives rounding: as before
    dumped = json.dumps(spec.model_dump(mode="json"))
    assert [v for v in range(1001, 1005) if str(v) in dumped] == []
    generated = execute_spec(spec, rows=50, seed=bytes(32)).rows
    assert len({r["ref"] for r in generated}) > 1 and len({r["amount"] for r in generated}) > 1
    declared = heuristic_spec(
        _schema({"n": {"type": "integer", "minimum": 1000, "maximum": 1049}}), locale="en_US"
    )
    assert declared.fields[0].params == {"min": 1000, "max": 1049}


async def test_llm_choice_of_shapes_or_placeholders_is_not_accepted() -> None:
    """Live run: ``segment`` (three labels in four rows, so not categorical) came back from the
    model as a ``choice`` over value shapes, and every generated segment was ``xxxxxxxx``. The
    model never sees labels: a categorical field keeps DPS's labels, and on any other field a
    choice of shapes or placeholders is ignored with a note. A choice of real-looking labels the
    model knows for such a field is still taken."""
    rows = [
        {"segment": seg, "tier": tier, "channel": channel, "product": product, "full_name": name}
        for seg, tier, channel, product, name in [
            ("retail", "GOLD", "branch", "savings", "Arjun Mehta"),
            ("premier", "GOLD", "online", "current", "Priya Nair"),
            ("retail", "BASIC", "mobile", "savings", "Kavya Raghunathan"),
            ("business", "BASIC", "branch", "deposit", "Rohan Kulkarni"),
        ]
    ]
    result = profile_from_sample(rows, DETECTOR)
    profiled = {f.name: f for f in result.profile.fields}
    assert profiled["segment"].enum is None and profiled["tier"].enum == ["BASIC", "GOLD"]
    labels = ["Savings", "Current", "Fixed Deposit"]
    reply = json.dumps(
        {
            "fields": [
                {
                    "name": "segment",
                    "generator": "choice",
                    "params": {"values": profiled["segment"].shapes},
                },
                {"name": "tier", "generator": "choice", "params": {"values": ["xxxx", "xxxxx"]}},
                {
                    "name": "channel",
                    "generator": "choice",
                    "params": {"values": ["<label_1>", "<label_2>"]},
                },
                {"name": "product", "generator": "choice", "params": {"values": labels}},
                {"name": "full_name", "generator": "choice", "params": {"values": ["Xxxxx Xxxxx"]}},
            ],
            "notes": ["segment looks categorical"],
        }
    )
    gateway = ProviderSeamGateway(reply)
    spec, info = await propose_spec(result, gateway, locale="en_IN")
    assert info == {"llm_calls": 1, "source": "llm", "fallback": None} and len(gateway.sent) == 1
    sent = normalise(json.dumps(gateway.sent))
    values = {str(v) for row in rows for v in row.values()}
    assert [v for v in values if normalise(v) in sent] == []
    by = {f.name: f for f in spec.fields}
    heuristic = {f.name: f for f in heuristic_spec(result, locale="en_IN").fields}
    assert by["segment"] == heuristic["segment"] and by["segment"].generator == "pattern"
    assert by["channel"] == heuristic["channel"]
    assert by["tier"].generator == "choice" and by["tier"].params["values"] == ["BASIC", "GOLD"]
    assert by["product"].generator == "choice" and by["product"].params["values"] == labels
    assert by["full_name"].generator == "entity"
    assert any(note.startswith("2 llm choice generator(s) ignored") for note in spec.notes)
    generated = execute_spec(spec, rows=30, seed=bytes(32)).rows
    shape = re.compile(r"[xX9\W_]+")
    assert not [r for r in generated if shape.fullmatch(r["segment"] + r["channel"])]
    assert {r["tier"] for r in generated} <= {"BASIC", "GOLD"}


@pytest.mark.ner
def test_live_sample_with_presidio_keeps_names_and_types() -> None:
    """The live run's sample through the real Presidio: the caller's names, ``cust_id`` an
    integer (not a date) over 1000..9999, and none of the sample in the prompt."""
    pytest.importorskip("presidio_analyzer")
    spacy = pytest.importorskip("spacy")
    if not any(spacy.util.is_package(m) for m in ("en_core_web_lg", "en_core_web_sm")):
        pytest.skip("no spaCy English model installed")
    detector = Detector(
        DetectorConfig(locale="en_IN", ner_enabled=True), ner=PresidioNer(languages=("en",))
    )
    result = profile_from_sample(LIVE_SAMPLE, detector)
    by = {f.name: f for f in result.profile.fields}
    assert list(by) == list(LIVE_SAMPLE[0])
    assert by["cust_id"].type == "integer" and by["cust_id"].entity_type in (None, "CUSTOMER_ID")
    spec = {f.name: f for f in heuristic_spec(result, locale="en_IN").fields}
    assert spec["cust_id"].generator == "integer"
    assert spec["cust_id"].params == {"min": 1000, "max": 9999}
    sent = normalise(render_prompt(result.profile)[1]["content"])
    assert [v for row in LIVE_SAMPLE for v in map(str, row.values()) if normalise(v) in sent] == []


# -- the sample sweep at volume (S8): whole digit runs, redraws, exempt types, exact bounds ------
#: Values that hold a sampled ID of LIVE_SAMPLE (1001..1004) only inside a longer run of digits:
#: a date, an amount, a code. None of them is a copy.
ACCIDENTAL: list[Any] = ["2010-01-04", "2001-10-03", 21001.5, 1001.25, "x10012", 41003, "1001-2020"]
#: Copies: an ID (a value, an integral float or numeric string, full-width digits, inside a code
#: or an e-mail, next to another ID across a space or any separator that never joins the digits of
#: a date, an amount or a range: ``;``, ``|``, ``_``, quotes, brackets, ``#``, ``+``, a zero-width
#: space, or a comma before anything but a group of two or three digits, as in a CSV cell or a
#: compact JSON list), a sampled date (also inside a datetime, after a ``T`` or before a space or an
#: ``_``, or in a ``;`` list), a sampled name (re-cased, inside text) and an e-mail.
LEAKS: list[Any] = [
    1001,
    1002.0,
    "１００３",
    "１００４．００",  # a full-width whole number (review r2, B2)
    "CUST-1003",
    "a1004@example.com",
    "1001, 1002",
    "1001,1002",  # a CSV list in a cell (review r2, B1)
    "[1001,1002]",  # a compact JSON list
    "1001,1002.00",  # not a grouped whole number: a list
    "1001,10\u0966\u0968",  # 1001,1002 with Devanagari digits: a digit counts in any script
    "1003 1004",
    "1001.00",
    "1,002.00",
    "1001;1002",
    "1001|1002",
    '["1001","1002"]',
    "TXN_4821_1003",
    "1001_1002",
    "(1001)(1002)",
    "#1001#1002",
    "1001+1002",
    "1003\u200b1004",
    "2019-04-11",
    "2016-09-02T08:00:00+00:00",
    "2016-09-02 10:15:00",
    "2016-09-02;2019-04-11",
    "2016-09-02_10:15:00",
    "[2016-09-02]10:15",
    "met ARJUN MEHTA today",
    "priya.nair@example.in",
]
#: Joined like a date, an amount or a range (``-`` ``/`` ``.`` ``:``, also full-width, and a comma
#: before a group of exactly two or three digits: thousands, lakh, a decimal comma), so not read as
#: two IDs: documented trade-offs, not refused.
JOINED: list[Any] = [
    "1003-07",
    "1003/1004",
    "１００１－２０２０",
    "10,01,002",  # lakh grouping
    "1001,25",  # a decimal comma
    "1001,250.50",  # thousands
    "48,250.50",
    "1001,\u0968\u096b",  # 1001,25 with Devanagari digits
]


def _seeds(count: int) -> list[bytes]:
    return [hashlib.sha256(f"s8-run-{i}".encode()).digest() for i in range(count)]


def _one(name: str = "v", **field: Any) -> GeneratorSpec:
    return GeneratorSpec(name="t", locale="en_IN", fields=[FieldSpec(name=name, **field)])


def test_the_sample_sweep_matches_numbers_as_whole_digit_runs() -> None:
    """Live run at 100 rows: a sampled ID 1001 was "found" inside generated dates (2010-01-04) and
    amounts (21001.5). A short all-digit original now counts only from a break to a break (the
    edge of a run of digits, or whitespace: review r1 found ``1001, 1002`` and a space-separated
    datetime let through by whole runs alone; or any other character that never joins the digits
    of a date, an amount or a range: its re-check found ``1001;1002``, ``["1001","1002"]`` and
    ``TXN_4821_1003`` let through; review r2 found the lists ``1001,1002`` and ``[1001,1002]``, so a
    comma joins only before a group of exactly two or three digits, and a full-width ``1004.00``,
    so a whole number is read after NFKC); every copy still fails, and the guard the executor
    steers with flags exactly what the sweep refuses."""
    result = profile_from_sample(LIVE_SAMPLE, DETECTOR)
    assert [f.entity_type for f in result.profile.fields][0] == "CUSTOMER_ID"
    assert verify_against_sample([{"v": v} for v in [*ACCIDENTAL, *JOINED]], result).passed
    for value in LEAKS:
        assert not verify_against_sample([{"v": value}], result).passed, value
    guard = sample_guard(result)
    assert guard is not None
    assert [v for v in [*ACCIDENTAL, *JOINED] if guard(v)] == []
    assert [v for v in LEAKS if not guard(v)] == []


def test_the_guard_flags_exactly_what_the_sweep_refuses() -> None:
    """The executor keeps a value the guard flags only when it cannot draw another, and then the
    sweep must refuse the run: the two must agree on every value, copy or not."""
    result = profile_from_sample(LIVE_SAMPLE, DETECTOR)
    guard = sample_guard(result)
    assert guard is not None
    spec = GeneratorSpec(
        name="t",
        locale="en_IN",
        fields=[
            FieldSpec(name="a", generator="integer", params={"min": 990, "max": 1010}),
            FieldSpec(name="b", generator="decimal", params={"min": 0, "max": 2000, "scale": 1}),
            FieldSpec(
                name="c", generator="date", params={"start": "2001-01-01", "end": "2019-12-31"}
            ),
            FieldSpec(name="d", generator="pattern", params={"template": "X9a9X99"}),
            FieldSpec(name="e", generator="entity", params={"entity_type": "EMAIL"}),
            FieldSpec(name="f", generator="choice", params={"values": [*LEAKS, *ACCIDENTAL]}),
        ],
    )
    values = [v for row in execute_spec(spec, rows=400, seed=bytes(32)).rows for v in row.values()]
    flagged = [guard(v) for v in values]
    refused = [not verify_against_sample([{"v": v}], result).passed for v in values]
    assert flagged == refused and any(flagged) and not all(flagged)


def _groups_digits(text: str, comma: int) -> bool:
    """True when the comma at ``text[comma]`` comes before exactly two or three digits (of any
    script) and then no digit: it groups a number (``1,001``, ``10,01,002``, ``1001,25``)."""
    end = comma + 1
    while end < len(text) and text[end].isdecimal():
        end += 1
    return end - comma - 1 in (2, 3)


def _naive_found(needle: str, raw: str) -> bool:
    """The sample sweep's rule the slow way, for one original in one output string read after
    NFKC: a string that is a whole number (``1,001.00``, every comma before a group of two or three
    digits) is read as it; a text original, or an all-digit one of nine or more digits, is a
    substring of the normalised string (M0); a shorter all-digit one must start and end at a break,
    the edge of a run of digits or a character that is neither a letter, a digit nor one of ``-``
    ``/`` ``.`` ``:``, nor a comma that groups a number (:func:`_groups_digits`)."""
    text = unicodedata.normalize("NFKC", raw)
    body = text.strip()
    sign = body[:1] if body[:1] in ("+", "-") else ""
    whole, dot, zeros = body[len(sign) :].partition(".")
    groups = whole.split(",")
    if (
        dot
        and zeros
        and set(zeros) == {"0"}
        and groups[0].isdecimal()
        and all(len(group) in (2, 3) and group.isdecimal() for group in groups[1:])
    ):
        text = sign + "".join(groups)
    words: list[str] = []  # each character on its own, so every break is between two of them
    cuts: list[int] = []
    for i, ch in enumerate(text):
        words.append(normalise(ch))
        joiner = ch in "-/.:" or (ch == "," and _groups_digits(text, i))
        if ch == "_" or not (ch.isalnum() or joiner):
            cuts.append(len(words) - 1)  # before and after this character
            cuts.append(len(words))
    hay = "".join(words)
    if re.fullmatch(r"[0-9]+", needle) is None or len(needle) >= 9:
        return needle in hay
    ends = [0, *itertools.accumulate(len(word) for word in words)]
    breaks = {0, len(hay), *(ends[cut] for cut in cuts)}
    for start in range(len(hay) - len(needle) + 1):
        end = start + len(needle)
        if hay[start:end] != needle:
            continue
        left = start in breaks or hay[start - 1] not in "0123456789"
        right = end in breaks or hay[end] not in "0123456789"
        if left and right:
            return True
    return False


def test_the_sweep_reads_a_string_as_normalise_does() -> None:
    """The digit rule cuts an output string at every break character and normalises the pieces one
    by one: together they must be the whole string normalised, where text originals are matched
    (M0), whatever the script, compatibility form, combining mark or case folding (U+0345, a
    combining mark and so a break, folds to a letter)."""
    rng = random.Random(3)
    alphabet = (
        "aZ09_-/.:,;| \t\"'[](){}#+@"
        "\u00e9\u0301\u0345\u1fb3\u2121\u2460\u00bc\u00df\uff11\uff0d\uff0c\uff0e\u200b\u0130"
        "\u0967"
    )
    for _ in range(3000):
        text = "".join(rng.choice(alphabet) for _ in range(rng.randint(0, 12)))
        if schema_profile._INTEGRAL_TEXT.fullmatch(unicodedata.normalize("NFKC", text)):
            continue  # a whole number (also full-width: review r2, B2) is read as an integer
        assert schema_profile._words(text)[0] == normalise(text), ascii(text)


def _naive_sweep(rows: list[dict[str, Any]], result: ProfileResult) -> Verification:
    """The sample sweep the slow way: every original against every output string (M0's
    ``verify_output`` loop) with the digit rule of :func:`_naive_found`."""
    strings = list(output_strings(schema_profile._canonical(rows)))
    hits: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    checked = 0
    for finding, original in schema_profile._sweep_originals(result):
        needle = normalise(original)
        if len(needle) < SWEEP_MIN_LEN:
            continue
        checked += 1
        for path, _key, text in strings:
            found = _naive_found(needle, text)
            if found and (path, finding.entity_type) not in seen:
                seen.add((path, finding.entity_type))
                hits.append({"path": path, "entity_type": finding.entity_type})
    return Verification(passed=not hits, checked=checked, hits=hits)


def test_the_indexed_sweep_equals_the_naive_one() -> None:
    """The sample sweep indexes the originals (one lookup per position of each output string)
    instead of comparing every original with every string (13 s on e1f3a3a for 10 000 rows against
    a 1 000-row sample). It must find exactly what the naive loop finds — M0's ``verify_output`` for text
    originals — on copies, near-misses, re-cased and embedded values, keys and nested rows."""
    rng = random.Random(8)
    sample = [dict(r, ref=f"R{rng.randint(10, 99)}-{1001 + i}") for i, r in enumerate(LIVE_SAMPLE)]
    result = profile_from_sample(sample, DETECTOR)
    pool: list[Any] = [*ACCIDENTAL, *LEAKS, *JOINED, "Pune", "Navi Mumbai", "xx", "", 0, 3.5, None]
    pool += [True, "25", "250", "\u0967\u0966\u0966\u0969"]  # 1003 in Devanagari digits
    pool += [str(v) for row in sample for v in row.values()]
    pool += [r["ref"].lower() for r in sample]
    seps = [" ", " ", ";", "_", "|", "-", "/", ",", ",", ".", ":", '","', "\u200b", "－", "\u0345"]
    seps += ["\uff0c"]  # a full-width comma: a comma after NFKC
    rows: list[dict[str, Any]] = []
    for i in range(300):
        picks = rng.sample(pool, 4)
        left, right = rng.choice(seps), rng.choice(seps)
        mixed = f"{rng.choice(pool)}{left}{rng.randint(0, 99999)}{right}{rng.choice(pool)}"
        rows.append({"a": picks[0], "b": {"c": picks[1], "d": picks[2]}, "e": [picks[3], mixed]})
        if i % 50 == 0:
            rows.append({str(rng.choice(pool)): i})  # a key holding a value
    swept, naive = verify_against_sample(rows, result), _naive_sweep(rows, result)
    assert swept == naive and not swept.passed and len(swept.hits) > 20
    for row in rows:  # and one row at a time
        assert verify_against_sample([row], result) == _naive_sweep([row], result)
    text_only = profile_from_sample([{"full_name": r["full_name"]} for r in sample], DETECTOR)
    canonical = schema_profile._canonical(rows)
    assert verify_against_sample(rows, text_only) == verify_output(
        schema_profile._sweep_originals(text_only), canonical
    )


#: A PHONE column: sampled mobiles of ten digits (fictitious).
MOBILES = ["9810010012", "9820020023", "9830030034", "9840040045"]


def test_a_long_number_is_found_wherever_its_digits_are() -> None:
    """Review r1 (B1): once all-digit originals had to be whole runs of digits, a sampled mobile
    written with a country code (``+91 9810010012``, ``+91-98100-10012``: DPS's own PHONE format)
    was no longer refused, nor were two sampled IDs next to each other (``1001 1002``). An
    all-digit original of nine digits or more (longer than a date's eight, so nothing generated
    holds one by accident) is found anywhere in the digits, as in M0; a shorter one where it
    starts and ends at a run's edge or at whitespace, so ``2010-01-04`` and ``21001.5`` still do
    not hold ``1001``."""
    sample = [{"cust_id": 1001 + i, "mobile": m} for i, m in enumerate(MOBILES)]
    result = profile_from_sample(sample, DETECTOR)
    assert [f.entity_type for f in result.profile.fields] == ["CUSTOMER_ID", "PHONE"]
    guard = sample_guard(result)
    assert guard is not None
    copies = [
        "+91 9810010012",
        "+91-98100-10012",
        "(+91) 98200 20023",
        "09830030034",
        "tel:+919840040045;ext=1",
        "1001 1002",
        "IDs 1003,  1004",
        "1001\t2020",
    ]
    for value in copies:
        assert not verify_against_sample([{"v": value}], result).passed, value
        assert guard(value), value
    others = ["+91 98100 10013", "2010-01-04", 21001.5, "1001-2020", "x10012", "981001001"]
    assert verify_against_sample([{"v": v} for v in others], result).passed
    assert [v for v in others if guard(v)] == []


def test_the_executor_draws_again_what_holds_a_sample_value() -> None:
    """Live run: the wider ID range (1000..9999) also drew an exact sampled ID. With a sample the
    executor draws such a value again; a spec literal is never drawn again (the sweep refuses it),
    and once one value cannot get away from the sample (``MAX_SAMPLE_REDRAWS`` draws) the run is
    lost to the sweep anyway, so the executor stops steering instead of spending 20 draws a row."""
    result = profile_from_sample(LIVE_SAMPLE, DETECTOR)
    guard = sample_guard(result)
    near = _one(generator="integer", params={"min": 1001, "max": 1010})
    assert not verify_against_sample(
        execute_spec(near, rows=200, seed=bytes(32)).rows, result
    ).passed
    avoided = execute_spec(near, rows=200, seed=bytes(32), avoid=guard)
    assert avoided.sample_redraws > 0 and {r["v"] for r in avoided.rows} == set(range(1005, 1011))
    assert verify_against_sample(avoided.rows, result).passed
    stuck = execute_spec(
        _one(generator="integer", params={"min": 1001, "max": 1004}),
        rows=50,
        seed=bytes(32),
        avoid=guard,
    )
    assert stuck.sample_redraws == schema_gen.MAX_SAMPLE_REDRAWS
    assert not verify_against_sample(stuck.rows, result).passed
    literal = _one(generator="constant", params={"value": "priya.nair@example.in"})
    out = execute_spec(literal, rows=3, seed=bytes(32), avoid=guard)
    assert out.sample_redraws == 0 and not verify_against_sample(out.rows, result).passed


def test_steering_changes_only_values_that_hold_a_sample_value() -> None:
    """A guard that flags nothing leaves every row as it was (the executor draws nothing extra), and
    a guard that flags some values changes only the rows that held one: the same spec and seed
    replay the same rows wherever the sample was not in the way."""
    spec = GeneratorSpec(
        name="t",
        locale="en_IN",
        fields=[
            FieldSpec(name="id", generator="sequence", params={"start": 1001}, unique=True),
            FieldSpec(name="n", generator="integer", params={"min": 1001, "max": 1010}),
            FieldSpec(name="who", generator="entity", params={"entity_type": "PERSON_NAME"}),
            FieldSpec(name="mail", generator="entity", params={"entity_type": "EMAIL"}),
            FieldSpec(name="ref", generator="pattern", params={"template": "99"}, unique=True),
            FieldSpec(
                name="on", generator="date", params={"start": "2019-01-01", "end": "2019-12-31"}
            ),
        ],
    )
    plain = execute_spec(spec, rows=60, seed=bytes(32))
    assert plain.sample_redraws == 0 and plain.unique_retries > 0
    assert execute_spec(spec, rows=60, seed=bytes(32), avoid=lambda value: False) == plain
    free = spec.model_copy(update={"fields": spec.fields[1:4] + spec.fields[5:]})  # nothing unique
    before = execute_spec(free, rows=200, seed=bytes(32)).rows
    flagged = {1003, before[7]["who"], before[11]["on"]}
    after = execute_spec(free, rows=200, seed=bytes(32), avoid=flagged.__contains__).rows
    held = [i for i, row in enumerate(before) if set(row.values()) & flagged]
    assert {7, 11} <= set(held) and [r for r in after if set(r.values()) & flagged] == []
    assert [i for i in range(200) if before[i] != after[i]] == held


def test_a_redrawn_entity_keeps_one_persona_per_row() -> None:
    """A sampled 4-digit ID inside a generated e-mail (``first.last1234@…``) is a copy the sweep
    refuses, so the e-mail is drawn again. Drawing that field alone took another persona's
    e-mail, and the row's name and e-mail stopped agreeing (one row in ten with a 1 000-row
    sample of 4-digit IDs): the whole row persona is drawn again instead."""
    spec = GeneratorSpec(
        name="t",
        locale="en_IN",
        fields=[
            FieldSpec(name="full_name", generator="entity", params={"entity_type": "PERSON_NAME"}),
            FieldSpec(name="email", generator="entity", params={"entity_type": "EMAIL"}),
            FieldSpec(name="dob", generator="entity", params={"entity_type": "DATE_OF_BIRTH"}),
        ],
    )
    first = execute_spec(spec, rows=40, seed=bytes(32)).rows
    digits = [re.search(r"(\d+)@", r["email"]).group(1) for r in first]  # type: ignore[union-attr]
    ids = [d for d in digits[::2] if len(d) == 4 and d[0] != "0"]
    assert len(ids) >= 5
    result = profile_from_sample([{"cust_id": int(d)} for d in ids], DETECTOR)
    steered = execute_spec(spec, rows=40, seed=bytes(32), avoid=sample_guard(result))
    assert verify_against_sample(steered.rows, result).passed
    changed = [i for i, (a, b) in enumerate(zip(first, steered.rows, strict=True)) if a != b]
    assert len(changed) >= len(ids) and steered.sample_redraws >= len(ids)
    for i in changed:
        row = steered.rows[i]
        given, family = row["full_name"].split(" ")
        local = row["email"].split("@")[0].rstrip("0123456789")
        assert local == f"{ascii_fold(given)}.{ascii_fold(family)}", i
        assert row["full_name"] != first[i]["full_name"]  # a new persona, not a new e-mail alone


def test_a_sequence_skips_sample_values() -> None:
    """A model may propose ``sequence{start: 1000}`` for the ID it sees as ``field_1`` (integer,
    magnitude 10^3): every run then wrote 1001..1004. A sequence is fixed by its row, so it skips
    a sampled value and stays unique and ascending."""
    result = profile_from_sample(LIVE_SAMPLE, DETECTOR)
    spec = _one(name="cust_id", generator="sequence", params={"start": 999}, unique=True)
    plain = execute_spec(spec, rows=12, seed=bytes(32)).rows
    assert [r["cust_id"] for r in plain] == list(range(999, 1011))
    assert not verify_against_sample(plain, result).passed
    steered = execute_spec(spec, rows=12, seed=bytes(32), avoid=sample_guard(result))
    assert [r["cust_id"] for r in steered.rows] == [999, 1000, *range(1005, 1015)]
    assert steered.sample_redraws == 4 and verify_against_sample(steered.rows, result).passed
    coded = _one(name="ref", generator="sequence", params={"start": 1000, "prefix": "C-"})
    rows = execute_spec(coded, rows=3, seed=bytes(32), avoid=sample_guard(result)).rows
    assert [r["ref"] for r in rows] == ["C-1000", "C-1005", "C-1006"]


def test_a_uuid_that_holds_a_sampled_id_is_drawn_again() -> None:
    """A UUID is 32 hex digits: a sampled 4-digit ID is a whole digit run in one of a few thousand
    (``…a4123c…``), so a 10 000-row run with a uuid key failed on that alone."""
    spec = _one(name="ref", generator="uuid", unique=True)
    plain = execute_spec(spec, rows=50, seed=bytes(32)).rows
    run = next(
        m
        for r in plain
        for m in re.findall(r"(?<![0-9])[1-9][0-9]{3,5}(?![0-9])", normalise(r["ref"]))
    )
    result = profile_from_sample([{"cust_id": int(run)}, {"cust_id": 1001}], DETECTOR)
    assert not verify_against_sample(plain, result).passed
    steered = execute_spec(spec, rows=50, seed=bytes(32), avoid=sample_guard(result))
    assert steered.sample_redraws >= 1 and verify_against_sample(steered.rows, result).passed


def test_a_field_a_rule_overwrites_is_not_steered() -> None:
    """A ``rule`` writes its field in every row, so what was generated there never reaches the
    output: drawing it again would only cost time (and could stop steering for nothing)."""
    result = profile_from_sample(LIVE_SAMPLE, DETECTOR)
    spec = GeneratorSpec.model_validate(
        {
            "name": "t",
            "fields": [
                {"name": "score", "generator": "integer", "params": {"min": 1, "max": 9}},
                {"name": "ref", "generator": "integer", "params": {"min": 1001, "max": 1004}},
                {"name": "near", "generator": "integer", "params": {"min": 1001, "max": 1010}},
            ],
            "constraints": [
                {
                    "kind": "rule",
                    "field": "ref",
                    "when": [{"field": "score", "op": "ge", "value": 5, "then": 7}],
                    "else": 0,
                }
            ],
        }
    )
    out = execute_spec(spec, rows=50, seed=bytes(32), avoid=sample_guard(result))
    assert {r["ref"] for r in out.rows} <= {0, 7} and {r["near"] for r in out.rows} >= {1005}
    assert 0 < out.sample_redraws < 50 * schema_gen.MAX_SAMPLE_REDRAWS
    assert verify_against_sample(out.rows, result).passed


async def test_a_model_sequence_on_a_flagged_id_skips_the_sampled_ids() -> None:
    """With NER on, ``cust_id`` is a flagged key, so the model chooses its generator; it sees an
    integer of magnitude 10^3 with every value distinct and may well answer
    ``sequence{start: 1000}``, which wrote the sampled IDs 1001..1004 into every run."""
    result = profile_from_sample(LIVE_SAMPLE, NER_DETECTOR)
    reply = json.dumps(
        {
            "fields": [
                {
                    "name": "field_1",
                    "generator": "sequence",
                    "params": {"start": 1000},
                    "unique": True,
                }
            ]
        }
    )
    gateway = ProviderSeamGateway(reply)
    spec, info = await propose_spec(result, gateway, locale="en_IN")
    assert info["source"] == "llm" and len(gateway.sent) == 1
    assert (spec.fields[0].name, spec.fields[0].generator) == ("cust_id", "sequence")
    guard = sample_guard(result)
    for seed in _seeds(20):
        out = execute_spec(spec, rows=100, seed=seed, avoid=guard)
        assert verify_against_sample(out.rows, result).passed
        ids = [r["cust_id"] for r in out.rows]
        assert ids == sorted(set(ids)) and ids[:3] == [1000, 1005, 1006]


def test_cities_and_card_expiries_are_not_swept() -> None:
    """``CITY`` (318 cities under en_IN) and ``CARD_EXPIRY`` (48 MM/YY values) are drawn from small
    fixed spaces, like gender and nationality: 4 sampled cities failed 72% of 100-row runs, and a
    1 000-row sample holds about 300 of the 318, so steering away from them is impossible (still
    422) and, where it works, the output shows the sample's cities by their absence. The sweep
    does not look for them; the sampled cities are drawn as often as any other."""
    rows = [dict(r, card_expiry=f"0{i + 1}/29") for i, r in enumerate(LIVE_SAMPLE)]
    result = profile_from_sample(rows, DETECTOR)
    by = {f.name: f for f in result.profile.fields}
    assert (by["city"].entity_type, by["card_expiry"].entity_type) == ("CITY", "CARD_EXPIRY")
    assert {"CITY", "CARD_EXPIRY", "GENDER", "NATIONALITY"} <= schema_profile.SWEEP_EXEMPT
    held = [{"city": r["city"], "card_expiry": r["card_expiry"]} for r in rows]
    assert verify_against_sample(held, result).passed
    guard = sample_guard(result)
    assert guard is not None and not [v for r in held for v in r.values() if guard(v)]
    spec = heuristic_spec(result, locale="en_IN")
    generated = execute_spec(spec, rows=1000, seed=bytes(32), avoid=guard).rows
    assert {r["city"] for r in generated} >= {r["city"] for r in LIVE_SAMPLE}
    assert verify_against_sample(generated, result).passed


def test_a_flagged_key_is_never_categorical() -> None:
    """A key the detector flags has its values swept as originals; making the field categorical
    as well had DPS's own ``choice`` write those labels into every run (Presidio flags keys such
    as ``cust_id`` and ``ifsc``), so every synthesis was refused. Its values are shaped instead."""
    detector = Detector(
        DetectorConfig(locale="en_IN", ner_enabled=True), ner=SpacyLikeNer(places=["region"])
    )
    rows = [{"region": "WEST" if i % 2 else "EAST", "amount": 10 * i + 5} for i in range(8)]
    result = profile_from_sample(rows, detector)
    by = {f.name: f for f in result.profile.fields}
    assert by["region"].enum is None and result.stats["region"].originals
    spec = heuristic_spec(result, locale="en_IN")
    assert {f.name: f for f in spec.fields}["region"].generator == "pattern"
    generated = execute_spec(spec, rows=200, seed=bytes(32), avoid=sample_guard(result)).rows
    assert verify_against_sample(generated, result).passed
    unflagged = profile_from_sample(rows, DETECTOR)  # an ordinary key keeps its labels
    assert {f.name: f for f in unflagged.profile.fields}["region"].enum == ["EAST", "WEST"]


@pytest.mark.parametrize("detector", [DETECTOR, NER_DETECTOR], ids=["patterns", "ner"])
def test_the_live_sample_synthesizes_20_runs_of_100_rows(detector: Detector) -> None:
    """The live run's sample, 20 runs of 100 rows: 17 to 19 of 20 were refused (422 leakage) before
    any change; none may be, and every run must still pass the sweep."""
    result = profile_from_sample(LIVE_SAMPLE, detector)
    spec = heuristic_spec(result, locale="en_IN")
    guard = sample_guard(result)
    refused = 0
    for seed in _seeds(20):
        rows = execute_spec(spec, rows=100, seed=seed, avoid=guard).rows
        refused += not verify_against_sample(rows, result).passed
    assert refused == 0


def test_an_id_like_key_avoids_its_sampled_values() -> None:
    """Review r1 (N3): an ID-like key the detector does not type (``id``, ``order_ref``) is not
    swept, so a generated ``id`` could equal a sampled one — a real identifier in synthetic data —
    and a ``sequence`` started near the sample wrote them all. With a sample, such a field avoids
    its own sampled values exactly (values shorter than ``SWEEP_MIN_LEN`` excepted, like the
    sweep). One that cannot (its range is the sample's) keeps them without stopping the steering
    of the swept fields, and nothing is refused for it (it is not swept)."""
    sample = [
        {"id": 5001 + i, "order_ref": f"R-700{i}", "cust_id": 1001 + i, "n": 1 + i}
        for i in range(4)
    ]
    result = profile_from_sample(sample, DETECTOR)
    by = {f.name: f for f in result.profile.fields}
    assert by["id"].entity_type is None and by["order_ref"].entity_type is None
    assert not result.stats["id"].originals and not result.stats["order_ref"].originals
    ids = sample_ids(result)
    assert set(ids) == {"id", "order_ref", "cust_id"}  # ``n`` is no key; 1..4 are too short
    assert sample_ids(profile_from_sample([{"id": 7}, {"id": 8}], DETECTOR)) == {}
    spec = GeneratorSpec(
        name="t",
        locale="en_IN",
        fields=[
            FieldSpec(name="id", generator="sequence", params={"start": 4999}, unique=True),
            FieldSpec(name="order_ref", generator="pattern", params={"template": "R-7009"}),
            FieldSpec(name="cust_id", generator="integer", params={"min": 1001, "max": 1010}),
        ],
    )
    plain = execute_spec(spec, rows=30, seed=bytes(32))
    assert {r["id"] for r in plain.rows} >= {5001, 5004}
    assert {r["order_ref"] for r in plain.rows} & {"R-7000", "R-7003"}
    out = execute_spec(spec, rows=30, seed=bytes(32), avoid=sample_guard(result), avoid_in=ids)
    assert [r["id"] for r in out.rows][:4] == [4999, 5000, 5005, 5006]
    assert not {r["order_ref"] for r in out.rows} & {f"R-700{i}" for i in range(4)}
    assert not {r["cust_id"] for r in out.rows} & {1001, 1002, 1003, 1004}
    assert verify_against_sample(out.rows, result).passed
    unsampled = plain.rows[0]["id"], out.rows[0]["id"]
    assert unsampled == (4999, 4999)  # the rows differ only where a sampled value was in the way
    doomed = spec.model_copy(
        update={
            "fields": [
                FieldSpec(name="id", generator="integer", params={"min": 5001, "max": 5004}),
                spec.fields[2],
            ]
        }
    )
    kept = execute_spec(doomed, rows=40, seed=bytes(32), avoid=sample_guard(result), avoid_in=ids)
    assert {r["id"] for r in kept.rows} <= {5001, 5002, 5003, 5004}  # kept, not refused
    assert not {r["cust_id"] for r in kept.rows} & {1001, 1002, 1003, 1004}  # still steered
    assert kept.sample_redraws < 40 + schema_gen.MAX_SAMPLE_REDRAWS * 2
    assert verify_against_sample(kept.rows, result).passed


def _dense_ids(inside: int, rows: int = 100) -> tuple[list[dict[str, Any]], set[int]]:
    """A later page of a table: ``id`` 1000..1099 (fictitious), and a customer ID per row of which
    ``inside`` lie in 1000..1099 (the others in 2000..9999)."""
    rng = random.Random(5)
    cust = rng.sample(range(1000, 1000 + rows), inside) + rng.sample(
        range(2000, 10000), rows - inside
    )
    rng.shuffle(cust)
    return [{"id": 1000 + i, "cust_id": c} for i, c in enumerate(cust)], set(cust)


def test_a_field_that_cannot_avoid_its_own_values_still_avoids_the_swept_ones() -> None:
    """Review r1 re-check (B2): an ``id`` whose sampled values fill its range (``integer`` over
    1000..1100 for a sampled 1000..1099) gave up on them, and was then not steered at all: it wrote
    the sampled customer IDs that lie inside its range, and the sweep refused every run (``a46d1c5``
    passed it). Such a field stops avoiding its own values only, and keeps steering around the
    swept ones — also the value it gives up on (with half the range swept, that one is a sampled
    customer ID every other time), for a drawn integer and for a skipping ``sequence``."""
    sample, cust = _dense_ids(inside=50)
    result = profile_from_sample(sample, DETECTOR)
    by = {f.name: f for f in result.profile.fields}
    assert (by["id"].entity_type, by["cust_id"].entity_type) == (None, "CUSTOMER_ID")
    spec = heuristic_spec(result, locale="en_IN")
    assert {f.name: f.params for f in spec.fields}["id"] == {"min": 1000, "max": 1100}
    sequence = spec.model_copy(
        update={
            "fields": [
                FieldSpec(name="id", generator="sequence", params={"start": 1000}, unique=True),
                *spec.fields[1:],
            ]
        }
    )
    guard, ids = sample_guard(result), sample_ids(result)
    for proposal in (spec, sequence):
        for seed in _seeds(10):
            out = execute_spec(proposal, rows=150, seed=seed, avoid=guard, avoid_in=ids)
            generated = [r["id"] for r in out.rows]
            assert verify_against_sample(out.rows, result).passed, proposal.fields[0].generator
            assert len(set(generated) & cust) == 0  # no sampled customer ID in the id column
            assert set(generated) & set(range(1000, 1100))  # its own values: kept, as documented


#: Wide decimal columns (name, precision, scale) whose bound has more than 28 significant digits.
WIDE_DECIMALS = [("g", 38, 10), ("h", 38, 18), ("i", 30, 10)]


def test_declared_bounds_are_kept_exactly() -> None:
    """``NUMBER(3)`` became ``integer{max: 1000}`` (999 rounded to two significant figures) and
    ``NUMBER(5,2)`` ``decimal{max: 1000.0}``: values the columns cannot hold. Declared DDL and JSON
    Schema bounds are the column's contract: integers exact, decimals the largest float not above
    the bound (``NUMBER(18,2)``'s bound is no float), integer fields inside fractional bounds."""
    ddl = (
        "create table t (a NUMBER(3), b NUMBER(5,2), c numeric(20), d int, e NUMBER(18,2), "
        "f DECIMAL(20,12), g NUMBER(38,10), h DECIMAL(38,18), i NUMBER(30,10))"
    )
    by = {f.name: f for f in heuristic_spec(profile_from_ddl(ddl), locale="en_US").fields}
    assert (by["a"].generator, by["a"].params) == ("integer", {"min": 0, "max": 999})
    assert (by["b"].generator, by["b"].params) == ("decimal", {"min": 0, "max": 999.99, "scale": 2})
    assert by["c"].params == {"min": 0, "max": 10**20 - 1}
    assert by["d"].params == {"min": 0, "max": 1000}  # no declared bound: the default range
    with localcontext(prec=100):  # review r1 (B2): 28-digit arithmetic rounded these up
        wide = {n: Decimal(10) ** (p - s) - Decimal(1).scaleb(-s) for n, p, s in WIDE_DECIMALS}
    tops = {"e": Decimal("9999999999999999.99"), "f": Decimal("99999999.999999999999"), **wide}
    for name, top in tops.items():  # the largest float whose decimal form is not above the bound
        high = by[name].params["max"]
        assert Decimal(str(high)) <= top < Decimal(str(math.nextafter(high, math.inf))), name
    rows = execute_spec(
        GeneratorSpec(name="t", locale="en_US", fields=list(by.values())), rows=300, seed=bytes(32)
    ).rows
    assert max(r["a"] for r in rows) <= 999 and max(r["b"] for r in rows) <= 999.99
    assert all(Decimal(str(r[name])) <= top for r in rows for name, top in tops.items())
    schema = _schema(
        {
            "n": {"type": "integer", "minimum": 1, "maximum": 99999},
            "x": {"type": "number", "minimum": 0.5, "maximum": 1234.5},
            "k": {"type": "integer", "minimum": 1.5, "maximum": 9.7},
        }
    )
    fields = {f.name: f for f in heuristic_spec(schema, locale="en_US").fields}
    assert fields["n"].params == {"min": 1, "max": 99999}
    assert (fields["x"].params["min"], fields["x"].params["max"]) == (0.5, 1234.5)
    assert fields["k"].params == {"min": 2, "max": 9}
