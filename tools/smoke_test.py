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
"""

from __future__ import annotations

import os
import sys
import time

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

results: list[tuple[str, bool, str]] = []


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
    print(f"\nall {len(results)} checks passed against {BASE}")


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
