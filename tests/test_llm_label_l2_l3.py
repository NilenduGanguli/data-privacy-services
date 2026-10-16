"""T3 metadata labelling in L2 discovery and L3 classification (M5 Task 8B, spec §5.3, §8).

Opt-in per policy (``"llm_label" in detect.tiers``), per column / per field (never per row), and
value-free: the gateway receives names, declared types, value shapes, rates and redacted labels —
never a sampled or pre-filled value. A stub or unconfigured gateway never calls. The fake gateway
below replaces only the provider call: ``LlmGateway.complete`` runs as in production, so the egress
verifier (strict) and the per-task LLM figures see every prompt too.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.detect.llm_label import LLM_LABEL_TIER, job_llm_metrics
from dps.engine.policy import Policy, load_preset
from dps.engine.service import PrivacyEngine
from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape
from dps.layers.connectors.discovery import classify_columns, label_leftover_columns
from dps.layers.scanner.classify import (
    classify_fields,
    label_leftover_fields,
    leftover_field_meta,
    page_protected,
    prompt_label,
)
from dps.layers.scanner.models import FieldFacts, OptionFacts, PageSnapshot, TextFacts
from dps.layers.scanner.personas_fill import plan_field
from dps.llm.gateway import LlmGateway, LlmResult
from dps.observability import LLM_STATS, LlmStats
from tests.scanner.helpers import FakeNer, classification, field_facts

REF_VALUES = ["AB-1234", "CD-9876", "EF-5521", "GH-0042", "JK-7310", "LM-2208"]
PREFILLED = "EMP-004211"


def _settings(**kw: Any) -> Settings:
    base: dict[str, Any] = dict(
        _env_file=None,
        env="local",
        llm_provider="stellar",
        stellar_base_url="https://stellar.test",
        coin_url="https://coin.test",
        llm_egress_check="strict",
        ner_enabled=False,
        fpe_enabled=False,
    )
    return Settings(**{**base, **kw})


Reply = Callable[[list[dict[str, Any]]], str]


class FakeGateway(LlmGateway):
    """A configured ``stellar`` provider whose reply is scripted from the fields it was sent; every
    prompt is kept. Only the provider call is replaced (``complete`` runs as in production)."""

    def __init__(self, reply: Reply, settings: Settings | None = None) -> None:
        super().__init__(settings or _settings())
        self.reply = reply
        self.messages: list[list[dict[str, str]]] = []

    async def _stellar(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> LlmResult:
        self.messages.append(messages)
        return LlmResult(
            text=self.reply(json.loads(messages[1]["content"])), provider="stellar", model="t"
        )

    def prompt(self) -> str:
        return json.dumps(self.messages, ensure_ascii=False)


def verdicts(*answers: tuple[str, str, float]) -> Reply:
    """A reply naming fields by their *name* as sent: ``(name, entity type, confidence)``."""

    def reply(sent: list[dict[str, Any]]) -> str:
        by_name = {str(entry["name"]): str(entry["ref"]) for entry in sent}
        if any(name not in by_name for name, _, _ in answers):
            return "[]"
        return json.dumps(
            [
                {"ref": by_name[name], "entity_type": entity, "confidence": confidence}
                for name, entity, confidence in answers
            ]
        )

    return reply


def t3_policy(*, tier: bool = True) -> Policy:
    base = load_preset("gdpr-pseudonymise")
    tiers = [*base.detect.tiers, LLM_LABEL_TIER] if tier else list(base.detect.tiers)
    return base.model_copy(update={"detect": base.detect.model_copy(update={"tiers": tiers})})


def _ner_engine(settings: Settings, *names: str) -> PrivacyEngine:
    """An engine whose detectors run T2 (the preset lists ``ner``): a fake NER that finds
    ``names``. A field's label reaches a T3 prompt only when NER runs."""
    engine = PrivacyEngine(settings, gateway=None)
    engine.ner = FakeNer(*names)
    return engine


def _shape() -> TableShape:
    def col(name: str, kind: str, **kw: Any) -> ColumnInfo:
        return ColumnInfo(
            path=f"$.{name}", name=name, data_type=kw.pop("dt", kind), kind=kind, **kw
        )

    return TableShape(
        object=ObjectRef(owner="bank", name="loans"),
        key=["loan_id"],
        key_source="primary_key",
        columns=[
            col("loan_id", "int", is_key=True, nullable=False),
            col("cust_ref_cd", "text", dt="varchar(12)", length=12),
            col("branch_cd", "text", dt="varchar(8)", length=8),
            col("photo", "binary"),
        ],
    )


def _rows() -> list[dict[str, Any]]:
    return [
        {
            "loan_id": 500 + i,
            "cust_ref_cd": REF_VALUES[i % len(REF_VALUES)],
            "branch_cd": ["NORTH", "SOUTH"][i % 2],
            "photo": b"\x89PNG",
        }
        for i in range(24)
    ]


async def _discover(
    gateway: LlmGateway | None, policy: Policy, settings: Settings | None = None
) -> tuple[dict[str, Any], int | None, PrivacyEngine]:
    settings = settings or _settings()
    engine = PrivacyEngine(settings, gateway=gateway)
    shape, rows = _shape(), _rows()
    columns = classify_columns(
        shape,
        rows,
        policy=policy,
        detector=engine.detector(policy, "en_IN"),
        fpe_enabled=False,
    )
    assert next(c for c in columns if c.name == "cust_ref_cd").entity_type is None  # T0-T2 miss
    labelled, calls = await label_leftover_columns(
        columns, shape, rows, policy=policy, engine=engine, settings=settings
    )
    return {c.name: c for c in labelled}, calls, engine


async def test_discovery_labels_an_ambiguous_column() -> None:
    fake = FakeGateway(verdicts(("cust_ref_cd", "CUSTOMER_ID", 0.93)))
    cols, calls, _ = await _discover(fake, t3_policy())
    ref = cols["cust_ref_cd"]
    assert ref.entity_type == "CUSTOMER_ID" and ref.confidence == 0.85  # T3 never outranks T1
    assert "llm_label" in ref.detectors and ref.sensitivity is not None
    assert ref.suggested.method != "keep" and ref.method == ref.suggested
    assert (
        ref.synthesize_suggested is not None and ref.synthesize_method == ref.synthesize_suggested
    )
    assert calls == 1 and len(fake.messages) == 1
    prompt = fake.prompt()
    assert "cust_ref_cd" in prompt and "XX-9999" in prompt
    assert not any(value in prompt for value in REF_VALUES + ["NORTH", "SOUTH"])
    # keys and binary columns are never sent; a column T0-T2 classified is not a leftover
    sent = {entry["name"] for entry in json.loads(fake.messages[0][1]["content"])}
    assert sent == {"cust_ref_cd", "branch_cd"}
    assert cols["loan_id"].entity_type is None and cols["photo"].method.method == "keep"


async def test_policy_without_the_tier_never_calls() -> None:
    fake = FakeGateway(verdicts(("cust_ref_cd", "CUSTOMER_ID", 0.93)))
    cols, calls, _ = await _discover(fake, t3_policy(tier=False))
    assert fake.messages == [] and calls is None  # not requested: no metrics either
    assert cols["cust_ref_cd"].entity_type is None and cols["cust_ref_cd"].method.method == "keep"


async def test_low_confidence_and_unknown_types_are_ignored() -> None:
    fake = FakeGateway(
        verdicts(("cust_ref_cd", "CUSTOMER_ID", 0.4), ("branch_cd", "NOT_A_TYPE", 0.99))
    )
    cols, calls, _ = await _discover(fake, t3_policy())
    assert calls == 1 and len(fake.messages) == 1
    assert cols["cust_ref_cd"].entity_type is None and cols["branch_cd"].entity_type is None
    assert "llm_label" not in cols["cust_ref_cd"].detectors


def _page(*, prefilled: str | None = PREFILLED) -> tuple[list[Any], PageSnapshot]:
    leftover = field_facts(
        key="0:1",
        field_no=1,
        label="Ref code",
        label_source="label_for",
        name="refcd",
        maxlength=10,
        required=True,
        placeholder="EMP-000000",
        value=prefilled,
    )
    email = field_facts(
        key="0:2", field_no=2, label="E-mail", label_source="label_for", name="email"
    )
    secret = field_facts(key="0:3", field_no=3, input_type="password", name="pin_cd")
    hidden = field_facts(key="0:4", field_no=4, input_type="hidden", name="sess_ref")
    fields = [leftover, email, secret, hidden]
    texts = [
        TextFacts(frame_index=0, i=0, text="Ref code", in_table=False, label_hint=None),
        TextFacts(
            frame_index=0, i=1, text="Employee EMP-007733 approved", in_table=True, label_hint=None
        ),
        TextFacts(
            frame_index=0,
            i=2,
            text="Contact ananya.k@example-mail.in",
            in_table=False,
            label_hint=None,
        ),
    ]
    snap = PageSnapshot(
        url="http://127.0.0.1/apply",
        title="Apply",
        forms=[],
        fields=fields,
        texts=texts,
        links=[],
        clickables=[],
        frames=[],
    )
    return fields, snap


async def test_scanner_labels_a_leftover_field() -> None:
    settings = _settings()
    engine = _ner_engine(settings)  # the label is sent: NER runs
    policy = t3_policy()
    detector = engine.detector(policy, "en_IN")
    fields, snap = _page()
    classes = classify_fields(fields, detector=detector, min_score=policy.detect.min_score)
    assert classes["0:1"].entity_type is None and classes["0:2"].entity_type == "EMAIL"
    fake = FakeGateway(verdicts(("refcd", "EMPLOYEE_ID", 0.9)))
    protected = page_protected(snap, detector=detector)
    assert PREFILLED in protected and "Ref code" not in protected  # a label is metadata
    labelled, outcome = await label_leftover_fields(
        fields,
        classes,
        gateway=fake,
        settings=settings,
        policy=policy,
        detector=detector,
        protected=protected,
    )
    ref = labelled["0:1"]
    assert ref.entity_type == "EMPLOYEE_ID" and ref.confidence == 0.85
    assert "llm_label" in ref.detectors and "llm_label" in ref.evidence
    assert ref.sensitivity is not None
    assert labelled["0:2"] == classes["0:2"]  # T0-T2 verdicts are never touched
    assert outcome is not None and outcome.llm_calls == 1 and len(fake.messages) == 1
    plan = plan_field(fields[0], ref)  # persona attribute and fill as for any other classifier
    assert plan.persona_attr == "employee_id" and plan.fill_status == "filled"
    prompt = fake.prompt()
    assert "Ref code" in prompt and "refcd" in prompt and "XXX-999999" in prompt
    assert PREFILLED not in prompt and "EMP-007733" not in prompt and "ananya" not in prompt
    # one field per leftover only: no credential, hidden or classified field is sent
    sent = json.loads(fake.messages[0][1]["content"])
    assert [entry["name"] for entry in sent] == ["refcd"]
    assert sent[0]["data_type"] == "input[type=text]"
    assert sent[0]["hints"] == {
        "autocomplete": "",
        "maxlength": 10,
        "pattern_len": 0,
        "required": True,
    }


def test_leftover_field_meta_is_value_free() -> None:
    engine = PrivacyEngine(_settings(), gateway=None)
    detector = engine.detector(t3_policy(), "en_IN")
    fields, _ = _page()
    classes = classify_fields(fields, detector=detector, min_score=0.6)
    metas = leftover_field_meta(fields, classes, detector=detector)
    assert [m.ref for m in metas] == ["field:1/1"]
    assert PREFILLED not in json.dumps([m.model_dump() for m in metas])


async def test_a_prompt_that_would_carry_a_page_value_is_never_sent() -> None:
    """The module's leak guard is the backstop for a label value no detector redacted: a name NER
    did not find in a label, which the page shows as a field's value, never leaves."""
    settings = _settings()
    engine = _ner_engine(settings)  # NER runs but knows no name: the label keeps it
    policy = t3_policy()
    detector = engine.detector(policy, "en_IN")
    fields, snap = _page()
    shown = field_facts(key="0:5", field_no=5, name="holder", value=NOMINEE)
    leaky = fields[0].model_copy(update={"label": f"Nominee {NOMINEE} agrees"})
    snap = snap.model_copy(update={"fields": [*snap.fields, shown]})
    classes = classify_fields([leaky], detector=detector, min_score=0.6)
    fake = FakeGateway(verdicts(("refcd", "EMPLOYEE_ID", 0.9)))
    labelled, outcome = await label_leftover_fields(
        [leaky],
        classes,
        gateway=fake,
        settings=settings,
        policy=policy,
        detector=detector,
        protected=page_protected(snap, detector=detector),
    )
    assert fake.messages == [] and labelled["0:1"].entity_type is None
    assert outcome is not None and outcome.fallback == "leak_guard"


#: B1 (S3b review r1): made-up record values in the labels of per-record controls, one per label
#: source; only the ``<label for>`` is rendered text (its digits are protected tokens of the page)
PLANTED = {
    "aria_label": "77120034519",
    "placeholder": "88120034519",
    "title": "66120034519",
    "aria_labelledby": "44120034519",
    "label_for": "55120034519",
}
NOMINEE = "Zebulon Quixote"


def _record_labels_page(*, rendered: bool) -> tuple[list[FieldFacts], PageSnapshot]:
    """Labels as ``dom.py`` resolves them: from attributes (``aria-label``, the ``placeholder``
    fallback, ``title``), a hidden ``aria-labelledby`` target and, if ``rendered``, a rendered
    ``<label for>``."""

    def box(no: int, name: str, label: str, source: str, **kw: Any) -> FieldFacts:
        return field_facts(
            key=f"0:{no}",
            field_no=no,
            input_type=kw.pop("input_type", "checkbox"),
            name=name,
            label=label,
            label_source=source,
            **kw,
        )

    fields = [
        box(
            1, "close_sel", (a := f"Close loan {PLANTED['aria_label']}"), "aria_label", aria_label=a
        ),
        box(
            2,
            "ref_txt",
            PLANTED["placeholder"],
            "placeholder",
            input_type="text",
            placeholder=PLANTED["placeholder"],
        ),
        box(
            3,
            "memo_txt",
            (t := f"Memo for loan {PLANTED['title']}"),
            "title",
            input_type="text",
            title=t,
        ),
        box(4, "nom_ok", f"Loan {PLANTED['aria_labelledby']} nominee", "aria_labelledby"),
        box(5, "nominee_ok", (n := f"Nominee {NOMINEE} agrees"), "aria_label", aria_label=n),
    ]
    texts = [TextFacts(frame_index=0, i=0, text="Your loans", in_table=False, label_hint=None)]
    if rendered:
        shown = f"Close loan {PLANTED['label_for']}"
        fields.append(box(6, "close_two", shown, "label_for"))
        texts.append(TextFacts(frame_index=0, i=1, text=shown, in_table=False, label_hint=None))
    snap = PageSnapshot(
        url="http://127.0.0.1/loans",
        title="Loans",
        forms=[],
        fields=fields,
        texts=texts,
        links=[],
        clickables=[],
        frames=[],
    )
    return fields, snap


async def _label_record_page(
    engine: PrivacyEngine,
    settings: Settings,
    *,
    rendered: bool = True,
    min_score: float | None = None,
) -> tuple[FakeGateway, dict[str, dict[str, Any]], dict[str, int]]:
    """Label :func:`_record_labels_page` through the real ``complete()`` (``strict``), the provider
    faked at ``_stellar``: the prompt's entries by field name and the scan's LLM figures.
    ``min_score``: the policy's ``detect.min_score`` (else the preset's)."""
    policy = t3_policy()
    if min_score is not None:
        policy = policy.model_copy(
            update={"detect": policy.detect.model_copy(update={"min_score": min_score})}
        )
    detector = engine.detector(policy, "en_IN")
    fields, snap = _record_labels_page(rendered=rendered)
    classes = classify_fields(fields, detector=detector, min_score=policy.detect.min_score)
    assert [k for k, c in classes.items() if c.entity_type is not None] == []  # all leftovers
    fake = FakeGateway(lambda sent: "[]", settings)
    token = LLM_STATS.set(LlmStats())
    try:
        _, outcome = await label_leftover_fields(
            fields,
            classes,
            gateway=fake,
            settings=settings,
            policy=policy,
            detector=detector,
            protected=page_protected(snap, detector=detector),
        )
        assert outcome is not None
        figures = job_llm_metrics(outcome.llm_calls)
    finally:
        LLM_STATS.reset(token)
    prompt = fake.prompt()
    leaked = [k for k, v in PLANTED.items() if v in prompt]
    leaked += [part for part in NOMINEE.split() if part in prompt]
    assert leaked == []  # no planted value, whatever the label's source
    assert len(fake.messages) == 1 and figures == {"calls": 1, "raw_values": 0, "blocked": 0}
    sent = {str(e["name"]): e for m in fake.messages for e in json.loads(m[1]["content"])}
    return fake, sent, figures


@pytest.mark.parametrize("rendered", [False, True])
async def test_l3_labels_are_value_free_whatever_their_source(rendered: bool) -> None:
    """B1: a label from an attribute, a hidden ``aria-labelledby`` target or a rendered ``<label
    for>`` reaches the prompt with every token holding a digit or an ``@`` as its shape and the
    names NER finds redacted; the placeholder fallback is sent as its shape only. The page's call
    is made: a number in a rendered label no longer blocks it."""
    settings = _settings()
    engine = _ner_engine(settings, NOMINEE)
    _, sent, _ = await _label_record_page(engine, settings, rendered=rendered)
    assert sent["close_sel"]["label"] == "Close loan 99999999999"
    assert sent["memo_txt"]["label"] == "Memo for loan 99999999999"
    assert sent["nom_ok"]["label"] == "Loan 99999999999 nominee"
    assert sent["nominee_ok"]["label"] == "Nominee <PERSON_NAME> agrees"
    assert "label" not in sent["ref_txt"] and sent["ref_txt"]["shapes"] == ["99999999999"]
    if rendered:
        assert sent["close_two"]["label"] == "Close loan 99999999999"


@pytest.mark.parametrize("min_score", [0.86, 0.9, 1.0])
async def test_l3_label_names_are_redacted_whatever_the_policy_min_score(min_score: float) -> None:
    """R2-B1 (S3b review r2): T2 scores a name 0.85 (Presidio's spaCy default, as ``FakeNer``) and
    a policy's ``detect.min_score`` may be anything up to 1 — the threshold for what a job
    transforms, never for what a model may read: a label is redacted for a prompt of every span
    any tier reports, whatever its score. The name never reaches the provider, the label is still
    sent and the page's call is still made."""
    settings = _settings()
    engine = _ner_engine(settings, NOMINEE)
    _, sent, figures = await _label_record_page(engine, settings, min_score=min_score)
    assert sent["nominee_ok"]["label"] == "Nominee <PERSON_NAME> agrees"
    assert figures == {"calls": 1, "raw_values": 0, "blocked": 0}


@pytest.mark.parametrize("min_score", [0.0, 0.6, 0.86, 0.9, 1.0])
def test_prompt_label_is_redacted_recall_first(min_score: float) -> None:
    """A label taken from the neighbouring cell of a per-record row (``nearest_text``): the name
    T2 finds is redacted at any policy ``min_score``; the caller's detector keeps its threshold."""
    engine = _ner_engine(_settings(), NOMINEE)
    base = t3_policy()
    policy = base.model_copy(
        update={"detect": base.detect.model_copy(update={"min_score": min_score})}
    )
    detector = engine.detector(policy, "en_IN")
    f = field_facts(name="sel_a", label=NOMINEE, label_source="nearest_text")
    assert prompt_label(f, detector) == "<PERSON_NAME>"
    f = field_facts(name="nominee_ok", label=f"Nominee {NOMINEE} agrees", label_source="title")
    assert prompt_label(f, detector) == "Nominee <PERSON_NAME> agrees"
    assert detector.config.min_score == min_score


@pytest.mark.parametrize("ner", ["off", "failed_to_load"])
async def test_l3_labels_are_sent_only_when_ner_runs(ner: str) -> None:
    """A label is page-authored free text, and only NER finds a person's name with no cue around
    it: without T2 (or with an NER whose model failed to load) no label is sent. The fields are
    still labelled — by name, type, placeholder shape and hints — in one call."""
    settings = _settings()
    engine = PrivacyEngine(settings, gateway=None)
    if ner == "failed_to_load":
        engine.ner = FakeNer(NOMINEE, available=False)
    _, sent, _ = await _label_record_page(engine, settings)
    assert set(sent) == {"close_sel", "ref_txt", "memo_txt", "nom_ok", "nominee_ok", "close_two"}
    assert [name for name, e in sent.items() if "label" in e] == []
    assert sent["ref_txt"]["shapes"] == ["99999999999"]


def test_a_label_value_token_keeps_its_redaction_tags() -> None:
    """Value tokens become shapes; a ``<ENTITY>`` tag the label redaction wrote stays readable."""
    engine = _ner_engine(_settings(), NOMINEE)
    detector = engine.detector(t3_policy(), "en_IN")
    f = field_facts(
        name="ack",
        label=f"Ack ({NOMINEE}),a.b@x ref 7c",
        label_source="label_wrap",
        input_type="checkbox",
    )
    [meta] = leftover_field_meta([f], {f.key: classification(None, None)}, detector=detector)
    assert meta.label == "Ack (<PERSON_NAME>),x.x@x ref 9x"
    # a page's own text in tag form is no redaction tag when it holds a digit: it is shaped
    page_tag = f.model_copy(update={"label": "Code <A1234567>"})
    [meta] = leftover_field_meta([page_tag], {f.key: classification(None, None)}, detector=detector)
    assert meta.label == "Code <X9999999>"


@pytest.mark.parametrize("layer", ["l2", "l3"])
async def test_stub_gateway_is_a_no_op(layer: str) -> None:
    stub_settings = Settings(_env_file=None, llm_provider="stub", ner_enabled=False)
    stub = FakeGateway(verdicts(("cust_ref_cd", "CUSTOMER_ID", 0.93)), stub_settings)
    token = LLM_STATS.set(LlmStats())
    try:
        if layer == "l2":
            cols, calls, _ = await _discover(stub, t3_policy(), stub_settings)
            assert cols["cust_ref_cd"].entity_type is None and calls == 0
        else:
            engine = PrivacyEngine(stub_settings, gateway=None)
            detector = engine.detector(t3_policy(), "en_IN")
            fields, snap = _page()
            classes = classify_fields(fields, detector=detector, min_score=0.6)
            labelled, outcome = await label_leftover_fields(
                fields,
                classes,
                gateway=stub,
                settings=stub_settings,
                policy=t3_policy(),
                detector=detector,
                protected=page_protected(snap, detector=detector),
            )
            assert labelled == classes and outcome is not None and outcome.verdicts == []
            calls = outcome.llm_calls
        assert stub.messages == []
        assert job_llm_metrics(calls or 0)["calls"] == 0  # progress.metrics.llm.calls
    finally:
        LLM_STATS.reset(token)


async def test_job_metrics_follow_the_task_figures() -> None:
    token = LLM_STATS.set(LlmStats())
    try:
        fake = FakeGateway(verdicts(("cust_ref_cd", "CUSTOMER_ID", 0.93)))
        _, calls, _ = await _discover(fake, t3_policy())
        assert job_llm_metrics(calls or 0) == {"calls": 1, "raw_values": 0, "blocked": 0}
    finally:
        LLM_STATS.reset(token)


def _cols(*names: str) -> TableShape:
    def col(name: str) -> ColumnInfo:
        return ColumnInfo(
            path=f"$.{name}", name=name, data_type="varchar(40)", kind="text", length=40
        )

    return TableShape(
        object=ObjectRef(owner="bank", name="accounts"),
        key=["acct_id"],
        key_source="primary_key",
        columns=[
            ColumnInfo(
                path="$.acct_id",
                name="acct_id",
                data_type="int",
                kind="int",
                is_key=True,
                nullable=False,
            ),
            *[col(n) for n in names],
        ],
    )


async def test_wide_tables_are_labelled_in_calls_of_at_most_the_field_cap() -> None:
    """Per column, never per row: a table with more leftover columns than
    ``DPS_LLM_LABEL_MAX_FIELDS`` is labelled in several calls of at most that many fields."""
    settings = _settings(llm_label_max_fields=1)
    fake = FakeGateway(verdicts(("cust_ref_cd", "CUSTOMER_ID", 0.93)), settings)
    engine = PrivacyEngine(settings, gateway=fake)
    cols, calls, _ = await _discover(fake, t3_policy(), settings)
    assert calls == 2 and [len(json.loads(m[1]["content"])) for m in fake.messages] == [1, 1]
    assert cols["cust_ref_cd"].entity_type == "CUSTOMER_ID"
    assert engine.gateway is fake


async def test_a_prompt_the_egress_verifier_stops_is_never_sent_and_is_counted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Fail closed, under ``strict``: a prompt the egress verifier stops never leaves; the columns
    stay as T0–T2 left them and ``progress.metrics.llm.blocked`` counts it. Here a sampled value
    ("Personal") is a word of the labelling instructions, and the verifier is made to scan them
    (no registered fixed prompt): since FINAL r1 privacy-ops B1 a registered fixed system prompt is
    not scanned, so this false positive no longer happens on its own."""
    import dps.llm.egress as egress

    monkeypatch.setattr(egress, "fixed_system_prompts", lambda: frozenset())
    shape = _cols("kind_desc", "seg_cd")
    rows = [
        {"acct_id": i, "kind_desc": ["Personal", "Business"][i % 2], "seg_cd": f"S{i % 3}"}
        for i in range(30)
    ]
    settings = _settings()
    fake = FakeGateway(verdicts(("seg_cd", "CASE_ID", 0.9)), settings)
    engine = PrivacyEngine(settings, gateway=fake)
    policy = t3_policy()
    columns = classify_columns(
        shape, rows, policy=policy, detector=engine.detector(policy, "en_IN"), fpe_enabled=False
    )
    token = LLM_STATS.set(LlmStats())
    try:
        labelled, calls = await label_leftover_columns(
            columns, shape, rows, policy=policy, engine=engine, settings=settings
        )
        assert fake.messages == [] and calls == 0 and labelled == columns
        assert job_llm_metrics(calls or 0) == {"calls": 0, "raw_values": 0, "blocked": 1}
    finally:
        LLM_STATS.reset(token)


def _select_page(*, selected_value: str) -> tuple[list[Any], PageSnapshot]:
    options = [
        OptionFacts(value="", text="Choose…", label="Choose…", disabled=False, selected=False),
        OptionFacts(value="IN", text="India", label="India", disabled=False, selected=False),
        OptionFacts(
            value="SAL", text="Salaried 2024", label="Salaried 2024", disabled=False, selected=False
        ),
    ]
    options = [o.model_copy(update={"selected": o.value == selected_value}) for o in options]
    select = field_facts(
        key="0:1",
        field_no=1,
        tag="select",
        input_type="select-one",
        name="residence",
        label="Country of residence",
        label_source="label_for",
        options=options,
    )
    snap = PageSnapshot(
        url="http://127.0.0.1/apply",
        title="Apply",
        forms=[],
        fields=[select],
        texts=[
            TextFacts(
                frame_index=0, i=0, text="Country of residence", in_table=False, label_hint=None
            )
        ],
        links=[],
        clickables=[],
        frames=[],
    )
    return [select], snap


async def test_a_placeholder_option_is_page_text_and_never_blocks_the_call() -> None:
    """A select showing its placeholder option ("Choose…", value "") holds no value: it is not
    protected — "choose" is a word of the fixed instructions, where the egress verifier looks for
    protected values too — so the page's labelling call goes out. A real selected option is a
    value; option tokens holding a digit are protected whether selected or not."""
    settings = _settings()
    engine = PrivacyEngine(settings, gateway=None)
    policy = t3_policy()
    detector = engine.detector(policy, "en_IN")
    fields, snap = _select_page(selected_value="")
    protected = page_protected(snap, detector=detector)
    assert "Choose…" not in protected and "India" not in protected and "2024" in protected
    _, chosen = _select_page(selected_value="IN")
    assert {"IN", "India"} <= set(page_protected(chosen, detector=detector))
    classes = classify_fields(fields, detector=detector, min_score=policy.detect.min_score)
    assert classes["0:1"].entity_type is None
    fake = FakeGateway(verdicts(("residence", "LOCATION", 0.92)), settings)
    labelled, outcome = await label_leftover_fields(
        fields,
        classes,
        gateway=fake,
        settings=settings,
        policy=policy,
        detector=detector,
        protected=protected,
    )
    assert outcome is not None and outcome.llm_calls == 1 and len(fake.messages) == 1
    assert labelled["0:1"].entity_type == "LOCATION"
    sent = json.loads(fake.messages[0][1]["content"])
    assert sent[0]["data_type"] == "select[type=select-one]" and "options" not in sent[0]
    assert "India" not in fake.prompt() and "Salaried" not in fake.prompt()


def test_page_chosen_attributes_reach_the_prompt_only_as_known_tokens() -> None:
    """An ``autocomplete`` or ``type`` attribute is page-chosen text: the prompt carries only a
    dictionary token (``on``/``off`` or a field-name token) and a known input type."""
    engine = PrivacyEngine(_settings(), gateway=None)
    detector = engine.detector(t3_policy(), "en_IN")
    odd = field_facts(
        key="0:1",
        field_no=1,
        name="refcd",
        label="Ref code",
        input_type="ananya-k-example",
        autocomplete="section-ananya nickname-x",
    )
    on = field_facts(key="0:2", field_no=2, name="refcd2", label="Ref 2", autocomplete="on")
    unclassified = classify_fields([odd, on], detector=detector, min_score=0.6)
    metas = leftover_field_meta([odd, on], unclassified, detector=detector)
    by_ref = {m.ref: m for m in metas}
    assert by_ref["field:1/1"].data_type == "input[type=text]"
    assert by_ref["field:1/1"].hints["autocomplete"] == ""
    assert by_ref["field:1/2"].hints["autocomplete"] == "on"
    assert "ananya" not in json.dumps([m.model_dump() for m in metas])


def _select(key: str, options: list[tuple[str, str, bool]], **kw: Any) -> Any:
    """A closed select (or, with ``size``/``multiple``, a listbox) of ``(value, text, selected)``."""
    return field_facts(
        key=key,
        field_no=int(key.split(":")[1]),
        tag="select",
        input_type="select-one",
        options=[
            OptionFacts(value=v, text=t, label=t, disabled=False, selected=s) for v, t, s in options
        ],
        **kw,
    )


def test_a_drawn_placeholder_is_no_protected_value_but_a_drawn_value_is() -> None:
    """Release merge (S3b Task 8B x S1-M4 r1): ``shown_texts`` now reads what a select draws,
    its placeholder included, so ``page_protected`` handed the strict egress verifier "Choose…" —
    the fixed instructions say "choose", and every T3 call of a page with an untouched select was
    blocked. A placeholder phrase of an empty-valued option is page text; whatever else a select
    draws (a real value behind an empty value, every option of a listbox) is protected."""
    placeholder = _select("0:1", [("", "Choose…", True), ("IN", "India", False)], name="residence")
    behind_empty = _select("0:2", [("", "Pickering Zebulon", True)], name="holder")
    listbox = _select(
        "0:3",
        [("a", "Ottoline Brackwater", False), ("b", "Quorra Pleasence", False)],
        name="nominees",
        listbox=True,
    )
    snap = PageSnapshot(
        url="http://127.0.0.1/apply",
        title="Apply",
        forms=[],
        fields=[placeholder, behind_empty, listbox],
        texts=[],
        links=[],
        clickables=[],
        frames=[],
    )
    detector = Detector(DetectorConfig(locale="en_IN"))
    protected = page_protected(snap, detector=detector)
    assert "Choose…" not in protected
    assert "Pickering Zebulon" in protected
    assert {"Ottoline Brackwater", "Quorra Pleasence"} <= set(protected)
