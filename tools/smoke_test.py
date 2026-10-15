"""End-to-end smoke test against a running DPS (default http://127.0.0.1:8400).

    docker compose up --build -d
    DPS_API_KEY=dps_local_dev_key_change_me python tools/smoke_test.py

Environment:

- ``DPS_BASE_URL`` (or ``DPS_TEST_APP_URL``): the app; default ``http://127.0.0.1:${DPS_APP_PORT:-8400}``.
- ``DPS_API_KEY`` (or ``DPS_BOOTSTRAP_API_KEY``): a key with every Layer 1 scope; default the compose
  stack's local demo key.
- ``DPS_SMOKE_WAIT_SECONDS``: how long to wait for ``/health`` before the first check (default 90).
- ``DPS_SMOKE_REQUIRE_OCR=1``: the OCR checks (scanned PDF, image) must run — a stack without OCR
  fails instead of skipping them (the M1 milestone gate runs ``docker compose --profile docs``).
- ``DPS_SMOKE_M2``: ``auto`` (default) runs the job-platform checks when ``/readyz`` reports a
  configured metadata database; ``off`` skips them (the plain ``docker compose up`` stack has the
  database but not the ``sources`` profile's demo source, so those checks cannot pass there); ``on``
  runs them whatever ``/readyz`` says.

Waits for ``/health``, then runs the checks in order, prints a one-line verdict per check and exits
non-zero on the first failure. Never prints a request or response body, a value or a key: a
detail is a count, a method name or a component state, and a response of an unexpected shape is
reported by exception class only.

M1 checks (documents, OCR, schema) upload the committed synthetic files in ``demo/documents``.

When ``/readyz`` reports the metadata database (M2), nine job-platform checks follow against the
``sources`` profile (seeded ``bank`` schema, MinIO, the demo secrets): readiness, four connections
(created or reused), their tests, a counted selection, discovery and acceptance, a table run into
``bank_uat.customers_smoke`` with its evidence, a parquet export to MinIO with its manifest, a
dry-run in-place job on ``bank_restore.contacts`` and the worker count. They are rerunnable; the
key material a run returns is never printed or kept. ``DPS_SMOKE_JOB_TIMEOUT`` (default 180 s)
bounds each run.

``DPS_SMOKE_SCANNER=1`` adds eleven UI-scanner checks (M4) against ``docker compose --profile
scanner``: the browser-worker is online, a scan of the demo onboarding site (``/apply``, deny
``/logout`` and ``/admin``, the mounted demo session, five ``en_IN`` personas) is accepted with key
and seed, succeeds with classified fields and the expected risk flags, the demo site's
``/__received`` counters (non-GET requests, GET-form submissions, ``/logout`` and ``/admin`` hits)
do not move while ``non_get_allowed`` is 0, the identity page has its four parallel-view
screenshots and one is served as a PNG, a masked capture is served, the persona export holds none of
the demo site's customer values, the evidence pack says 0 submissions, and ``/scanner/new`` is
served. ``DPS_SMOKE_DEMO_URL`` is the demo site as the browser-worker sees it (default
``http://demo-site:8410``); the script itself reads the counters at ``DPS_TEST_DEMO_SITE`` (default
``http://127.0.0.1:${DPS_DEMO_SITE_PORT:-8410}``). ``DPS_SMOKE_SCAN_TIMEOUT`` (default 300 s)
bounds the scan. Run with ``DPS_SMOKE_M2=off`` unless the ``sources`` profile is up too.
``--m3`` adds seven Oracle + MongoDB checks after the M2 ones; they need the ``oracle`` and
``sources`` profiles and the three-engine demo seed (``python tools/seed_sources.py --target all``):
``m3 connections`` (the four demo connections of ``tools/m2_api.M3_CONNECTIONS`` created or reused,
and with M2's ``bank-pg-ro``/``bank-uat`` every ``/test`` ok), ``m3 discover`` (PostgreSQL
``bank.customers``, MongoDB ``crm.profiles``, Oracle ``CRM.CUSTOMERS`` discovered and accepted),
``m3 group`` (a new job group; its key is in the create answer only), ``m3 runs`` (three ``table``
runs under the group's key into ``bank_uat.customers_smoke``, ``crm_uat.profiles_smoke`` and
``CRM_MASKED.CUSTOMERS_SMOKE``; ``DPS_SMOKE_M3_TIMEOUT``, default 600 s, bounds them together),
``m3 consistency 3/3`` (the group's check passes with three consistent pairs), ``m3 evidence``
(each pack verified, under the group's key id) and ``m3 raw pii to llm`` (the counter is 0). The
key is held in memory for the three submissions and dropped; nothing of it is printed.
"""

from __future__ import annotations

import argparse
import base64
import io
import json
import os
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # `python tools/smoke_test.py`

from tools.m2_api import M3_CONNECTIONS  # noqa: E402

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
REQUIRE_OCR = os.environ.get("DPS_SMOKE_REQUIRE_OCR") == "1"
H = {"X-API-KEY": KEY}
DEMO = Path(__file__).resolve().parents[1] / "demo" / "documents"
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
skipped: list[str] = []


def m2_wanted(components: dict[str, Any], setting: str | None) -> tuple[bool, str]:
    """Whether to run the job-platform checks, and the note printed when they are skipped.

    ``setting`` is ``DPS_SMOKE_M2`` (``auto`` / ``on`` / ``off``, case-insensitive; empty = auto).
    """
    choice = (setting or "auto").strip().lower()
    if choice not in ("auto", "on", "off"):
        raise SystemExit("DPS_SMOKE_M2 must be auto, on or off")
    if choice == "off":
        return False, "note: DPS_SMOKE_M2=off: the M2 job checks are skipped"
    configured = "db" in components and components["db"].get("detail") != "unconfigured"
    if choice == "on" or configured:
        return True, ""
    return False, "note: /readyz reports no metadata database: the M2 job checks are skipped"


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


def skip(name: str, why: str) -> None:
    """A check that cannot run on this stack; a failure under ``DPS_SMOKE_REQUIRE_OCR=1``."""
    if REQUIRE_OCR:
        check(name, False, why)
    skipped.append(name)
    print(f"SKIP  {name} — {why}", flush=True)


def upload(
    c: httpx.Client, name: str, mode: str, request: dict[str, Any] | None = None
) -> httpx.Response:
    """POST a demo document to ``/api/v1/documents/<mode>`` (multipart ``file`` + ``request``)."""
    data = {"request": json.dumps(request)} if request is not None else {}
    return c.post(
        f"/api/v1/documents/{mode}",
        headers=H,
        files={"file": (name, (DEMO / name).read_bytes())},
        data=data,
        timeout=180.0,
    )


def text_layer_chars(pdf_b64: str) -> int | None:
    """Characters pypdf extracts from a produced PDF (``None`` when pypdf is not installed)."""
    try:
        from pypdf import PdfReader
    except ImportError:
        return None
    reader = PdfReader(io.BytesIO(base64.b64decode(pdf_b64)))
    return sum(len((page.extract_text() or "").strip()) for page in reader.pages)


def _json(response: httpx.Response) -> dict[str, Any]:
    """The body of a 200 response, else ``{}`` (a refusal's detail is never printed)."""
    if response.status_code != 200:
        return {}
    body: dict[str, Any] = response.json()
    return body


def _types(body: dict[str, Any]) -> set[str]:
    return {f["entity_type"] for p in body.get("pages", []) for f in p["findings"]}


def m1_checks(c: httpx.Client) -> None:
    """Documents (text, redaction, reveal, CSV), OCR (scan, image) and schema synthesis (M1)."""
    ocr = c.get("/readyz").json()["components"].get("ocr", {})
    ocr_ready = bool(ocr.get("ok"))
    entities = c.get("/api/v1/entities", headers=H)
    items = _json(entities).get("items", [])
    check("entities", entities.status_code == 200 and len(items) >= 46, f"{len(items)} types")

    det = upload(c, "kyc-text.pdf", "detect")
    body = _json(det)
    boxes = sum(len(f["boxes"]) for p in body.get("pages", []) for f in p["findings"])
    check(
        "documents detect (text PDF)",
        det.status_code == 200 and boxes > 0,
        f"HTTP {det.status_code}, {body.get('report', {}).get('findings_total')} findings, "
        f"{boxes} boxes",
    )

    # A redacted PDF is re-read with OCR whenever OCR is configured, and refused (503
    # ocr_unavailable) when that OCR is down. Without OCR ready, ask for the structural checks only.
    options: dict[str, Any] = {"redacted_pdf": True}
    if not ocr_ready:
        options["ocr_verify"] = False
    red = upload(c, "kyc-text.pdf", "obfuscate", {"options": options})
    rbody = _json(red)
    redaction = rbody.get("document", {}).get("redaction") or {}
    pdf_b64 = rbody.get("redacted_pdf_b64")
    chars = text_layer_chars(pdf_b64) if pdf_b64 else -1
    check(
        "documents redacted PDF has no text",
        red.status_code == 200 and redaction.get("passed") is True and chars in (0, None),
        f"HTTP {red.status_code}, {redaction.get('boxes')} boxes, "
        f"text characters {chars}, ocr_checked={redaction.get('ocr_checked')}",
    )

    original = (DEMO / "kyc.txt").read_text(encoding="utf-8")
    obf = upload(c, "kyc.txt", "obfuscate")
    obody = _json(obf)
    rev = c.post(
        "/api/v1/reveal",
        headers=H,
        json={
            "text": obody.get("result"),
            "key_material": obody.get("key_material"),
            "reveal_hints": obody.get("reveal_hints"),
        },
    )
    check(
        "documents text reveal round trip",
        obf.status_code == 200
        and rev.status_code == 200
        and obody["result"] != original
        and rev.json()["result"] == original,
        f"HTTP {obf.status_code}/{rev.status_code}",
    )

    source_csv = (DEMO / "customers.csv").read_text(encoding="utf-8")
    syn = upload(c, "customers.csv", "synthesize", {"policy": "synthetic-realistic"})
    result_csv = _json(syn).get("result_csv") or ""
    first_email = source_csv.splitlines()[1].split(",")[2]
    check(
        "documents CSV synthesize",
        syn.status_code == 200
        and result_csv.splitlines()[:1] == source_csv.splitlines()[:1]
        and len(result_csv.splitlines()) == len(source_csv.splitlines())
        and first_email not in result_csv,
        f"HTTP {syn.status_code}, {max(len(result_csv.splitlines()) - 1, 0)} rows",
    )

    if ocr_ready:
        scan = upload(c, "kyc-scanned.pdf", "obfuscate", {"options": {"redacted_pdf": True}})
        sbody = _json(scan)
        sred = sbody.get("document", {}).get("redaction") or {}
        types = _types(sbody)
        check(
            "scanned PDF via OCR, redacted and re-read",
            scan.status_code == 200
            and sbody["document"]["ocr_pages"] == [1, 2]
            and sred.get("ocr_checked") is True
            and sred.get("ocr_hits") == 0
            and {"AADHAAR", "EMAIL"} <= types,
            f"HTTP {scan.status_code}, engine {ocr.get('engine')}, {', '.join(sorted(types))}",
        )
        image = upload(c, "id-card.png", "detect")
        found = len(_json(image).get("findings", []))
        check(
            "image via OCR",
            image.status_code == 200 and found >= 3,
            f"HTTP {image.status_code}, {found} findings",
        )
    else:
        why = f"ocr {ocr.get('detail', 'absent')}: docker compose --profile docs up -d"
        skip("scanned PDF via OCR, redacted and re-read", why)
        skip("image via OCR", "ocr not ready")

    ddl = (
        "CREATE TABLE customers (customer_id INTEGER PRIMARY KEY, full_name VARCHAR(80), "
        "email VARCHAR(120), balance NUMERIC(12,2))"
    )
    spec = c.post("/api/v1/schema/generator-spec", headers=H, json={"ddl": ddl})
    fields = _json(spec).get("spec", {}).get("fields", [])
    check(
        "schema generator-spec from DDL",
        spec.status_code == 200 and len(fields) == 4,
        f"HTTP {spec.status_code}, {len(fields)} fields",
    )
    first = c.post(
        "/api/v1/schema/synthesize", headers=H, json={"spec": spec.json()["spec"], "rows": 25}
    )
    fbody = _json(first)
    again = c.post(
        "/api/v1/schema/synthesize",
        headers=H,
        json={"spec": spec.json()["spec"], "rows": 25, "seed": fbody.get("synthesis_seed")},
    )
    check(
        "schema synthesize is reproducible",
        first.status_code == 200
        and again.status_code == 200
        and len(fbody["rows"]) == 25
        and fbody["rows"] == again.json()["rows"],
        f"HTTP {first.status_code}/{again.status_code}, {len(fbody.get('rows', []))} rows",
    )

    demo = c.get("/demo/kyc-scanned.pdf")
    check(
        "demo scan served to the console",
        demo.status_code == 200 and demo.content.startswith(b"%PDF-"),
        f"HTTP {demo.status_code}",
    )


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


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """``--m3``: also run the Oracle + MongoDB checks (module docstring)."""
    p = argparse.ArgumentParser(prog="python tools/smoke_test.py")
    p.add_argument("--m3", action="store_true", help="add the M3 Oracle + MongoDB checks")
    return p.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    """Run the Layer 1 checks in order (then M1, M2 and, with ``--m3``, M3)."""
    args = parse_args(argv)
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

        m1_checks(c)

        if scanner_wanted(os.environ.get("DPS_SMOKE_SCANNER")):
            with httpx.Client(base_url=DEMO_FROM_HERE, timeout=5.0) as demo:
                scanner_checks(c, demo)

        root = c.get("/")
        check(
            "console served",
            root.status_code == 200 and b'<div id="root">' in root.content,
            f"HTTP {root.status_code}",
        )
        run_m2, note = m2_wanted(components, os.environ.get("DPS_SMOKE_M2"))
        if run_m2:
            m2_checks(c)
        else:
            print(note)
        if args.m3:
            if not run_m2:
                check("m3 needs the job platform", False, "the M2 checks did not run")
            m3_checks(c)
    tail = f", {len(skipped)} skipped (OCR not ready)" if skipped else ""
    print(f"\nall {len(results)} checks passed against {BASE}{tail}")


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
    c: httpx.Client,
    conn_id: str,
    owner: str | None,
    name: str,
    overrides: list[dict[str, object]],
    timeout: float = 60.0,
) -> tuple[str | None, str]:
    """Discover one table, apply review overrides, accept; ``(snapshot id, detail)``."""
    obj = {"owner": owner, "name": name}
    r = c.post(
        "/api/v1/jobs/discover", json={"connection_id": conn_id, "objects": [obj]}, headers=H
    )
    if r.status_code != 202:
        return None, f"HTTP {r.status_code} {r.json().get('code')}"
    job = poll(c, r.json()["job"]["id"], timeout)
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


# -- M4: the UI scanner (profile `scanner`) ------------------------------------------------------


def scanner_wanted(setting: str | None) -> bool:
    """Whether to run the scanner checks: only when ``DPS_SMOKE_SCANNER`` is exactly ``1``."""
    return setting == "1"


def demo_from_here(env: Mapping[str, str]) -> str:
    """The demo site as this script reaches it (a lane's compose publishes it on its own port)."""
    explicit = env.get("DPS_TEST_DEMO_SITE")
    if explicit:
        return explicit.rstrip("/")
    return f"http://127.0.0.1:{env.get('DPS_DEMO_SITE_PORT') or '8410'}"


#: The demo site as the browser-worker sees it (the scan's start URL host).
DEMO_FROM_WORKER = (os.environ.get("DPS_SMOKE_DEMO_URL") or "http://demo-site:8410").rstrip("/")
DEMO_FROM_HERE = demo_from_here(os.environ)
SCAN_TIMEOUT = float(os.environ.get("DPS_SMOKE_SCAN_TIMEOUT") or "300")
#: The demo site's customer values (made up), which no export may carry. The birth date is left out:
#: a generated persona may share a date, and a date is not identifying on its own.
DEMO_VALUES: tuple[str, ...] = tuple(
    str(v)
    for k, v in json.loads(
        (Path(__file__).resolve().parents[1] / "demo/onboarding/data.json").read_text("utf-8")
    ).items()
    if k != "dob"
)
#: The demo site's counters that must not move during a scan: every non-GET request, every
#: GET-form submission, and every hit on the paths the scan denies.
SITE_COUNTERS = ("non_get", "form_get", "logout_hits", "admin_hits")


def _received(demo: httpx.Client) -> dict[str, int]:
    """The demo site's ``/__received`` counters (counts only)."""
    body = demo.get("/__received").json()
    return {name: int(body[name]) for name in SITE_COUNTERS}


def _artefact_png(c: httpx.Client, artefact_id: str | None) -> bool:
    """Whether the app serves ``artefact_id`` as a PNG."""
    if not artefact_id:
        return False
    r = c.get(f"/api/v1/artefacts/{artefact_id}", headers=H)
    return r.status_code == 200 and r.content[:4] == b"\x89PNG"


def _browser_ready(c: httpx.Client, wait: float, interval: float) -> dict[str, Any]:
    """``/readyz``'s ``browser`` component, polled for up to ``wait`` seconds: the app refreshes it
    from worker heartbeats every 15 s, so a stack that has just come up still reads ``unknown``."""
    deadline = time.monotonic() + wait
    while True:
        browser: dict[str, Any] = c.get("/readyz").json()["components"].get("browser", {})
        if browser.get("ok") or time.monotonic() >= deadline:
            return browser
        time.sleep(interval)


def scanner_checks(
    c: httpx.Client,
    demo: httpx.Client,
    *,
    timeout: float = SCAN_TIMEOUT,
    interval: float = 3.0,
    browser_wait: float = 60.0,
) -> None:
    """The eleven UI-scanner checks (module docstring); ``demo`` reads the demo site's counters."""
    browser = _browser_ready(c, browser_wait, interval)
    check("browser worker online", bool(browser.get("ok")), str(browser.get("detail")))

    before = _received(demo)
    created = c.post(
        "/api/v1/scans",
        headers=H,
        json={
            "url": f"{DEMO_FROM_WORKER}/apply",
            "scope": {"max_pages": 25, "max_depth": 3, "deny": ["/logout", "/admin"]},
            "auth": {"storage_state_ref": "dps/ui/demo-onboarding"},
            "mode": "synthesize",
            "fill": {"personas": 5, "locale": "en_IN"},
        },
    )
    body = created.json() if created.status_code == 202 else {}
    check(
        "scan accepted, key and seed returned once",
        created.status_code == 202
        and bool((body.get("key_material") or {}).get("dek"))
        and bool((body.get("synthesis_seed") or {}).get("seed")),
        f"HTTP {created.status_code}",
    )
    body.pop("key_material", None)  # never kept or printed
    body.pop("synthesis_seed", None)
    sid = body["scan"]["id"]

    detail: dict[str, Any] = {}
    deadline = time.monotonic() + timeout
    while True:
        detail = c.get(f"/api/v1/scans/{sid}", headers=H).json()
        if detail["scan"]["state"] in TERMINAL or time.monotonic() >= deadline:
            break
        time.sleep(interval)
    s = detail.get("summary", {})
    check(
        "scan succeeded",
        detail["scan"]["state"] == "succeeded",
        f"{detail['scan']['state']}: {s.get('pages_visited')} pages, {s.get('fields')} fields, "
        f"{s.get('personal_fields')} personal",
    )
    flags = {f["flag"] for f in s.get("risk_flags", [])}
    wanted = {"autocomplete_enabled_sensitive", "pii_in_query_string", "pii_rendered_unmasked"}
    check(
        "classification and risk flags",
        s.get("personal_fields", 0) >= 20 and flags >= wanted,
        f"{s.get('personal_fields')} personal fields, {len(s.get('risk_flags', []))} risk flags, "
        f"{len(wanted - flags)} expected flag kinds missing",
    )

    after = _received(demo)
    moved = {name: after[name] - before[name] for name in SITE_COUNTERS}
    check(
        "no submission reached the site",
        not any(moved.values()) and s.get("non_get_allowed") == 0,
        ", ".join(f"{name} +{n}" for name, n in moved.items())
        + f"; non_get_allowed {s.get('non_get_allowed')}, blocked {s.get('blocked_requests')}",
    )

    pages = c.get(f"/api/v1/scans/{sid}/pages", params={"limit": 200}, headers=H).json()["items"]
    ident = next((p for p in pages if p["url_redacted"].endswith("/apply/identity")), {})
    shots = ident.get("artefacts", {})
    parallel = {"original", "annotated", "filled_synthesize", "filled_obfuscate"}
    check(
        "parallel view captured",
        parallel <= set(shots),
        f"{len(parallel & set(shots))}/4 screenshots of the identity page",
    )
    check("screenshot served", _artefact_png(c, shots.get("filled_synthesize")))
    masked = [p["artefacts"]["masked_capture"] for p in pages if "masked_capture" in p["artefacts"]]
    check(
        "masked capture served",
        s.get("captures", 0) >= 1 and bool(masked) and _artefact_png(c, masked[0]),
        f"{s.get('captures')} captures, {len(masked)} masked",
    )

    export = c.get(f"/api/v1/scans/{sid}/export", params={"format": "json"}, headers=H)
    text = export.text.lower() if export.status_code == 200 else ""
    found = sum(1 for v in DEMO_VALUES if v.lower() in text)
    check(
        "persona export",
        export.status_code == 200 and export.json().get("personas") == 5 and found == 0,
        f"HTTP {export.status_code}, {found} demo values found",
    )

    evidence = c.get("/api/v1/evidence", params={"job_id": sid}, headers=H).json().get("items", [])
    pack = (
        c.get(f"/api/v1/evidence/{evidence[0]['id']}", headers=H).json()["pack"] if evidence else {}
    )
    scan_block = pack.get("scan") or {}
    check(
        "evidence: submissions 0",
        bool(scan_block)
        and scan_block.get("submissions") == 0
        and scan_block.get("non_get_allowed") == 0,
        f"{len(evidence)} packs",
    )
    console = c.get("/scanner/new")
    check(
        "scanner console served",
        console.status_code == 200 and b'<div id="root">' in console.content,
        f"HTTP {console.status_code}",
    )


# -- M3: Oracle and MongoDB under one job group (profiles `oracle` + `sources`) -------------------

M3_TIMEOUT = float(os.environ.get("DPS_SMOKE_M3_TIMEOUT") or "600")


@dataclass(frozen=True)
class M3Member:
    """One system of the three-engine demo: source connection/object → target connection/object."""

    engine: str
    source: str
    obj: dict[str, str | None]
    target: str
    target_obj: dict[str, str | None]


#: Submitted in this order: the newest run (Oracle) anchors the consistency check's sample.
M3_MEMBERS: tuple[M3Member, ...] = (
    M3Member(
        "postgres",
        "bank-pg-ro",
        {"owner": "bank", "name": "customers"},
        "bank-uat",
        {"owner": "bank_uat", "name": "customers_smoke"},
    ),
    M3Member(
        "mongo",
        "smoke-mdb-src",
        {"owner": None, "name": "profiles"},
        "smoke-mdb-tgt",
        {"owner": None, "name": "profiles_smoke"},
    ),
    M3Member(
        "oracle",
        "smoke-ora-src",
        {"owner": "CRM", "name": "CUSTOMERS"},
        "smoke-ora-tgt",
        {"owner": "CRM_MASKED", "name": "CUSTOMERS_SMOKE"},
    ),
)


def m3_run_spec(
    member: M3Member, ids: dict[str, str], snap_id: str, group_id: str, key: dict[str, Any]
) -> dict[str, Any]:
    """A ``table`` run of ``member`` under the group's key (truncate-insert, target created)."""
    return {
        "mode": "obfuscate",
        "snapshot_id": snap_id,
        "group_id": group_id,
        "purpose": PURPOSE,
        "source": {"connection_id": ids[member.source], "object": member.obj, "parallelism": 2},
        "target": {
            "write_mode": "table",
            "connection_id": ids[member.target],
            "object": member.target_obj,
            "create_if_missing": True,
            "table_load": "truncate_insert",
        },
        "consistency": {"scope": "key", "key_material": key},
    }


def consistency_verdict(body: dict[str, Any]) -> tuple[bool, str]:
    """Passed with 3 of 3 pairs consistent; the detail is counts, coverage and problem codes."""
    consistent = int(body.get("consistent_pairs") or 0)
    total = int(body.get("total_pairs") or 0)
    ok = bool(body.get("passed")) and consistent == total == 3
    detail = f"{consistent}/{total} pairs consistent, {int(body.get('sampled') or 0)} sampled"
    pairs = body.get("pairs") or []
    if pairs:
        detail += f", min coverage {min(float(p.get('coverage') or 0.0) for p in pairs):g}"
    codes = sorted({str(p.get("code")) for p in body.get("problems") or []})
    if codes:
        detail += ", problems " + ", ".join(codes)
    return ok, detail


def ensure_m3_connections(c: httpx.Client) -> tuple[dict[str, str], int]:
    """``({name: id}, created)`` for M3's four demo connections, created when missing."""
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
    for name, spec in M3_CONNECTIONS.items():
        if name not in found:
            made = c.post("/api/v1/connections", json={"name": name, **spec}, headers=H)
            made.raise_for_status()
            found[name] = made.json()["id"]
            created += 1
    return {name: found[name] for name in M3_CONNECTIONS}, created


def _rows_written(job: dict[str, object]) -> str:
    progress = job.get("progress")
    return str(progress.get("rows_written")) if isinstance(progress, dict) else "-"


def m3_checks(c: httpx.Client) -> None:
    """The seven Oracle + MongoDB checks (module docstring)."""
    m2_ids, _ = ensure_connections(c)
    m3_ids, created = ensure_m3_connections(c)
    ids = {**m3_ids, "bank-pg-ro": m2_ids["bank-pg-ro"], "bank-uat": m2_ids["bank-uat"]}
    roles = {
        **{n: spec["roles"][-1] for n, spec in M3_CONNECTIONS.items()},
        "bank-pg-ro": "source",
        "bank-uat": "target",
    }
    tested = [
        name
        for name, role in roles.items()
        if c.post(f"/api/v1/connections/{ids[name]}/test", json={"role": role}, headers=H)
        .json()
        .get("ok")
    ]
    check(
        "m3 connections",
        len(tested) == len(roles),
        f"{created} created, {len(M3_CONNECTIONS) - created} reused, "
        f"{len(tested)}/{len(roles)} tested ok",
    )

    snaps: list[str] = []
    details: list[str] = []
    for m in M3_MEMBERS:
        snap, detail = discover_and_accept(
            c, ids[m.source], m.obj["owner"], str(m.obj["name"]), [], timeout=300.0
        )
        if snap:
            snaps.append(snap)
        else:
            details.append(f"{m.engine}: {detail}")
    check(
        "m3 discover",
        len(snaps) == len(M3_MEMBERS),
        f"{len(snaps)}/{len(M3_MEMBERS)} snapshots accepted" + "".join(f"; {d}" for d in details),
    )

    made = c.post("/api/v1/job-groups", json={"name": f"smoke-m3-{int(time.time())}"}, headers=H)
    made_body = made.json() if made.status_code == 201 else {}
    group = made_body.get("group") or {}
    key: dict[str, Any] = made_body.get("key_material") or {}
    kid = key.get("kid")
    stored = c.get(f"/api/v1/job-groups/{group.get('id')}", headers=H) if group else None
    check(
        "m3 group",
        made.status_code == 201
        and bool(kid)
        and stored is not None
        and stored.status_code == 200
        and "key_material" not in stored.text
        and str(key.get("dek") or "-") not in stored.text,
        f"HTTP {made.status_code}; the key is in the create answer only",
    )

    jobs: list[str] = []
    refused: list[str] = []
    for m, snap in zip(M3_MEMBERS, snaps, strict=True):
        r = c.post("/api/v1/jobs/run", json=m3_run_spec(m, ids, snap, group["id"], key), headers=H)
        if r.status_code == 202:
            jobs.append(r.json()["job"]["id"])
        else:
            refused.append(f"{m.engine}: HTTP {r.status_code} {r.json().get('code')}")
    del key, made_body  # the group's key material: never kept beyond the submissions
    deadline = time.monotonic() + M3_TIMEOUT
    done = [poll(c, jid, max(1.0, deadline - time.monotonic())) for jid in jobs]
    states = [str(j.get("state")) for j in done]
    rows = [_rows_written(j) for j in done]
    errors = [str(j.get("error_code")) for j in done if j.get("state") != "succeeded"]
    check(
        "m3 runs",
        not refused and states == ["succeeded"] * len(M3_MEMBERS),
        f"{states.count('succeeded')}/{len(M3_MEMBERS)} succeeded, rows "
        + "/".join(rows)
        + " (pg/mongo/oracle)"
        + "".join(f"; {e}" for e in refused + errors),
    )

    res = c.post(
        f"/api/v1/job-groups/{group['id']}/consistency-check", json={"sample": 500}, headers=H
    )
    ok, detail = consistency_verdict(res.json() if res.status_code == 200 else {})
    check("m3 consistency 3/3", res.status_code == 200 and ok, f"HTTP {res.status_code}, {detail}")

    verified = 0
    for job in done:
        pack = evidence_of(c, job).get("pack") or {}
        if (
            isinstance(pack, dict)
            and (pack.get("verification") or {}).get("passed")
            and (pack.get("consistency") or {}).get("kid") == kid
        ):
            verified += 1
    check(
        "m3 evidence",
        verified == len(M3_MEMBERS),
        f"{verified}/{len(M3_MEMBERS)} packs verified under the group's key id",
    )
    check(
        "m3 raw pii to llm",
        b"dps_llm_raw_pii_sent_total 0.0" in c.get("/metrics").content,
        "dps_llm_raw_pii_sent_total 0",
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
