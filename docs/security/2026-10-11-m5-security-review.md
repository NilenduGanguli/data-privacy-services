# DPS M5 security review (Task 17) — findings, fixes and guard tests

**Branch:** `fsec` (from main `64c46e3`: M0–M3 + M5; M4 merges later) · **Date:** 2026-10-14 ·
**Plan:** `docs/superpowers/plans/2026-10-11-m5-hardening-and-showcase.md` Task 17, programme plan §2.6 F1.

## 1. Scope and method

The five areas the milestone names — **auth and tenancy**, **injection** (selections in three
engines, MongoDB filters, policy regexes, CSV, PDF, HTML), **SSRF and egress** (scanner, tokenizer,
OCR, Stellar/COIN, connection endpoints), **secrets and logs**, **crypto and key custody** — each
reviewed by its own finder (code walk of every route group and egress sink, plus probes against this
branch's own compose stack, values always fake, outputs counts and booleans only), then every finding
put to an independent adversarial refutation (30 verifiers). This document lists the plan's
S1–S13 items, every **confirmed** finding with its fix and the test that fails without it, and the
candidates that were **refuted**. Probes and raw reports: `~/.cache/dps-build/reports/S3-*`.

`bandit` is not installed in the lane environment; its checks are covered by
`tests/security/test_secrets_hygiene.py` (no `pickle`, `eval`/`exec`, unsafe YAML,
`allow_pickle=True`, `verify=False` outside the documented COIN mint; no private keys, cloud
credentials or raw DPS keys tracked; no `.env` tracked; no `dangerouslySetInnerHTML` in the console).

## 2. The plan's items S1–S13

| # | Area | Finding (as designed before M5) | Fix | Test | Status |
|---|---|---|---|---|---|
| S1 | logs/errors | 422 bodies echoed input; pydantic's `union_tag_invalid` and value-quoting validators still echoed it after M0's handler (finder F5: an SSN as `pk_strategy.kind`, as `supported_entity`, as a method) | `dps/layers/api/validation.py`: `{"detail": [{"loc","msg","type"}], "code": "invalid_request"}`; quoting messages replaced (`dps.errors.safe_error_items`); non-identifier `loc` parts shown as `<key>`; the engine's 400 `invalid policy`, the policy store's `invalid_policy`, the recognizer test and connection validation use the same scrubbing; `custom.py`, `policy.py`, `jsonpath.py` validators name the rule, never the value | `tests/security/test_validation_errors.py` | fixed + test |
| S2 | secrets | `repr(Settings)` printed 12 secrets; `ApplyOptions`/`TransformContext` printed the SEC-4 binding key and the token cache | every secret setting `Field(default="", repr=False)`; `binding_keys`, `binding`, `external_cache`, `external` `field(repr=False)` | `tests/security/test_secrets_hygiene.py::test_settings_repr_hides_secrets`, `::test_engine_dataclasses_hide_binding_keys_and_token_maps` | fixed + test |
| S3 | injection (policy) | policy-supplied regexes run with no ReDoS guard: `(a+)+$` (6 characters) pinned a CPU on ~40 characters, on every request using a stored policy | `dps/engine/detect/regex_guard.py::check_pattern` (length ≤ 512, no backreferences/conditionals, bounds ≤ 1000, no unbounded repeat inside a repeat bounded above 3, no empty alternative inside such a repeat) run by `PatternSpecModel` (so `/policies/test-recognizer`, drafts and inline policies); stacked `*` in declared-field globs collapse | `tests/security/test_regex_guard.py` | fixed + test |
| S4 | injection (selections) | `validate_selection` / the MongoDB allow-list are the only barrier before SQL/BSON | none needed: the adversarial corpus is refused on both dialects; comments are stripped (M2 design), never reach the statement | `tests/security/test_selection_injection.py` | accepted (control holds) + test |
| S5 | SSRF (scanner) | no `check_scan_url`: numeric encodings, IPv4-mapped IPv6, metadata IPs and DNS that resolves to link-local were not refused; Chromium resolved any name | `dps.netguard.check_scan_url`, `forbidden_ip_reason` (IPv4-mapped/NAT64/6to4 unwrapped), `check_resolved_host`, `chromium_resolver_rules`; wiring into `POST /scans` and the crawler's launch flags lands **with the M4 merge** (no scanner on this branch yet) | `tests/security/test_netguard.py` | functions + test; wiring at M4 merge |
| S6 | auth | a route added without `require_scope` would be public | `require_scope`/`require_any_scope` tag their dependency (`__dps_scope__`); the walk uses `iter_route_contexts` (FastAPI 0.143 nests included routers, so the plan's `app.routes` walk saw 2 routes) | `tests/security/test_route_auth.py` | fixed + test |
| S7 | tenancy | object routes could leak existence across tenants | none needed: RLS + `tenant_of` hold (finder: 27 object routes 404) | `tests/security/test_cross_tenant.py` (`db`) | accepted (control holds) + test |
| S8 | key admin | a tenant admin could mint broader keys | `check_grant` (Task 9) | `tests/test_auth_db.py::test_no_privilege_escalation` | fixed (Task 9) |
| S9 | exports | audit CSV cells starting `= + - @` run in spreadsheets | `csv_cell` (Task 11); the L2 `files` CSV export lacked it — finding F-07 | `tests/test_admin_api.py::test_audit_csv_is_streamed_and_formula_safe` | fixed (Task 11); F-07 see §3 |
| S10 | PDF | Paragraph mini-markup from pack strings | `pdf_text` escaping (Task 14) | `tests/test_evidence_pdf.py::test_markup_is_escaped_and_unicode_folded` | fixed (Task 14) |
| S11 | caching | `/api/` answers carrying a DEK, a seed, revealed originals or tenant data had no `Cache-Control` | `_SecurityHeaders`: `Cache-Control: no-store` + `Pragma: no-cache` on every `/api/` response that sets no caching of its own | `tests/security/test_headers.py` | fixed + test |
| S12 | exposure | `/docs` and OpenAPI public in prod; CORS `*` | Task 16 | `tests/test_posture_m5.py::test_docs_and_cors_follow_the_environment` | fixed (Task 16) |
| S13 | logs | M5 modules must stay value-free; third-party loggers inherited `DPS_LOG_LEVEL=DEBUG` (Presidio logged the words around each hit — finding F-09) | root logger never below WARNING, NER loggers capped (`dps/observability.py`); allow-listed `rekey` log line; value-free `rekey` configuration error; policy-store errors scrubbed | `tests/test_no_values_in_logs_m5.py`, `tests/test_observability.py::test_third_party_loggers_are_capped` | fixed + test |

## 3. Confirmed findings (finder panel, each survived adversarial refutation)

Severity as confirmed. "Test" fails without the fix (the finders' probes showed each failure live).

| Id | Sev | Finding | Fix | Test | Status |
|---|---|---|---|---|---|
| F-01 | HIGH | Production-write admin gate (D3) bypassable: a `jobs` key cloned the production connection without its tag (same `secret_ref`) and rewrote production in place (40/40 rows) | see §3.1 | `tests/api/test_production_resource_gate.py` | fixed + test |
| F-02 | HIGH | ReDoS via policy-supplied recognizer regex | S3 | `tests/security/test_regex_guard.py` | fixed |
| F-03 | HIGH | S5 scanner SSRF guard missing | S5 (wiring at the M4 merge: a blocking item of that merge) | `tests/security/test_netguard.py` | functions fixed; wiring pending M4 |
| F-04 | HIGH | With NER on, a date of birth was downgraded to `DATE` (`keep`) and returned in clear | see §3.4 | `tests/test_ner_dob_precedence.py` | fixed + test |
| F-05 | MED | S11 no-store (reported by four finders) | S11 | `tests/security/test_headers.py` | fixed |
| F-06 | MED | RLS posture blind to `owner_directory`'s role: with the migration login unset, the API role read every tenant's jobs/connections/policies | see §3.6 | `tests/test_rls_posture_unit.py::test_a_policy_open_to_the_runtime_role_is_named`, `tests/db/test_rls.py::test_migrating_as_the_runtime_role_is_refused_in_every_environment` | fixed + test |
| F-07 | MED | CSV formula injection in the L2 `files` export | see §3.7 | `tests/connectors/test_files.py::test_csv_cells_a_spreadsheet_would_evaluate_are_neutralised` | fixed + test |
| F-08 | MED | No SSRF guard on tenant-controlled connection endpoints (S3 `endpoint_url`, DB hosts) | see §3.8 | `tests/security/test_connection_ssrf.py`, `tests/api/test_connection_ssrf_api.py` | fixed + test |
| F-09 | MED | Presidio wrote customer text to the logs at `DEBUG` | S13 | `tests/test_observability.py::test_third_party_loggers_are_capped`, `tests/test_no_values_in_logs_m5.py` | fixed |
| F-10 | MED | Caller-chosen custom entity names: unbounded `/metrics` label values (readable without a key) and process-global taxonomy entries | see §3.10 | — | pending |
| F-11 | MED | Tokenization oracle under DPS's bank credentials (any profile string, any caller with `obfuscate`) | see §3.11 | — | pending |
| F-12 | MED | Synthesize mode emitted the bank's real tokens as "synthetic" values | see §3.12 | — | pending |
| F-13 | MED | Retry and job-group admission bound only the public `kid`, not the DEK | see §3.13 | — | pending |
| F-14 | MED | The once-only job/group key was lost when the answer failed after commit (strict audit refusal) | see §3.14 | — | pending |
| F-15 | LOW | S13 log-scan test missing | S13 | `tests/test_no_values_in_logs_m5.py` | fixed |
| F-16 | LOW | S2 secret reprs | S2 | `tests/security/test_secrets_hygiene.py` | fixed |
| F-17 | LOW | S1 residual echo in 422/400 bodies | S1 | `tests/security/test_validation_errors.py` | fixed |
| F-18 | LOW | Inline-policy deny/allow-list terms stored in clear in `dps_job.spec` and listed by `GET /jobs` | see §3.18 | — | pending |
| F-19 | LOW | `external_tokenize` token cache ignored the profile | see §3.19 | — | pending |
| F-20 | LOW | Synthesis seeds never wrapped although a seed de-pseudonymises D-A keys | see §3.20 | — | pending |
| F-21 | LOW | KEK rotation: sealed blobs opened with the current KEK only | see §3.21 | — | pending |
| F-22 | LOW | SEC-4 bind tag not injective over (entity type, profile) | see §3.22 | — | pending |

### 3.1 F-01 — D3 bound to the production credential

A `secret_ref` held by a `production`-tagged connection is a **production credential**
(`dps.connections.production_refs`). Without `admin`: creating a connection that names it, or
patching another connection onto it, is 403 `admin_scope_required`; `validate_run` treats an
`in_place`/`append` through **any** connection holding it like the tagged one (403
`admin_scope_required`, also on `POST /jobs/run/validate`); and `POST /jobs/{id}/retry` of an
`in_place`/`append` job re-checks the rule (`targets.check_retry_scope`) — a retry restarts the
writes, and before this a `jobs` key could restart an admin-approved production write. The
connection's `secret_ref` name stays visible to `jobs` keys (it is a name, not a secret). Inline
credentials need the password itself; the endpoint alone was not made a production marker (UAT and
production schemas can share a database). Test: `tests/api/test_production_resource_gate.py`
(the clone, the patch, an admin-made untagged holder, the retry rule).

### 3.4 F-04 — a stronger detector never weakens protection

`patterns.resolve_overlaps` first drops every NER hit (`ner:*`) that overlaps a typed hit (pattern,
checksum, name cue, custom recognizer) of a **higher sensitivity tier**, then ranks the rest as
before (validated, score, length). Presidio's `DATE_TIME` → `DATE` (LOW, kept) no longer beats the
pattern tier's `DATE_OF_BIRTH` (MEDIUM). Proof with the real Presidio model (the finder's p06, re-run
after the fix): the birth date is transformed in all four phrasings with NER on and off
(`dob_in_clear=False` 8/8). Test: a Presidio-like adapter (higher score, longer span) through the
whole engine, with a source-vs-output check.

### 3.6 F-06 — no tenant policy may apply to the runtime login

`assert_rls_posture` now lists every policy besides `tenant_isolation` on a tenant table that
applies to the runtime login — `PUBLIC`, the login itself or a role it belongs to (`pg_policies.roles`
joined with `pg_has_role`) — as `<table>: policy <name> applies to the runtime role …`
(`db.CROSS_TENANT_PROBLEM`). That is what `owner_directory` (`USING (true)`, created `TO
CURRENT_USER`) becomes when migrations run as the API login (`DPS_PG_MIGRATION_USER` unset). The
app refuses to boot on such a problem in every environment, local included (other posture problems
remain fatal in staging/prod only). HANDOFF documents the rule.

### 3.7 F-07 — formula guard on the CSV export

`files.py::_csv_cell` writes a text cell with a leading apostrophe when a spreadsheet would
evaluate it: it starts with `=`, `@`, TAB or CR, or with `+`/`-` followed by anything but a number or
phone shape (`+1 (555) 010-0100` and `-12.50` stay as they are, so exported phone numbers are not
mangled; `+cmd|…` and `-2+3+cmd|…` are guarded). Numbers, dates, booleans and bytes are rendered in
forms no spreadsheet evaluates and are never prefixed. JSONL and Parquet are unchanged (not
spreadsheet-evaluated).

### 3.8 F-08 — the SSRF guard on connection endpoints

`netguard.check_connection_endpoint` shares the scanner guard's core (`forbidden_ip_reason`,
`check_resolved_host`): every host a connection dials — `params.host`, an s3 `endpoint_url`'s host,
the MongoDB `uri` and Oracle `dsn` hosts of its credentials — must not be a numeric encoding nor be or
resolve to link-local/metadata, multicast, unspecified, reserved or (outside local) loopback
addresses. It runs at `POST /connections` and `PATCH` (DNS off the event loop) and again at use
(`open_connector`, `open_target_store`), so a row stored earlier or a DNS change is caught before any
driver dials; `POST /connections/{id}/test` answers the refusal as 422 instead of probing. RFC 1918
stays allowed. Residual: the driver resolves the name again (rebinding window) — the egress
NetworkPolicy of the OpenShift manifests is the network-level backstop; SRV targets of a
`mongodb+srv://` URI are not resolved by the guard.

## 4. Considered and refuted

- **`pvc` file-export targets not tenant-namespaced under `DPS_EXPORT_ROOT`** — every write key carries
  a unique per-job (ULID) or per-connection segment and `check_key` refuses `..`, so tenants never
  overwrite each other; file stores are target-only and no route reads them back (artefact reads are
  RLS-scoped and tenant-keyed). Reading another tenant's export needs the shared volume itself, which
  only the operator or the downstream consumer holds. Defence-in-depth note only.
- **ReDoS via a declared-field path glob** — reproduces, but it is a weaker special case of the
  recognizer-regex ReDoS (same principal, any length), fixed by S3; adjacent `*` now collapse anyway.
- **Unbounded key custody for paused or stalled run jobs** — contract §6.1 defines custody as "the
  job's lifetime", paused/waiting are live states, resume needs the key, and the owner ends custody by
  canceling (which purges it). A maximum pause duration would be a policy, not a D2 fix.

## 5. Residual risks

- Python `re` has no timeout: the static guard is heuristic (other ambiguous alternations can still
  backtrack); input length caps (`max_text_chars`, document limits) bound the work.
- Private (RFC 1918) targets are allowed by design for internal UAT sites and databases.
- DNS can change between DPS's check and the client's own resolution (rebinding window); the
  OpenShift egress NetworkPolicy (Task 25) is the network-level backstop.
- Failed authentications have no tenant and are not audited; they are counted in
  `dps_requests_total{outcome="rejected"}`. DB keys propagate to other replicas within
  `DPS_KEY_CACHE_SECONDS`.

## 6. Re-running the review

```bash
./lane.sh pytest -q tests/security tests/test_no_values_in_logs_m5.py tests/test_contract_surface.py
DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -m db tests/security/test_cross_tenant.py \
  tests/api/test_production_resource_gate.py
./lane.sh python /Users/neelu/.cache/dps-build/reports/S3-<lens>/<probe>.py   # the finders' probes
```
