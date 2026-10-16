"""M5 Task 8B (L2) end to end: ``discover_job`` labels the columns T0–T2 left unclassified when the
job's policy lists ``llm_label``, before the snapshot is stored, and records
``progress.metrics.llm``. The LLM is faked at the provider seam only (``_stellar``): the egress
verifier in ``LlmGateway.complete`` (strict) sees every prompt. Against the metadata DB and the
lane's source-pg."""

from __future__ import annotations

import dataclasses
import json
from typing import Any

import pytest

from dps import jobs, snapshots
from dps.config import Settings
from dps.engine.service import PrivacyEngine
from dps.ids import PREFIX_JOB, new_id
from dps.jobs import JobKind, JobPhase, JobState
from dps.jobs_models import DiscoverSpec, SealedSelection, redact_spec
from dps.layers.connectors.discovery import discover_job
from dps.llm.gateway import LlmGateway, LlmResult
from dps.observability import LLM_STATS, LlmStats
from dps.secrets import seal_json
from dps.worker import HandlerContext
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]

REFS = ["AB-1234", "CD-9876", "EF-5521", "GH-0042", "JK-7310", "LM-2208"]
POLICY: dict[str, Any] = {
    "preset": "gdpr-pseudonymise",
    "detect": {"tiers": ["structural", "patterns", "llm_label"]},
}


class ProviderSeam(LlmGateway):
    """A configured ``stellar`` provider labelling ``cust_ref_cd`` (by the name it is sent);
    every prompt is kept."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.messages: list[list[dict[str, str]]] = []

    async def _stellar(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> LlmResult:
        self.messages.append(messages)
        sent = json.loads(messages[1]["content"])
        reply = [
            {"ref": e["ref"], "entity_type": "CUSTOMER_ID", "confidence": 0.93}
            for e in sent
            if e["name"] == "cust_ref_cd"
        ]
        return LlmResult(text=json.dumps(reply), provider="stellar", model="t")


async def test_discover_job_labels_leftover_columns_and_records_the_calls(
    migrated_db: Any,
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: Any,
    kek: bytes,
) -> None:
    obj = await source_pg.table(
        "loans",
        "loan_id bigint PRIMARY KEY, cust_ref_cd varchar(12), branch_cd varchar(8)",
        [(500 + i, REFS[i % len(REFS)], ["NORTH", "SOUTH"][i % 2]) for i in range(60)],
    )
    conn = await register_source("t1")
    settings = handler_ctx.settings.model_copy(
        update={
            "llm_provider": "stellar",
            "stellar_base_url": "https://stellar.test",
            "coin_url": "https://coin.test",
            "llm_egress_check": "strict",
        }
    )
    seam = ProviderSeam(settings)
    ctx = dataclasses.replace(
        handler_ctx,
        settings=settings,
        engine=PrivacyEngine(
            settings, gateway=seam, cipher_factory=handler_ctx.engine.cipher_factory
        ),
    )
    spec = DiscoverSpec(connection_id=conn.id, objects=[obj], policy=POLICY, sample_rows=50)
    job_id = new_id(PREFIX_JOB)
    job = await jobs.create_job(
        tenant_id="t1",
        kind=JobKind.discover,
        phase=JobPhase.discover,
        job_id=job_id,
        spec=redact_spec(spec, engine="postgres"),
        connection_id=conn.id,
        object_ref=obj.fqn(),
        sealed_selection=seal_json(
            kek, SealedSelection(policy=POLICY), aad=f"dps_job.spec:{job_id}"
        ),
    )
    [claimed] = await jobs.claim_jobs(ctx.worker_id, kinds=(JobKind.discover,), batch=1)
    token = LLM_STATS.set(LlmStats())  # as Consumer._guard binds it for the handler's task
    try:
        await discover_job(ctx, claimed)
    finally:
        LLM_STATS.reset(token)
    done = await jobs.get_job("t1", job.id)
    assert done is not None and done.state == JobState.succeeded, done.error_code
    assert done.progress.metrics == {"llm": {"calls": 1, "raw_values": 0, "blocked": 0}}
    snap = await snapshots.get_snapshot("t1", done.progress.snapshot_ids[0])
    assert snap is not None
    cols = {c.name: c for c in snap.columns}
    ref = cols["cust_ref_cd"]
    assert ref.entity_type == "CUSTOMER_ID" and ref.confidence == 0.85
    assert "llm_label" in ref.detectors and ref.method.method != "keep"
    assert cols["branch_cd"].entity_type is None and "llm_label" not in cols["branch_cd"].detectors
    # one value-free prompt: names and shapes, never a sampled value
    assert len(seam.messages) == 1
    prompt = json.dumps(seam.messages)
    assert "cust_ref_cd" in prompt and "XX-9999" in prompt
    assert not any(v in prompt for v in [*REFS, "NORTH", "SOUTH"])
    stored = json.dumps(snap.model_dump(mode="json"))
    assert not any(v in stored for v in REFS)


async def test_discover_job_without_the_tier_records_no_llm_figures(
    migrated_db: Any,
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: Any,
    kek: bytes,
) -> None:
    obj = await source_pg.customers_table(rows=60)
    conn = await register_source("t1")
    spec = DiscoverSpec(connection_id=conn.id, objects=[obj], sample_rows=50)
    job = await jobs.create_job(
        tenant_id="t1",
        kind=JobKind.discover,
        phase=JobPhase.discover,
        job_id=new_id(PREFIX_JOB),
        spec=redact_spec(spec, engine="postgres"),
        connection_id=conn.id,
        object_ref=obj.fqn(),
    )
    [claimed] = await jobs.claim_jobs(handler_ctx.worker_id, kinds=(JobKind.discover,), batch=1)
    await discover_job(handler_ctx, claimed)
    done = await jobs.get_job("t1", job.id)
    assert done is not None and done.state == JobState.succeeded
    assert "llm" not in done.progress.metrics


#: The seeded demo ``bank`` tables (``tools/seed_sources.py``).
BANK_TABLES = (
    "accounts",
    "addresses",
    "cards",
    "contacts",
    "customers",
    "employees",
    "kyc_documents",
    "loan_applicants",
    "loans",
    "transactions",
)


class AnySeam(LlmGateway):
    """A configured provider that answers every labelling prompt with no verdict."""

    def __init__(self, settings: Settings) -> None:
        super().__init__(settings)
        self.messages: list[list[dict[str, str]]] = []

    async def _stellar(
        self, messages: list[dict[str, str]], temperature: float, max_tokens: int
    ) -> LlmResult:
        self.messages.append(messages)
        return LlmResult(text="[]", provider="stellar", model="t")


@pytest.mark.parametrize("table", BANK_TABLES)
async def test_t3_reaches_the_provider_on_every_seeded_bank_table(
    migrated_db: Any,
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: Any,
    kek: bytes,
    table: str,
) -> None:
    """FINAL r1 privacy-ops B1: under strict egress, T3 was blocked on 5 of the 10 demo tables (a
    sampled ``channel``, ``doc_type``, ``product``, ``id_type`` or ``segment`` code is also a word
    of the labelling instructions' type list; a sampled risk score matched a rate's digits or a
    value shape). Every table with leftover columns now reaches the provider, value-free."""
    import os

    seeded = await source_pg.admin.fetchval("SELECT to_regclass($1)", f"bank.{table}")
    if seeded is None:
        message = "the demo bank is not seeded (docker compose --profile sources up seed-sources)"
        if os.environ.get("DPS_TEST_REQUIRE_SERVICES") == "1":
            pytest.fail(message)
        pytest.skip(message)
    conn = await register_source("t1")
    settings = handler_ctx.settings.model_copy(
        update={
            "llm_provider": "stellar",
            "stellar_base_url": "https://stellar.test",
            "coin_url": "https://coin.test",
            "llm_egress_check": "strict",
        }
    )
    seam = AnySeam(settings)
    ctx = dataclasses.replace(
        handler_ctx,
        settings=settings,
        engine=PrivacyEngine(
            settings, gateway=seam, cipher_factory=handler_ctx.engine.cipher_factory
        ),
    )
    from dps.layers.connectors.base import ObjectRef

    obj = ObjectRef(owner="bank", name=table)
    spec = DiscoverSpec(connection_id=conn.id, objects=[obj], policy=POLICY, sample_rows=500)
    job_id = new_id(PREFIX_JOB)
    await jobs.create_job(
        tenant_id="t1",
        kind=JobKind.discover,
        phase=JobPhase.discover,
        job_id=job_id,
        spec=redact_spec(spec, engine="postgres"),
        connection_id=conn.id,
        object_ref=obj.fqn(),
        sealed_selection=seal_json(
            kek, SealedSelection(policy=POLICY), aad=f"dps_job.spec:{job_id}"
        ),
    )
    [claimed] = await jobs.claim_jobs(ctx.worker_id, kinds=(JobKind.discover,), batch=1)
    token = LLM_STATS.set(LlmStats())
    try:
        await discover_job(ctx, claimed)
    finally:
        LLM_STATS.reset(token)
    done = await jobs.get_job("t1", job_id)
    assert done is not None and done.state == JobState.succeeded, done.error_code
    llm = done.progress.metrics.get("llm") or {}
    assert llm.get("blocked") == 0, (table, llm)
    snap = await snapshots.get_snapshot("t1", done.progress.snapshot_ids[0])
    assert snap is not None
    leftovers = [c for c in snap.columns if c.entity_type is None and not c.is_key]
    if leftovers:
        assert llm.get("calls", 0) >= 1 and len(seam.messages) == llm["calls"], (table, llm)
        assert llm.get("raw_values") == 0
