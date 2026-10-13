"""D-A end to end: synthetic tables never carry linkable real identifiers (programme plan §3).

Two tables of one job group (the same synthesis seed) are synthesized from the lane's
``source-pg``: ``customers`` with the copula engine and ``accounts`` (foreign key
``customer_id``) with surrogates only. Every claim is a **source-vs-target comparison** (counts
only): no source key, account number or customer id survives in any target, the foreign key still
joins, an ``in_place`` synthesize of a table with a sensitive key is refused before a job exists,
and a column reviewed as "not personal" that holds personal data is kept only with a
``review_required`` verification.
"""

from __future__ import annotations

import json
from collections.abc import Awaitable, Callable
from datetime import date
from decimal import Decimal
from typing import Any

import pytest

pytest.importorskip("scipy")

from dps import jobs  # noqa: E402
from dps.auth import LOCAL_PRINCIPAL  # noqa: E402
from dps.connections import Connection  # noqa: E402
from dps.engine.synth.keys import KeyPseudonyms  # noqa: E402
from dps.engine.synth.surrogates import generate_synthesis_seed, seed_bytes  # noqa: E402
from dps.jobs import JobState  # noqa: E402
from dps.jobs_models import ObjectRef, RunSpec, SnapshotOverride, SpecError  # noqa: E402
from dps.layers.connectors.evidence import get_evidence  # noqa: E402
from dps.layers.connectors.submit import submit_run  # noqa: E402
from dps.worker import HandlerContext  # noqa: E402
from tests.connectors.helpers import discover_and_accept, drive  # noqa: E402
from tests.fixtures.sources import SourcePg  # noqa: E402

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]
PURPOSE = {"ticket": "CHG-51771", "legal_basis": "legitimate interest — synthetic UAT data"}
RegisterSource = Callable[..., Awaitable[Connection]]
CUSTOMERS = 400
ACCOUNTS_DDL = (
    "account_number varchar(16) PRIMARY KEY, customer_id bigint NOT NULL, "
    "product varchar(12) NOT NULL, balance numeric(14,2) NOT NULL, opened_on date NOT NULL, "
    "contact varchar(254)"
)


def _accounts(n: int) -> list[tuple[Any, ...]]:
    return [
        (
            f"9{(31_000_000_000_000 + i * 7919) % 10**15:015d}",
            10_000_001 + (i * 37) % CUSTOMERS,
            ["savings", "current", "deposit"][i % 3],
            Decimal(1000 + (i * 7331) % 900_000) / 100,
            date(2015 + i % 9, 1 + i % 12, 1 + i % 27),
            f"holder.{i:04d}@example-mail.in",
        )
        for i in range(n)
    ]


def _spec(
    conn_id: str, obj: ObjectRef, snap_id: str, target: dict[str, Any], **extra: Any
) -> RunSpec:
    return RunSpec.model_validate(
        {
            "mode": "synthesize",
            "snapshot_id": snap_id,
            "purpose": PURPOSE,
            "target": target,
            "source": {
                "connection_id": conn_id,
                "object": obj.model_dump(),
                "parallelism": 2,
                "partition_rows": 1000,
                "batch_rows": 200,
            },
            **extra,
        }
    )


async def _submit(ctx: HandlerContext, spec: RunSpec) -> Any:
    sub = await submit_run(
        spec, tenant_id="t1", principal=LOCAL_PRINCIPAL, settings=ctx.settings, kek=ctx.kek
    )
    return await drive(ctx, "t1", sub.job.id)


async def _column(pg: SourcePg, table: str, column: str) -> list[Any]:
    rows = await pg.admin.fetch(f'SELECT "{column}" AS v FROM "{pg.schema}"."{table}"')
    return [r["v"] for r in rows]


async def test_a_job_group_replaces_every_identifier_and_keeps_the_joins(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    customers = await source_pg.customers_table(rows=CUSTOMERS)
    accounts = await source_pg.table("accounts", ACCOUNTS_DDL, _accounts(900))
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap_c = await discover_and_accept(handler_ctx, "t1", src.id, customers)
    snap_a = await discover_and_accept(handler_ctx, "t1", src.id, accounts)
    entity = {c.name: c.entity_type for c in snap_a.columns}
    assert entity["customer_id"] == "CUSTOMER_ID" and entity["account_number"] is not None
    seed = generate_synthesis_seed()
    consistency = {"synthesis_seed": seed.model_dump(mode="json")}

    def table(name: str) -> dict[str, Any]:
        return {
            "write_mode": "table",
            "connection_id": uat.id,
            "object": {"owner": source_pg.schema, "name": name},
        }

    job_c = await _submit(
        handler_ctx,
        _spec(
            src.id,
            customers,
            snap_c.id,
            table("customers_synth"),
            options={"tabular": "copula"},
            consistency=consistency,
        ),
    )
    assert job_c.state == JobState.succeeded, (job_c.error_code, job_c.error)
    job_a = await _submit(
        handler_ctx,
        _spec(src.id, accounts, snap_a.id, table("accounts_synth"), consistency=consistency),
    )
    assert job_a.state == JobState.succeeded, (job_a.error_code, job_a.error)

    # -- source vs target, counts only ----------------------------------------------------------
    src_customer_ids = set(await _column(source_pg, "customers", "customer_id"))
    src_account_customers = await _column(source_pg, "accounts", "customer_id")
    src_numbers = set(await _column(source_pg, "accounts", "account_number"))
    tgt_customer_ids = await _column(source_pg, "customers_synth", "customer_id")
    tgt_account_customers = await _column(source_pg, "accounts_synth", "customer_id")
    tgt_numbers = await _column(source_pg, "accounts_synth", "account_number")
    every_source_id = src_customer_ids | set(src_account_customers)
    survivors = {
        "customers_synth.customer_id": len(every_source_id & set(tgt_customer_ids)),
        "accounts_synth.customer_id": len(every_source_id & set(tgt_account_customers)),
        "accounts_synth.account_number": len(src_numbers & set(tgt_numbers)),
    }
    assert survivors == dict.fromkeys(survivors, 0), survivors
    assert len(set(tgt_customer_ids)) == CUSTOMERS and len(set(tgt_numbers)) == 900  # still keys
    # the foreign key joins the synthetic parent: same customer, same pseudonym, in both tables
    joined = await source_pg.admin.fetchval(
        f'SELECT count(*) FROM "{source_pg.schema}".accounts_synth a '
        f'JOIN "{source_pg.schema}".customers_synth c USING (customer_id)'
    )
    assert joined == 900
    keys = KeyPseudonyms(seed_bytes(seed))
    assert sorted(tgt_account_customers) == sorted(
        keys.integer("CUSTOMER_ID", v) for v in src_account_customers
    )
    # no source value of a sensitive column of either table anywhere in the targets
    secrets = {str(v) for v in every_source_id | src_numbers}
    secrets |= {str(v) for v in await _column(source_pg, "customers", "national_id") if v}
    secrets |= {str(v) for v in await _column(source_pg, "customers", "email") if v}
    for name in ("customers_synth", "accounts_synth"):
        rows = await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}"."{name}"')
        found = sum(1 for r in rows for v in r.values() if v is not None and str(v) in secrets)
        assert found == 0, (name, found)

    # -- the evidence names the pseudonymised columns ---------------------------------------------
    for job in (job_c, job_a):
        ev = await get_evidence("t1", job.progress.evidence_id)
        assert ev is not None
        methods = {c["path"]: c["method"] for c in ev.pack.columns}
        assert methods["$.customer_id"] == "key_pseudonym", methods
        assert ev.pack.verification["passed"] is True, ev.pack.verification
        dump = json.dumps(ev.pack.model_dump(mode="json"))
        assert not any(s in dump for s in list(secrets)[:50])
    ev_a = await get_evidence("t1", job_a.progress.evidence_id)
    assert ev_a is not None
    assert {c["path"]: c["method"] for c in ev_a.pack.columns}["$.account_number"] == (
        "key_pseudonym"
    )


async def test_in_place_synthesize_of_a_sensitive_key_is_refused_before_a_job_exists(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    customers = await source_pg.customers_table(rows=60)
    rw = await register_source("t1", ref="dps/src/test-pg-rw", roles={"source", "target"})
    snap = await discover_and_accept(handler_ctx, "t1", rw.id, customers)
    before = len((await jobs.list_jobs("t1", kind=jobs.JobKind.run))[0])
    with pytest.raises(SpecError) as err:
        await submit_run(
            _spec(rw.id, customers, snap.id, {"write_mode": "in_place", "confirm_write": True}),
            tenant_id="t1",
            principal=LOCAL_PRINCIPAL,
            settings=handler_ctx.settings,
            kek=handler_ctx.kek,
        )
    assert (err.value.code, err.value.status) == ("synthesize_in_place_sensitive_key", 422)
    assert len((await jobs.list_jobs("t1", kind=jobs.JobKind.run))[0]) == before


async def test_a_kept_column_that_holds_personal_data_needs_review(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    accounts = await source_pg.table("accounts", ACCOUNTS_DDL, _accounts(120))
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    # the owner reviews ``contact`` (e-mail addresses) as not personal: it is copied as it is
    review = [SnapshotOverride(path="$.contact", entity_type=None)]
    snap = await discover_and_accept(handler_ctx, "t1", src.id, accounts, overrides=review)
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": "accounts_review"},
    }
    job = await _submit(handler_ctx, _spec(src.id, accounts, snap.id, target))
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None
    v = ev.pack.verification
    assert v["review_required"] is True and v["passed"] is False, v
    assert v["review_columns"] == ["contact"]
    assert v["post_sample"]["synthetic_kept"]["kept_column_hits"] > 0
    assert "example-mail.in" not in json.dumps(ev.pack.model_dump(mode="json"))
