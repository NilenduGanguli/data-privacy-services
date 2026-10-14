"""S3 review F-10: caller-chosen custom entity names stay out of the anonymous ``/metrics`` and
cannot grow a registry without bound — and (review round 1 B3) one tenant can never fill a registry
another tenant needs.

``dps_findings_total`` labels every custom type ``custom`` (built-in types keep their names), a
policy carries at most 50 custom recognizers (20 patterns, 1000 deny/allow terms each) and 500
declared fields, and a registry stores only tiers above what a name already reads as, up to
``MAX_CUSTOM_ENTITIES`` names — a new name past that is refused (fail closed: 400), never
downgraded. Custom tiers belong to the call that declared them: every API request and every worker
handler task binds its own registry (``dps.engine.call_scope``); the process-global ``TAXONOMY``
serves only code outside a request or a job."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from dps.app import create_app
from dps.config import Settings
from dps.engine import taxonomy
from dps.engine.call_scope import call_scope
from dps.engine.detect.custom import DeclaredField, PolicyCustomDetector, RecognizerSpec
from dps.engine.detect.regex_guard import call_budget
from dps.engine.policy import DetectConfig
from dps.engine.taxonomy import (
    TAXONOMY,
    EntityInfo,
    Family,
    Sensitivity,
    entity_info,
    register_entity,
)
from dps.worker import Consumer


@pytest.fixture
def restore_taxonomy() -> Iterator[None]:
    """Start from the built-in entries (other tests register custom names outside any call) and
    put the process-global registry back afterwards."""
    saved = dict(TAXONOMY)
    TAXONOMY.clear()
    TAXONOMY.update(taxonomy._BUILTIN)
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
    """The process-global registry (code outside a request or a job)."""
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


@pytest.mark.usefixtures("restore_taxonomy")
def test_a_calls_registry_is_its_own_and_bounded(monkeypatch: pytest.MonkeyPatch) -> None:
    """A call registers into its own registry: visible to the call, gone after it, never in the
    process-global one; the cap applies per call."""
    monkeypatch.setattr(taxonomy, "MAX_CUSTOM_ENTITIES", 3)
    with call_scope():
        for i in range(3):
            register_entity(EntityInfo(f"CALL_{i}", Family.OTHER, Sensitivity.HIGH, "custom:x"))
        register_entity(EntityInfo("CALL_0", Family.OTHER, Sensitivity.CRITICAL, "custom:x"))
        assert entity_info("CALL_0").sensitivity is Sensitivity.CRITICAL
        assert entity_info("CALL_2").sensitivity is Sensitivity.HIGH
        with pytest.raises(ValueError, match="registry is full"):
            register_entity(EntityInfo("CALL_9", Family.OTHER, Sensitivity.HIGH, "custom:x"))
        with call_scope():  # another call: a fresh registry, nothing inherited
            assert entity_info("CALL_0").sensitivity is Sensitivity.MEDIUM
            register_entity(EntityInfo("CALL_9", Family.OTHER, Sensitivity.HIGH, "custom:x"))
        assert entity_info("CALL_9").sensitivity is Sensitivity.MEDIUM
    assert entity_info("CALL_0").sensitivity is Sensitivity.MEDIUM
    assert not [name for name in TAXONOMY if name.startswith("CALL_")]


_TWO_TENANT_KEYS = json.dumps(
    [
        {"name": "filler", "key": "dps_filler", "tenant_ids": ["ta"], "scopes": ["detect"]},
        {
            "name": "victim",
            "key": "dps_victim",
            "tenant_ids": ["tb"],
            "scopes": ["detect", "obfuscate"],
        },
    ]
)
_FILLER = {"X-API-KEY": "dps_filler", "X-DPS-Tenant": "ta"}
_VICTIM = {"X-API-KEY": "dps_victim", "X-DPS-Tenant": "tb"}


def _victim_policy(entity: str) -> dict[str, object]:
    return {
        "preset": "gdpr-pseudonymise",
        "detect": {
            "custom_recognizers": [
                {
                    "name": "staff-id",
                    "supported_entity": entity,
                    "sensitivity": "HIGH",
                    "patterns": [{"name": "p", "regex": r"\bEMP-\d{6}\b", "score": 0.9}],
                }
            ]
        },
    }


@pytest.mark.usefixtures("restore_taxonomy")
def test_one_tenant_cannot_fill_the_registry_another_tenant_needs() -> None:
    """S3 review round 1 B3 (the reviewer's probe): a ``detect``-only key of tenant ``ta`` sent
    policies of 50 HIGH recognizers and 500 HIGH declared fields; 10 requests filled the
    process-wide registry, and tenant ``tb``'s next new HIGH custom name answered 400 on
    ``/detect`` and ``/obfuscate`` until the process restarted."""
    client = TestClient(
        create_app(
            Settings(
                _env_file=None,
                env="local",
                auth_enabled=True,
                api_keys=_TWO_TENANT_KEYS,
                llm_provider="stub",
                rate_limit_enabled=False,
                ner_enabled=False,
            )
        )
    )
    text = "staff EMP-123456 note"
    before = client.post(
        "/api/v1/detect",
        json={"text": text, "policy": _victim_policy("BANK_STAFF_ID")},
        headers=_VICTIM,
    )
    assert before.status_code == 200, before.text
    for n in range(12):  # more names than MAX_CUSTOM_ENTITIES in all
        policy = {
            "preset": "gdpr-pseudonymise",
            "detect": {
                "custom_recognizers": [
                    {
                        "name": f"r{i}",
                        "supported_entity": f"FILL_{n}_{i}",
                        "sensitivity": "HIGH",
                        "patterns": [{"name": "p", "regex": r"\bZZ-\d{4}\b", "score": 0.5}],
                    }
                    for i in range(50)
                ],
                "declared_fields": [
                    {"path": f"$.f{i}", "entity_type": f"FILLD_{n}_{i}", "sensitivity": "HIGH"}
                    for i in range(500)
                ],
            },
        }
        filled = client.post(
            "/api/v1/detect", json={"text": "x", "policy": policy}, headers=_FILLER
        )
        assert filled.status_code == 200, (n, filled.text)
    assert len(TAXONOMY) == len(taxonomy._BUILTIN)  # nothing reached the process registry
    for route, entity in (
        ("/api/v1/detect", "BANK_BRANCH_ID"),
        ("/api/v1/obfuscate", "BANK_DESK_ID"),
    ):
        r = client.post(
            route, json={"text": text, "policy": _victim_policy(entity)}, headers=_VICTIM
        )
        assert r.status_code == 200, (route, r.text)
        found = [f for f in r.json()["findings"] if f["entity_type"] == entity]
        assert found and all(f["sensitivity"] == "HIGH" for f in found), route


@pytest.mark.usefixtures("restore_taxonomy")
def test_a_tier_never_leaks_from_one_request_into_another() -> None:
    """A request reads the tiers its own policy declares: another request naming the same custom
    type without a tier reads the MEDIUM default (it used to inherit whatever any earlier request
    of any tenant had registered in the process)."""
    client = TestClient(
        create_app(Settings(_env_file=None, env="local", auth_enabled=False, llm_provider="stub"))
    )
    text = "staff EMP-123456 note"
    declared = client.post(
        "/api/v1/detect", json={"text": text, "policy": _victim_policy("LEAK_ID")}
    ).json()
    assert {f["sensitivity"] for f in declared["findings"] if f["entity_type"] == "LEAK_ID"} == {
        "HIGH"
    }
    plain_policy = _victim_policy("LEAK_ID")
    plain_policy["detect"]["custom_recognizers"][0].pop("sensitivity")  # type: ignore[index]
    plain = client.post("/api/v1/detect", json={"text": text, "policy": plain_policy}).json()
    assert {f["sensitivity"] for f in plain["findings"] if f["entity_type"] == "LEAK_ID"} == {
        "MEDIUM"
    }


@pytest.mark.usefixtures("restore_taxonomy")
def test_each_worker_handler_task_gets_its_own_call_scope() -> None:
    """The worker binds a fresh scope per handler task (jobs and partitions): two jobs running
    side by side never see each other's custom tiers or share a pattern budget."""
    seen: dict[str, tuple[Sensitivity, Sensitivity, int]] = {}

    async def handler(own: str, tier: Sensitivity) -> None:
        PolicyCustomDetector(
            [
                RecognizerSpec(
                    name="r",
                    supported_entity=own,
                    sensitivity=tier,
                    patterns=[{"name": "p", "regex": r"\bQQ-\d{4}\b", "score": 0.9}],
                )
            ],
            [],
        )
        await asyncio.sleep(0)  # let the other handler register first
        budget = call_budget()
        assert budget is not None
        seen[own] = (
            entity_info("JOB_A_ID").sensitivity,
            entity_info("JOB_B_ID").sensitivity,
            id(budget),
        )

    async def main() -> None:
        consumer = object.__new__(Consumer)  # _guard needs no consumer state
        await asyncio.gather(
            asyncio.create_task(
                consumer._guard(handler("JOB_A_ID", Sensitivity.HIGH), tenant_id="ta")
            ),
            asyncio.create_task(
                consumer._guard(handler("JOB_B_ID", Sensitivity.CRITICAL), tenant_id="tb")
            ),
        )

    asyncio.run(main())
    assert seen["JOB_A_ID"][:2] == (Sensitivity.HIGH, Sensitivity.MEDIUM)
    assert seen["JOB_B_ID"][:2] == (Sensitivity.MEDIUM, Sensitivity.CRITICAL)
    assert seen["JOB_A_ID"][2] != seen["JOB_B_ID"][2]
    assert entity_info("JOB_A_ID").sensitivity is Sensitivity.MEDIUM


@pytest.mark.usefixtures("restore_taxonomy")
def test_a_detector_reused_by_a_later_call_registers_its_tiers_there() -> None:
    """A worker builds a job's transformer once and reuses it in every partition handler task,
    each with its own registry: the policy's tiers are registered again in each call that uses
    the detector (``CustomTiers.ensure``), never read as the MEDIUM default."""
    spec = RecognizerSpec(
        name="r",
        supported_entity="REUSED_ID",
        sensitivity=Sensitivity.HIGH,
        patterns=[{"name": "p", "regex": r"\bQQ-\d{4}\b", "score": 0.9}],
    )
    declared = DeclaredField(path="$.x", entity_type="REUSED_DECL", sensitivity="CRITICAL")
    with call_scope():
        detector = PolicyCustomDetector([spec], [declared])
        assert entity_info("REUSED_ID").sensitivity is Sensitivity.HIGH
    with call_scope():
        assert entity_info("REUSED_ID").sensitivity is Sensitivity.MEDIUM
        detector.scan("ref QQ-1234", None)
        assert entity_info("REUSED_ID").sensitivity is Sensitivity.HIGH
        assert entity_info("REUSED_DECL").sensitivity is Sensitivity.CRITICAL
    with call_scope():
        detector.declared("$.x")
        assert entity_info("REUSED_DECL").sensitivity is Sensitivity.CRITICAL
    assert entity_info("REUSED_ID").sensitivity is Sensitivity.MEDIUM


@pytest.mark.usefixtures("restore_taxonomy")
def test_a_cached_transformer_registers_its_policys_tiers_in_each_partition_call() -> None:
    """The worker's ``RuntimeCache`` shares one ``BatchTransformer`` between the partition
    handler tasks of a job; whole-value columns of a custom type read its declared tier in each."""
    from dps.engine.batch import BatchTransformer, ColumnPlan
    from dps.engine.policy import MethodSpec, resolve_policy
    from dps.engine.service import PrivacyEngine
    from dps.engine.synth.surrogates import generate_synthesis_seed
    from dps.layers.connectors.base import ColumnInfo, ObjectRef, TableShape

    policy = resolve_policy(
        {
            "preset": "gdpr-pseudonymise",
            "detect": {
                "declared_fields": [
                    {"path": "$.BADGE", "entity_type": "STAFF_BADGE", "sensitivity": "HIGH"}
                ]
            },
        },
        default_name="gdpr-pseudonymise",
    )
    shape = TableShape(
        object=ObjectRef(owner="HR", name="STAFF"),
        key=["ID"],
        key_source="primary_key",
        columns=[
            ColumnInfo(path="$.ID", name="ID", data_type="NUMBER(12)", kind="int", is_key=True),
            ColumnInfo(path="$.BADGE", name="BADGE", data_type="VARCHAR2(20)", kind="text"),
        ],
    )
    badge = ColumnPlan(
        path="$.BADGE",
        name="BADGE",
        entity_type="STAFF_BADGE",
        method=MethodSpec(method="redact"),
        text_column=False,
        reversible=False,
        kind="text",
        length=None,
    )
    engine = PrivacyEngine(
        Settings(_env_file=None, llm_provider="stub", ner_enabled=False, fpe_enabled=False)  # type: ignore[call-arg]
    )
    with call_scope():  # the first partition handler builds the job's runtime
        transformer = BatchTransformer(
            engine,
            policy=policy,
            mode="synthesize",
            columns=[badge],
            shape=shape,
            key=None,
            seed=generate_synthesis_seed(),
            locale="en_IN",
            unclassified="keep",
            verify="warn",
        )
    for row_id in (1, 2):  # later partition handlers reuse it, each in its own call
        with call_scope():
            assert entity_info("STAFF_BADGE").sensitivity is Sensitivity.MEDIUM
            transformer.transform([{"ID": row_id, "BADGE": f"B-000{row_id}"}])
            assert entity_info("STAFF_BADGE").sensitivity is Sensitivity.HIGH
    assert entity_info("STAFF_BADGE").sensitivity is Sensitivity.MEDIUM
