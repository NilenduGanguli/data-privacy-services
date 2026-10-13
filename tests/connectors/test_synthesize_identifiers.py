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
import math
from collections import Counter
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
from dps.engine.synth.surrogates import (  # noqa: E402
    SurrogateEngine,
    generate_synthesis_seed,
    seed_bytes,
)
from dps.engine.taxonomy import Sensitivity, entity_info  # noqa: E402
from dps.jobs import JobState  # noqa: E402
from dps.jobs_models import (  # noqa: E402
    ClassificationSnapshot,
    ObjectRef,
    RunSpec,
    SnapshotOverride,
    SpecError,
)
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


# -- G2 review r1: B1 (classified attributes) and B2 (unclassified keys) ----------------------

ADDRESSES = 400
ADDRESSES_DDL = (
    "address_id bigint PRIMARY KEY, customer_id bigint NOT NULL, kind varchar(12) NOT NULL, "
    "line1 varchar(200) NOT NULL, city varchar(80) NOT NULL, state_code varchar(8), "
    "postal_code varchar(10) NOT NULL, country_code char(2) NOT NULL, gender varchar(6), "
    "age smallint, moved_in date NOT NULL"
)


def _addresses(n: int) -> list[tuple[Any, ...]]:
    """The demo ``bank.addresses`` shape plus gender, age and a date (synthetic fixture values)."""
    eng = SurrogateEngine(seed_bytes(generate_synthesis_seed()), default_locale="en_IN")
    return [
        (
            700_000_001 + i,
            10_000_001 + i % 300,
            ["home", "work"][i % 2],
            eng.surrogate("STREET_ADDRESS", "12 Placeholder Road", "en_IN", f"a{i}")[:200],
            eng.surrogate("CITY", "Pune", "en_IN", f"c{i}")[:80],
            "MH",
            f"{400001 + (i * 7919) % 99000:06d}",
            "IN",
            ["female", "male"][i % 2],
            18 + (i * 13) % 70,
            date(2010 + i % 12, 1 + i % 12, 1 + (i * 7) % 28),
        )
        for i in range(n)
    ]


def _chance_bound(values: list[Any], n: int) -> int:
    """Most row-aligned equalities ``n`` independent draws can reach by chance: a draw from any
    distribution equals a given row's value with probability at most the source's top frequency
    ``p``; 5 standard deviations above ``n·p`` (plus 3) is never reached by chance, and a copied
    column reaches ``n``."""
    p = max(Counter(values).values()) / len(values)
    return math.ceil(n * p + 5 * math.sqrt(n * p * (1 - p)) + 3)


def _personal_attributes(snap: ClassificationSnapshot) -> list[str]:
    """Columns discovery classified with an entity type that is neither a key, a join identifier
    nor MEDIUM+ (the LOW attributes ``keep_non_sensitive`` keeps) and whose synthesize method is
    ``keep``: B1's columns."""
    keep = {"keep"}
    out = []
    for c in snap.columns:
        method = c.synthesize_method or c.synthesize_suggested
        if c.entity_type is None or c.is_key or method is None or method.method not in keep:
            continue
        if entity_info(c.entity_type).sensitivity.rank < Sensitivity.MEDIUM.rank:
            out.append(c.name)
    return out


def _pseudonym_label(job: Any, column: str) -> str:
    assert job.plan is not None
    entry = next(p for p in job.plan["pseudonyms"] if p["name"] == column)
    return str(entry.get("label") or entry["entity_type"])


@pytest.mark.parametrize("tabular", ["copula", "none"])
async def test_classified_attributes_are_never_copied_row_aligned(
    tabular: str,
    handler_ctx: HandlerContext,
    source_pg: SourcePg,
    register_source: RegisterSource,
) -> None:
    """B1: city, postal code, gender, age and dates a policy keeps (LOW) are drawn by the copula
    or surrogated — row-aligned equality with the target row's own source row stays at chance."""
    addresses = await source_pg.table("addresses", ADDRESSES_DDL, _addresses(ADDRESSES))
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, addresses)
    personal = _personal_attributes(snap)
    assert {"city", "postal_code", "gender", "age"} <= set(personal), personal
    seed = generate_synthesis_seed()
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": f"addresses_{tabular}"},
    }
    job = await _submit(
        handler_ctx,
        _spec(
            src.id,
            addresses,
            snap.id,
            target,
            options={"tabular": tabular},
            consistency={"synthesis_seed": seed.model_dump(mode="json")},
        ),
    )
    assert job.state == JobState.succeeded, (job.error_code, job.error)

    # -- source vs target, counts only: align each target row with its own source row ----------
    keys = KeyPseudonyms(seed_bytes(seed))
    label = _pseudonym_label(job, "address_id")
    source_rows = {
        keys.integer(label, r["address_id"]): dict(r)
        for r in await source_pg.admin.fetch(f'SELECT * FROM "{source_pg.schema}".addresses')
    }
    target_rows = [
        dict(r)
        for r in await source_pg.admin.fetch(
            f'SELECT * FROM "{source_pg.schema}"."addresses_{tabular}"'
        )
    ]
    real_keys = {r["address_id"] for r in source_rows.values()}
    assert len(target_rows) == ADDRESSES
    assert sum(1 for r in target_rows if r["address_id"] in real_keys) == 0
    aligned = [(r, source_rows[r["address_id"]]) for r in target_rows]  # every row aligns
    equal = {c: sum(1 for t, s in aligned if t[c] is not None and t[c] == s[c]) for c in personal}
    bounds = {c: _chance_bound([s[c] for s in source_rows.values()], ADDRESSES) for c in personal}
    over = {c: (equal[c], bounds[c]) for c in personal if equal[c] > bounds[c]}
    assert not over, over  # before the fix: city, postal_code, gender, age 400/400
    qi = ("city", "postal_code", "gender", "age")
    assert sum(1 for t, s in aligned if all(t[c] == s[c] for c in qi)) <= 2

    # -- the evidence says how each of them was produced ----------------------------------------
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None
    pack = ev.pack.model_dump(mode="json")
    methods = {c["path"]: c["method"] for c in pack["columns"]}
    drawn = set((pack.get("tabular") or {}).get("columns") or [])
    for c in personal:
        assert methods.get(f"$.{c}") == "surrogate" or f"$.{c}" in drawn, (c, methods, drawn)
    if tabular == "copula":
        assert {"$.gender", "$.age"} <= drawn, drawn  # modelled where the engine can
    assert pack["verification"]["passed"] is True, pack["verification"]


LOANS = 300
LOANS_DDL = (
    "loan_id bigint PRIMARY KEY, customer_id bigint NOT NULL, product varchar(24) NOT NULL, "
    "principal numeric(14,2) NOT NULL, rate numeric(5,3) NOT NULL, term_months smallint NOT NULL, "
    "status varchar(12) NOT NULL, internal_score smallint NOT NULL"
)


def _loans(n: int) -> list[tuple[Any, ...]]:
    return [
        (
            5_000_000_001 + i * 3,
            10_000_001 + i % 250,
            ["home", "auto", "personal"][i % 3],
            Decimal(100000 + (i * 7919) % 9_000_000) / 100,
            Decimal(5 + (i * 37) % 900) / 100,
            [12, 24, 36, 60][i % 4],
            ["active", "closed"][i % 2],
            300 + (i * 31) % 600,
        )
        for i in range(n)
    ]


def _repayments(n: int) -> list[tuple[Any, ...]]:
    return [
        (
            8_000_000_001 + i * 7,
            5_000_000_001 + (i % LOANS) * 3,
            Decimal(1000 + (i * 331) % 90_000) / 100,
            ["ach", "card", "cash"][i % 3],
        )
        for i in range(n)
    ]


async def test_unclassified_keys_and_their_foreign_keys_are_pseudonymised(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    """B2: ``loan_id`` (a key discovery does not classify) is never written with its real value
    next to the pseudonymised ``customer_id`` — nor is a declared foreign key to it — and the
    group's tables still join."""
    loans = await source_pg.table("loans", LOANS_DDL, _loans(LOANS))
    repayments = await source_pg.table(
        "repayments",
        "repayment_id bigint PRIMARY KEY, "
        f'loan_id bigint NOT NULL REFERENCES "{source_pg.schema}".loans, '
        "amount numeric(12,2) NOT NULL, channel varchar(8) NOT NULL",
        _repayments(900),
    )
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap_l = await discover_and_accept(handler_ctx, "t1", src.id, loans)
    snap_r = await discover_and_accept(handler_ctx, "t1", src.id, repayments)
    entity_l = {c.name: c.entity_type for c in snap_l.columns}
    entity_r = {c.name: c.entity_type for c in snap_r.columns}
    assert entity_l["loan_id"] is None and entity_l["customer_id"] == "CUSTOMER_ID", entity_l
    assert entity_r["loan_id"] is None and entity_r["repayment_id"] is None, entity_r
    seed = generate_synthesis_seed()
    consistency = {"synthesis_seed": seed.model_dump(mode="json")}

    def table(name: str) -> dict[str, Any]:
        return {
            "write_mode": "table",
            "connection_id": uat.id,
            "object": {"owner": source_pg.schema, "name": name},
        }

    job_l = await _submit(
        handler_ctx,
        _spec(
            src.id,
            loans,
            snap_l.id,
            table("loans_synth"),
            options={"tabular": "copula"},
            consistency=consistency,
        ),
    )
    assert job_l.state == JobState.succeeded, (job_l.error_code, job_l.error)
    job_r = await _submit(
        handler_ctx,
        _spec(src.id, repayments, snap_r.id, table("repayments_synth"), consistency=consistency),
    )
    assert job_r.state == JobState.succeeded, (job_r.error_code, job_r.error)

    # -- source vs target, counts only ----------------------------------------------------------
    real_loan_ids = set(await _column(source_pg, "loans", "loan_id"))
    real_repayment_ids = set(await _column(source_pg, "repayments", "repayment_id"))
    real_customers = set(await _column(source_pg, "loans", "customer_id"))
    tgt_loan_ids = await _column(source_pg, "loans_synth", "loan_id")
    tgt_fk = await _column(source_pg, "repayments_synth", "loan_id")
    tgt_repayment_ids = await _column(source_pg, "repayments_synth", "repayment_id")
    tgt_customers = await _column(source_pg, "loans_synth", "customer_id")
    survivors = {
        "loans_synth.loan_id": len(real_loan_ids & set(tgt_loan_ids)),
        "loans_synth.customer_id": len(real_customers & set(tgt_customers)),
        "repayments_synth.loan_id": len(real_loan_ids & set(tgt_fk)),
        "repayments_synth.repayment_id": len(real_repayment_ids & set(tgt_repayment_ids)),
    }
    assert survivors == dict.fromkeys(survivors, 0), survivors  # before the fix: loan_id 300
    assert len(set(tgt_loan_ids)) == LOANS and len(set(tgt_repayment_ids)) == 900  # still keys
    joined = await source_pg.admin.fetchval(
        f'SELECT count(*) FROM "{source_pg.schema}".repayments_synth r '
        f'JOIN "{source_pg.schema}".loans_synth l USING (loan_id)'
    )
    assert joined == 900  # the foreign key still joins inside the job group
    labels = {
        "loans.loan_id": _pseudonym_label(job_l, "loan_id"),
        "repayments.loan_id": _pseudonym_label(job_r, "loan_id"),
    }
    assert (
        labels["loans.loan_id"]
        == labels["repayments.loan_id"]
        == (f"KEY|{source_pg.schema}.loans.loan_id")
    ), labels
    for job, column in ((job_l, "loan_id"), (job_r, "repayment_id"), (job_r, "loan_id")):
        ev = await get_evidence("t1", job.progress.evidence_id)
        assert ev is not None
        assert {c["path"]: c["method"] for c in ev.pack.columns}[f"$.{column}"] == "key_pseudonym"
