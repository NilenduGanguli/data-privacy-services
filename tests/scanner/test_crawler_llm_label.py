"""M5 Task 8B (L3): T3 metadata labelling in the crawl of the demo site.

The policy opts in (``llm_label`` in ``detect.tiers``); the LLM is faked at the provider seam only
(``_stellar``), so ``LlmGateway.complete`` — and its egress verifier under ``strict`` — sees every
prompt as in production. One call per page with leftover fields; the labelled fields carry
``llm_label`` into the inventory and the fill plan; no prompt holds a page value; nothing is
submitted (the demo site's counters).
"""

from __future__ import annotations

import json
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
from tests.scanner.helpers import field_facts, make_runtime, received

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
