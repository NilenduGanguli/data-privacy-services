# S3 security finding — lens: SSRF & egress (branch `fsec`)

Date: 2026-10-14. Reviewer: S3 (ssrf-egress). Branch `fsec` = current `main` (M0–M3 + M5).
Worktree: `/Users/neelu/document_intelligence/dps-wt-fsec`. Probes (offline, read-only):
`/Users/neelu/.cache/dps-build/reports/S3-ssrf-egress/`. **No repository file was modified.**

## Scope and method
Lens = outbound egress and SSRF: tokenizer adapter, OCR client, Stellar/COIN + Vertex gateway,
S3 endpoints, DB connection hosts, webhook-like URLs, TLS-only egress posture, metadata-IP /
numeric-host encodings, DNS rebinding. Method: read every egress sink in `dps/`, enumerate
caller/tenant-controlled vs operator-controlled inputs, and run an offline unit probe against this
worktree's own venv (`./lane.sh python -I`). I also checked the M5 **Task 17 S1–S13** items that
fall in this lens and report the unaddressed ones.

Egress sinks found (grep of httpx/boto3/asyncpg/pymongo/oracledb): `dps/llm/gateway.py` (COIN mint,
Stellar) operator URLs; `dps/layers/documents/ocr.py` (Azure Read) operator URL + validated
`Operation-Location`; `dps/engine/transform/external.py` (bank tokenizer) operator URL;
`dps/storage.py` (boto3 S3) operator artefact endpoint **and tenant target `endpoint_url`**; the
PG/Oracle/Mongo connectors (tenant `host`/`uri`). **No caller-controlled fetch-by-URL / webhook /
image-URL sink exists** (grep for webhook/callback/image_url/urlopen returned nothing), so the SSRF
surface is confined to connection endpoints + the (not-yet-merged) scanner.

### What is already sound (no finding)
- httpx `follow_redirects` defaults to **False** (httpx 0.28.1, verified) and OCR sets it
  explicitly; the COIN/Stellar/tokenizer clients therefore never follow a 3xx to a new host, so a
  compromised upstream cannot bounce the bearer token / subscription key elsewhere.
- `AzureReadClient._same_origin` re-parses the `Operation-Location` with **both** the stdlib parser
  and httpx and refuses the poll (with the subscription key) unless **both** place it on the
  endpoint's origin — a solid parser-differential defence.
- COIN mint `verify=False` is reachable only via `DPS_COIN_TLS_INSECURE`, and the posture guard
  (`dps/posture.py`) refuses it in staging/prod unless `DPS_ALLOW_INSECURE_COIN_MINT` acknowledges
  the documented VDI exception. API calls always verify.
- TLS-only egress (`require_tls_connection`, `_egress_problems`) correctly forces `https` on every
  operator egress URL and on connection schemes in staging/prod (`test_egress_tls.py` passes).

---

## Finding A — MEDIUM — no SSRF / metadata-IP guard on tenant-controlled connection endpoints
**Files:** `dps/netguard.py` (`require_tls_connection`, `normalize_host`),
`dps/storage.py:369` (`target_store`), `dps/layers/connectors/registry.py` (`open_target_store`,
`open_connector`), reachable via `dps/layers/api/routers/connections.py` (`create`, `patch`,
`test`, `count`, `objects`, `describe`).

**Claim.** The only host-level validation applied to a connection is `require_tls_connection`,
which checks the **scheme** (`https`) and nothing else. There is **no** refusal of cloud-metadata
addresses, link-local, loopback, multicast/reserved, IPv4-mapped-IPv6, or numeric-host encodings,
and **no** DNS pinning. `normalize_host` shapes the string but passes `169.254.169.254`,
`0x7f000001` and `2130706433` straight through. A tenant holding only the **`jobs`** scope can
register a non-`production` S3 **target** (admin is required only when the `production` tag is set —
`connections.py:139`) whose `params.endpoint_url` is `https://169.254.169.254/`,
`https://metadata.google.internal/`, `https://127.0.0.1:9000/` or an internal service, and DPS will
dial it: `target_store` passes `conn.params.endpoint_url` straight into boto3 (`storage.py:394`).
The same holds for PG/Oracle/Mongo `host` (asyncpg/oracledb/pymongo connect to it) and for a
Mongo `mongodb+srv://` URI (an SRV/TXT DNS lookup to a secret-named domain).

**Exploit / failure scenario.** With `jobs` scope only:
1. `POST /api/v1/connections` `{engine:"s3", roles:["target"], params:{bucket:"uat",
   endpoint_url:"https://10.0.0.5:8500/"}, secret_ref|credentials:…}` → 201.
2. `POST /api/v1/connections/{id}/test` → `_test()` → `open_target_store` → `test_store` →
   `store.put(...)`/`delete(...)` is a **synchronous** boto3 request to the tenant-chosen host; the
   HTTP outcome (success / `storage_error` + botocore code / timeout) is returned in the response.
   That is a repeatable **blind-to-semi-blind SSRF probe** of the pod's network position — internal
   port scanning, reachability of internal admin ports, IMDS endpoints — all under TLS-only posture
   (`https://<internal>/` passes the scheme check). No job need ever run.

Evidence (offline, prod posture `egress_tls_required=True`):
`/Users/neelu/.cache/dps-build/reports/S3-ssrf-egress/probe_endpoint_ssrf.py` — all seven SSRF
targets (metadata v4/v6, `metadata.google.internal`, Alibaba `100.100.100.200`, loopback,
internal `10.0.0.5`, numeric `0x7f000001`) are **ACCEPTED**; `netguard` exposes no `check_scan_url`
and no `_forbidden_ip`. Protocol constraints (boto3 speaks S3; drivers do a handshake) bound the
blast radius, so this is MEDIUM, not HIGH — but the design already treats metadata/link-local as
must-refuse for the scanner (see Finding B), and that refusal is simply missing on the connection
surface.

**Fix.** Add the SSRF host guard to `dps/netguard.py` (the `_forbidden_ip` logic described in
Finding B: refuse link-local, the four metadata ranges, IPv4-mapped-IPv6, multicast/unspecified/
reserved, and loopback outside `env=="local"`; reject numeric-host encodings that are not valid IP
literals; resolve the host and re-check every address so DNS cannot rebind to a forbidden range).
Call it on `endpoint_url`/`host` at connection **accept** time (`create`/`patch`) and again at
**use** (in `open_target_store` / `open_connector`, so a connection stored before the rule or a
rebinding at connect time is still caught). Keep RFC-1918 allowed by design (internal UAT), exactly
as the scanner guard does. Add a test mirroring `test_netguard.py` for the connection surface.

## Finding B — HIGH (lands with M4) — Task 17 **S5** SSRF guard is entirely unimplemented
**Files (to be created):** `dps/netguard.py` (`check_scan_url`, `chromium_resolver_rules`,
`_forbidden_ip`, `_NUMERIC_HOST`, `_METADATA`), `tests/security/test_netguard.py`; wiring in the M4
`POST /scans` router + crawler launch flags.

**Claim.** M5 plan Task 17 S5 specifies `netguard.check_scan_url` (refuse numeric host encodings,
IPv4-mapped-IPv6, metadata IPs, DNS resolving to link-local, loopback outside local) plus launching
Chromium with `--host-resolver-rules` that resolve **only** registered hosts. On branch `fsec`
**none of it exists**: `grep` for `check_scan_url|chromium_resolver_rules|_forbidden_ip|_NUMERIC_HOST`
across `dps/` returns nothing, `dps/layers/scanner/` is absent, and `tests/security/` does not
exist. The scanner (M4) merges separately; as written in the pre-merge plan, when it lands `POST
/scans` would accept a scan URL whose host is `http://0x7f000001/`, `http://[::ffff:169.254.169.254]/`,
or a name that DNS-resolves to `169.254.169.254`, and Chromium would resolve arbitrary names itself
— caller-controlled SSRF to cloud metadata / internal services from the browser worker.

**Exploit / failure scenario.** Post-M4-merge, a tenant submits a scan of
`https://rebind.attacker.test/` that resolves to `169.254.169.254` (or supplies the numeric literal
directly); the browser worker fetches cloud-metadata / internal endpoints. This is the canonical L3
SSRF the plan calls out and the whole reason `netguard` Part 2 was specified.

**Fix.** Implement Task 17 S5 exactly as the plan writes it (the `check_scan_url` + resolver-rules
code block in `docs/superpowers/plans/2026-10-11-m5-hardening-and-showcase.md` §Task 17) and add
`tests/security/test_netguard.py` (the plan's parametrised allowed/refused table). Must be a
blocking gate of the M4 pre-merge, not deferred past it. Finding A should reuse the same
`_forbidden_ip` core so connections and scans share one guard.

## Finding C — LOW — Task 17 **S13** log-scan regression test missing for the egress modules
**Files:** `tests/test_no_values_in_logs_m5.py` (to be created).

**Claim.** The M5 value-free log-scan test specified by S13 — which must exercise, among others,
the tokenizer (`external.py`), `netguard`, and the LLM gateway/egress — does not exist; only
`test_no_values_in_logs_m1/m2/m3.py` are present. By reading, the in-lens egress modules log only
codes/status/host/attempt/exception-class (tokenizer: `logger.warning("tokenizer_unavailable",
status=…, attempt=…)`; gateway: provider/host/status/type; netguard raises value-free), so this is
a **missing regression test**, not a confirmed leak. Without it a future edit to those modules
could start logging a value undetected.

**Fix.** Add `tests/test_no_values_in_logs_m5.py` per the plan (configure the real JSON logging
pipeline, drive each M5 module with sentinel values, assert the sentinels never appear in captured
stdout/stderr), including the tokenizer and netguard cases.

## Finding D — LOW — Task 17 **S11** `Cache-Control: no-store` not set on `/api/` responses
**File:** `dps/app.py` (`_SecurityHeaders`, ~line 85).

**Claim.** `_SecurityHeaders` sets `X-Content-Type-Options`, `X-Frame-Options`, `Referrer-Policy`
and (outside local) HSTS, but **not** `Cache-Control`. S11 requires `no-store` on every `/api/`
response because some responses carry key material (returned DEKs, admin key-once, token-wrap
blobs); an intermediary/proxy could cache them. Borderline to this lens (it concerns data leaving
DPS in responses rather than an SSRF target), reported because it is an unaddressed Task 17 item.

**Fix.** In `send_with_headers`, when the request path starts with `/api/`,
`headers.setdefault("Cache-Control", "no-store")`; add the `test_headers.py` assertions from the
plan (`/api/v1/policies/presets` has `no-store`, `/health` does not).

---

## Task 17 S1–S13 status within this lens (for the F1 security-review doc)
| # | In lens? | Status on `fsec` |
|---|---|---|
| S1 422 scrub | adjacent | **addressed** — `dps/app.py:_validation_error` returns only `type/loc/msg` |
| S5 SSRF scanner | **yes** | **UNADDRESSED** → Finding B (+ connection twin, Finding A) |
| S11 no-store | borderline | **UNADDRESSED** → Finding D |
| S12 CORS `*` / docs | adjacent | CORS `*` refused by `dps/posture.py:_m5_problems`; docs exposure = another lens |
| S13 value-free logs (egress modules) | **yes** | module code is value-free; **test missing** → Finding C |
| S2 S3 S4 S6 S7 S8 S9 S10 | no (other lenses) | not assessed here |

## Commands to reproduce
```
cd /Users/neelu/document_intelligence/dps-wt-fsec
./lane.sh python -I /Users/neelu/.cache/dps-build/reports/S3-ssrf-egress/probe_endpoint_ssrf.py
grep -rn 'check_scan_url\|_forbidden_ip\|chromium_resolver_rules' dps   # → no matches (S5 absent)
ls tests/security tests/test_no_values_in_logs_m5.py                    # → absent (S5/S13 tests)
```
