# DPS M5 security review (Task 17) — findings, fixes and guard tests

**Branch:** `fsec` (from main `64c46e3`: M0–M3 + M5; M4 merged into `fsec` on 2026-10-15, §3.24) · **Date:** 2026-10-14
(the scanner's SSRF guard wired, T3 labelling on discovery's and the scanner's paths and the D4 re-check: 2026-10-15, §3.25–§3.27) ·
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
| S1 | logs/errors | 422 bodies echoed input; pydantic's `union_tag_invalid` and value-quoting validators still echoed it after M0's handler (finder F5: an SSN as `pk_strategy.kind`, as `supported_entity`, as a method) | `dps/layers/api/validation.py`: `{"detail": [{"loc","msg","type"}], "code": "invalid_request"}`; quoting messages replaced (`dps.errors.safe_error_items`); non-identifier `loc` parts shown as `<key>`; the engine's 400 `invalid policy`, the policy store's `invalid_policy`, the recognizer test and connection validation use the same scrubbing; `custom.py`, `policy.py`, `jsonpath.py` validators name the rule, never the value | `tests/security/test_validation_errors.py`; scans: `tests/security/test_scanner_paths.py` (§3.24) | fixed + test |
| S2 | secrets | `repr(Settings)` printed 12 secrets; `ApplyOptions`/`TransformContext` printed the SEC-4 binding key and the token cache | every secret setting `Field(default="", repr=False)`; `binding_keys`, `binding`, `external_cache`, `external` `field(repr=False)` | `tests/security/test_secrets_hygiene.py::test_settings_repr_hides_secrets`, `::test_engine_dataclasses_hide_binding_keys_and_token_maps` | fixed + test |
| S3 | injection (policy) | policy-supplied regexes run with no ReDoS guard: `(a+)+$` (6 characters) pinned a CPU on ~40 characters, on every request using a stored policy | two layers (§3.2): the static first filter `regex_guard.check_pattern` (length ≤ 512, no backreferences/conditionals, bounds ≤ 1000, no unbounded repeat inside a repeat bounded above 3, no empty alternative inside such a repeat) run by `PatternSpecModel` (so `/policies/test-recognizer`, drafts and inline policies), and — review rounds 1 and 2 — the bound: patterns and deny lists run on the `regex` module, each run charged its own CPU (5 ms in the calling thread holding the GIL, then a matcher process), under a per-call allowance of 10 ms + 20 µs per run + 0.5 µs per character per pattern, capped at `DPS_PATTERN_CPU_SECONDS` per request and per NDJSON item (422 `custom_pattern_too_slow` past it); deny lists are matched as a trie; declared-field globs are matched without a regex (`custom.PathGlob`), linear in the path | `tests/security/test_regex_guard.py`, `test_pattern_budget_load.py`, `test_pattern_matcher.py` | fixed + test (rounds 1–2) |
| S4 | injection (selections) | `validate_selection` / the MongoDB allow-list are the only barrier before SQL/BSON | none needed: the adversarial corpus is refused on both dialects; comments are stripped (M2 design), never reach the statement | `tests/security/test_selection_injection.py` | accepted (control holds) + test |
| S5 | SSRF (scanner) | no `check_scan_url`: numeric encodings, IPv4-mapped IPv6, metadata IPs and DNS that resolves to link-local were not refused; Chromium resolved any name | `dps.netguard.check_scan_url`, `forbidden_ip_reason` (IPv4-mapped/NAT64/6to4 unwrapped; loopback decided before "reserved", so `::1` follows the environment), `check_resolved_host`, `chromium_resolver_rules`; wired (§3.25): `POST /scans` and the browser-worker run `check_scan_url`, the scope proxy resolves and checks every hop's addresses and dials them itself, Chromium resolves nothing but the proxy's address | `tests/security/test_netguard.py`, `test_scan_ssrf.py`, `tests/scanner/test_scan_ssrf_browser.py` | fixed + test (wired 2026-10-15) |
| S6 | auth | a route added without `require_scope` would be public | `require_scope`/`require_any_scope` tag their dependency (`__dps_scope__`); the walk uses `iter_route_contexts` (FastAPI 0.143 nests included routers, so the plan's `app.routes` walk saw 2 routes) | `tests/security/test_route_auth.py` | fixed + test |
| S7 | tenancy | object routes could leak existence across tenants | none needed: RLS + `tenant_of` hold (finder: 27 object routes 404; the scan routes too after the M4 merge, §3.24) | `tests/security/test_cross_tenant.py` (`db`) | accepted (control holds) + test |
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
| F-01 | HIGH | Production-write admin gate (D3) bypassable: a `jobs` key cloned the production connection without its tag (same `secret_ref`) and rewrote production in place (40/40 rows) | see §3.1 (completed in review round 1, B2) | `tests/api/test_production_resource_gate.py` | fixed + test |
| F-02 | HIGH | ReDoS via policy-supplied recognizer regex (and, review round 1 B1, via stacked repeats and separated-star declared-field globs; round 2: the bound refused legitimate scans under load and allowed ~250× the legitimate work) | S3, §3.2 | `tests/security/test_regex_guard.py`, `test_pattern_budget_load.py`, `test_pattern_matcher.py` | fixed + test (rounds 1–2) |
| F-03 | HIGH | S5 scanner SSRF guard missing | S5, wired on the scanner's paths after the M4 merge: see §3.25 | `tests/security/test_netguard.py`, `test_scan_ssrf.py`, `tests/scanner/test_scan_ssrf_browser.py` | fixed + test |
| F-04 | HIGH | With NER on, a date of birth was downgraded to `DATE` (`keep`) and returned in clear | see §3.4 | `tests/test_ner_dob_precedence.py` | fixed + test |
| F-05 | MED | S11 no-store (reported by four finders) | S11 | `tests/security/test_headers.py` | fixed |
| F-06 | MED | RLS posture blind to `owner_directory`'s role: with the migration login unset, the API role read every tenant's jobs/connections/policies | see §3.6 | `tests/test_rls_posture_unit.py::test_a_policy_open_to_the_runtime_role_is_named`, `tests/db/test_rls.py::test_migrating_as_the_runtime_role_is_refused_in_every_environment` | fixed + test |
| F-07 | MED | CSV formula injection in the L2 `files` export | see §3.7 | `tests/connectors/test_files.py::test_csv_cells_a_spreadsheet_would_evaluate_are_neutralised` | fixed + test |
| F-08 | MED | No SSRF guard on tenant-controlled connection endpoints (S3 `endpoint_url`, DB hosts) | see §3.8 | `tests/security/test_connection_ssrf.py`, `tests/api/test_connection_ssrf_api.py` | fixed + test |
| F-09 | MED | Presidio wrote customer text to the logs at `DEBUG` | S13 | `tests/test_observability.py::test_third_party_loggers_are_capped`, `tests/test_no_values_in_logs_m5.py` | fixed |
| F-10 | MED | Caller-chosen custom entity names: unbounded `/metrics` label values (readable without a key) and process-global taxonomy entries | see §3.10 (registry per call since review round 1, B3) | `tests/security/test_custom_entity_bounds.py` | fixed + test |
| F-11 | MED | Tokenization oracle under DPS's bank credentials (any profile string, any caller with `obfuscate`) | see §3.11 | `tests/security/test_tokenizer_profiles.py` | fixed + test |
| F-12 | MED | Synthesize mode emitted the bank's real tokens as "synthetic" values | see §3.12 | `tests/security/test_synthesize_keyless.py` | fixed + test |
| F-13 | MED | Retry and job-group admission bound only the public `kid`, not the DEK | see §3.13 | `tests/security/test_key_binding.py` | fixed + test |
| F-14 | MED | The once-only job/group key was lost when the answer failed after commit (strict audit refusal) | see §3.14 | `tests/api/test_once_only_key_custody.py` | fixed + test |
| F-15 | LOW | S13 log-scan test missing | S13 | `tests/test_no_values_in_logs_m5.py` | fixed |
| F-16 | LOW | S2 secret reprs | S2 | `tests/security/test_secrets_hygiene.py` | fixed |
| F-17 | LOW | S1 residual echo in 422/400 bodies | S1 | `tests/security/test_validation_errors.py` | fixed |
| F-18 | LOW | Inline-policy deny/allow-list terms stored in clear in `dps_job.spec` and listed by `GET /jobs` | see §3.18 | `tests/api/test_inline_policy_sealed.py` | fixed + test |
| F-19 | LOW | `external_tokenize` token cache ignored the profile | see §3.19 | `tests/security/test_external_profile_cache.py` | fixed + test |
| F-20 | LOW | Synthesis seeds never wrapped although a seed de-pseudonymises D-A keys | see §3.20 | `tests/security/test_seed_wrap.py` | fixed + test |
| F-21 | LOW | KEK rotation: sealed blobs opened with the current KEK only | see §3.21 | `tests/security/test_kek_rotation_window.py` | fixed + test |
| F-22 | LOW | SEC-4 bind tag not injective over (entity type, profile) | see §3.22 | `tests/security/test_binding_tag_v2.py` | fixed + test |

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
production schemas can share a database).

**Review round 1 (B2).** The first fix gated only a patch that *switched to* a production
credential, and SEC-3 accepts the same `secret_ref` restated as "credentials". So a `jobs` key
re-pointed an untagged connection that already held the production credential (`PATCH {params:
{host, port}, secret_ref: <the same ref>}` → 200), and the next `/test` sent the production login
and password to a fake PostgreSQL server of its choice; it could also delete that connection (204).
Such a holder exists when an admin made it, or when a `jobs` key made it before the production
connection was tagged. Rule as built: a connection that **holds** a production credential
(`connections.holds_production_credentials`) is treated like the tagged one — **any** `PATCH`
(endpoint, secret, roles, name, tags) and any `DELETE` needs `admin` (403
`admin_scope_required`), checked under the row lock (and, for an endpoint change, before the TLS and
SSRF checks). Test: `tests/api/test_production_resource_gate.py` (the clone, the patch, an
admin-made untagged holder, the retry rule, and the reviewer's re-point/delete probe in both
cases: every patch and the delete are 403, nothing changes, and a counting listener at the
requested endpoint receives no connection).

### 3.2 F-02 / S3 — policy patterns: a bounded allowance, charged on each run's own CPU (review rounds 1 and 2)

The first fix was the static filter alone. It refuses only nested repeats, so stacked repeats over
the same characters still backtracked polynomially (`\w*\w*\w*\w*\w*!` and `.*a.*a.*a.*b` > 20 s on
500 characters; through `/policies/test-recognizer`, `/detect` and `/obfuscate`, no answer within
45 s on 400 characters), and a declared-field glob with separated stars (`$.*a*a*a*a*a*a*a*a*a*b`,
compiled to a regex the filter never saw) took > 20 s on a 63-character name. Round 1 bounded each
call with the `regex` module's matching `timeout`; review round 2 found that this bound used the
wrong clock (R2-B1) and was far too generous (R2-B2):

- R2-B1: the `regex` timeout counts the CPU of the whole **process**, and a matcher that releases
  the GIL (`concurrent=True`) is starved by any thread running Python. Legitimate scans were
  refused whenever other requests were busy: 4/4, 8/8 and 12/12 concurrent 1000-name deny-list
  scans of 400 KB, every 10–50 ms scan beside one busy Python thread, and on a real uvicorn server 2
  of 3 deny-list `/detect` requests while four ordinary requests ran.
- R2-B2: 1 s per call plus 1 µs per character per pattern, accrued over a whole NDJSON stream. A
  1 MB `/detect` with 1000 patterns, one of them stacked, ran 1001.5 CPU-seconds before its 422
  (the same request with linear patterns: 3.97 s); every ~2 KB request with one stacked pattern
  burned about 1 s.

Fix as built (round 2):

- **Each run is charged its own CPU, and stopped on it alone.** A run starts in the calling thread
  **holding the GIL** (`concurrent=False`) for at most 5 ms (`regex_guard.SLICE_SECONDS`): no other
  Python thread runs meanwhile, so the process clock of the timeout is the run's own, and no run
  holds the GIL longer than that (the event loop keeps its turns). A run that needs longer
  continues in a **matcher process** (`dps.engine.detect.pattern_process`, child
  `pattern_child.py`): a pool of
  `DPS_PATTERN_PROCESSES` (default 2) single-threaded children started on first use
  (`python -I`, an empty environment, no output), one run at a time, where the `regex` timeout is
  exactly that run's CPU. A pattern whose last such run predicts it would outgrow the slice again
  starts there directly. So a refusal depends on the run's own work only, never on other requests;
  long pattern runs occupy at most `DPS_PATTERN_PROCESSES` CPUs per server process however many
  arrive (others wait for a free child); a child that dies or hangs is replaced and its run refused
  (fail closed).
- **A small allowance with a ceiling.** Every call — an API request (`_CallScope`), each NDJSON item,
  a worker handler task — starts with 10 ms and earns, for every pattern it runs on a text, 20 µs +
  0.5 µs per character (`regex_guard.BUDGET_*`). Measured legitimate cost (the build Mac): 1.2–2.3 µs
  per run on a short text; 0.002–0.1 µs per character for realistic bank patterns (the
  case-insensitive e-mail class is the dearest); about 0.03 µs per character for a 1000-name deny
  list. An API request and each NDJSON item may spend at most `DPS_PATTERN_CPU_SECONDS` (default
  5 s) in total; an NDJSON item spends only what it earned itself plus the stream's 10 ms base, never
  what earlier items earned (`PatternBudget.for_stream` / `item`); no single run anywhere — L2 jobs
  included — takes longer than `DPS_PATTERN_CPU_SECONDS`. A pattern past what is left is stopped
  and the call refused: 422 `custom_pattern_too_slow` on every engine route, NDJSON item, document
  and the recognizer test; an L2 job (or partition) fails with it, without retries (now truly
  deterministic: the charge is the run's own CPU); a post-write sample that could not run makes the
  verification `review_required` — fail closed, as the identifier check already did. Nothing is
  skipped silently.
- **Deny lists as a trie.** A deny list is one pattern shaped as a trie of its terms
  (`custom.deny_list_pattern`, compiled per term set once): every edge is the exact set of text
  characters some terms accept at that place — as the matcher's own case-insensitive comparison
  decides, which is not transitive for the Turkish i's (`I`~`i`, `I`~`ı`, `i`~`İ`, never `ı`~`i`) —
  and edges never share a character, so the matcher follows one path per position: 0.034 µs per
  character for 1000 names against 0.68 µs for the flat alternation, with the flat alternation's
  exact spans (a differential test).
- **The static filter stays** as the first layer: it refuses the classic shapes when a policy is
  validated, with a clear message (and also a pattern the `regex` module cannot compile, and —
  round 2, N-r2-1 — an unescaped `{` that is not a repeat count or an unescaped `[` inside a set,
  which `regex` would read as a fuzzy match or a POSIX class).
- **Globs without a regex.** `custom.PathGlob` cuts the glob and the path at `.`, `[` and `]` and
  matches each name by finding its literal pieces in order (leftmost placement is optimal for `*`):
  linear in the path whatever the stars. A differential test checks it against the old regex
  semantics on 6000 random globs and paths.

Proof:
- `tests/security/test_pattern_budget_load.py`: eight concurrent 400 KB deny-list scans beside a
  spinning Python thread are all answered; through the app, six deny-list requests beside four
  ordinary 1 MB requests and a spinning thread all answer 200 and the attack still answers 422; a
  1000-pattern policy on 50 KB answers 200 when linear and is refused at the ceiling (2 s in the
  test) when one pattern is stacked; an NDJSON attack item after 120 benign items is refused on its
  own allowance. On round 1's code four of these five fail (refusals; 51 s; the attack item ran
  11 s and passed) — `~/.cache/dps-build/reports/S3-fix-r2/failfirst-base.log`.
- `tests/security/test_pattern_matcher.py`: a matcher process gives `re`'s spans (astral characters
  and lone surrogates included), runs that leave the thread keep their spans, a run is charged its
  own CPU beside a busy thread, the run cap holds without a ceiling, a matcher killed mid-run
  refuses its run and is replaced, an item spends only its own earnings, routing, and the deny-list
  trie (differential, deep lists, cost).
- `tests/security/test_regex_guard.py` (round 1): the stacked patterns, the separated-star glob and
  the reviewer's route probe, in a child process with a hard time limit.
- Probes (`~/.cache/dps-build/reports/S3-fix-r2/probes/`, the reviewer's round-2 probes re-run):
  0 refusals for 1, 4, 8 and 12 concurrent deny-list scans (round 1: all refused); 3/3 scans beside
  a busy Python thread answered (round 1: 3/3 refused); on a real uvicorn server every deny-list
  request answers 200 with 1, 2 or 4 ordinary requests in flight (round 1: 2 of 3 refused with 4);
  `/detect` attacks are refused after 0.14 s (1 pattern, 100 KB; round 1: 1.2 s), 0.56 s (100 ×
  10 KB; 2.1 s) and at the 5 s ceiling for 1000 patterns on 10 KB, 50 KB and 1 MB (round 1: 11.6 s,
  51.7 s and 1001.5 CPU-seconds), while the linear twins answer 200 (1000 patterns on 1 MB: 3.9 s;
  round 1: 4.0 s). With the lane's Linux image (Python 3.12, uid 10001, read-only root, no
  network) running this branch's code, a matcher process returns `re`'s spans, stops the attack at
  its 0.3 s limit, and eight concurrent deny-list scans beside a spinning thread all answer.

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

### 3.10 F-10 — bounded labels and a registry per call

`dps_findings_total` labels a custom type `custom` (L1 and L2), so neither a flood of names nor a
tenant's project names reach the keyless `/metrics`. Policies are capped at 50 recognizers (20
patterns, 50 context words, 1000 deny/allow terms each) and 500 declared fields, inline or stored.

**Review round 1 (B3).** The first fix kept the registry process-global, capped at 5000 names and
fail-closed. One tenant's `detect` key then filled it in 10 requests (50 HIGH recognizers and 500
HIGH declared fields each), and every other tenant's next new HIGH custom type answered 400 on
`/detect` and `/obfuscate` until the process restarted — on a fresh replica, an attacker who filled
it first broke other tenants' stored policies. Rule as built: custom tiers belong to the **call**
that declared them. Every API request (the app's `_CallScope` middleware, so every route) and every
worker handler task (`Consumer._guard`) binds its own `taxonomy.CustomEntities`
(`dps.engine.call_scope`), which `register_entity` fills and `entity_info` reads first; it is never
shared with another call, so no tenant can fill a registry another tenant (or another request)
needs, and a tier no longer leaks between calls (a request naming a custom type without a tier
reads the MEDIUM default, whatever earlier requests declared). A detector or batch transformer
built in one call and used in another (a worker caches a job's transformer across its partition
handlers) registers its policy's tiers again in each call that uses it (`custom.CustomTiers`). A
registration still only raises protection (never below what the name already reads as; built-in
names refused), at most 5000 names per registry (a policy declares at most 550). The
process-global registry remains only for code outside a request or a job (tools, tests). Tests:
`tests/security/test_custom_entity_bounds.py`
(the reviewer's two-tenant probe as a test: 12 filling requests all answer 200, nothing reaches the
process registry, and the victim's new HIGH names answer 200 with tier HIGH on `/detect` and
`/obfuscate`; a call's registry is its own and capped; no tier leaks between requests; two worker
handler tasks see only their own tiers and budgets; a reused detector and a cached transformer
register their policy's tiers in each later call).

Residual: `/metrics` stays keyless by design (ops scrape); per-tenant LLM counters carry tenant
ids — restrict the ops port with the OpenShift NetworkPolicy (Task 25).

### 3.11 F-11 — a tenant tokenizes only its allowed data elements

`DPS_TOKENIZER_PROFILES` (JSON, tenant or `*` → `ENTITY:profile` entries) lists what each tenant
may tokenize. Layer 1 (`apply._tokenize_prepass`, every route including NDJSON and documents) and
Layer 2 (`BatchTransformer._pretokenize`, `TokenizerProfileRefused`) check every batch against the
tenant bound to the call (`CURRENT_TENANT`) **before** the first call: 403
`tokenizer_profile_not_allowed` / job code of the same name. Every Layer 1 tokenization batch is
audited before the call (`external.tokenize`, like SEC-4's detokenize audit), with the profile label
when an allow-list is set. Production refuses to boot with a tokenizer and no allow-list; HANDOFF tells
the bank to give DPS its own data elements, so DPS tokens never equal the tokens bank systems store.
Not done (optional in the finding): a low-entropy entity refusal. Residual (review round 1 N2):
within an allowed (entity type, profile), any `obfuscate` caller of that tenant can still tokenize a
whole small domain (every date of birth, say) and so build a codebook that inverts that data
element's tokens — those an L2 job wrote included — without the `reveal` scope. Allow-list only
data elements whose domain is too large to enumerate, or give low-entropy types no profile.

### 3.12 F-12 — synthesize mode is key-less

`SynthesizeConfig` refuses any method outside `KEYLESS_METHODS` (now defined in
`dps.engine.policy`; `dps.engine.batch.KEYLESS_METHODS` is the same set), so a policy that would make
`/synthesize`, NDJSON, `/documents/synthesize`, a stored policy or an L2 free-text span call the bank
tokenizer (or any keyed method) is refused when validated; `build_plan` raises 422
`synthesize_method_invalid` as the backstop for a policy built around validation. The test drives
the three L1 routes with a counting tokenizer: zero calls. M0's planner test used an FPE synthesize
override to exercise the planner's fallback; it now asserts the refusal.

### 3.13 F-13 — retry and group admission bind the DEK

`submit_run` stores the key check value (public: HKDF of the DEK salted by the kid) in the redacted
spec (`consistency.kcv`); `seal_retry_secret` refuses key material whose DEK does not match it (409
`key_material_mismatch`), so a `jobs` caller cannot re-seal its own DEK under the owner's kid.
`create_group` stores the group key's kcv (migration 046) and `_check_group` compares it in constant
time (409 `group_key_mismatch`). Jobs and groups created before keep the kid-only rule. With F-01, a
retry of a production `in_place`/`append` job also needs `admin` again.

### 3.14 F-14 — a failed creation answer leaves no keyed row behind

If the creation audit of `POST /jobs/run` or `POST /job-groups` is refused after the row was committed
(strict sink: 503 `audit_unavailable`), the route removes the row before answering:
`jobs.discard_unannounced` deletes an unclaimed queued job (sealed key and `Idempotency-Key` with
it — a retry with the same key creates a fresh job and returns its key) or cancels a claimed one
(the cancel-finalize purges the key); `groups.discard_group` deletes the new group. A best-effort
`error` audit records the rollback when the sink takes it. Residual: a response lost in transit is
invisible to the server — callers who cannot afford a lost key pass their own key material or seed
(documented in the contract); a two-phase start was not added. Residual (review round 1 N3): if a
worker claims the job between the commit and the refused creation audit, an `in_place` run may
write partitions before the cancel lands; those rows are then under a key nobody holds (a small
window; the same callers' remedy applies).

### 3.18 F-18 — policy bodies sealed in the job row

`redact_spec` replaces a policy body with `policy_label` (`sealed`, `name`, `version`, `sha256`) and
`submit_run`/`submit_discover` seal the body with the selection in `spec_enc` (a discover with an
inline policy now always seals, and needs the KEK). The run and discover workers reopen it; the
policy store's pinned-job counts keep working from the label's name and version.

### 3.19 F-19 — token caches keyed by profile

Every `external_tokenize` cache — L1's tokenize and detokenize pre-passes, the L2 batch pre-pass,
the L2 reveal cache (`external_reveal_cache`) and the method's lookups — is keyed by
`(entity type, profile, value or token)` (`methods.ExternalKey`, `external_key`), so two columns of
one entity type under two data elements are each tokenized, written and revealed under their own
profile.

### 3.20 F-20 — seeds are wrapped like DEKs

`crypto.rsa_oaep_wrap` (now shared by DEKs and seeds) and `surrogates.wrap_synthesis_seed` return
`WrappedSeed` (`wrapped_seed`, `wrap`, `seed_fingerprint`). `/synthesize` validates `wrap_key` before
any work and wraps its seed (JSON, NDJSON header, documents); `POST /jobs/run` wraps a generated
seed; `POST /job-groups` takes `wrap_key` (key and seed) and refuses unknown fields. The unwrapped
seed reproduces the output when presented again.

### 3.21 F-21 — the rotation window works as the contract says

`load_kek` registers `DPS_JOB_KEK_PREVIOUS` as the current KEK's fallback; `open_sealed` tries it
only when the current KEK fails (still bound to the row's AAD; a third key never opens), so workers,
discovery and connection credentials keep working until `dps.rekey` runs, which opens strictly
(`fallback=False`) to tell current blobs from those it must re-seal. Durable idempotency fingerprints
are also compared under the previous KEK, so a pre-rotation `Idempotency-Key` replay is not refused
as reused (end-to-end test on a running app).

### 3.22 F-22 — v2 bind tags bind exactly one (entity type, profile)

New external hints carry `bind_v: 2`: the tag's subkey info is `external_tokenize.v2|` + JSON
`[entity, profile]`, so (`ACCT`, `p|q`) and (`ACCT|p`, `q`) — and `None` vs `""` — get different tags.
Hints without a version are verified as v1 (issued before); an external hint's entity type must be
an identifier (`^[A-Z][A-Z0-9_]{0,63}$`), which also makes v1's split unambiguous except for the
`None`/`""` profile (residual for v1 hints only).

### 3.23 Where each fix landed (branch `fsec`)

| Commit | Content |
|---|---|
| `dc85450` | Task 0: the contract-surface guard |
| `6061767` | Task 17: S1, S2, S3, S5 (functions), S6, S11, S13 and F-09 — the guard tests under `tests/security/` |
| `1fd9730` | S1 test correction (the inline-policy cases reach the value validators) |
| `f112044` | F-01 |
| `54b5bb4` | F-06 |
| `6c4a3ef` | F-07 |
| `514280d` | F-08 |
| `ceff56c` | F-04 |
| `c24c96f` | F-10 and F-12 |
| `e89ece8` | F-11 |
| `6ec3933` | F-19 |
| `4e92dd2` | F-13 (migration 046) |
| `fde5a94` | F-14 |
| `7c3fd5c` | F-18 |
| `da904a9` | F-20 |
| `d2bada1` | F-21 |
| `0c63e01` | F-22 |
| `4d2b234` | this review document (round 0) |
| `be2b326` | review round 1 B2: F-01 completed — holders of a production credential |
| `e7882af` | review round 1 B1 + B3: F-02/S3 per-call pattern budget and the glob matcher; F-10 registry per call |
| `c371d2f` | review round 2 R2-B1 + R2-B2: each pattern run charged its own CPU (in-thread slice, then matcher processes), the capped per-call allowance with per-item NDJSON budgets, deny lists as a trie, the post-write sample failing closed, and N-r2-1's refusals |
| `c1d8c36` | `m4` merged into `fsec` (§3.24): the F-11 posture rule belongs to the serving roles |
| `cf4f66f` | Task 0: M4's modules and scan routes join the contract-surface guard |
| `dae4665` | §3.24: F-02, F-07, F-10, F-14, F-18 and F-21 on the scanner's paths |
| `9bf03d1` | §3.24: S7 walks a scan's routes; the browser-worker posture test names the allow-list |
| `5c27637` | `fllm`'s `0fd76d0` cherry-picked: the egress verifier's pattern scan reads call data only (T3 prompts reach the model under `strict`) |
| `2e41975` | §3.26: Task 8B — T3 labelling in discovery and the scanner (value-free, opt-in) |
| `90ed7df` | §3.25: S5 / F-03 — the scanner's SSRF guard at accept, on the browser-worker and on every hop; Chromium resolves nothing itself |
| `ee31fa4` | §3.26: the discover and scan jobs record `progress.metrics.llm` |

Proof beyond the tests: the finders' probes re-run on the fixed branch
(`~/.cache/dps-build/reports/S3-impl/probes/*.after.out`) — the D3 clone is refused (403, 0 rows
changed), `(a+)+$` is refused before it runs, the oracle makes 0 tokenizer calls under an allow-list,
`/synthesize` never returns a bank token, a forged-profile cache writes 20/20 under the right
profile, every key-bearing answer is `no-store`, `repr(Settings)` shows 0/13 secrets, 1000-recognizer
requests add 0 metric series, a previous-KEK blob opens in the rotation window, and with the real
Presidio model a birth date is transformed in 8/8 runs.

### 3.24 The M4 merge (2026-10-15): the fixes on the scanner's paths

`m4` (the UI scanner, with main as of `144cc65` merged into it) was merged into `fsec` with
`--no-ff` (merge commit `c1d8c36`; three conflicts, each kept both sides: the contract's migration
table and scan-spec paragraph, `discovery.py`'s imports, and `posture.py`, where the F-11 rule now
belongs to the roles that serve — the app and the worker — as the tokenizer token rule already
did: a scan's fill has no tokenizer adapter, so `external_tokenize` fails closed there). Each fix of
this review that has a twin on the scanner's paths was checked there; the twins that did not hold
were fixed, each with a test that fails without the fix (`tests/security/test_scanner_paths.py`,
`tests/scanner/test_scan_pattern_budget.py`):

| Fix | Scanner path | As merged | Fix | Test |
|---|---|---|---|---|
| F-02 | a policy pattern (custom recognizer, deny list) that runs past its budget during a crawl | `PatternTooSlow` escaped the scan handler: the job stayed `running` until its lease expired and the reaper retried it, each attempt burning the pattern's CPU again | `ScanRunner.run` stops the crawl and records `RunOutcome.error_code`; the scan ends `failed` with `custom_pattern_too_slow` at once (no retry, key purged, a failed evidence pack naming the code; request counts saved, nothing runs a pattern again); the browser-worker sizes its matcher processes from `DPS_PATTERN_PROCESSES` like the app and the worker | `test_scan_pattern_budget.py` (both cases), `test_scanner_paths.py::test_the_browser_worker_sizes_its_matcher_processes` |
| F-07 | the scan inventory CSV and the export CSV (labels come from the scanned site, which may be hostile) | `+`/`-` followed by a digit was never guarded: `-2+3+cmd\|' /C calc'!A0` reached the CSV as a formula | the L2 export's rule: `+`/`-` followed by more than a number or phone shape gets the apostrophe (a lone sign too, as M4 chose) | `test_scan_csv_cells_are_guarded_at_least_like_the_l2_export` (differential against the L2 export's guard) |
| F-10 | `dps_scan_fields_total{entity_type}` on the anonymous `/metrics` | a field classified with a policy's custom entity type put the caller-chosen name on `/metrics` | `crawler.count_field_metrics` labels it `custom` (`taxonomy.metric_entity_label`) | `test_a_custom_entity_type_is_counted_as_custom_on_the_scan_metrics`, `test_every_scan_field_counter_goes_through_the_bounded_label` |
| F-14 | `POST /scans` returns the scan's key and seed once | a strict audit sink refusing `scan.create` (503) left the scan queued under a key nobody held; its `Idempotency-Key` replay then returned it without the key | the scan is removed (`jobs.discard_unannounced`) before the 503; a retry gets a fresh scan and its key | `test_a_refused_scan_creation_audit_leaves_no_scan_behind` |
| F-18 | a scan's policy body — inline, or an `@name` reference resolved at accept | stored in clear in `dps_job.spec` and returned by `GET /scans`, `GET /scans/{id}`, `GET /jobs`, `GET /jobs/{id}` (deny-list terms included) | `redact_scan_spec` stores the value-free label; `POST /scans` seals the body in `spec_enc` (the run jobs' `SealedSelection` blob, `dps.layers.scanner.seal_scan_policy`); `open_runtime`, the finalize of a canceled scan and its evidence reopen it (`stored_policy`) | `test_a_scan_policy_body_never_rests_in_clear_and_the_worker_reopens_it`; M4's `test_scans_api.py` and `test_scan_policy_refs.py` now read the body from `spec_enc` |
| F-21 | `POST /scans` `Idempotency-Key` replays during a KEK rotation | a replay admitted under the previous KEK was refused as `idempotency_key_reused` (the fingerprint is keyed by the KEK) | the fingerprints under `DPS_JOB_KEK_PREVIOUS` are compared too, as on the jobs routes | `test_a_scan_admitted_under_the_previous_kek_replays_during_the_rotation` |
| F-11 | the browser-worker's production posture | the merged F-11 rule refused a browser-worker that shares the app's tokenizer configuration (M4's posture test failed) | the allow-list rule applies to the roles that serve (app, worker); a scan never calls the tokenizer | `tests/scanner/test_browser_worker_posture.py` (the app and the worker still name `DPS_TOKENIZER_PROFILES`) |

Held without a change (tests kept or extended): S1 — a scan spec refusal never echoes the input
(the app's scrubbed 422 for uncoded refusals, `{"detail": [{loc, msg, type}], "code":
"invalid_request"}` — M4's codeless 422 became this one; or a scan code whose detail names the code
only: `test_a_scan_spec_refusal_never_echoes_the_input`); S6 — every `/api/v1/scans*` route declares
`scan` (`test_route_auth.py` walks them); S7 — another tenant's scan and its pages, events,
inventory, export, cancel and `/jobs/{id}` answer 404 (`test_cross_tenant.py` extended); S11 —
every scans answer is `no-store`; S2 — M4 adds no secret setting; F-06 — the scan tables carry
`tenant_isolation` only (the RLS posture check passes at boot); F-09/S13 — the browser-worker
configures the same logging (root floor, capped libraries; M4's own log scan
`tests/test_no_values_in_logs_m4.py`). Not applicable: F-01/F-08 (no connection), F-12/F-19/F-22
(a scan's fill never tokenizes externally), F-13 (`/jobs/{id}/retry` refuses scans:
`job_not_retryable`), F-20 (a scan's seed draws synthetic personas only; the DEK, which reverses
the obfuscated captures' tokens, is wrapped when `wrap_key` is given). The contract-surface guard
(Task 0) lists M4's modules and scan routes now instead of its strict `xfail`.

Still open for the scanner at this point, done next on `fsec`: the S5 wiring (§3.25), Task 8B's
labelling on the scanner's path (§3.26) and the D4 re-check (§3.27) — M4's model-level
`url_invalid` still answers a non-http(s) scheme or userinfo before `check_scan_url` would.

### 3.25 S5 / F-03 — the scanner's SSRF guard, wired (2026-10-15)

The functions of Task 17 (§2, S5) are now on every path a scan takes to the network. Four layers,
each with a test that fails without it:

| Layer | What it refuses | Where | Test |
|---|---|---|---|
| Accept | `POST /scans` runs `netguard.check_scan_url` (in a thread: it resolves the host) before any job exists: a numeric host encoding (`0x7f000001`, `2130706433`, `127.1`), a registered host that is — or resolves to — a link-local/cloud-metadata, multicast, unspecified or reserved address (IPv4-mapped, NAT64, 6to4 unwrapped), loopback outside `env=local`, a registered name that does not resolve; `http` when `DPS_EGRESS_TLS_REQUIRED`. 422 with the guard's code (`host_forbidden`, `host_not_registered`) and a value-free detail; audited as a refused `scan.create`; nothing queued | `dps/layers/api/routers/scans.py` | `tests/security/test_scan_ssrf.py::test_post_scans_runs_the_ssrf_guard_before_any_job_exists` (`db`) |
| Run | the browser-worker checks the start URL again (`scanner.check_start_url`) before any browser starts: a name rebound since accept, or a scan queued before the check existed, fails with the code — key purged, no retry | `dps/layers/scanner/__init__.py` | `tests/scanner/test_scan_ssrf_browser.py::test_the_browser_worker_checks_the_start_url_again_before_any_browser` (`db`) |
| Every hop | the scope proxy — every connection of a scan context goes through it (D-C) — dials an allowed hop only through `ScanGuard._dial_hop`: the host is resolved once (`netguard.resolve_addresses`, on the event loop) and every address must pass `forbidden_ip_reason`; the proxy then connects to those addresses itself, so a second lookup cannot answer differently (DNS rebinding mid-crawl, a sub-resource of another registered host, an image pointing at a registered name that resolves to `169.254.169.254`). 403, counted `blocked` / `host_forbidden` (resource type `unrouted`); a name that does not resolve stays a 502 | `dps/layers/scanner/proxy.py`, `guard.py`, `crawler.py` (`allow_loopback = env == "local"`) | `tests/security/test_scan_ssrf.py` (positive control: a name that exists only in the guard's resolver is reached at the checked address; metadata plain, IPv4-mapped, among other addresses, NAT64-wrapped, over plain HTTP and as a `CONNECT`; loopback outside local; the real resolver) |
| Browser | Chromium resolves no name itself: `--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1` (`browser.RESOLVER_RULES`, `launch_args()`) — `MAP *` matches IP literals too, so a connection that bypassed the proxy would reach no name and no address but the proxy's | `dps/layers/scanner/browser.py` | `tests/scanner/test_scan_ssrf_browser.py::test_the_production_launch_reaches_nothing_but_through_the_proxy`, `tests/security/test_scan_ssrf.py::test_the_scan_browser_resolves_only_the_proxy_address` |

Measured on the way (both fixed, with tests): the plan's rule — the registered hosts excluded —
makes every scan fail, because `MAP *` also covers the proxy's address (`ERR_PROXY_CONNECTION_FAILED`
on every page; with `EXCLUDE 127.0.0.1` a proxied context reaches a registered *name* while an
unproxied one reaches nothing — `ERR_NAME_NOT_RESOLVED`); and `forbidden_ip_reason` refused `::1`
as "reserved" (it lies in `::/8`) before asking whether it is loopback, so `localhost` (127.0.0.1
and ::1) could not be scanned even in `env=local`, nor named as a connection host there (F-08).
Loopback is decided first now; outside local it is still refused (`test_netguard.py`).

### 3.26 Task 8B — T3 metadata labelling on discovery's and the scanner's paths (privacy, P1)

M5 Task 8B wires M1's labelling module into L2 discovery and L3 classification
(`docs/superpowers/build/M5-DEVIATIONS.md`, Task 8B). What keeps it inside P1 (no raw value to an
LLM), each with a test through the real `LlmGateway.complete()` under `strict`, the provider faked
at its seam only (`tests/test_llm_label_l2_l3.py`, `tests/scanner/test_crawler_llm_label.py`):

- **Opt-in, per column / per field:** only a policy that lists `llm_label` and a real provider
  (never `stub`); never per row.
- **Metadata only:** a column's leaf name, declared type, ≤ 3 value shapes (`XX-9999`), kind, null
  rate, distinct ratio and table name; a field's `name`/`id`, its placeholder's shape, a known
  input type and dictionary-only autocomplete, `maxlength`, `pattern` length and `required`, and
  its label only as `classify.prompt_label` makes it value-free (below). Names, labels and refs a
  detector finds a value in are aliased (`field_N`), as in Layer 1.
- **A field's label is value-free whatever its source** (S3b review r1, B1 — corrected
  2026-10-15). As first built, the prompt carried the label after detector redaction only, and
  `page_protected` protects digit/`@` tokens of *rendered* text only: a label taken from an
  `aria-label`, a `title`, the `placeholder` fallback or a hidden `aria-labelledby` target was in
  no protected list, so a record value no detector names went to the model unchanged — the
  reviewer's probe (real Chromium, `ScanRunner`, the real `complete()` under `strict`, the
  provider faked at `_stellar`) found 4 of 4 made-up values (three 11-digit numbers, one person
  name) in the messages the provider received while `progress.metrics.llm` said
  `{calls 1, raw_values 0, blocked 0}`; the same number in a rendered `<label for>` blocked the
  page's whole call. Banking UIs label per-record controls with their record ("Close loan …",
  "Nominee … agrees"), so this was the realistic case. Now (`prompt_label`):
  - the `placeholder` fallback is never a label: the placeholder is sent as its shape only;
  - a label is sent only when the scanner's detector runs NER (T2 configured, in the policy's
    tiers and its model loaded; `ner_runs`): a label is page-authored free text, and no other
    tier finds a bare person's name. Without NER (the default images: `DPS_NER_ENABLED=false`,
    `WITH_NER=0`) the prompt carries no label at all; the field is still labelled by its
    name, type, placeholder shape and hints, in the same one call;
  - the label is detector-redacted (`<PERSON_NAME>`) **recall-first**: every span any detector
    tier reports is redacted, at any score — the policy's `detect.min_score` does not apply to a
    prompt's label (`classify.PROMPT_LABEL_MIN_SCORE = 0`, S3b review r2, R2-B1, below); then
    every token that still holds a digit or an `@` is replaced by its shape
    (`Close loan 99999999999`); only digit-free redaction tags are kept. A number in a rendered
    label therefore no longer blocks the page's call.

  Proof: `tests/test_llm_label_l2_l3.py` (`test_l3_labels_are_value_free_whatever_their_source`,
  `test_l3_labels_are_sent_only_when_ner_runs` — NER off and an NER whose model failed to load —,
  `test_a_label_value_token_keeps_its_redaction_tags`) and, end to end with real Chromium and
  pages whose labels come from `aria-label`, `placeholder`, `title`, a hidden `aria-labelledby`
  target and a rendered `<label for>`, `tests/scanner/test_crawler_llm_label.py::
  test_record_values_in_labels_never_reach_the_model` (NER off and on): the provider receives none
  of the values, one call per page is made, `{calls 1, raw_values 0, blocked 0}`, no write
  reaches the site. All nine were red on the code before the fix. The reviewer's probe on the
  fix: 0 of 4 values sent on its attribute page, and its rendered-label page makes its call.

  **No threshold dependency** (S3b review r2, R2-B1 — corrected 2026-10-15). As first fixed,
  the label was redacted with the policy's detector, which drops every finding below the
  policy's `detect.min_score`. T2 scores a person's name 0.85 (Presidio's spaCy default,
  `NerModelConfiguration.default_score`) and a valid policy may set any `min_score` up to 1, so a
  custom policy above 0.85 (with the `ner` and `llm_label` tiers and a `WITH_NER` image) sent
  every name NER found in every label while `ner_runs` held: the reviewer's Chromium probe — a
  nominees table whose per-row checkboxes take their label from the neighbouring name cell
  (`nearest_text`) — found 2 of 2 names in the provider's messages at `min_score` 0.9 while
  `progress.metrics.llm` said `{calls 1, raw_values 0, blocked 0}`. The shipped presets use
  0.5–0.6. Now the prompt's label is redacted by a copy of the scanner's detector (same tiers,
  NER adapter, locale and custom recognizers) at score 0 — not at the NER adapter's floor (0.4):
  a typed hit that wins over an overlapping NER hit (`resolve_overlaps`) may itself score below
  0.4 (`NHS_NUMBER`/`SWIFT_BIC` 0.3 with context, custom recognizers from 0) and must not take
  the name's redaction with it. Over-redaction costs label detail only; the policy's threshold
  still governs what a job transforms and the label the inventory stores. Proof (every case
  above 0.85 red before the fix, all green after): `tests/test_llm_label_l2_l3.py::
  test_l3_label_names_are_redacted_whatever_the_policy_min_score` (0.86, 0.9, 1.0, through the
  real `complete()` under `strict`, the provider faked at `_stellar`: the name never reaches the
  provider, the label is sent, one call, `{calls 1, raw_values 0, blocked 0}`) and
  `test_prompt_label_is_redacted_recall_first` (0 to 1; the caller's detector keeps its
  threshold); end to end, `tests/scanner/test_crawler_llm_label.py::
  test_names_in_labels_never_reach_the_model_whatever_the_policy_min_score` (the nominees table
  at 0.6 and 0.9: both labels sent as `<PERSON_NAME>`, 0 name parts sent, one call, no write).
  The reviewer's probes on the fix: 0 of 2 name parts sent at 0.86 and 0.9, 0 of 2 names on the
  table at 0.9.
- **Checked twice before it leaves:** the module's leak guard against the `protected` originals —
  every sampled value of the leftover columns (L2); what the page's fields hold or show, the
  detectors' findings in its text and every token of its text holding a digit or an `@` (L3,
  `page_protected`) — then the gateway's egress verifier (`strict` in staging/prod). A prompt that
  would carry one is never sent (`leak_guard` / `egress_blocked`, counted).
- **Bounded influence:** a label's confidence is capped at 0.85 (T3 never outranks a checksum),
  below the policy's `min_score` it changes nothing, only built-in entity types are accepted, a
  T0–T2 verdict is never changed, and an anchor is never moved. The reviewer sees `llm_label` in
  the snapshot's detectors and can override it.
- **Counted:** `progress.metrics.llm = {calls, raw_values, blocked}` on discover and scan jobs (the
  Task 16B roll-ups read it); `dps_llm_label_fields_total` by outcome.

Residual (open, for the owner; §5): the egress verifier looks for protected originals in the
fixed system instructions too, so a value that occurs inside them blocks the call (fail closed).

### 3.27 The D4 re-check after S5 and Task 8B (2026-10-15)

S5 put a resolver and a dialler into the scope proxy, a new launch flag on Chromium, and Task 8B an
LLM call into the per-page pipeline: D4 (the scanner never submits anything) was proven again, on
counters, never on the absence of an error.

| Proof | Result |
|---|---|
| The browser proofs on this branch, real Chromium and counting servers (`tests/scanner/test_no_submit.py`, `test_guard.py` — the D-C proof: beacon, keepalive, SharedWorker, WebSocket, redirect to a foreign host —, `test_crawler.py`, `test_scan_job_e2e.py` through `get_browser` with the new flag) | all pass: the full lane suite with dps-db, source-pg, the demo site and Chromium — 3672 passed, 232 skipped (tests whose services or optional extras were not started for this run), 0 failed |
| A crawl that labels fields with T3 (provider faked at `_stellar`, egress verifier `strict`): `tests/scanner/test_crawler_llm_label.py` | the demo site's `non_get`, `form_get`, `logout_hits`, `admin_hits` unchanged; no allowed non-GET in the request log |
| Live, the lane's `scanner` stack rebuilt from this branch: `DPS_SMOKE_SCANNER=1 DPS_SMOKE_M2=off python tools/smoke_test.py` | 34/34 checks (2 OCR checks skipped: no `docs` profile): a 21-page scan of the demo site, 63 fields (51 personal), `non_get +0, form_get +0, logout_hits +0, admin_hits +0; non_get_allowed 0`, the guard blocked 60 POSTs and 1 GET; the evidence pack says 0 submissions; 3 masked captures; the persona export holds 0 demo values |
| Live T3 scan on the same stack, the browser-worker pointed at a fake Stellar/COIN on the host (`~/.cache/dps-build/reports/S3b-impl/probes/live_t3_scan.py`) | 202, succeeded; `progress.metrics.llm = {calls 2, raw_values 0, blocked 0}`; 2 fields labelled `LOCATION` (detector `llm_label`); demo counters +0; `non_get_allowed` 0; the 2 prompts the model received hold 0 demo values, metadata keys only |
| Live S5 probe (`probes/live_scan_ssrf.py`) | numeric encodings (3) → 422 `host_forbidden`; a tenant admin registers `169.254.169.254` and its IPv4-mapped form → both still 422 `host_forbidden`, value-free; nothing queued; the tenant's hosts restored |
| Container logs of the stack (app, worker, browser-worker, demo site) after both scans | 0 demo values (raw or normalised) |

## 4. Considered and refuted

- **`pvc` file-export targets not tenant-namespaced under `DPS_EXPORT_ROOT`** — every write key carries
  a unique per-job (ULID) or per-connection segment and `check_key` refuses `..`, so tenants never
  overwrite each other; file stores are target-only and no route reads them back (artefact reads are
  RLS-scoped and tenant-keyed). Reading another tenant's export needs the shared volume itself, which
  only the operator or the downstream consumer holds. Defence-in-depth note only.
- ~~ReDoS via a declared-field path glob~~ — **not refuted** (review round 1 B1): the glob was
  compiled to a regex the S3 filter never saw, and collapsing *adjacent* stars left separated stars
  backtracking (`$.*a*a*a*a*a*a*a*a*a*b`: > 20 s on a 63-character name). Fixed in §3.2: globs are
  matched without a regex, linear in the path.
- **Unbounded key custody for paused or stalled run jobs** — contract §6.1 defines custody as "the
  job's lifetime", paused/waiting are live states, resume needs the key, and the owner ends custody by
  canceling (which purges it). A maximum pause duration would be a policy, not a D2 fix.

## 5. Residual risks

- Policy patterns (review rounds 1–2): the static filter is a heuristic; the bound is the per-call
  allowance, charged on each run's own CPU (§3.2). What one request with a stacked pattern can
  spend, measured through `/detect`: 10 ms + 20 µs + 0.5 µs per character per pattern — about 11 ms
  on a 2 KB text (round 1: about 1 s), 0.06 s on 100 KB (round 1: 1.2 s) — and never more than
  `DPS_PATTERN_CPU_SECONDS` (5 s), which 1000-pattern policies reach from about 10 KB of text
  (1000 × 10 KB, × 50 KB, × 1 MB: refused at 5 s; round 1: 11.6 s, 51.7 s, 1001.5 s). Against the
  legitimate work of the same request the allowance is 5–250 times the measured per-character cost
  of real patterns (0.5 µs earned, 0.002–0.1 µs spent), and more on tiny texts, where the 10 ms
  base dominates. At the rate limit (50 requests/s per key) one `detect` key can therefore make a
  replica spend about 0.5 CPU-second per second with 2 KB single-pattern requests, and up to 5 CPU-
  seconds per request with large policies — but every run past 5 ms executes in the matcher
  processes, so all such runs together occupy at most `DPS_PATTERN_PROCESSES` CPUs per server
  process (default 2), plus 5 ms GIL slices in request threads; other tenants' long pattern runs
  then queue for a matcher process (latency, not refusal), and their short runs are unaffected. An
  NDJSON stream spends at most its items' own allowances plus one 10 ms base. Legitimate requests
  are refused only when a pattern costs more than about 0.25 µs per character, or a policy's total
  work passes the ceiling (1000 patterns over 1 MB of text are charged about 2.7 CPU-seconds and
  answer 200 in 3.9 s, inside the 5 s default; raise `DPS_PATTERN_CPU_SECONDS` for heavier
  policies); the error tells the owner to
  simplify the pattern or send less text per call. The allowance and the ceiling are this round's
  calibration (for the owner to confirm); a deployment tightens or relaxes them through the two
  settings.
- Policy patterns run on the `regex` module (`VERSION0`), not `re` (review round 2 N-r2-1): the
  syntax is `re`'s, and the two constructs the engine would read differently are refused when a
  policy is validated (`check_pattern`): an unescaped `{` that is not a repeat count (a fuzzy-match
  constraint there, `(?:ACCT1234){e<=2}`) and an unescaped `[` inside a set (`[[:digit:]]`, a POSIX
  class there) — escape them. Two Unicode details still differ (open): `\s` does not match the
  ASCII separators FS, GS, RS and US (`\x1c`–`\x1f`, which `re` treats as whitespace) — write
  `[\s\x1c-\x1f]` where legacy extracts use them; `\w` and `\b` include combining marks, so a
  span may run one character further than under `re` (more is masked, never less).
- Production credentials are per tenant (review round 2 N-r2-3): a `_shared/` secret ref that is a
  production credential in tenant A is not one in tenant B, so a B `jobs` key can still re-point a B
  connection that names it — only when an operator shares one production login across tenants
  (do not).
- Connections (review round 1 B2): a production credential needs `admin` wherever it is named,
  changed or deleted, but a `jobs` key may still name any of its tenant's **non-production**
  `secret_ref`s in a new connection with an endpoint of its choice (that login then reaches that
  endpoint, inside the egress rules). Restrict `jobs` keys or mount fewer refs per tenant.
- F-08 (review round 1 N1, open): python-oracledb honours `https_proxy` in an Easy Connect string
  or descriptor and dials the proxy first; `netguard` checks only the `HOST=` / Easy Connect host,
  so an inline `dsn` naming a metadata address as proxy passes (reachable only with inline
  credentials, which the production posture forbids, or operator secret files). Server-directed
  hops (MongoDB members a seed advertises, Oracle listener redirects) are not checked either; the
  egress NetworkPolicy is the backstop.
- Private (RFC 1918) targets are allowed by design for internal UAT sites and databases.
- DNS can change between DPS's check and the client's own resolution (rebinding window) for
  **connections** (F-08: the database drivers resolve the host themselves); the OpenShift egress
  NetworkPolicy (Task 25) is the network-level backstop. The **scanner** has no such window: its
  scope proxy dials the very addresses it checked (§3.25).
- Failed authentications have no tenant and are not audited; they are counted in
  `dps_requests_total{outcome="rejected"}`. DB keys propagate to other replicas within
  `DPS_KEY_CACHE_SECONDS`.
- `DPS_TOKENIZER_PROFILES` unset means unrestricted tokenization (local/dev); the production posture
  refuses a tokenizer without it, and the bank should give DPS its own data elements (HANDOFF).
- A once-only key whose answer is lost in transit (not refused by DPS) cannot be detected by the
  server; callers that cannot afford that supply their own key material or seed.
- v1 bind tags (hints issued before F-22) still verify; their `None`/`""` profile ambiguity remains
  for those hints only.
- `/metrics` is keyless by design (ops scraping); restrict the ops port by NetworkPolicy (Task 25).
- The scanner (S5, §3.25): private (RFC 1918 / unique-local) targets stay allowed by design; a
  tenant admin can register such a host, and every hop to it is then allowed. Over HTTPS the proxy
  sees only the tunnel's host, never the path (the request guard and the route see the rest).
  Unproxied contexts of the scan browser can still reach 127.0.0.1 directly (the proxy's own
  address must stay resolvable); no scan context is ever made without the proxy
  (`browser.new_scan_context`).
- T3 labelling (§3.26): the egress verifier looks for protected originals in the fixed system
  instructions too (`fllm`'s `0fd76d0` kept that, and `tests/test_llm_egress.py` pins it), so a
  sampled or page value that happens to occur inside them — normalised, as a substring:
  "Personal", "Choose", short first names such as "Seth", "Devi", "Abel" (measured ≈0.4–0.7 % of
  Faker first and last names, ≈5 % of generic words) — blocks that table's or page's labelling
  call under `strict`: fail closed (no label; `blocked` counted), never a value sent. On the lane's
  seeded demo `bank` tables it stopped the calls of 5 of 11 tables, each by a match inside the
  fixed instructions (one table also matched in its call data). Direction for the owner: exempt the registered constant system prompts from
  the protected scan (the module's leak guard checks every protected value against the call data
  before the verifier runs, so nothing the call carries goes unchecked).
- T3 labelling, L3: a field's label reaches a prompt only when NER runs (§3.26), with its value
  tokens as shapes; a name NER misses in a label (or a value holding no digit and no `@` that no
  detector names: a city, a bare word) is still sent — NER's recall is the bound, and the
  module's leak guard stops it only when the page also shows it as a field's value or a finding.
  A field's `name`/`id` attribute is sent as is when it is identifier-like (`_PLAIN_NAME`: no run
  of four digits) and no detector finds a whole value in it. Opt in on sites whose labels carry
  no customer data, and run NER where labels matter.

## 6. Re-running the review

```bash
./lane.sh pytest -q tests/security tests/test_no_values_in_logs_m5.py tests/test_contract_surface.py
# the M4 merge (§3.24): the fixes on the scanner's paths (the db cases need the lane's dps-db)
./lane.sh pytest -q tests/security/test_scanner_paths.py tests/scanner/test_scan_pattern_budget.py \
  tests/scanner/test_browser_worker_posture.py
DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -m db tests/security/test_cross_tenant.py \
  tests/api/test_production_resource_gate.py
./lane.sh python /Users/neelu/.cache/dps-build/reports/S3-<lens>/<probe>.py   # the finders' probes
./lane.sh python /Users/neelu/.cache/dps-build/reports/S3-impl/probes/<probe>.py  # re-run copies
# review round 1 (B1-B3): the reviewer's probes and the bounded re-runs
./lane.sh python /Users/neelu/.cache/dps-build/reports/S3-review-r1/probes/probe_redos_api.py
./lane.sh python /Users/neelu/.cache/dps-build/reports/S3-review-r1/probes/probe_glob_redos.py
./lane.sh python /Users/neelu/.cache/dps-build/reports/S3-fix-r1/probes/redos_dps_matcher.py
./lane.sh python /Users/neelu/.cache/dps-build/reports/S3-fix-r1/probes/probe_f10_registry_exhaustion_after.py
PYTHONPATH=. DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -s -p tests.fixtures.services \
  -p tests.fixtures.db -p tests.fixtures.sources -c pyproject.toml --rootdir=. \
  /Users/neelu/.cache/dps-build/reports/S3-review-r1/probes/test_probe_f01_repoint.py
# review round 2 (R2-B1, R2-B2): load, scale and matcher tests; the reviewer's probes re-run
./lane.sh pytest -q tests/security/test_pattern_budget_load.py tests/security/test_pattern_matcher.py
P=/Users/neelu/.cache/dps-build/reports/S3-review-r2/probes
./lane.sh python $P/concurrency_false_refusal.py && ./lane.sh python $P/gil_busy_false_refusal.py
./lane.sh python $P/probe_e2e_false_refusal.py 18519      # real uvicorn server on a free port
./lane.sh python -c "import os,runpy;[os.environ.pop(k) for k in list(os.environ) if k.startswith('DPS_') and not k.startswith('DPS_TEST_')];runpy.run_path('$P/probe_budget_scaling.py',run_name='__main__')"
# the scanner's SSRF guard and T3 labelling (§3.25, §3.26; the db cases need the lane's dps-db,
# the browser ones Chromium and the demo site, which the fixture starts with node)
./lane.sh pytest -q tests/security/test_scan_ssrf.py tests/security/test_netguard.py \
  tests/scanner/test_scan_ssrf_browser.py tests/test_llm_label_l2_l3.py \
  tests/scanner/test_crawler_llm_label.py
# the D4 re-check (§3.27): the no-submit proofs, then a live scan on the lane's scanner stack
./lane.sh pytest -q -m browser tests/scanner/test_no_submit.py tests/scanner/test_guard.py
```
