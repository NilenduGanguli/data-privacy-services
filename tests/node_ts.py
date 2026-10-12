"""Run a console TypeScript module under Node (type stripping, Node >= 22.6) from a Python test.

The console's pure modules (``frontend/src/lib/*.ts``) hold logic the API depends on — inline
policies, recognizer specs, the YAML export — so tests check them against the engine here.
Skips when Node is missing or too old (the app image has no Node).
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[1]
LIB = ROOT / "frontend" / "src" / "lib"
_RUNNER = """
const mod = await import(process.argv[1]);
let input = '';
for await (const chunk of process.stdin) input += chunk;
const run = new Function('mod', 'input', process.argv[2]);
process.stdout.write(JSON.stringify(await run(mod, JSON.parse(input))));
"""


def node() -> str:
    """The ``node`` binary, or skip."""
    found = shutil.which("node")
    if found is None:
        pytest.skip("node is not installed")
    version = subprocess.run([found, "--version"], capture_output=True, text=True, check=True)
    major, minor = (int(p) for p in re.findall(r"\d+", version.stdout)[:2])
    if (major, minor) < (22, 6):
        pytest.skip("node >= 22.6 is needed to load TypeScript")
    return found


def run_ts(module: str, body: str, data: Any = None) -> Any:
    """Import ``frontend/src/lib/<module>`` as ``mod`` and return ``body`` (a function body that
    sees ``mod`` and ``input`` = ``data``) as JSON."""
    run = subprocess.run(
        [
            node(),
            "--experimental-strip-types",
            "--no-warnings",
            "--input-type=module",
            "-e",
            _RUNNER,
            (LIB / module).as_uri(),
            body,
        ],
        input=json.dumps(data),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert run.returncode == 0, run.stderr[-2000:]
    return json.loads(run.stdout)
