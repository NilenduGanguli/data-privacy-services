"""S3 review F-10: caller-chosen custom entity names stay out of the anonymous ``/metrics`` and
cannot grow the process-global taxonomy without bound.

``dps_findings_total`` labels every custom type ``custom`` (built-in types keep their names), a
policy carries at most 50 custom recognizers (20 patterns, 1000 deny/allow terms each) and 500
declared fields, and the custom registry stores only tiers above the MEDIUM default, up to
``MAX_CUSTOM_ENTITIES`` names — a new name past that is refused (fail closed: 400), never
downgraded."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dps.app import create_app
from dps.config import Settings
from dps.engine import taxonomy
from dps.engine.detect.custom import RecognizerSpec
from dps.engine.policy import DetectConfig
from dps.engine.taxonomy import TAXONOMY, EntityInfo, Family, Sensitivity, register_entity


@pytest.fixture
def restore_taxonomy() -> Iterator[None]:
    saved = dict(TAXONOMY)
    yield
    TAXONOMY.clear()
    TAXONOMY.update(saved)


def _recognizer(entity: str, **kw: object) -> dict[str, object]:
    return {
        "name": f"r-{entity.lower()}",
        "supported_entity": entity,
        "patterns": [{"name": "p", "regex": r"\bPF-\d{4}\b", "score": 0.9}],
        **kw,
    }


@pytest.mark.usefixtures("restore_taxonomy")
def test_custom_entity_names_never_become_metric_labels() -> None:
    client = TestClient(
        create_app(Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub"))
    )
    for i in range(5):
        policy = {
            "preset": "gdpr-pseudonymise",
            "detect": {"custom_recognizers": [_recognizer(f"PROJECT_FALCON_{i}_ID")]},
        }
        r = client.post("/api/v1/detect", json={"text": "ref PF-1234 here", "policy": policy})
        assert r.status_code == 200, r.text
        assert any(f["entity_type"] == f"PROJECT_FALCON_{i}_ID" for f in r.json()["findings"])
    exposition = client.get("/metrics").text
    assert "PROJECT_FALCON" not in exposition
    assert 'entity_type="custom"' in exposition


@pytest.mark.usefixtures("restore_taxonomy")
def test_the_custom_registry_is_bounded_and_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    before = len(TAXONOMY)
    for i in range(50):  # MEDIUM / LOW are the unregistered default: nothing is stored
        register_entity(EntityInfo(f"PLAIN_{i}", Family.OTHER, Sensitivity.LOW, "declared"))
        register_entity(EntityInfo(f"PLAINM_{i}", Family.OTHER, Sensitivity.MEDIUM, "declared"))
    assert len(TAXONOMY) == before
    monkeypatch.setattr(taxonomy, "MAX_CUSTOM_ENTITIES", 3)
    for i in range(3):
        register_entity(EntityInfo(f"HOT_{i}", Family.OTHER, Sensitivity.HIGH, "custom:x"))
    register_entity(EntityInfo("HOT_0", Family.OTHER, Sensitivity.CRITICAL, "custom:x"))  # raise
    assert taxonomy.entity_info("HOT_0").sensitivity is Sensitivity.CRITICAL
    with pytest.raises(ValueError, match="registry is full"):
        register_entity(EntityInfo("HOT_9", Family.OTHER, Sensitivity.HIGH, "custom:x"))
    assert taxonomy.entity_info("HOT_9").sensitivity is Sensitivity.MEDIUM  # never stored


def test_a_policy_cannot_carry_unbounded_declarations() -> None:
    with pytest.raises(ValidationError):
        DetectConfig(custom_recognizers=[_recognizer(f"E_{i}") for i in range(51)])  # type: ignore[misc]
    many = [{"name": f"p{i}", "regex": r"\d{4}", "score": 0.5} for i in range(21)]
    with pytest.raises(ValidationError):
        RecognizerSpec.model_validate({**_recognizer("E_X"), "patterns": many})
    with pytest.raises(ValidationError):
        RecognizerSpec.model_validate(
            {**_recognizer("E_X"), "deny_list": [f"t{i}" for i in range(1001)]}
        )
    with pytest.raises(ValidationError):
        DetectConfig(
            declared_fields=[{"path": f"$.f{i}", "entity_type": "E_X"} for i in range(501)]
        )  # type: ignore[misc]
    DetectConfig(custom_recognizers=[_recognizer(f"E_{i}") for i in range(50)])  # type: ignore[misc]
