"""Live Vertex test of DPS's three LLM features through the real HTTP API.

Usage (from a DPS worktree whose .venv has the [vertex,ner] extras and en_core_web_sm):
    .venv/bin/python -I ~/.cache/dps-build/vertex_live/vertex_live_test.py <sa-key.json> <gcp-project>

Runs DPS (no database: static keys, Layer 1 only) in-process on 127.0.0.1:8433 with
DPS_LLM_PROVIDER=vertex, records every prompt handed to Vertex, and checks that no raw input value
ever reaches the model (P1). All "PII" below is invented. Key material is never printed: only the
key's path is referenced. Prompts are saved next to this file as prompts_sent.json.
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import secrets
import sys
import threading
import time

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)  # never pick up a repo's .env
API_KEY = "dps_vtx_" + secrets.token_hex(16)  # throwaway, lives only in this process
os.environ.update(
    {
        "GOOGLE_APPLICATION_CREDENTIALS": sys.argv[1],
        "DPS_ENV": "local",
        "DPS_LLM_PROVIDER": "vertex",
        "DPS_VERTEX_PROJECT": sys.argv[2],
        "DPS_LLM_MODEL": "gemini-2.5-flash",
        "DPS_LLM_TEXT_REWRITE": "true",
        "DPS_BOOTSTRAP_API_KEY": API_KEY,
        "DPS_AUTH_ENABLED": "true",
        "DPS_FPE_ENABLED": "false",
        "DPS_LOG_LEVEL": "WARNING",
    }
)

import httpx  # noqa: E402
import uvicorn  # noqa: E402
from stdnum import verhoeff  # noqa: E402

from dps.llm import gateway as gw  # noqa: E402

PROMPTS: list[list[dict[str, str]]] = []
_orig_vertex = gw.LlmGateway._vertex


def _recording_vertex(self, messages, temperature, max_tokens):  # type: ignore[no-untyped-def]
    PROMPTS.append([dict(m) for m in messages])
    return _orig_vertex(self, messages, temperature, max_tokens)


gw.LlmGateway._vertex = _recording_vertex  # type: ignore[method-assign]

from dps.app import create_app  # noqa: E402
from dps.config import Settings  # noqa: E402

PORT = int(os.environ.get("VERTEX_LIVE_PORT", "8433"))
BASE = f"http://127.0.0.1:{PORT}"
H = {"X-API-KEY": API_KEY}

_aadhaar11 = "4" + "".join(str(secrets.randbelow(10)) for _ in range(10))
AADHAAR = _aadhaar11 + verhoeff.calc_check_digit(_aadhaar11)

# --- invented inputs ---------------------------------------------------------------------------
T3_DATA = {
    "acct": {
        "beneficiary_nm": "Kavya Raghunathan",
        "kin_contact_line": "Suresh Raghunathan",
        "residence_ln1": "14 Lakeview Crescent, Koregaon Park",
        "ref_x7": "rel-mgr-allocated",
        "branch_code": "PUN-0042",
        "risk_band": "medium",
    }
}
REWRITE_TEXT = (
    f"Customer Arjun Mehta (Aadhaar {AADHAAR[:4]} {AADHAAR[4:8]} {AADHAAR[8:]}, "
    "email arjun.mehta@example.in, phone +91 98765 43210) called on 12 March 2025 to "
    "dispute a debit of INR 48,250 on card 4111 1111 1111 1111. He asked that the refund go "
    "to his HDFC account and that Priya Nair in the Pune branch call him back."
)
SAMPLE_ROWS = [
    {"cust_id": 1001, "full_name": "Arjun Mehta", "email": "arjun.mehta@example.in",
     "city": "Pune", "segment": "retail", "balance": 48250.50, "opened": "2019-04-11"},
    {"cust_id": 1002, "full_name": "Priya Nair", "email": "priya.nair@example.in",
     "city": "Kochi", "segment": "premier", "balance": 812300.00, "opened": "2016-09-02"},
    {"cust_id": 1003, "full_name": "Kavya Raghunathan", "email": "kavya.r@example.in",
     "city": "Chennai", "segment": "retail", "balance": 1520.75, "opened": "2022-01-19"},
    {"cust_id": 1004, "full_name": "Rohan Kulkarni", "email": "rohan.k@example.in",
     "city": "Mumbai", "segment": "business", "balance": 2390000.00, "opened": "2014-06-30"},
]
RAW_VALUES = [
    "Kavya Raghunathan", "Suresh Raghunathan", "Lakeview Crescent", "Koregaon",
    "Arjun Mehta", "arjun.mehta@example.in", "98765 43210", "9876543210", AADHAAR,
    AADHAAR[:4] + " " + AADHAAR[4:8], "4111 1111 1111 1111", "4111111111111111", "Priya Nair",
    "priya.nair@example.in", "kavya.r@example.in", "Rohan Kulkarni", "rohan.k@example.in",
    "Mehta", "Raghunathan", "Kulkarni",
]


def leaks(text: str) -> list[str]:
    low = text.lower()
    digits = re.sub(r"\D", "", text)
    hit = [v for v in RAW_VALUES if v.lower() in low]
    hit += [v for v in (AADHAAR, "4111111111111111", "9876543210") if v in digits]
    return sorted(set(hit))


def hr(title: str) -> None:
    print(f"\n=== {title} " + "=" * max(0, 70 - len(title)))


async def main() -> int:
    failures: list[str] = []
    async with httpx.AsyncClient(base_url=BASE, headers=H, timeout=120) as c:
        hr("0. readiness")
        r = await c.get("/readyz")
        comps = r.json().get("components", r.json())
        llm = comps.get("llm-gateway") if isinstance(comps, dict) else None
        print("readyz", r.status_code, "llm-gateway:", json.dumps(llm))

        hr("1. gateway smoke (direct)")
        g = gw.get_gateway(Settings())
        t = time.perf_counter()
        res = await g.complete(
            [{"role": "system", "content": "Reply with the single word READY."},
             {"role": "user", "content": "Status?"}],
            temperature=0.0, max_tokens=16, purpose="smoke",
        )
        print(f"provider={res.provider} model={res.model} reply={res.text.strip()!r} "
              f"latency={time.perf_counter() - t:.2f}s")
        if res.provider != "vertex" or not res.text.strip():
            failures.append("smoke")

        hr("2. T3 LLM labelling (POST /api/v1/obfuscate, policy + llm_label tier)")
        policy = {"preset": "gdpr-pseudonymise",
                  "detect": {"tiers": ["structural", "patterns", "ner", "llm_label"]}}
        r = await c.post("/api/v1/obfuscate", json={"data": T3_DATA, "policy": policy})
        body = r.json()
        print("status", r.status_code)
        if r.status_code != 200:
            print(json.dumps(body)[:800])
            failures.append("t3")
        else:
            for f in body["findings"]:
                print(f"  {f['locator']['path']:<28} {f['entity_type']:<18} "
                      f"score={f['score']:.2f} via {','.join(f['detectors'])}")
            print("  report.llm:", body["report"].get("llm"))
            via_llm = [f for f in body["findings"]
                       if any(d.startswith("llm_label") for d in f["detectors"])]
            print(f"  findings from llm_label: {len(via_llm)}")
            if not via_llm:
                failures.append("t3-no-llm-findings")
            out_leaks = leaks(json.dumps(body["result"]))
            print("  raw values left in output:", out_leaks or "none")
            if out_leaks:
                failures.append("t3-output-leak")

        hr("3. LLM text rewrite (POST /api/v1/synthesize, text, llm_rewrite=true)")
        r = await c.post("/api/v1/synthesize",
                         json={"text": REWRITE_TEXT, "policy": "synthetic-realistic",
                               "options": {"llm_rewrite": True}})
        body = r.json()
        print("status", r.status_code)
        if r.status_code != 200:
            print(json.dumps(body)[:800])
            failures.append("rewrite")
        else:
            print("  report.llm:", body["report"].get("llm"))
            print("  synthetic output:\n   ", body["result"])
            out_leaks = leaks(body["result"])
            print("  raw values left in output:", out_leaks or "none")
            if out_leaks:
                failures.append("rewrite-output-leak")

        hr("4. generator spec (POST /api/v1/schema/generator-spec, sample, use_llm)")
        r = await c.post("/api/v1/schema/generator-spec",
                         json={"sample": SAMPLE_ROWS, "name": "customers", "use_llm": True})
        body = r.json()
        print("status", r.status_code)
        if r.status_code != 200:
            print(json.dumps(body)[:800])
            failures.append("genspec")
        else:
            print(f"  source={body['source']} fallback={body['fallback']} llm={body['llm']}")
            names = [fld.get("name") for fld in body["spec"].get("fields", [])]
            print("  spec field names:", names)
            if names[:1] != ["cust_id"]:
                failures.append("genspec-names")
            if body["source"] != "llm":
                failures.append("genspec-not-llm")
            r2 = await c.post("/api/v1/schema/synthesize",
                              json={"spec": body["spec"], "name": "customers", "rows": 4})
            b2 = r2.json()
            print("  schema/synthesize status", r2.status_code)
            if r2.status_code == 200:
                for row in (b2.get("rows") or [])[:4]:
                    print("   ", json.dumps(row))
                out_leaks = leaks(json.dumps(b2.get("rows")))
                print("  sample values reproduced:", out_leaks or "none")
            else:
                print(json.dumps(b2)[:600])
                failures.append("schema-synthesize")

        hr("5. metrics")
        r = await c.get("/metrics")
        for line in r.text.splitlines():
            if line.startswith(("dps_llm_calls_total", "dps_llm_label_fields_total")) and \
                    not line.endswith(" 0.0"):
                print(" ", line)

    hr("6. P1 check: what was actually sent to Vertex")
    print(f"prompts sent: {len(PROMPTS)}")
    all_leaks: set[str] = set()
    with open(os.path.join(HERE, "prompts_sent.json"), "w") as fh:
        json.dump(PROMPTS, fh, indent=1)
    for i, msgs in enumerate(PROMPTS):
        text = "\n".join(m["content"] for m in msgs)
        lk = leaks(text)
        all_leaks |= set(lk)
        user = next((m["content"] for m in msgs if m.get("role") == "user"), "")
        print(f"  [{i}] {len(text):>6} chars, raw values: {lk or 'none'}")
        print("      user part preview:", re.sub(r"\s+", " ", user)[:260])
    if all_leaks:
        failures.append("P1")
    hr("RESULT")
    print("PASS" if not failures else f"FAIL: {failures}")
    return 0 if not failures else 1


def run() -> int:
    app = create_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=PORT, log_level="warning"))
    th = threading.Thread(target=server.run, daemon=True)
    th.start()
    for _ in range(100):
        if server.started:
            break
        time.sleep(0.1)
    try:
        return asyncio.run(main())
    finally:
        server.should_exit = True
        th.join(timeout=10)


if __name__ == "__main__":
    sys.exit(run())
