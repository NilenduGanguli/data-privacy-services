"""The S3 security review's fixes hold on the UI scanner's paths too (M4 merged into ``fsec``).

The review fixed Layers 1 and 2 before the scanner was on this branch; each fix below has a
scanner twin the merge brought in, and each test fails without the twin's fix:

* F-07 — the scan inventory and export CSVs guard a ``+``/``-`` cell the way the L2 export does
  (``-2+3+cmd|…`` is a formula; ``+91 98451 20377`` and ``-5`` stay data).
* F-10 — ``dps_scan_fields_total`` labels a custom entity type ``custom`` (caller-chosen names
  stay off the anonymous ``/metrics``).
* F-02 — the browser-worker sizes its matcher processes from ``DPS_PATTERN_PROCESSES`` like the
  app and the worker (and a scan whose policy pattern runs past its CPU budget fails at once with
  ``custom_pattern_too_slow``: ``tests/scanner/test_scan_pattern_budget.py``).
* F-14 — a ``POST /scans`` whose creation audit is refused leaves no scan (nor its sealed key)
  behind, so a retry gets a fresh scan and its key.
* F-18 — an inline (or resolved ``@name``) scan policy is sealed in ``spec_enc``; ``dps_job.spec``
  and every read keep a value-free label, and the browser-worker reopens the body.
* F-21 — a ``POST /scans`` replay admitted under the previous KEK is still a replay.
* S1 — a scan spec refusal never echoes the input (the app's scrubbed 422, or a scan code whose
  detail names the code only); a control that holds, kept as a test.

(F-11 for the browser-worker role: ``tests/scanner/test_browser_worker_posture.py``.)
"""

from __future__ import annotations

import asyncio
import base64
import csv
import io
import json
from pathlib import Path
from typing import Any

import asyncpg
import pytest
from fastapi.testclient import TestClient

from dps.app import create_app
from dps.config import Settings
from dps.jobs import ClaimedJob, JobKind, JobPhase, JobState
from dps.layers.connectors.files import _csv_cell
from dps.layers.scanner.inventory import write_csv
from dps.observability import metrics_payload
from dps.secrets import load_kek
from tests.api.test_api_connections import FULL
from tests.api.test_once_only_key_custody import _RefusingSink
from tests.fixtures.db import TEST_KEK, _truncate, api_settings
from tests.scanner.helpers import classification

URL = "http://127.0.0.1:8410/apply"
SENTINEL = "Zebulon Quixote"
POLICY: dict[str, Any] = {
    "preset": "gdpr-pseudonymise",
    "name": "vip-masking",
    "detect": {
        "custom_recognizers": [
            {"name": "vips", "supported_entity": "VIP_NAME", "deny_list": [SENTINEL]}
        ]
    },
}


# --- S1: refusals never echo the input --------------------------------------------------------------
SSN = "123-45-6789"


@pytest.mark.parametrize(
    "body",
    [
        {"url": URL, "mode": SSN},  # a literal: uncoded, the scrubbed 422
        {"url": URL, "fill": {"locale": SSN}},  # fill_invalid
        {"url": URL, "scope": {"deny": [SSN]}},  # scope_invalid (pattern)
        {"url": URL, "consistency": {"scope": SSN}},
        {"url": URL, "auth": {"storage_state_ref": f"{SSN}/../x"}},  # auth_invalid
        {"url": SSN * 3},  # url_invalid
        {"url": URL, SSN: 1},  # an unknown key that is a value (decision D4)
        {"url": URL, "policy": {"preset": "gdpr-pseudonymise", SSN: 1}},
    ],
)
def test_a_scan_spec_refusal_never_echoes_the_input(body: dict[str, Any]) -> None:
    from fastapi.exceptions import RequestValidationError
    from pydantic import ValidationError

    from dps.jobs_models import ScanSpec
    from dps.layers.api.validation import scrubbed
    from dps.layers.scanner.models import spec_refusal_code

    try:
        spec = ScanSpec.model_validate(body)
    except ValidationError as exc:
        errors = exc.errors()
    else:  # the model accepts it: validate_scan or the engine refuses it at accept (coded)
        from dps.engine.service import EngineError, PrivacyEngine
        from dps.layers.scanner.models import ScanSpecError, validate_scan

        settings = Settings(_env_file=None, env="local", llm_provider="stub")
        refused: Exception
        try:
            validate_scan(spec, settings=settings, hosts=frozenset({"127.0.0.1"}), tenant_id="t1")
            PrivacyEngine(settings).policy(spec.policy)
        except (ScanSpecError, EngineError) as exc:
            refused = exc
        else:
            raise AssertionError("the body was accepted")
        assert SSN not in str(refused)  # POST /scans answers its code only, value-free
        return
    response = asyncio.run(scrubbed(None, RequestValidationError(errors)))  # type: ignore[arg-type]
    payload = json.loads(bytes(response.body))
    assert SSN not in json.dumps(payload) and payload["code"] == "invalid_request"
    assert all(set(item) == {"loc", "msg", "type"} for item in payload["detail"])
    code = spec_refusal_code(errors)  # a coded refusal answers "the scan was refused: <code>"
    assert code is None or SSN not in code


# --- F-07: CSV cells ------------------------------------------------------------------------------
ATTACKS = ("-2+3+cmd|' /C calc'!A0", "+1+cmd|' /C calc'!A0", "-SUM(1)", '=HYPERLINK("x")', "@x")
DATA = ("+91 98451 20377", "-5", "-12.50", "+1 (555) 010-0100", "plain", "a=b", "")


def test_scan_csv_cells_are_guarded_at_least_like_the_l2_export() -> None:
    cells = [*ATTACKS, *DATA, "\tx", "\rx", "+", "-"]
    data = write_csv(["c"], [[c] for c in cells]).decode("utf-8")
    got = [row[0] for row in list(csv.reader(io.StringIO(data)))[1:]]
    for cell, out in zip(cells, got, strict=True):
        l2 = _csv_cell(cell)
        if l2.startswith("'"):
            assert out == l2, f"the scan CSV keeps a cell the L2 export guards: {cell!r}"
        else:  # data the L2 export keeps stays data (a lone sign is guarded, as M4 chose)
            assert out in (l2, "'" + cell), cell
    for attack in ATTACKS:
        assert got[cells.index(attack)] == "'" + attack
    for value in DATA:
        assert got[cells.index(value)] == value


# --- F-10: metric labels --------------------------------------------------------------------------
def test_a_custom_entity_type_is_counted_as_custom_on_the_scan_metrics() -> None:
    from dps.layers.scanner.crawler import count_field_metrics

    count_field_metrics(
        [
            classification("PROJECT_FALCON_7_ID", "MEDIUM"),
            classification("EMAIL", "MEDIUM"),
            classification(None, None),
        ]
    )
    exposition = metrics_payload()[0].decode("utf-8")
    assert "PROJECT_FALCON" not in exposition
    assert 'dps_scan_fields_total{entity_type="custom"}' in exposition
    assert 'dps_scan_fields_total{entity_type="EMAIL"}' in exposition


def test_every_scan_field_counter_goes_through_the_bounded_label() -> None:
    root = Path(__file__).resolve().parents[2] / "dps"
    for path in [*sorted((root / "layers" / "scanner").glob("*.py")), root / "browser_worker.py"]:
        text = path.read_text(encoding="utf-8")
        for chunk in text.split("SCAN_FIELDS.labels(")[1:]:
            assert chunk.startswith("metric_entity_label("), path.name


# --- F-02: pattern budget (the scan job itself: tests/scanner/test_scan_pattern_budget.py) ------
async def test_the_browser_worker_sizes_its_matcher_processes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import dps.browser_worker as bw

    sizes: list[int] = []
    monkeypatch.setattr(bw, "configure_pattern_processes", sizes.append)
    settings = Settings(_env_file=None, env="local", llm_provider="stub", pattern_processes=3)
    with pytest.raises(SystemExit):  # no metadata database: it stops right after the posture
        await bw.amain(settings)
    assert sizes == [3]


# --- F-14: once-only key custody ------------------------------------------------------------------
@pytest.mark.db
def test_a_refused_scan_creation_audit_leaves_no_scan_behind(
    db_settings: Settings, tmp_path: Path
) -> None:
    sink = _RefusingSink({"scan.create"})
    try:
        with TestClient(create_app(api_settings(db_settings, tmp_path), audit_sink=sink)) as c:
            headers = {**FULL, "Idempotency-Key": "scan-custody-1"}
            first = c.post("/api/v1/scans", json={"url": URL}, headers=headers)
            assert (first.status_code, first.json()["code"]) == (503, "audit_unavailable")
            assert "key_material" not in first.text and "synthesis_seed" not in first.text
            assert c.get("/api/v1/scans", headers=FULL).json()["items"] == []  # nothing queued
            again = c.post("/api/v1/scans", json={"url": URL}, headers=headers)
            assert again.status_code == 202, again.text  # a fresh scan, and its key, this time
            assert again.json()["key_material"]["dek"] and again.json()["synthesis_seed"]["seed"]
            rejected = [e for e in sink.events if e.outcome in ("error", "rejected")]
            assert "scan.create" in {e.action for e in rejected}
    finally:
        asyncio.run(_truncate(db_settings))


# --- F-18: sealed policy bodies -------------------------------------------------------------------
async def _job_row(settings: Settings, job_id: str) -> dict[str, Any]:
    conn = await asyncpg.connect(
        host=settings.pg_host,
        port=settings.pg_port,
        user=settings.pg_user,
        password=settings.pg_password,
        database=settings.pg_database,
    )
    try:
        await conn.execute("SELECT set_config('app.tenant_id', 't1', false)")
        row = await conn.fetchrow(
            "SELECT spec::text AS spec, spec_enc, key_enc, kid, mode, created_at, updated_at "
            f"FROM {settings.pg_schema}.dps_job WHERE id = $1",
            job_id,
        )
        assert row is not None
        return dict(row)
    finally:
        await conn.close()


async def _worker_side(settings: Settings, job: ClaimedJob) -> tuple[Any, str]:
    """What the browser-worker does first: open the runtime (the policy included) on its pools."""
    from dps import db
    from dps.engine.service import PrivacyEngine
    from dps.layers.scanner import _policy_name, open_runtime
    from dps.layers.scanner.store import ScanStore

    ctx: Any = type("Ctx", (), {"settings": settings, "kek": TEST_KEK})()
    ctx.engine = PrivacyEngine(settings)
    await db.init_pool(settings)
    await db.init_worker_pool(settings)
    try:
        runtime = await open_runtime(ctx, job, ScanStore(settings.pg_schema))
        return runtime, _policy_name(ctx, job, None)  # a canceled scan's finalize names it too
    finally:
        await db.close_pools()


@pytest.mark.db
def test_a_scan_policy_body_never_rests_in_clear_and_the_worker_reopens_it(
    db_settings: Settings, tmp_path: Path
) -> None:
    settings = api_settings(db_settings, tmp_path)
    try:
        with TestClient(create_app(settings)) as c:
            made = c.post("/api/v1/scans", json={"url": URL, "policy": POLICY}, headers=FULL)
            assert made.status_code == 202, made.text
            scan_id = made.json()["scan"]["id"]
            reads = [
                made.text,
                c.get(f"/api/v1/scans/{scan_id}", headers=FULL).text,
                c.get("/api/v1/scans", headers=FULL).text,
                c.get(f"/api/v1/jobs/{scan_id}", headers=FULL).text,
                c.get("/api/v1/jobs", headers=FULL).text,
            ]
            assert all(SENTINEL not in text for text in reads)
        row = asyncio.run(_job_row(settings, scan_id))
        assert SENTINEL not in row["spec"] and row["spec_enc"] is not None
        label = json.loads(row["spec"])["policy"]
        assert label["sealed"] is True and label["name"] == "vip-masking" and label["sha256"]
        job = ClaimedJob(
            id=scan_id,
            tenant_id="t1",
            kind=JobKind.scan,
            state=JobState.running,
            phase=JobPhase.scan,
            mode=row["mode"],
            spec=json.loads(row["spec"]),
            kid=row["kid"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            attempts=1,
            max_attempts=3,
            key_enc=row["key_enc"],
            spec_enc=row["spec_enc"],
            lease_expires_at=row["updated_at"],
        )
        runtime, name = asyncio.run(_worker_side(settings, job))
        [recognizer] = runtime.policy.detect.custom_recognizers
        assert list(recognizer.deny_list) == [SENTINEL] and runtime.policy.name == "vip-masking"
        assert name == "vip-masking"
    finally:
        asyncio.run(_truncate(db_settings))


# --- F-21: idempotency across a KEK rotation ------------------------------------------------------
@pytest.mark.db
def test_a_scan_admitted_under_the_previous_kek_replays_during_the_rotation(
    api_client: Any,
) -> None:
    c, _sink = api_client
    old, new = TEST_KEK, bytes(range(1, 33))
    headers = {**FULL, "Idempotency-Key": "scan-rotation-1"}
    first = c.post("/api/v1/scans", json={"url": URL}, headers=headers)
    assert first.status_code == 202, first.text
    rotated = c.app.state.settings.model_copy(
        update={
            "job_kek": base64.b64encode(new).decode(),
            "job_kek_previous": base64.b64encode(old).decode(),
        }
    )
    c.app.state.settings, c.app.state.kek = rotated, load_kek(rotated)
    again = c.post("/api/v1/scans", json={"url": URL}, headers=headers)
    assert again.status_code == 200 and again.headers["idempotent-replay"] == "true", again.text
    assert again.json()["scan"]["id"] == first.json()["scan"]["id"]
    assert again.json()["key_material"] is None  # a replay never returns the key again
