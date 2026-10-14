"""Evidence packs: value-free content, labels, reveal map, hashes and the HTML page (offline)."""

from __future__ import annotations

import json

from dps.engine.policy import MethodSpec
from dps.layers.connectors.base import PkOffset
from dps.layers.connectors.evidence import (
    LABEL_OBFUSCATE,
    LABEL_SYNTHESIZE,
    EvidencePack,
    build_pack,
    canonical_json,
    pack_sha256,
    render_html,
)
from tests.connectors.factories import evidence_inputs

SECRETS = ["Ananya", "example-mail", "10000001", "2025-01-01", "AAAAAAAAAAAA"]


def test_pack_is_value_free_and_labelled() -> None:
    pack = build_pack(evidence_inputs(mode="obfuscate"))
    text = canonical_json(pack).decode()
    assert pack.label == LABEL_OBFUSCATE and not any(s in text for s in SECRETS)
    assert pack.source["selection"]["where_template"] == '"created_at" >= :l1'
    assert pack.source["selection"]["binds"] == 1
    assert pack.consistency["kid"].startswith("k_") and "dek" not in text
    assert all(
        "checkpoint" not in json.dumps(p) and "last_key" not in json.dumps(p)
        for p in pack.partitions
    )
    assert {c["path"] for c in pack.reveal_map} == {"$.full_name", "$.email", "$.dob"}
    assert all(c["reveal"] and c["anchor_path"] == "$.customer_id" for c in pack.reveal_map)
    assert build_pack(evidence_inputs(mode="synthesize")).label == LABEL_SYNTHESIZE


def test_pack_sections_follow_the_run() -> None:
    pack = build_pack(evidence_inputs(mode="obfuscate"))
    assert pack.target["write_mode"] == "table" and pack.target["created"] is True
    assert pack.target["object"] == "bank_uat.customers"
    assert pack.target["connection_name"] == "bank-uat"
    assert pack.policy == {"name": "gdpr-pseudonymise", "hash": pack.policy["hash"]}
    assert pack.snapshot["overrides"] == 0 and pack.snapshot["accepted_by"] == "owner-key"
    assert pack.counts["rows_written"] == 2500 and pack.counts["partitions"] == 2
    assert pack.counts["resumed_partitions"] == 1 and pack.counts["batches"] == 4
    assert pack.counts["values_transformed"] == 7400
    by_path = {c["path"]: c for c in pack.columns}
    assert by_path["$.dob"]["values"] == 2400 and by_path["$.email"]["method"] == "email"
    assert pack.verification["fallbacks"] == {"fpe_domain_too_small": 2}
    assert pack.consistency["key_custody"]["returned"] is True
    assert pack.partitions[1] == {
        "no": 1,
        "rows": 1250,
        "attempts": 2,
        "resumed": 1,
        "state": "done",
    }
    assert pack.timings["duration_s"] >= 0 and pack.requested_by == "k2"
    assert len(pack.config_hash) == 64 and pack.id.startswith("ev_")
    synth = build_pack(evidence_inputs(mode="synthesize"))
    assert synth.reveal_map == [] and synth.consistency["kid"] is None
    assert synth.consistency["seed_fingerprint"] == "0123456789abcdef"


def test_reveal_map_marks_date_shift_without_a_recoverable_anchor() -> None:
    inputs = evidence_inputs(mode="obfuscate")
    assert inputs.plan is not None
    hashed_anchor = inputs.plan.columns + [
        inputs.plan.columns[0].model_copy(
            update={
                "path": "$.customer_id",
                "name": "customer_id",
                "entity_type": "CUSTOMER_ID",
                "method": MethodSpec(method="hmac_pseudonym"),
                "reversible": False,
            }
        )
    ]
    inputs.plan = inputs.plan.model_copy(update={"columns": hashed_anchor})
    reveal = {c["path"]: c["reveal"] for c in build_pack(inputs).reveal_map}
    assert reveal == {"$.full_name": True, "$.email": True, "$.dob": False}
    offset = evidence_inputs(mode="obfuscate")
    assert offset.plan is not None
    offset.plan = offset.plan.model_copy(
        update={
            "target": offset.plan.target.model_copy(
                update={"write_mode": "append", "pk_strategy": PkOffset(offset=10)}
            )
        }
    )
    assert {c["path"]: c["reveal"] for c in build_pack(offset).reveal_map}["$.dob"] is False


def test_a_failed_prepare_still_makes_a_pack() -> None:
    inputs = evidence_inputs(mode="obfuscate", outcome="failed")
    inputs.plan, inputs.snapshot = None, None
    inputs.errors = [{"code": "row_count_mismatch", "where": "prepare"}]
    inputs.verification = {"mode": "strict", "hits": 0, "passed": True}
    pack = build_pack(inputs)
    assert pack.outcome == "failed" and pack.columns == [] and pack.reveal_map == []
    assert pack.errors == [{"code": "row_count_mismatch", "where": "prepare"}]
    assert pack.source["object"] == "bank.customers" and pack.snapshot["id"] is None
    assert EvidencePack.model_validate_json(canonical_json(pack)) == pack


def test_hash_is_stable_and_sensitive() -> None:
    pack = build_pack(evidence_inputs(mode="obfuscate"))
    assert pack_sha256(pack) == pack_sha256(pack.model_copy(deep=True))
    assert pack_sha256(pack) != pack_sha256(pack.model_copy(update={"outcome": "failed"}))
    again = EvidencePack.model_validate(json.loads(canonical_json(pack)))
    assert pack_sha256(again) == pack_sha256(pack)


def test_html_escapes_everything_and_has_no_scripts() -> None:
    inputs = evidence_inputs(mode="obfuscate", connection_name="crm<script>alert(1)</script>")
    pack = build_pack(inputs)
    page = render_html(pack, sha=pack_sha256(pack))
    assert "<script" not in page and "&lt;script&gt;" in page and pack_sha256(pack) in page
    assert "pseudonymised — still personal data" in page and "Signatures" in page
    assert "Checkpoint positions" in page and "@media print" in page
    assert "<th>Files</th>" not in page  # a table target has no files row
    synth = build_pack(evidence_inputs(mode="synthesize"))
    synth_page = render_html(synth, sha=pack_sha256(synth))
    assert "synthetic — measured, not claimed anonymous" in synth_page
    assert "<script" not in synth_page


def test_a_failed_run_never_reads_as_verified() -> None:
    """N3: a failed job with 0 rows is "not verified · job failed", even when the counters it
    reached held no hit, and an older pack that recorded passed: true renders the same."""
    inputs = evidence_inputs(mode="obfuscate", outcome="failed")
    inputs.plan = None
    inputs.errors = [{"code": "length_overflow", "where": "prepare"}]
    inputs.verification = {"mode": "strict", "hits": 0, "passed": True, "status": "passed"}
    pack = build_pack(inputs)
    assert pack.verification["passed"] is False and pack.verification["status"] == "incomplete"
    page = render_html(pack, sha=pack_sha256(pack))
    assert "not verified · job failed" in page and "verified · 0 leaks" not in page
    legacy = pack.model_copy(
        update={"verification": {**pack.verification, "passed": True, "status": "passed"}}
    )
    legacy_page = render_html(legacy, sha=pack_sha256(legacy))
    assert "not verified · job failed" in legacy_page and "verified · 0 leaks" not in legacy_page
    canceled = build_pack(evidence_inputs(mode="obfuscate", outcome="canceled"))
    assert canceled.verification["passed"] is False
    assert "not verified · job canceled" in render_html(canceled, sha=pack_sha256(canceled))
    done = build_pack(evidence_inputs(mode="obfuscate"))
    assert done.verification["passed"] is True
    assert "verified · 0 leaks" in render_html(done, sha=pack_sha256(done))


def test_fallback_counters_carry_the_prefix_once() -> None:
    """M2 verify r2 issue 3: the engine's fallback notes already start with ``fallback:``; the job
    counters used to read ``fallback:fallback:fpe_domain_too_small``."""
    from dps.engine.batch import BatchResult
    from dps.engine.verify import Verification
    from dps.layers.connectors.evidence import _split_counters
    from dps.layers.connectors.jobs_run import _counters

    result = BatchResult(
        rows=[],
        values_by_method={"det_token": 4, "fpe_text": 2},
        fallbacks={"fallback:fpe_domain_too_small": 3, "fallback:fpe_unavailable": 1},
        verification=Verification(passed=True, checked=6),
        values_by_column={"tax_id": 3},
    )
    counters = _counters(result)
    assert counters == {
        "det_token": 4,
        "fpe_text": 2,
        "fallback:fpe_domain_too_small": 3,
        "fallback:fpe_unavailable": 1,
        "col:tax_id": 3,
    }
    assert not any(k.startswith("fallback:fallback:") for k in counters)
    # counters stored before the fix still read as one reason in the pack
    _, fallbacks, _, _ = _split_counters(
        {"fallback:fallback:fpe_domain_too_small": 5, "fallback:fpe_domain_too_small": 2}
    )
    assert fallbacks == {"fpe_domain_too_small": 7}


def test_m5_synthesize_pack_carries_the_measured_blocks() -> None:
    """M5 Task 6: fidelity/privacy/tabular come from ``progress.metrics``; redraws are summed."""
    inputs = evidence_inputs(mode="synthesize")
    inputs.job.spec["options"]["tabular"] = "copula"
    metrics = {
        "fidelity": {"score": 0.93, "ks": {"$.balance": 0.02}, "engine": "copula"},
        "privacy": {"exact_row_matches": 0, "nndr": {"ratio": 1.02, "measured": 1.0}},
        "tabular": {"engine": "copula", "columns": ["$.balance"], "excluded": []},
    }
    inputs.job.progress.metrics = metrics
    inputs.job.progress.values_by_method |= {"copula": 5000, "copula_resample": 3}
    inputs.policy_version = "4"
    pack = build_pack(inputs)
    assert pack.fidelity == metrics["fidelity"] and pack.tabular == metrics["tabular"]
    assert pack.privacy is not None and pack.privacy["exact_row_resamples_run"] == 3
    assert pack.privacy["nndr"]["ratio"] == 1.02 and pack.label == LABEL_SYNTHESIZE
    assert pack.policy["version"] == "4"
    # a redraw is a count, never a transformed value
    assert pack.counts["values_transformed"] == 7400 + 5000
    by_path = {c["path"]: c for c in pack.columns}
    assert by_path["$.email"]["data_type"] and by_path["$.email"]["override"] is False
    assert isinstance(by_path["$.email"]["confidence"], float)
    html = render_html(pack, sha=pack_sha256(pack))
    assert "Tabular model" in html and "exact_row_resamples_run" in html


def test_m5_synthesize_without_tabular_says_nothing_was_measured() -> None:
    pack = build_pack(evidence_inputs(mode="synthesize"))
    assert pack.fidelity is None and pack.tabular is None and pack.utility is None
    assert pack.privacy == {
        "engine": "none",
        "measured": False,
        "reason": "options.tabular=none: non-sensitive columns were copied from the source",
    }
    assert build_pack(evidence_inputs(mode="obfuscate")).privacy is None
    assert "version" not in build_pack(evidence_inputs(mode="obfuscate")).policy


def test_a_mongo_pack_counts_each_nested_leaf_under_its_path() -> None:
    """S1-M3 r1 (privacy-console B2): the transformer counts a nested document leaf under its
    path (``col:$.contact.email``), a top-level field under its name (``col:customerId``); the
    pack reads each count with the same key, so a nested leaf's VALUES is never empty."""
    import dataclasses

    from dps.engine.batch import ColumnPlan

    inputs = evidence_inputs(mode="obfuscate")
    assert inputs.plan is not None
    leaves = [
        ("$.contact.email", "contact", "EMAIL", "email"),
        ("$.contact.mobile", "contact", "PHONE", "det_token"),
        ("$.accounts[*].iban", "accounts", "IBAN", "det_token"),
        ("$.customerId", "customerId", "CUSTOMER_ID", "det_token"),
    ]
    plans = [
        ColumnPlan(
            path=path,
            name=name,
            entity_type=entity,
            method=MethodSpec(method=method),
            text_column=False,
            reversible=True,
            kind="text",
            length=None,
        )
        for path, name, entity, method in leaves
    ]
    counters = {
        "det_token": 9000,
        "email": 3469,
        "col:$.contact.email": 3469,
        "col:$.contact.mobile": 3400,
        "col:$.accounts[*].iban": 2100,
        "col:customerId": 3469,
    }
    job = inputs.job.model_copy(
        update={"progress": inputs.job.progress.model_copy(update={"values_by_method": counters})}
    )
    pack = build_pack(
        dataclasses.replace(inputs, job=job, plan=inputs.plan.model_copy(update={"columns": plans}))
    )
    values = {c["path"]: c["values"] for c in pack.columns}
    assert values == {
        "$.contact.email": 3469,
        "$.contact.mobile": 3400,
        "$.accounts[*].iban": 2100,
        "$.customerId": 3469,
    }
