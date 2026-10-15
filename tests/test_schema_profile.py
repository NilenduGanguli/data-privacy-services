from __future__ import annotations

import json
import re
import time
from collections.abc import Sequence
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.detect.ner_presidio import PresidioNer
from dps.engine.detect.patterns import Hit
from dps.engine.shapes import value_shape
from dps.engine.synth.schema_gen import RuleConstraint, SchemaError, execute_spec
from dps.engine.synth.schema_profile import (
    ProfileResult,
    heuristic_spec,
    parse_ddl,
    profile_from_ddl,
    profile_from_json_schema,
    profile_from_sample,
    propose_spec,
    render_prompt,
    verify_against_sample,
)
from dps.engine.verify import normalise
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
