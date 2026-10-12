"""End-to-end smoke test against a running DPS (default http://127.0.0.1:8400).

    docker compose up --build -d
    DPS_API_KEY=dps_local_dev_key_change_me python tools/smoke_test.py

Environment:

- ``DPS_BASE_URL`` (or ``DPS_TEST_APP_URL``): the app; default ``http://127.0.0.1:${DPS_APP_PORT:-8400}``.
- ``DPS_API_KEY`` (or ``DPS_BOOTSTRAP_API_KEY``): a key with every Layer 1 scope; default the compose
  stack's local demo key.
- ``DPS_SMOKE_WAIT_SECONDS``: how long to wait for ``/health`` before the first check (default 90).

Waits for ``/health``, then runs the checks in order, prints a one-line verdict per check and exits
non-zero on the first failure. Never prints a request or response body, a value or a key: a
detail is a count, a method name or a component state, and a response of an unexpected shape is
reported by exception class only.

When ``/readyz`` reports the metadata database (M2), nine job-platform checks follow against the
``sources`` profile (seeded ``bank`` schema, MinIO, the demo secrets): readiness, four connections
(created or reused), their tests, a counted selection, discovery and acceptance, a table run into
``bank_uat.customers_smoke`` with its evidence, a parquet export to MinIO with its manifest, a
dry-run in-place job on ``bank_restore.contacts`` and the worker count. They are rerunnable; the
key material a run returns is never printed or kept. ``DPS_SMOKE_JOB_TIMEOUT`` (default 180 s)
bounds each run.
"""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

import httpx

BASE = (
    os.environ.get("DPS_BASE_URL")
    or os.environ.get("DPS_TEST_APP_URL")
    or f"http://127.0.0.1:{os.environ.get('DPS_APP_PORT') or '8400'}"
).rstrip("/")
KEY = (
    os.environ.get("DPS_API_KEY")
    or os.environ.get("DPS_BOOTSTRAP_API_KEY")
    or "dps_local_dev_key_change_me"  # the compose stack's local demo literal
)
WAIT_SECONDS = float(os.environ.get("DPS_SMOKE_WAIT_SECONDS") or "90")
H = {"X-API-KEY": KEY}
# A fictional customer: made-up values whose identifiers only have to pass their checksums.
SAMPLE = {
    "customer_id": "CUST-884120",
    "full_name": "Ananya Krishnan",
    "dob": "1988-03-14",
    "aadhaar": "2341 2341 2346",
    "email": "ananya.k@example-mail.in",
    "phone": "+91 98451 20377",
    "risk_score": 712,
    "notes": "Mail ann.k@example-mail.in about 1988-03-14",
}

# The console Playground's own samples (the file the Playground imports).
PLAYGROUND = json.loads(
    (Path(__file__).resolve().parents[1] / "frontend/src/lib/samples.json").read_text("utf-8")
)
NAME_PARTS = ("ananya", "krishnan")

results: list[tuple[str, bool, str]] = []


def _flip(text: str) -> str:
    """``text`` with its middle character changed (a mistyped key)."""
    i = len(text) // 2
    return text[:i] + ("A" if text[i] != "A" else "B") + text[i + 1 :]


def check(name: str, ok: bool, detail: str = "") -> None:
    """Record and print one verdict; stop the run at the first failure."""
    results.append((name, ok, detail))
    print(f"{'PASS' if ok else 'FAIL'}  {name}{(' — ' + detail) if detail else ''}", flush=True)
    if not ok:
        passed = sum(1 for _, o, _ in results if o)
        print(f"\n{passed}/{len(results)} passed; stopping at first failure")
        sys.exit(1)


def wait_for_health(c: httpx.Client, seconds: float) -> None:
    """Poll ``/health`` until it answers 200 (the image's healthcheck has a 15 s start period)."""
    deadline = time.monotonic() + seconds
    while True:
        try:
            if c.get("/health").status_code == 200:
                return
        except httpx.HTTPError:
            pass
        if time.monotonic() >= deadline:
            print(
                f"FAIL  {BASE}/health did not answer 200 within {seconds:.0f}s — is the stack up?"
            )
            sys.exit(1)
        time.sleep(1.0)


def main() -> None:
    """Run the Layer 1 checks in order."""
    with httpx.Client(base_url=BASE, timeout=30.0) as c:
        wait_for_health(c, WAIT_SECONDS)
        check("health", c.get("/health").json().get("status") == "ok")
        ready = c.get("/readyz")
        components = ready.json().get("components", {})
        check(
            "readyz",
            ready.status_code == 200 and ready.json()["ready"] is True,
            ", ".join(f"{k}={'ok' if v['ok'] else 'off'}" for k, v in components.items()),
        )
        check(
            "metrics zero counter", b"dps_llm_raw_pii_sent_total 0.0" in c.get("/metrics").content
        )
        check("auth required", c.post("/api/v1/detect", json={"data": SAMPLE}).status_code == 401)

        det = c.post("/api/v1/detect", json={"data": SAMPLE}, headers=H)
        dbody = det.json() if det.status_code == 200 else {}
        check(
            "detect",
            det.status_code == 200 and dbody["record_sensitivity"] == "CRITICAL",
            f"HTTP {det.status_code}, {dbody.get('report', {}).get('findings_total')} findings",
        )

        obf = c.post(
            "/api/v1/obfuscate", json={"data": SAMPLE, "policy": "gdpr-pseudonymise"}, headers=H
        )
        body = obf.json() if obf.status_code == 200 else {}
        report = body.get("report", {})
        check(
            "obfuscate",
            obf.status_code == 200
            and report["verification"]["passed"]
            and body["result"]["email"] != SAMPLE["email"]
            and body["result"]["risk_score"] == 712,
            f"HTTP {obf.status_code}, methods={report.get('methods_used')} "
            f"fallbacks={report.get('fallbacks')}",
        )

        rev = c.post(
            "/api/v1/reveal",
            json={
                "data": body["result"],
                "key_material": body["key_material"],
                "reveal_hints": body["reveal_hints"],
            },
            headers=H,
        )
        check(
            "reveal restores original",
            rev.status_code == 200 and rev.json()["result"] == SAMPLE,
            f"HTTP {rev.status_code}, {rev.json().get('revealed') if rev.status_code == 200 else '-'} values revealed",
        )

        wrong_key = {**body["key_material"], "dek": _flip(body["key_material"]["dek"])}
        refused = c.post(
            "/api/v1/reveal",
            json={
                "data": body["result"],
                "key_material": wrong_key,
                "reveal_hints": body["reveal_hints"],
            },
            headers=H,
        )
        check(
            "reveal refuses a wrong key",
            refused.status_code == 400
            and "check value" in refused.json().get("detail", "")
            and SAMPLE["aadhaar"] not in refused.text
            and wrong_key["dek"] not in refused.text,
            f"HTTP {refused.status_code}",
        )

        ptext = c.post("/api/v1/obfuscate", json={"text": PLAYGROUND["text"]}, headers=H)
        pbody = ptext.json() if ptext.status_code == 200 else {}
        pver = pbody.get("report", {}).get("verification") or {}
        coverage = pver.get("coverage") or {}
        check(
            "playground text sample: no name in output, coverage stated",
            ptext.status_code == 200
            and not any(part in pbody["result"].lower() for part in NAME_PARTS)
            and any(f["entity_type"] == "PERSON_NAME" for f in pbody["findings"])
            and pver["passed"]
            and (pver["status"] == "partial") == bool(coverage["gaps"])
            and (coverage["ner"] == "on" or bool(coverage["gaps"])),
            f"HTTP {ptext.status_code}, status={pver.get('status')}, ner={coverage.get('ner')}, "
            f"gaps={len(coverage.get('gaps', []))}",
        )

        again = c.post(
            "/api/v1/obfuscate",
            headers=H,
            json={
                "data": SAMPLE,
                "options": {"consistency": {"scope": "key", "key_material": body["key_material"]}},
            },
        )
        check(
            "consistency under the same key",
            again.status_code == 200
            and again.json()["result"]["aadhaar"] == body["result"]["aadhaar"],
            f"HTTP {again.status_code}",
        )

        syn = c.post(
            "/api/v1/synthesize", json={"data": SAMPLE, "policy": "synthetic-realistic"}, headers=H
        )
        sbody = syn.json() if syn.status_code == 200 else {}
        check(
            "synthesize",
            syn.status_code == 200
            and sbody["report"]["verification"]["passed"]
            and "ananya" not in str(sbody["result"]).lower()
            and sbody["report"]["llm"]["raw_pii_sent"] == 0,
            f"HTTP {syn.status_code}, methods={sbody.get('report', {}).get('methods_used')}",
        )

        txt = c.post("/api/v1/synthesize", json={"text": SAMPLE["notes"]}, headers=H)
        check(
            "synthesize text",
            txt.status_code == 200 and "example-mail" not in txt.json()["result"],
            f"HTTP {txt.status_code}",
        )

        presets = c.get("/api/v1/policies/presets", headers=H)
        names = {p["name"] for p in presets.json()} if presets.status_code == 200 else set()
        check(
            "presets",
            names >= {"gdpr-pseudonymise", "pci-dss", "synthetic-realistic"},
            f"{len(names)} presets",
        )

        plan = c.post(
            "/api/v1/policies/plan",
            json={"data": SAMPLE, "policy": "pci-dss", "mode": "obfuscate"},
            headers=H,
        )
        rows = len(plan.json()["plan"]) if plan.status_code == 200 else 0
        check("plan preview", plan.status_code == 200 and rows >= 5, f"{rows} plan rows")

        root = c.get("/")
        check(
            "console served",
            root.status_code == 200 and b'<div id="root">' in root.content,
            f"HTTP {root.status_code}",
        )
        if components.get("db", {}).get("detail") == "unconfigured" or "db" not in components:
            print("note: /readyz reports no metadata database: the M2 job checks are skipped")
        else:
            m2_checks(c)
    print(f"\nall {len(results)} checks passed against {BASE}")


# -- M2: the job platform (profile `sources`) ----------------------------------------------------

JOB_TIMEOUT = float(os.environ.get("DPS_SMOKE_JOB_TIMEOUT") or "180")
PURPOSE = {"ticket": "SMOKE-0001", "legal_basis": "legitimate interest - smoke test"}
#: In-network endpoints of the compose stack (the app and the worker resolve these names).
SOURCE_PG = {"host": "source-pg", "port": 5432, "database": "bank"}
CONNECTIONS: dict[str, dict[str, object]] = {
    "bank-pg-ro": {
        "engine": "postgres",
        "params": SOURCE_PG,
        "secret_ref": "dps/src/bank-pg-ro",
        "roles": ["source"],
        "tags": ["production"],
    },
    "bank-uat": {
        "engine": "postgres",
        "params": SOURCE_PG,
        "secret_ref": "dps/tgt/bank-uat",
        "roles": ["target"],
    },
    "bank-restore-rw": {
        "engine": "postgres",
        "params": SOURCE_PG,
        "secret_ref": "dps/src/bank-restore-rw",
        "roles": ["source", "target"],
    },
    "exports-s3": {
        "engine": "s3",
        "params": {"bucket": "dps-exports", "endpoint_url": "http://minio:9000"},
        "secret_ref": "dps/s3/exports",
        "roles": ["target"],
    },
}
TERMINAL = ("succeeded", "failed", "dead", "canceled")


def ensure_connections(c: httpx.Client) -> tuple[dict[str, str], int]:
    """``({name: id}, created)`` for the four demo connections, created when missing."""
    found: dict[str, str] = {}
    cursor: str | None = None
    while True:
        page = c.get("/api/v1/connections", params={"limit": 200, "cursor": cursor}, headers=H)
        page.raise_for_status()
        body = page.json()
        found.update({item["name"]: item["id"] for item in body["items"]})
        cursor = body["next_cursor"]
        if not cursor:
            break
    created = 0
    for name, spec in CONNECTIONS.items():
        if name not in found:
            made = c.post("/api/v1/connections", json={"name": name, **spec}, headers=H)
            made.raise_for_status()
            found[name] = made.json()["id"]
            created += 1
    return {name: found[name] for name in CONNECTIONS}, created


def poll(c: httpx.Client, job_id: str, timeout: float) -> dict[str, object]:
    """``GET /jobs/{id}`` every second until the job is terminal (or ``timeout``)."""
    deadline = time.monotonic() + timeout
    while True:
        job: dict[str, object] = c.get(f"/api/v1/jobs/{job_id}", headers=H).json()
        if job.get("state") in TERMINAL or time.monotonic() >= deadline:
            return job
        time.sleep(1.0)


def discover_and_accept(
    c: httpx.Client, conn_id: str, owner: str, name: str, overrides: list[dict[str, object]]
) -> tuple[str | None, str]:
    """Discover one table, apply review overrides, accept; ``(snapshot id, detail)``."""
    obj = {"owner": owner, "name": name}
    r = c.post(
        "/api/v1/jobs/discover", json={"connection_id": conn_id, "objects": [obj]}, headers=H
    )
    if r.status_code != 202:
        return None, f"HTTP {r.status_code} {r.json().get('code')}"
    job = poll(c, r.json()["job"]["id"], 60)
    progress = job.get("progress") or {}
    snaps = progress.get("snapshot_ids") if isinstance(progress, dict) else None
    if job.get("state") != "succeeded" or not snaps:
        return None, f"discover {job.get('state')} {job.get('error_code')}"
    snap_id = str(snaps[0])
    if overrides:
        reviewed = c.patch(f"/api/v1/snapshots/{snap_id}", json={"overrides": overrides}, headers=H)
        if reviewed.status_code != 200:
            return None, f"review HTTP {reviewed.status_code} {reviewed.json().get('code')}"
    accepted = c.post(f"/api/v1/snapshots/{snap_id}/accept", headers=H)
    if accepted.status_code != 200:
        return None, f"accept HTTP {accepted.status_code}"
    return snap_id, "accepted"


def run_job(c: httpx.Client, spec: dict[str, object]) -> dict[str, object]:
    """Submit a run (the returned key is discarded unread) and wait for it."""
    r = c.post("/api/v1/jobs/run", json={"purpose": PURPOSE, **spec}, headers=H)
    if r.status_code != 202:
        return {"state": "refused", "error_code": r.json().get("code"), "http": r.status_code}
    return poll(c, r.json()["job"]["id"], JOB_TIMEOUT)


def evidence_of(c: httpx.Client, job: dict[str, object]) -> dict[str, object]:
    """The job's evidence record (``{}`` when it has none)."""
    progress = job.get("progress") or {}
    evidence_id = progress.get("evidence_id") if isinstance(progress, dict) else None
    if not evidence_id:
        return {}
    r = c.get(f"/api/v1/evidence/{evidence_id}", headers=H)
    return r.json() if r.status_code == 200 else {}


def m2_checks(c: httpx.Client) -> None:
    """The nine job-platform checks (module docstring)."""
    comps = c.get("/readyz").json().get("components", {})
    db_ok, jobs_ok = comps.get("db", {}).get("ok"), comps.get("jobs", {}).get("ok")
    check("m2 readyz db and jobs", bool(db_ok and jobs_ok), f"db={db_ok}, jobs={jobs_ok}")

    ids, created = ensure_connections(c)
    check("m2 connections", len(ids) == 4, f"{created} created, {4 - created} reused")
    tests = {
        "bank-pg-ro": {"role": "source", "object": "bank.customers"},
        "bank-uat": {"role": "target", "object": "bank_uat.customers_smoke", "write_mode": "table"},
        "bank-restore-rw": {"role": "source", "object": "bank_restore.contacts"},
        "exports-s3": {"role": "target", "write_mode": "files"},
    }
    passed = [
        name
        for name, body in tests.items()
        if c.post(f"/api/v1/connections/{ids[name]}/test", json=body, headers=H).json().get("ok")
    ]
    check("m2 connections tested", len(passed) == 4, f"{len(passed)}/4 ok")

    counted = c.post(
        f"/api/v1/connections/{ids['bank-pg-ro']}/count",
        json={"object": "bank.customers", "selection": {"where": "country_code = 'IN'"}},
        headers=H,
    )
    cbody = counted.json() if counted.status_code == 200 else {}
    check(
        "m2 count a selection",
        counted.status_code == 200 and cbody["rows"] > 0 and cbody["binds"] == 1,
        f"HTTP {counted.status_code}, {cbody.get('rows')} rows, {cbody.get('binds')} bind",
    )

    snap_id, detail = discover_and_accept(c, ids["bank-pg-ro"], "bank", "customers", [])
    check("m2 discover and accept", snap_id is not None, detail)

    table = run_job(
        c,
        {
            "mode": "obfuscate",
            "snapshot_id": snap_id,
            "source": {
                "connection_id": ids["bank-pg-ro"],
                "object": {"owner": "bank", "name": "customers"},
            },
            "target": {
                "write_mode": "table",
                "connection_id": ids["bank-uat"],
                "object": {"owner": "bank_uat", "name": "customers_smoke"},
                "table_load": "truncate_insert",
            },
        },
    )
    ev = evidence_of(c, table)
    pack = ev.get("pack") or {}
    passed_v = isinstance(pack, dict) and (pack.get("verification") or {}).get("passed")
    html = c.get(f"/api/v1/evidence/{ev.get('id')}/html", headers=H) if ev else None
    progress = table.get("progress") or {}
    check(
        "m2 table run with evidence",
        table.get("state") == "succeeded"
        and bool(passed_v)
        and html is not None
        and html.status_code == 200,
        f"{table.get('state')} {table.get('error_code') or ''}".strip()
        + f", {progress.get('rows_written') if isinstance(progress, dict) else '-'} rows",
    )

    files = run_job(
        c,
        {
            "mode": "obfuscate",
            "snapshot_id": snap_id,
            "source": {
                "connection_id": ids["bank-pg-ro"],
                "object": {"owner": "bank", "name": "customers"},
            },
            "target": {
                "write_mode": "files",
                "connection_id": ids["exports-s3"],
                "files": {"format": "parquet", "prefix": "smoke"},
            },
        },
    )
    fpack = evidence_of(c, files).get("pack") or {}
    ftarget = fpack.get("target") if isinstance(fpack, dict) else {}
    fmeta = (ftarget or {}).get("files") or {}
    check(
        "m2 parquet export to MinIO",
        files.get("state") == "succeeded" and bool(fmeta.get("manifest_artefact_id")),
        f"{files.get('state')} {files.get('error_code') or ''}".strip()
        + f", {fmeta.get('objects')} objects",
    )

    review = [{"path": "$.contact_id", "method": {"method": "keep"}}]
    restore_snap, rdetail = discover_and_accept(
        c, ids["bank-restore-rw"], "bank_restore", "contacts", review
    )
    dry = (
        run_job(
            c,
            {
                "mode": "obfuscate",
                "snapshot_id": restore_snap,
                "source": {
                    "connection_id": ids["bank-restore-rw"],
                    "object": {"owner": "bank_restore", "name": "contacts"},
                },
                "target": {"write_mode": "in_place", "confirm_write": True},
                "options": {"dry_run": True},
            },
        )
        if restore_snap
        else {"state": "not run", "error_code": rdetail}
    )
    dprogress = dry.get("progress") or {}
    outcome = dprogress.get("outcome") if isinstance(dprogress, dict) else None
    check(
        "m2 dry-run in place",
        dry.get("state") == "succeeded" and outcome == "dry_run",
        f"{dry.get('state')} {outcome or dry.get('error_code') or ''}".strip(),
    )

    stats = c.get("/api/v1/jobs/stats", headers=H).json()
    check(
        "m2 workers online",
        stats.get("workers_online", 0) >= 1,
        f"{stats.get('workers_online')} online",
    )


if __name__ == "__main__":
    try:
        main()
    except httpx.HTTPError as exc:
        print(f"FAIL  {type(exc).__name__} talking to {BASE} after {len(results)} checks")
        sys.exit(1)
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        # A response of an unexpected shape: name the class only (a message could quote a body).
        print(f"FAIL  unexpected response shape after {len(results)} checks ({type(exc).__name__})")
        sys.exit(1)
