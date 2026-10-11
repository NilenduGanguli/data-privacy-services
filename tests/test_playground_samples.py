"""The console Playground's own samples, run through the API exactly as the compose stack is set up.

Regression for M0 verification round 1, B1: under the shipped compose configuration (NER off,
patterns only) the Text sample came back with the customer's name in clear while the report said
the leakage sweep passed. The samples are read from ``frontend/src/lib/samples.json`` (the file
the Playground imports) and the settings from ``docker-compose.yml``, so a change to either is
tested here.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.config import Settings
from dps.engine.detect.ner_presidio import presidio_available
from dps.engine.policy import preset_names
from dps.engine.verify import NER_GAP, normalise

ROOT = Path(__file__).resolve().parents[1]
SAMPLES = json.loads((ROOT / "frontend/src/lib/samples.json").read_text(encoding="utf-8"))
NAME_PARTS = ("Ananya", "Krishnan")
_DEFAULT = re.compile(r"\$\{[A-Z0-9_]+:-([^}]*)\}")


def compose_settings(**overrides: Any) -> Settings:
    """``Settings`` from the compose file's ``app.environment``, each ``${VAR:-default}`` at its
    default. The image is built without the ``[ner]`` extra, so NER is off there; it is set off
    explicitly here because a developer venv may have Presidio installed (auto would turn it on).
    """
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["app"]["environment"]
    fields = {
        name.removeprefix("DPS_").lower(): _DEFAULT.sub(lambda m: m.group(1), str(value))
        for name, value in env.items()
    }
    fields.setdefault("ner_enabled", "false")
    fields.update(rate_limit_enabled="false", **overrides)
    return Settings(_env_file=None, **fields)


@pytest.fixture
def client() -> Any:
    settings = compose_settings()
    assert settings.auth_enabled and settings.bootstrap_api_key and settings.ner_enabled is False
    with TestClient(create_app(settings)) as c:
        c.headers["X-API-KEY"] = settings.bootstrap_api_key
        yield c


def _no_name(result: Any) -> bool:
    out = normalise(result if isinstance(result, str) else json.dumps(result, ensure_ascii=False))
    return all(normalise(part) not in out for part in NAME_PARTS)


@pytest.mark.parametrize("policy", sorted(preset_names()))
@pytest.mark.parametrize("mode", ["obfuscate", "synthesize"])
def test_text_sample_never_returns_the_name(client: Any, mode: str, policy: str) -> None:
    r = client.post(f"/api/v1/{mode}", json={"text": SAMPLES["text"], "policy": policy})
    assert r.status_code == 200, r.text
    body = r.json()
    assert _no_name(body["result"]), f"{mode}/{policy}: the customer's name survived"
    assert any(f["entity_type"] == "PERSON_NAME" for f in body["findings"])
    verification = body["report"]["verification"]
    # the name was found and checked, and the report still says what it could not cover
    assert verification["passed"] and verification["status"] == "partial"
    assert verification["coverage"]["ner"] == "off"
    assert verification["coverage"]["gaps"] == [NER_GAP["off"]]


def test_text_sample_reveals_back_to_the_original(client: Any) -> None:
    obf = client.post("/api/v1/obfuscate", json={"text": SAMPLES["text"]}).json()
    rev = client.post(
        "/api/v1/reveal",
        json={
            "text": obf["result"],
            "key_material": obf["key_material"],
            "reveal_hints": obf["reveal_hints"],
        },
    )
    assert rev.status_code == 200 and rev.json()["result"] == SAMPLES["text"]


@pytest.mark.parametrize("mode", ["obfuscate", "synthesize"])
def test_json_sample_never_returns_the_name(client: Any, mode: str) -> None:
    r = client.post(f"/api/v1/{mode}", json={"data": SAMPLES["json"]})
    assert r.status_code == 200, r.text
    body = r.json()
    assert _no_name(body["result"]) and body["report"]["verification"]["passed"]


@pytest.mark.ner
def test_text_sample_with_ner_on_still_never_returns_the_name() -> None:
    if not presidio_available():
        pytest.skip("needs the [ner] extra and a spaCy model")
    import spacy.util

    if not any(
        spacy.util.is_package(m) for m in ("en_core_web_sm", "en_core_web_md", "en_core_web_lg")
    ):
        pytest.skip("needs an English spaCy model")
    settings = compose_settings(ner_enabled="true")
    with TestClient(create_app(settings)) as c:
        c.headers["X-API-KEY"] = settings.bootstrap_api_key
        body = c.post("/api/v1/obfuscate", json={"text": SAMPLES["text"]}).json()
    assert _no_name(body["result"])
    verification = body["report"]["verification"]
    assert verification["coverage"]["ner"] == "on" and verification["status"] == "passed"
