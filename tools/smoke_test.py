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

Waits for ``/health``, then runs the checks in order, prints a one-line verdict per check and exits
non-zero on the first failure. Never prints a request or response body, a value or a key: a
detail is a count, a method name or a component state, and a response of an unexpected shape is
reported by exception class only.

M1 checks (documents, OCR, schema) upload the committed synthetic files in ``demo/documents``.
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

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

        m1_checks(c)

        root = c.get("/")
        check(
            "console served",
            root.status_code == 200 and b'<div id="root">' in root.content,
            f"HTTP {root.status_code}",
        )
    tail = f", {len(skipped)} skipped (OCR not ready)" if skipped else ""
    print(f"\nall {len(results)} checks passed against {BASE}{tail}")


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
