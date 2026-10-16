"""S8: a synthesize job over a PostgreSQL table writes one coherent person per row.

A table that splits a customer's name over ``first_name`` and ``last_name``, with their e-mail, a
nominee and call notes that repeat the customer's names after a title, is synthesized from the
lane's ``source-pg`` into a ``table`` target. Each synthetic row names one person in every column:
the e-mail is ``<first>.<last>…``, the notes name the same first and last name, the title's gender
is the first name's, and the nominee is someone else with their own e-mail. Every claim is a
**source-vs-target comparison** (counts only; no value is printed): no source name part, e-mail or
customer id survives in the target. The live Vertex run that found it (programme finish stage):
``{"first_name": "Arjun", "last_name": "Mehta", "email": "arjun.mehta@…"}`` came out
``Alfred / Michael / alfred.williams…``.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Any

import pytest
from faker.providers.person.en_IN import Provider as IndiaNames
from faker.providers.person.en_US import Provider as UsNames

from dps.auth import LOCAL_PRINCIPAL
from dps.connections import Connection
from dps.engine.synth.keys import KeyPseudonyms
from dps.engine.synth.personas import ascii_fold
from dps.engine.synth.surrogates import (
    SurrogateEngine,
    SynthesisSeed,
    generate_synthesis_seed,
    seed_bytes,
)
from dps.engine.verify import name_parts
from dps.jobs import JobState
from dps.jobs_models import RunSpec
from dps.layers.connectors.evidence import get_evidence
from dps.layers.connectors.submit import submit_run
from dps.worker import HandlerContext
from tests.connectors.helpers import discover_and_accept, drive
from tests.fixtures.sources import SourcePg

pytestmark = [pytest.mark.db, pytest.mark.integration, pytest.mark.pg_source]
PURPOSE = {"ticket": "CHG-51808", "legal_basis": "legitimate interest — synthetic UAT data"}
RegisterSource = Callable[..., Awaitable[Connection]]
ROWS = 300
PEOPLE_DDL = (
    "customer_id bigint PRIMARY KEY, first_name varchar(60) NOT NULL, "
    "last_name varchar(60) NOT NULL, email varchar(254) NOT NULL, nominee_name varchar(120), "
    "nominee_email varchar(254), notes text"
)
#: Test-only seed of the source rows (personas, reserved domains: no real person).
_ROWS_SEED = "B" * 43
MALE = frozenset(IndiaNames.first_names_male) | frozenset(UsNames.first_names_male)
FEMALE = frozenset(IndiaNames.first_names_female) | frozenset(UsNames.first_names_female)


def _people(n: int) -> list[tuple[Any, ...]]:
    """``n`` synthetic source rows: a customer, their e-mail, a nominee and call notes that call
    the customer by first name, then by title and last name (``Mr.``/``Mrs.`` by the persona)."""
    engine = SurrogateEngine(seed_bytes(SynthesisSeed(seed=_ROWS_SEED)), default_locale="en_IN")
    out: list[tuple[Any, ...]] = []
    for i in range(n):
        person = engine.persona(f"customer:{i}", "en_IN")
        nominee = engine.persona(f"nominee:{i}", "en_IN", avoid=(person.full_name,))
        title = "Mr." if person.gender == "male" else "Mrs."
        out.append(
            (
                30_000_001 + i,
                person.first_name,
                person.last_name,
                f"{person.email_local}@example-mail.in",
                nominee.full_name,
                f"{nominee.email_local}@example-mail.in",
                f"KYC call with {person.first_name}; {title} {person.last_name} prefers e-mail.",
            )
        )
    return out


def _spec(conn_id: str, obj: Any, snap_id: str, target: dict[str, Any], seed: Any) -> RunSpec:
    return RunSpec.model_validate(
        {
            "mode": "synthesize",
            "snapshot_id": snap_id,
            "purpose": PURPOSE,
            "target": target,
            "consistency": {"synthesis_seed": seed.model_dump(mode="json")},
            "source": {
                "connection_id": conn_id,
                "object": obj.model_dump(),
                "parallelism": 2,
                "partition_rows": 1000,
                "batch_rows": 100,
            },
        }
    )


def _local(name: str) -> str:
    first, last = name.split(" ")
    return f"{ascii_fold(first)}.{ascii_fold(last)}"


async def test_a_synthesize_job_writes_one_person_per_row(
    handler_ctx: HandlerContext, source_pg: SourcePg, register_source: RegisterSource
) -> None:
    people = await source_pg.table("people", PEOPLE_DDL, _people(ROWS))
    src = await register_source("t1")
    uat = await register_source("t1", ref="dps/src/test-pg-rw", roles={"target"}, name="uat")
    snap = await discover_and_accept(handler_ctx, "t1", src.id, people)
    entity = {c.name: c.entity_type for c in snap.columns}
    assert {entity[c] for c in ("first_name", "last_name", "nominee_name")} == {"PERSON_NAME"}
    assert entity["email"] == entity["nominee_email"] == "EMAIL"
    seed = generate_synthesis_seed()
    target = {
        "write_mode": "table",
        "connection_id": uat.id,
        "object": {"owner": source_pg.schema, "name": "people_synth"},
    }
    sub = await submit_run(
        _spec(src.id, people, snap.id, target, seed),
        tenant_id="t1",
        principal=LOCAL_PRINCIPAL,
        settings=handler_ctx.settings,
        kek=handler_ctx.kek,
    )
    job = await drive(handler_ctx, "t1", sub.job.id)
    assert job.state == JobState.succeeded, (job.error_code, job.error)
    ev = await get_evidence("t1", job.progress.evidence_id)
    assert ev is not None and ev.pack.verification["passed"] is True, ev.pack.verification

    schema = source_pg.schema
    source = {
        r["customer_id"]: r for r in await source_pg.admin.fetch(f'SELECT * FROM "{schema}".people')
    }
    written = await source_pg.admin.fetch(f'SELECT * FROM "{schema}".people_synth')
    assert len(written) == ROWS
    keys = KeyPseudonyms(seed_bytes(seed))
    by_key = {keys.integer("CUSTOMER_ID", k): row for k, row in source.items()}

    # -- every row names one person, consistently (counts only) ----------------------------------
    broken = dict.fromkeys(
        ("email", "nominee_email", "notes", "title_gender", "nominee_same_first"), 0
    )
    for row in written:
        src_row = by_key[row["customer_id"]]
        first, last = row["first_name"], row["last_name"]
        broken["email"] += not row["email"].startswith(_local(f"{first} {last}"))
        broken["nominee_email"] += not row["nominee_email"].startswith(_local(row["nominee_name"]))
        title = "Mr." if src_row["notes"].split("; ")[1].startswith("Mr.") else "Mrs."
        broken["notes"] += row["notes"] != f"KYC call with {first}; {title} {last} prefers e-mail."
        broken["title_gender"] += first not in (MALE if title == "Mr." else FEMALE)
        broken["nominee_same_first"] += row["nominee_name"].split(" ")[0] == first
    assert broken == dict.fromkeys(broken, 0), broken

    # -- source vs target: no source name part, e-mail or key survives (counts only) -------------
    survivors = {"name_parts": 0, "emails": 0, "keys": 0}
    source_emails = {r[c] for r in source.values() for c in ("email", "nominee_email")}
    for row in written:
        src_row = by_key[row["customer_id"]]
        own = name_parts([src_row["first_name"], src_row["last_name"], src_row["nominee_name"]])
        out = name_parts([row["first_name"], row["last_name"], row["nominee_name"], row["notes"]])
        survivors["name_parts"] += len(own & out)
        survivors["emails"] += len({row["email"], row["nominee_email"]} & source_emails)
        survivors["keys"] += row["customer_id"] in source
    assert survivors == dict.fromkeys(survivors, 0), survivors
