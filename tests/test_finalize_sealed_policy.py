"""FINAL r1 B1 (unit): finalize opens the run's sealed spec before its informational steps.

Since S3 review F-18 a job row keeps only a label of an inline or stored policy; asking the
engine for that label as a policy fails ``policy_invalid`` every time. Finalize now opens the
spec (:func:`jobs_run._finalize_spec`), records a step that still fails that way as an error in
the pack (which fails closed: review required) instead of retrying the job dead, and names the
policy version from the label when the body cannot be opened. The integration side is
``tests/connectors/test_run_sealed_policy.py``.
"""

from __future__ import annotations

import asyncio
import os
from types import SimpleNamespace
from typing import Any

import pytest

from dps.engine.policy import load_preset
from dps.jobs_models import RunSpec, SealedSelection, policy_label
from dps.layers.connectors import jobs_run
from dps.layers.connectors.targets import PrepareFailure
from dps.secrets import seal_json

KEK = os.urandom(32)
JOB = "j_01FINALR1B1SEALEDPOLICY0000"


def _spec(policy: Any) -> dict[str, Any]:  # noqa: ANN401
    return RunSpec.model_validate(
        {
            "mode": "obfuscate",
            "snapshot_id": "s_1",
            "purpose": {"ticket": "CHG-1", "legal_basis": "test"},
            "target": {"write_mode": "table", "connection_id": "c_2", "object": {"name": "t"}},
            "source": {"connection_id": "c_1", "object": {"name": "s"}},
            "policy": policy,
        }
    ).model_dump(mode="json")


def _body() -> dict[str, Any]:
    body = load_preset("gdpr-pseudonymise").model_dump(mode="json")
    body.update(name="retail-kyc", version=7)
    return body


def _job(*, sealed: bool) -> Any:  # noqa: ANN401
    body = _body()
    stored = _spec(body)
    stored["policy"] = policy_label(body)  # what redact_spec stores (F-18)
    blob = (
        seal_json(KEK, SealedSelection(policy=body), aad=f"dps_job.spec:{JOB}") if sealed else None
    )
    return SimpleNamespace(id=JOB, spec=stored, spec_enc=blob)


def _ctx() -> Any:  # noqa: ANN401
    from dps.config import Settings
    from dps.engine.service import PrivacyEngine

    engine = PrivacyEngine(Settings(_env_file=None, llm_provider="stub", ner_enabled=False))
    return SimpleNamespace(kek=KEK, engine=engine)


def test_the_label_alone_is_no_policy() -> None:
    """The defect: the redacted row's policy, given to the engine, is refused."""
    redacted = RunSpec.model_validate(_job(sealed=True).spec)
    with pytest.raises(PrepareFailure) as err:
        jobs_run._policy(_ctx(), redacted, "retail-kyc")
    assert err.value.code == "policy_invalid"


def test_finalize_opens_the_sealed_policy_body() -> None:
    ctx = _ctx()
    opened = jobs_run._finalize_spec(ctx, _job(sealed=True))
    assert opened is not None and isinstance(opened.policy, dict)
    policy = jobs_run._policy(ctx, opened, "retail-kyc")
    assert policy.name == "retail-kyc" and policy.version == 7


def test_a_seal_that_cannot_be_opened_is_reported_not_raised() -> None:
    assert jobs_run._finalize_spec(_ctx(), _job(sealed=False)) is None


def test_a_prepare_failure_in_an_informational_step_becomes_an_error() -> None:
    async def step() -> dict[str, Any]:
        raise PrepareFailure("policy_invalid", "the run policy is not valid")

    assert asyncio.run(jobs_run._best_effort(step())) == {"error": "policy_invalid"}
    passed, status, review = jobs_run.verification_outcome(0, False, {"error": "policy_invalid"})
    assert (passed, status, review) == (False, "review_required", True)  # fails closed
