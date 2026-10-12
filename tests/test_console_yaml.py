"""The console's policy export (Policies page → Download YAML) loads back as the same policy.

``frontend/src/lib/yaml.ts`` emits YAML from the inline policy the page sends to the API. This
runs that emitter under Node (TypeScript type stripping, Node ≥ 22.6) over every preset's
effective policy and its inline-draft form, and reads the output with PyYAML: the YAML must be
valid and equal to the JSON it came from. Skips when Node is not installed (the app image).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from dps.engine.policy import load_preset, preset_names

ROOT = Path(__file__).resolve().parents[1]
EMITTER = ROOT / "frontend" / "src" / "lib" / "yaml.ts"
SCRIPT = """
const { toYaml } = await import(process.argv[1]);
let input = '';
for await (const chunk of process.stdin) input += chunk;
process.stdout.write(JSON.stringify(JSON.parse(input).map((value) => toYaml(value))));
"""


def _node() -> str:
    node = shutil.which("node")
    if node is None:
        pytest.skip("node is not installed")
    version = subprocess.run([node, "--version"], capture_output=True, text=True, check=True)
    major, minor = (int(p) for p in re.findall(r"\d+", version.stdout)[:2])
    if (major, minor) < (22, 6):
        pytest.skip("node >= 22.6 is needed to load TypeScript")
    return node


def _emit(values: list[Any]) -> list[str]:
    run = subprocess.run(
        [
            _node(),
            "--experimental-strip-types",
            "--no-warnings",
            "--input-type=module",
            "-e",
            SCRIPT,
            EMITTER.as_uri(),
        ],
        input=json.dumps(values),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-2000:]
    out = json.loads(run.stdout)
    assert isinstance(out, list)
    return [str(item) for item in out]


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
    for value, text in zip(values, _emit(values), strict=True):
        assert yaml.safe_load(text) == value, text
