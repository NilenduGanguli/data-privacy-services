"""The console Playground's own samples, run through the API exactly as the compose stack is set up.

Regression for M0 verification round 1, B1: under the shipped compose configuration (NER off,
patterns only) the Text sample came back with the customer's name in clear while the report said
the leakage sweep passed. The samples are read from ``frontend/src/lib/samples.json`` (the file
the Playground imports) and the settings from ``docker-compose.yml``, so a change to either is
tested here.
"""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import pytest
import yaml
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.config import Settings
from dps.engine.detect.ensemble import Detector, DetectorConfig
from dps.engine.detect.ner_presidio import presidio_available
from dps.engine.planner import build_plan
from dps.engine.policy import preset_names, resolve_policy
from dps.engine.synth.personas import PersonaFactory
from dps.engine.synth.surrogates import SurrogateEngine, SynthesisSeed
from dps.engine.transform.apply import anchor_for
from dps.engine.transform.crypto import b64u
from dps.engine.verify import (
    NER_GAP,
    field_name_only_entities,
    label_only_entities,
    label_only_gap,
    name_only_gap,
    normalise,
)

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
    assert verification["coverage"]["gaps"] == [
        NER_GAP["off"],
        name_only_gap(field_name_only_entities(ner=False)),
        label_only_gap(label_only_entities()),
    ]


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


# --- seeds that used to put a part of the record into the output (A-prepare review r1, B1) -----
# Under compose (DPS_DEFAULT_LOCALE=en_IN) the persona for the sample's record comes from Faker
# en_IN, which can draw the customer's own surname, a surname holding it, or a street named after
# it. Before the fix the output kept that part of the name while the report said the sweep passed
# (or, for the street, the strict sweep refused the request). A coincidence of another kind made
# the strict sweep refuse too: a phone or card surrogate holding the card expiry's digits "0928".
# Each case first proves that the seed still collides for the samples as they are today.
CASES = (
    ("text", 443, "surname"),  # "Krishnan"
    ("text", 163, "surname"),  # "Ramakrishnan"
    ("json", 190, "surname"),  # "Krishnan"
    ("json", 258, "surname"),  # "Balakrishnan"
    ("json", 27, "street"),  # "04/90, Krishnan Ganj"
    ("json", 1514, "street"),  # "76/87, Krishna Nagar": "krishnanagar" once normalised
    ("json", 2757, "phone"),  # the phone surrogate holds 0928
    ("json", 544, "card"),  # the card surrogate holds 0928
)


def _raw_seed(i: int) -> bytes:
    return hashlib.sha256(f"dps-en-in-collision-{i}".encode()).digest()


def _sample_anchor(kind: str) -> str:
    payload = {"text": SAMPLES["text"]} if kind == "text" else {"data": SAMPLES["json"]}
    policy = resolve_policy("synthetic-realistic", default_name="synthetic-realistic")
    findings = Detector(DetectorConfig()).detect(**payload).findings
    return anchor_for(SAMPLES[kind], build_plan(findings, policy, "synthesize", fpe_enabled=False))


def _collides(kind: str, i: int, what: str) -> bool:
    """The undeclared draw for this seed holds a part of the record."""
    if what in ("surname", "street"):
        bare = PersonaFactory(_raw_seed(i)).for_anchor(_sample_anchor(kind), "en_IN")
        return "krishnan" in normalise(bare.last_name if what == "surname" else bare.street)
    entity, original = {
        "phone": ("PHONE", SAMPLES["json"]["phone"]),
        "card": ("CARD_PAN", SAMPLES["json"]["card"]["pan"]),
    }[what]
    fake = SurrogateEngine(_raw_seed(i)).surrogate(entity, original, "en_IN", "any")
    return normalise(SAMPLES["json"]["card"]["expiry"]) in normalise(fake)


@pytest.mark.parametrize(("kind", "i", "what"), CASES)
@pytest.mark.parametrize("policy", sorted(preset_names()))
def test_a_colliding_en_in_seed_never_returns_a_part_of_the_record(
    client: Any, policy: str, kind: str, i: int, what: str
) -> None:
    assert _collides(kind, i, what), "seed no longer collides: pick another"
    seed = SynthesisSeed(seed=b64u(_raw_seed(i))).model_dump(mode="json")
    payload = {"text": SAMPLES["text"]} if kind == "text" else {"data": SAMPLES["json"]}
    r = client.post(
        "/api/v1/synthesize",
        json={**payload, "policy": policy, "options": {"consistency": {"synthesis_seed": seed}}},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert _no_name(body["result"]), f"{kind}/{policy}/{i}: a part of the name survived"
    assert body["report"]["verification"]["passed"]
