"""M5 policy references on scans, on the browser-worker's side.

``POST /scans`` turns ``@<name>[:v<N>]`` into the tenant's stored body at accept time (tested over
HTTP in ``test_scans_api.py``); the worker then runs that body like an inline policy. A body the
deployment no longer accepts — a relaxed leakage check where ``DPS_VERIFY_WARN_ALLOWED`` is false —
fails the scan with the engine's code before anything is opened, and the finalize of a canceled
scan still names the policy (it never raises on it).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest

from dps.config import Settings
from dps.engine.policy import load_preset
from dps.engine.service import PrivacyEngine
from dps.engine.synth.surrogates import generate_synthesis_seed
from dps.engine.transform.crypto import generate_key_material
from dps.ids import new_id
from dps.jobs import ClaimedJob, JobKind, JobPhase, JobState
from dps.jobs_models import JobSecret, ScanSpec
from dps.layers.scanner import ScanSetupError, _policy_name, open_runtime, seal_scan_policy
from dps.layers.scanner.models import redact_scan_spec, validate_scan
from dps.secrets import seal

KEK = os.urandom(32)
URL = "http://demo-site:8410/apply"
HOSTS = frozenset({"demo-site"})


def _relaxed(name: str = "relaxed-kyc", version: int = 2) -> dict[str, Any]:
    body = load_preset("gdpr-pseudonymise").model_dump(mode="json")
    body["verify"]["leakage_check"] = "warn"
    body.update({"name": name, "version": version})
    return body


def _claimed(settings: Settings, policy: dict[str, Any]) -> ClaimedJob:
    """A claimed scan job as ``POST /scans`` stores it (sealed key, seed and start URL; the policy
    body sealed in ``spec_enc`` and its label in the spec, S3 security review F-18)."""
    spec = ScanSpec.model_validate({"url": URL, "policy": policy})
    validated = validate_scan(spec, settings=settings, hosts=HOSTS, tenant_id="t1")
    job_id = new_id("s")
    secret = JobSecret(
        key_material=generate_key_material("key").model_dump(mode="json"),
        synthesis_seed=generate_synthesis_seed().model_dump(mode="json"),
        scan_start_url=validated.start_url,
    )
    now = datetime.now(UTC)
    return ClaimedJob(
        id=job_id,
        tenant_id="t1",
        kind=JobKind.scan,
        state=JobState.running,
        phase=JobPhase.scan,
        mode="synthesize",
        spec=redact_scan_spec(validated, URL),
        created_at=now,
        updated_at=now,
        attempts=1,
        max_attempts=3,
        key_enc=seal(KEK, secret.model_dump_json().encode("utf-8"), aad=f"dps_job.key:{job_id}"),
        spec_enc=seal_scan_policy(KEK, job_id, spec.policy),
        lease_expires_at=now,
    )


def _ctx(settings: Settings) -> Any:
    return SimpleNamespace(settings=settings, kek=KEK, engine=PrivacyEngine(settings))


@pytest.fixture
def strict() -> Settings:
    return Settings(_env_file=None, env="local", llm_provider="stub", verify_warn_allowed=False)


async def test_a_stored_body_that_relaxes_verification_fails_the_scan_with_the_engines_code(
    strict: Settings,
) -> None:
    job = _claimed(Settings(_env_file=None, env="local", llm_provider="stub"), _relaxed())
    with pytest.raises(ScanSetupError) as err:
        await open_runtime(_ctx(strict), job, store=None)  # type: ignore[arg-type]
    assert err.value.code == "verify_warn_disallowed"


def test_the_finalize_of_a_canceled_scan_names_the_policy_without_raising(
    strict: Settings,
) -> None:
    relaxed = _claimed(strict, _relaxed())
    assert _policy_name(_ctx(strict), relaxed, None) == ""  # refused body: named by nobody
    stored = _claimed(strict, {**_relaxed("retail-kyc", 3), "verify": {"leakage_check": "strict"}})
    assert _policy_name(_ctx(strict), stored, None) == "retail-kyc"
