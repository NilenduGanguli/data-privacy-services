"""The console's policy export (Policies page → Download YAML) loads back as the same policy.

``frontend/src/lib/yaml.ts`` emits YAML from the inline policy the page sends to the API. This
runs that emitter under Node (``tests/node_ts.py``) over every preset's effective policy and its
inline-draft form, and reads the output with PyYAML: the YAML must be valid and equal to the JSON
it came from. Skips when Node is not installed (the app image).
"""

from __future__ import annotations

from typing import Any

import yaml

from dps.engine.policy import load_preset, preset_names
from tests.node_ts import run_ts


def _inputs() -> list[Any]:
    values: list[Any] = []
    for name in preset_names():
        policy = load_preset(name).model_dump(mode="json")
        values.append(policy)
        values.append(
            {
                "preset": name,
                "treat": {"CVV": {"method": "redact", "params": {}}},
                "detect": {
                    "declared_fields": [
                        {"path": "$.loan.internal_score", "entity_type": "BUSINESS_SENSITIVE"}
                    ],
                    "custom_recognizers": [
                        {
                            "name": "employee-id",
                            "supported_entity": "EMPLOYEE_ID",
                            "patterns": [
                                {"name": "employee-id", "regex": r"\bEMP-\d{6}\b", "score": 0.85}
                            ],
                            "context": ["staff id", "employee", "emp no"],
                            "deny_list": [],
                            "allow_list": ["EMP-000000", "yes", "null", "0.5", "a: b", "#x"],
                        }
                    ],
                },
            }
        )
    values.append({"edge": ["", "~", "-1", ".inf", "NaN", "y", "Ünïcode", 'q"uote', "back\\slash"]})
    values.append({"nested": [[1, 2], [], {}, [{"a": [None, True, 0.5]}]]})
    return values


def test_policy_yaml_export_loads_back_as_the_same_policy() -> None:
    values = _inputs()
    texts = run_ts("yaml.ts", "return input.map((value) => mod.toYaml(value));", values)
    for value, text in zip(values, texts, strict=True):
        assert yaml.safe_load(text) == value, text
