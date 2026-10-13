"""Policy store helpers that need no database (M5 Task 15): flattening, diffs, refs, YAML."""

from __future__ import annotations

from dps.engine.policy import load_preset
from dps.policy_store import REF, diff_policies, flatten_policy, policy_yaml


def test_flatten_keys_lists_by_name_or_path() -> None:
    flat = flatten_policy(
        {
            "detect": {
                "min_score": 0.6,
                "custom_recognizers": [
                    {"name": "emp", "patterns": [{"name": "p", "regex": "x", "score": 0.9}]}
                ],
                "declared_fields": [{"path": "$.a", "entity_type": "X"}],
            }
        }
    )
    assert flat["detect.min_score"] == 0.6
    assert flat["detect.custom_recognizers[emp].patterns[p].score"] == 0.9
    assert flat["detect.declared_fields[$.a].entity_type"] == "X"


def test_diff_reports_add_remove_change_and_ignores_name_version() -> None:
    v1 = load_preset("gdpr-pseudonymise").model_dump(mode="json")
    v2 = {
        **v1,
        "name": "other",
        "version": 2,
        "detect": {**v1["detect"], "min_score": 0.55},
        "treat": {k: v for k, v in v1["treat"].items() if k != "CVV"}
        | {"BUSINESS_SENSITIVE": {"method": "bucket", "params": {"ranges": [0, 300, 600, 900]}}},
    }
    changes = {(c.op, c.path) for c in diff_policies(v1, v2)}
    assert ("change", "detect.min_score") in changes
    assert ("remove", "treat.CVV.method") in changes
    assert ("add", "treat.BUSINESS_SENSITIVE.method") in changes
    assert ("add", "treat.BUSINESS_SENSITIVE.params.ranges") in changes
    assert not any(path in ("name", "version") for _, path in changes)


def test_reference_grammar_and_yaml() -> None:
    first, second = REF.match("@retail-kyc"), REF.match("@retail-kyc:v12")
    assert first is not None and first.groups() == ("retail-kyc", None)
    assert second is not None and second.groups() == ("retail-kyc", "12")
    assert REF.match("@Retail") is None and REF.match("retail-kyc") is None
    assert REF.match("@x:v") is None
    text = policy_yaml(load_preset("pci-dss").model_dump(mode="json"))
    assert text.startswith("consistency:") and "CARD_PAN:" in text


def test_yaml_writes_when_rules_as_if() -> None:
    body = {
        "synthesize": {
            "constraints": [
                {"path": "$.STATUS", "when": [{"if": {"path": "$.B", "op": "<", "value": 0}}]}
            ]
        }
    }
    text = policy_yaml({"synthesize": {"constraints": [{"path": "$.S", "when": [{"if_": {}}]}]}})
    assert "if:" in text and "if_" not in text
    assert "if:" in policy_yaml(body)
