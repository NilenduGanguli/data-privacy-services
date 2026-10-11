"""redact_spec: the value-free copy of a run/discover spec stored in dps_job.spec."""

from __future__ import annotations

import json

from dps.engine.transform.crypto import generate_key_material
from dps.jobs_models import DiscoverSpec, ObjectRef, RunSpec, Selection, redact_spec
from dps.layers.connectors.selection import parameterize_literals


def test_run_spec_redaction_keeps_no_values_or_keys() -> None:
    km = generate_key_material("key").model_dump(mode="json")
    sel = parameterize_literals(Selection(where="full_name = 'Ananya Krishnan'"), "postgres")
    spec = RunSpec.model_validate(
        {
            "mode": "obfuscate",
            "snapshot_id": "snap_1",
            "source": {
                "connection_id": "c_1",
                "object": {"owner": "bank", "name": "customers"},
                "selection": sel.model_dump(),
            },
            "target": {
                "write_mode": "table",
                "connection_id": "c_2",
                "object": {"owner": "bank_uat", "name": "customers"},
            },
            "consistency": {"key_material": km, "synthesis_seed": {"seed": "AAAA" * 11}},
            "purpose": {"ticket": "CHG-1", "legal_basis": "legitimate interest"},
        }
    )
    red = redact_spec(spec, engine="postgres")
    text = json.dumps(red)
    assert "Ananya" not in text and km["dek"] not in text and "AAAAAAAA" not in text
    assert red["source"]["selection"]["params"] == {"l1": "<bound>"}
    assert red["consistency"]["kid"] == km["kid"] and red["consistency"]["caller_key"] is True
    assert len(red["consistency"]["seed_fingerprint"]) == 16


def test_discover_spec_redaction() -> None:
    sel = parameterize_literals(Selection(where="country_code = 'MX'"), "postgres")
    red = redact_spec(
        DiscoverSpec(connection_id="c", objects=[ObjectRef(owner="bank", name="t")], selection=sel),
        engine="postgres",
    )
    assert "MX" not in json.dumps(red) and red["selection"]["where"] == '"country_code" = :l1'
