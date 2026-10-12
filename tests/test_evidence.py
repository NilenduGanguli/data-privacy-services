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
    synth = build_pack(evidence_inputs(mode="synthesize"))
    synth_page = render_html(synth, sha=pack_sha256(synth))
    assert "synthetic — measured, not claimed anonymous" in synth_page
    assert "<script" not in synth_page
