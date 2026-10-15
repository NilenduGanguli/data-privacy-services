"""Finalize clears the write ledger through a writer it builds without describing the source on
the target connection (S1-M3 post-merge verification r2, engines B2) — offline, with a fake connector
whose login cannot see the source object (the demo's ``CRM_MASKED`` against ``CRM.*``).

Before the fix, a load whose prepare created its target table (no ``target_shape``) described the
*source* object on the *target* connection: the target login raised ``object_not_found``, the
best-effort finalize step swallowed it, and the job's ledger rows stayed. The integration tests
(``test_oracle_ledger_cross_connection.py``, the resume-after-kill matrix) prove the live path;
this pins which shape the clearing writer gets in each case.
"""

from __future__ import annotations

from typing import Any

import pytest

from dps.jobs_models import ObjectRef, RunSpec
from dps.layers.connectors import jobs_run
from dps.layers.connectors.base import ConnectorError, TableShape
from tests.connectors.factories import evidence_inputs


class FakeWriter:
    def __init__(self, shape: TableShape) -> None:
        self.shape = shape
        self.cleared: list[str] = []

    async def clear_ledger(self, job_id: str) -> int:
        self.cleared.append(job_id)
        return 3

    async def close(self) -> None:
        return None


class TargetLogin:
    """The target connection's connector: it sees only the target schema (``bank_uat``)."""

    def __init__(self, source: str) -> None:
        self.source = source
        self.described: list[str] = []
        self.writers: list[FakeWriter] = []

    async def describe(self, obj: ObjectRef) -> TableShape:
        self.described.append(obj.fqn())
        if obj.fqn() == self.source:
            raise ConnectorError("object_not_found", "the object does not exist or is not visible")
        return TableShape(object=obj, key=[], key_source="rowid", columns=[])

    async def writer(self, target: Any, shape: TableShape) -> FakeWriter:
        self.writers.append(FakeWriter(shape))
        return self.writers[-1]

    async def close(self) -> None:
        return None


@pytest.mark.parametrize("case", ["snapshot", "target_shape", "neither"])
async def test_clearing_the_ledger_never_describes_the_source_on_the_target_connection(
    case: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    inputs = evidence_inputs()  # bank.customers → bank_uat.customers on another connection
    assert inputs.plan is not None and inputs.target_conn is not None
    spec = RunSpec.model_validate(inputs.job.spec)
    plan = inputs.plan
    assert plan.target.target_shape is None  # prepare created the table
    snapshot = inputs.snapshot if case == "snapshot" else None
    target_shape = TableShape(
        object=ObjectRef(owner="bank_uat", name="customers"), key=[], key_source="rowid", columns=[]
    )
    if case == "target_shape":
        plan = plan.model_copy(
            update={"target": plan.target.model_copy(update={"target_shape": target_shape})}
        )
    login = TargetLogin(spec.source.object.fqn())
    opened: list[str] = []

    async def fake_open(ctx: Any, job: Any, conn: Any, action: str) -> TargetLogin:
        opened.append(conn.id)
        return login

    monkeypatch.setattr(jobs_run, "_open", fake_open)
    out = await jobs_run._clear_ledger(
        None,  # type: ignore[arg-type]
        inputs.job,
        spec,
        plan,
        inputs.source_conn,
        inputs.target_conn,
        snapshot,
    )
    assert out == {"deleted": 3}
    assert opened == [inputs.target_conn.id]  # the written connection, never the source's
    [writer] = login.writers
    assert writer.cleared == [inputs.job.id]
    assert spec.source.object.fqn() not in login.described
    if case == "snapshot":  # the source shape the run was planned from: nothing described
        assert login.described == [] and writer.shape == inputs.snapshot.shape
    elif case == "target_shape":
        assert login.described == [] and writer.shape == target_shape
    else:  # the target exists once prepare ran: it is described on its own connection
        assert login.described == ["bank_uat.customers"]
