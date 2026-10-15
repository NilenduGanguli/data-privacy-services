"""M5 Task 8B (L3): T3 metadata labelling in the crawl of the demo site, and of a local page whose
labels carry record values (S3b review r1, B1).

The policy opts in (``llm_label`` in ``detect.tiers``); the LLM is faked at the provider seam only
(``_stellar``), so ``LlmGateway.complete`` — and its egress verifier under ``strict`` — sees every
prompt as in production. One call per page with leftover fields; the labelled fields carry
``llm_label`` into the inventory and the fill plan; no prompt holds a page value; nothing is
submitted (the demo site's counters, the local page's write log).
"""

from __future__ import annotations

import json
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any
from urllib.parse import urlsplit

import pytest

from dps.config import Settings
from dps.engine.detect.llm_label import LLM_LABEL_TIER
from dps.engine.policy import Policy
from dps.engine.service import PrivacyEngine
from dps.engine.verify import normalise
from dps.layers.scanner.models import FieldClassification, PageSnapshot
from dps.layers.scanner.runtime import MemoryScanSink
from dps.llm.gateway import LlmGateway, LlmResult
from tests.scanner.conftest import RotCipher
from tests.scanner.helpers import FakeNer, field_facts, make_runtime, received

#: what the fake model answers, by the field name it was sent (``name`` attribute)
ANSWERS = {"residence": ("LOCATION", 0.92), "income": ("AMOUNT", 0.9)}


class FakeGateway(LlmGateway):
    """A configured ``stellar`` provider answering :data:`ANSWERS` for the fields it is sent;
    every prompt is kept. Only the provider call is replaced."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.messages: list[list[dict[str, str]]] = []

    async def _stellar(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> LlmResult:
        self.messages.append(messages)
        sent = json.loads(messages[1]["content"])
        reply = [
            {
                "ref": e["ref"],
                "entity_type": ANSWERS[e["name"]][0],
                "confidence": ANSWERS[e["name"]][1],
            }
            for e in sent
            if e.get("name") in ANSWERS
        ]
        return LlmResult(text=json.dumps(reply), provider="stellar", model="t")


def _t3(policy: Policy) -> Policy:
    tiers = [*policy.detect.tiers, LLM_LABEL_TIER]
    return policy.model_copy(update={"detect": policy.detect.model_copy(update={"tiers": tiers})})


def _settings(scan_settings: Settings) -> Settings:
    return scan_settings.model_copy(
        update={
            "fpe_enabled": True,
            "llm_provider": "stellar",
            "stellar_base_url": "https://stellar.test",
            "coin_url": "https://coin.test",
            "llm_egress_check": "strict",
        }
    )


@pytest.mark.browser
@pytest.mark.slow
async def test_a_crawl_labels_leftover_fields_value_free(
    chromium: Any, scan_settings: Settings, demo_site_url: str, demo_data: dict[str, str]
) -> None:
    from dps.layers.scanner.crawler import ScanRunner

    settings = _settings(scan_settings)
    fake = FakeGateway(settings)
    engine = PrivacyEngine(settings, gateway=fake, cipher_factory=lambda key, tweak: RotCipher())
    before = received(demo_site_url)
    by_name: dict[tuple[str, str | None], Any] = {}
    allowed_writes = []
    for path in ("/apply/address", "/apply/employment"):  # one page each, no discovery
        runtime = make_runtime(demo_site_url, settings, engine, path=path, max_pages=1, max_depth=0)
        runtime.policy = _t3(runtime.policy)
        sink = MemoryScanSink()
        outcome = await ScanRunner(
            settings=settings, engine=engine, runtime=runtime, sink=sink, browser=chromium
        ).run()
        [page] = [p for p in sink.pages.values() if p.status == "done"]
        assert urlsplit(page.url_redacted).path == path
        # one call for the page's leftover fields, through the real complete() (strict)
        assert outcome.llm == {"calls": 1, "raw_values": 0, "blocked": 0}
        by_name.update({(path, f.name_attr): f for f in sink.fields[page.page_no]})
        allowed_writes += [
            r for r in sink.requests if r.decision == "allowed" and r.method not in ("GET", "HEAD")
        ]
    after = received(demo_site_url)
    assert len(fake.messages) == 2
    residence = by_name[("/apply/address", "residence")]
    income = by_name[("/apply/employment", "income")]
    for record, entity in ((residence, "LOCATION"), (income, "AMOUNT")):
        assert record.entity_type == entity and record.confidence == 0.85
        assert LLM_LABEL_TIER in record.detectors and LLM_LABEL_TIER in record.evidence
    for unlabelled in (("/apply/address", "landmark"), ("/apply/employment", "occupation")):
        record = by_name[unlabelled]
        assert record.entity_type is None and LLM_LABEL_TIER not in record.detectors
    # the labelled select is filled like any classified one (a persona attribute, not neutral)
    assert residence.fill_status == "filled" and residence.persona_attr is not None
    # value-free prompts: field metadata only, never a demo value
    prompt = normalise(json.dumps(fake.messages, ensure_ascii=False))
    assert not [
        k for k, v in demo_data.items() if len(normalise(v)) >= 4 and normalise(v) in prompt
    ]
    sent = [e for m in fake.messages for e in json.loads(m[1]["content"])]
    assert {e["name"] for e in sent} == {"landmark", "residence", "occupation", "income"}
    assert all(set(e) <= {"ref", "name", "data_type", "shapes", "label", "hints"} for e in sent)
    # D4: nothing was submitted while the pages were labelled and filled
    keys = ("non_get", "form_get", "logout_hits", "admin_hits")
    assert [after[k] for k in keys] == [before[k] for k in keys]
    assert allowed_writes == []


@pytest.mark.browser
@pytest.mark.slow
async def test_a_policy_without_the_tier_never_calls(
    chromium: Any, scan_settings: Settings, demo_site_url: str
) -> None:
    from dps.layers.scanner.crawler import ScanRunner

    settings = _settings(scan_settings)
    fake = FakeGateway(settings)
    engine = PrivacyEngine(settings, gateway=fake, cipher_factory=lambda key, tweak: RotCipher())
    runtime = make_runtime(
        demo_site_url, settings, engine, path="/apply/address", max_pages=1, max_depth=0
    )
    outcome = await ScanRunner(
        settings=settings, engine=engine, runtime=runtime, sink=MemoryScanSink(), browser=chromium
    ).run()
    assert fake.messages == [] and outcome.llm is None


#: B1 (S3b review r1): per-record controls labelled with made-up record values, one per label
#: source ``dom.py`` resolves (the hidden span is an ``aria-labelledby`` target, not rendered text)
RECORD_VALUES = {
    "aria_label": "77120034519",
    "placeholder": "88120034519",
    "title": "66120034519",
    "aria_labelledby": "44120034519",
    "label_for": "55120034519",
    "aria_label_person": "Zebulon Quixote",
}
_ATTRIBUTE_LABELS = """
  <input type="checkbox" name="close_sel" aria-label="Close loan {aria_label}">
  <input type="text" name="ref_txt" placeholder="{placeholder}">
  <input type="text" name="memo_txt" title="Memo for loan {title}">
  <span id="nom-l" hidden>Loan {aria_labelledby} nominee</span>
  <input type="checkbox" name="nom_ok" aria-labelledby="nom-l">
  <input type="checkbox" name="nominee_ok" aria-label="Nominee {aria_label_person} agrees">"""
_RENDERED_LABEL = """
  <label for="c1">Close loan {label_for}</label><input type="checkbox" id="c1" name="close_two">"""
_PAGE = """<!doctype html><html><head><title>Loans</title></head><body>
<h1>Your loans</h1>
<form method="post" action="/close">{controls}
  <button type="submit">Close</button>
</form></body></html>"""
#: ``/attrs``: labels from attributes and a hidden target only (the review's probe page);
#: ``/loans``: the same plus a rendered ``<label for>`` whose digits are protected page tokens
RECORD_PAGES = {
    "/attrs": _PAGE.format(controls=_ATTRIBUTE_LABELS.format(**RECORD_VALUES)),
    "/loans": _PAGE.format(controls=(_ATTRIBUTE_LABELS + _RENDERED_LABEL).format(**RECORD_VALUES)),
}


@contextmanager
def _record_site() -> Iterator[tuple[str, list[str]]]:
    """A site serving :data:`RECORD_PAGES`; every non-GET it receives is recorded."""
    writes: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            return

        def do_GET(self) -> None:  # noqa: N802
            page = RECORD_PAGES.get(self.path.split("?")[0])
            body = (page or "<html><body>nothing</body></html>").encode()
            self.send_response(200 if page else 404)
            self.send_header("content-type", "text/html; charset=utf-8")
            self.send_header("content-length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self) -> None:  # noqa: N802
            writes.append("POST")
            self.send_response(204)
            self.end_headers()

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", writes
    finally:
        server.shutdown()
        server.server_close()


@pytest.mark.browser
@pytest.mark.slow
@pytest.mark.parametrize("path", ["/attrs", "/loans"])
@pytest.mark.parametrize("ner", [False, True])
async def test_record_values_in_labels_never_reach_the_model(
    chromium: Any, scan_settings: Settings, ner: bool, path: str
) -> None:
    """B1, end to end: a real page whose per-record controls carry record values in an
    ``aria-label``, a ``placeholder``, a ``title``, a hidden ``aria-labelledby`` target (and, on
    ``/loans``, a rendered ``<label for>``). The provider (faked at ``_stellar``, behind the real
    ``complete()`` under ``strict``) never receives one, and the page's call is still made. Without
    NER no label is sent; with NER the labels are sent with their value tokens as shapes and the
    names it finds redacted."""
    from dps.layers.scanner.crawler import ScanRunner

    settings = _settings(scan_settings).model_copy(update={"fpe_enabled": False})
    fake = FakeGateway(settings)
    engine = PrivacyEngine(settings, gateway=fake)
    engine.ner = FakeNer(RECORD_VALUES["aria_label_person"]) if ner else None
    with _record_site() as (base, writes):
        runtime = make_runtime(
            base, settings, engine, path=path, max_pages=1, max_depth=0, cookies=False
        )
        runtime.policy = _t3(runtime.policy)
        sink = MemoryScanSink()
        outcome = await ScanRunner(
            settings=settings, engine=engine, runtime=runtime, sink=sink, browser=chromium
        ).run()
    fields = {f.name_attr: f for records in sink.fields.values() for f in records}
    sources = {"aria_label", "placeholder", "title", "aria_labelledby"}
    rendered = path == "/loans"
    assert {f.label_source for f in fields.values()} == sources | (
        {"label_for"} if rendered else set()
    )
    sent_text = json.dumps(fake.messages, ensure_ascii=False)
    leaked = [k for k, v in RECORD_VALUES.items() if v in sent_text]
    leaked += [p for p in RECORD_VALUES["aria_label_person"].split() if p in sent_text]
    assert leaked == []
    assert outcome.llm == {"calls": 1, "raw_values": 0, "blocked": 0} and len(fake.messages) == 1
    sent = {e["name"]: e for e in json.loads(fake.messages[0][1]["content"])}
    names = {"close_sel", "ref_txt", "memo_txt", "nom_ok", "nominee_ok"}
    assert set(sent) == names | ({"close_two"} if rendered else set())
    assert "label" not in sent["ref_txt"] and sent["ref_txt"]["shapes"] == ["99999999999"]
    labels = {name: e.get("label") for name, e in sent.items() if name != "ref_txt"}
    if ner:
        assert labels == {
            "close_sel": "Close loan 99999999999",
            "memo_txt": "Memo for loan 99999999999",
            "nom_ok": "Loan 99999999999 nominee",
            "nominee_ok": "Nominee <PERSON_NAME> agrees",
            **({"close_two": "Close loan 99999999999"} if rendered else {}),
        }
    else:
        assert set(labels.values()) == {None}
    assert writes == []  # D4: nothing submitted


def test_t3_labels_carry_over_to_a_re_extracted_tab_of_the_same_field() -> None:
    """The obfuscated capture re-extracts the page in a fresh tab: a field T3 labelled on the first
    visit keeps its label there (same key and signature), so the capture masks what the inventory
    says is personal; another field under the same key is not given it."""
    from dps.layers.scanner.crawler import ScanRunner

    def snap(*fields: Any) -> PageSnapshot:
        return PageSnapshot(
            url="http://127.0.0.1/x",
            title="x",
            forms=[],
            fields=list(fields),
            texts=[],
            links=[],
            clickables=[],
            frames=[],
        )

    first = field_facts(key="0:1", name="refcd", label="Ref code", value="EMP-004211")
    unclassified = FieldClassification(
        entity_type=None, sensitivity=None, confidence=0.0, detectors=[], evidence=[]
    )
    t3 = FieldClassification(
        entity_type="EMPLOYEE_ID",
        sensitivity="HIGH",
        confidence=0.85,
        detectors=[LLM_LABEL_TIER],
        evidence=[LLM_LABEL_TIER],
    )
    again = ScanRunner._with_labels({"0:1": unclassified}, snap(first), snap(first), {"0:1": t3})
    assert again["0:1"] == t3
    other = field_facts(key="0:1", name="branch", label="Branch")
    moved = ScanRunner._with_labels({"0:1": unclassified}, snap(other), snap(first), {"0:1": t3})
    assert moved["0:1"] == unclassified
