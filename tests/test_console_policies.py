"""The Policies page's draft logic (``frontend/src/lib/policyDraft.ts``) against the engine.

The page builds inline policies, recognizer specs and plan requests in the browser; these tests run
the same TypeScript under Node and send what it builds through the real validators and routes: the
method lists match the engine's registry, every preset's inline draft validates and takes effect,
the default recognizer finds exactly what the live preview shows, and the plan sample plans.
"""

from __future__ import annotations

import json
from typing import Any

from fastapi.testclient import TestClient

from dps.app import create_app
from dps.audit import MemoryAuditSink
from dps.config import Settings
from dps.engine.policy import (
    METHOD_NAMES,
    REVERSIBLE_METHODS,
    load_preset,
    preset_names,
    resolve_policy,
)
from tests.node_ts import run_ts


def _client() -> TestClient:
    settings = Settings(  # type: ignore[call-arg]
        _env_file=None, env="local", auth_enabled=False, llm_provider="stub", ner_enabled=False
    )
    return TestClient(create_app(settings, audit_sink=MemoryAuditSink()))


def test_method_lists_match_the_engine() -> None:
    out = run_ts(
        "policyDraft.ts", "return { methods: mod.METHODS, reversible: [...mod.REVERSIBLE] };"
    )
    assert set(out["methods"]) == set(METHOD_NAMES) - {"surrogate"}
    assert len(out["methods"]) == len(set(out["methods"]))
    assert set(out["reversible"]) == set(REVERSIBLE_METHODS)


def test_every_presets_inline_draft_validates_and_takes_effect() -> None:
    bases = {name: load_preset(name).model_dump(mode="json") for name in preset_names()}
    body = """
      const out = {};
      for (const [name, base] of Object.entries(input)) {
        let draft = mod.withTreatment(mod.EMPTY_DRAFT, base, 'CVV', 'null');
        draft = mod.declareField(draft, { path: '$.loan.internal_score', entity_type: 'BUSINESS_SENSITIVE', method: 'bucket' });
        draft = { ...draft, recognizers: [mod.recognizerSpec(mod.DEFAULT_RECOGNIZER)] };
        out[name] = { inline: mod.inlinePolicy(name, base, draft), changes: mod.draftChanges(draft) };
      }
      return out;
    """
    drafts: dict[str, Any] = run_ts("policyDraft.ts", body, bases)
    for name, item in drafts.items():
        policy = resolve_policy(item["inline"], default_name="gdpr-pseudonymise")
        base = load_preset(name)
        assert policy.name == f"{name}+overrides"
        assert policy.method_for("CVV", "obfuscate").method == "null"
        bucket = policy.method_for("BUSINESS_SENSITIVE", "obfuscate")
        assert (bucket.method, bucket.params) == ("bucket", {"ranges": [0, 300, 600, 900]})
        assert [d.path for d in policy.detect.declared_fields] == [
            *(d.path for d in base.detect.declared_fields),
            "$.loan.internal_score",
        ]
        assert policy.detect.custom_recognizers[-1].name == "employee-id"
        assert item["changes"] == 4  # CVV, the declaration, its treatment, the recognizer


def test_the_default_recognizer_finds_the_same_values_live_and_on_the_engine() -> None:
    page = run_ts(
        "policyDraft.ts",
        """
        const live = mod.livePreview(mod.DEFAULT_RECOGNIZER.regex, mod.listOf(mod.DEFAULT_RECOGNIZER.allow), mod.TEST_TEXT);
        return { spec: mod.recognizerSpec(mod.DEFAULT_RECOGNIZER), text: mod.TEST_TEXT,
                 live: live.hits.filter((h) => !h.allowed).map((h) => [h.start, h.end]) };
        """,
    )
    with _client() as c:
        r = c.post(
            "/api/v1/policies/test-recognizer",
            json={"recognizer": page["spec"], "text": page["text"]},
        )
    assert r.status_code == 200, r.text
    engine = [[m["start"], m["end"]] for m in r.json()["matches"]]
    assert engine == page["live"]
    assert [page["text"][s:e] for s, e in engine] == ["EMP-204417"]


def test_the_plan_sample_plans_with_a_draft() -> None:
    page = run_ts(
        "policyDraft.ts",
        """
        const base = input;
        const draft = mod.withTreatment(mod.EMPTY_DRAFT, base, 'CVV', 'det_token');
        return { sample: mod.PLAN_SAMPLE, inline: mod.inlinePolicy('gdpr-pseudonymise', base, draft) };
        """,
        load_preset("gdpr-pseudonymise").model_dump(mode="json"),
    )
    with _client() as c:
        r = c.post(
            "/api/v1/policies/plan",
            json={
                "data": json.loads(page["sample"]),
                "policy": page["inline"],
                "mode": "obfuscate",
            },
        )
    assert r.status_code == 200, r.text
    rows = r.json()["plan"]
    assert len(rows) >= 4
    assert {row["method"] for row in rows if row["entity_type"] == "CVV"} == {"det_token"}
