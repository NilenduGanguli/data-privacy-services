# S3 security finder — lens "secrets-logs" (branch `fsec` = main `64c46e3`: M0–M3 + M5)

Date: 2026-10-14 · Worktree: `/Users/neelu/document_intelligence/dps-wt-fsec` (read-only for this
lens: no repository file changed, `git status` clean) · Probes and raw captures:
`/Users/neelu/.cache/dps-build/reports/S3-secrets-logs/` (all values used are FAKE sentinels; every
probe prints counts / booleans / field names only).

**Verdict: 8 findings with evidence** — 2 medium, 5 low in lens (4 of them are M5 Task 17 items
S1, S2, S11, S13 still unaddressed), plus 1 HIGH out of lens found in passing (NER downgrades a date
of birth to a kept DATE). S9, S10 and S12 are addressed and verified.

## Method

1. Read RESUME §0–§3/§10, programme plan §2.5–§3/§5/§6, contract §0.1/§8.2, M5 plan Task 17,
   deviation logs (M0/M1/M5 notes on no-store, taxonomy registry, Task 17 deferral of secret reprs).
2. Code walk of every place a value/secret could leave the process: `dps/config.py`,
   `dps/observability.py`, `dps/app.py` (middleware, 422 handler, docs/CORS), `dps/auth*.py`,
   `dps/layers/api/{errors,common,ndjson,deps,idempotency}.py`, every router, connectors' error
   mapping (PG/Oracle/Mongo), `jobs_run.py`/`discovery.py` failure paths, `audit*.py`, evidence
   (JSON/HTML/PDF), reports, posture, gateway (COIN/Stellar/Vertex), OCR, tokenizer, secrets, CLIs
   (`keys_cli`, `rekey`, `migrate`), worker ops server, console storage/URLs, Dockerfile CMD.
3. Dynamic probes (in-process `create_app` + TestClient; the lane's `dps-db`/`source-pg` for the
   DB-backed ones; real Presidio NER with the cached `en_core_web_sm 3.8.0` loaded by path):
   | Probe | What it proves |
   |---|---|
   | `p01_settings_repr.py` | S2: which secret fields `repr/str(Settings)` print |
   | `p02_presidio_debug_child.py` | engine + NER at DEBUG vs INFO: customer words in stderr |
   | `p03_l1_api_child.py` | L1 HTTP API at DEBUG/INFO: log scan, Cache-Control, error-body echo |
   | `p04_db_api_child.py` | M2/M5 routes on a fresh migrated DB: Cache-Control, echo, log scan, audit/job/evidence rows scanned as superuser |
   | `p05_uncapped_loggers.py` | every third-party logger family left at DEBUG by `configure_logging("DEBUG")` |
   | `p06_ner_dob_downgrade.py` | (out of lens) DOB kept in clear when NER is on |
   | `p07_metric_label_cardinality.py` | caller-chosen label values: series/taxonomy growth |
   | `p08_422_union_tag_echo.py` | 422/400 bodies that echo a submitted value (real app + DB) |
   | `p09_metrics_custom_entity_disclosure.py` | tenant custom entity names on anonymous `/metrics` |

Re-run any probe from the worktree: `./lane.sh python /Users/neelu/.cache/dps-build/reports/S3-secrets-logs/<probe>.py [args]`
(p04/p08 need `docker compose --profile sources up -d dps-db source-pg`).

## Findings

### F1 · MEDIUM · Presidio (NER) writes customer text to the logs at `DPS_LOG_LEVEL=DEBUG`
- **Where:** `dps/observability.py:146-166` (`_THIRD_PARTY_LEVELS` has no `presidio-analyzer`) and
  `:197` (`logging.basicConfig(level=<DPS_LOG_LEVEL>)` puts the *root* logger, hence every uncapped
  library, at the configured level). Posture (`dps/posture.py`) does not forbid DEBUG in staging/prod.
- **Evidence:** p02 (engine) and p03 (HTTP `/api/v1/detect`, `/obfuscate`, `/plan`, documents):
  with NER on and level DEBUG, logger `presidio-analyzer` emits
  `LemmaContextAwareEnhancer`'s `"Context list is: <lemmas around each hit>"`
  (`presidio_analyzer/context_aware_enhancers/lemma_context_aware_enhancer.py:274`) —
  p03 stderr: 7 lines carry the fake surname, 8 the full fake date of birth; p02: 4 records with the
  surname, street, town and birth year. Same run at INFO: 0. The bypass is structural: these are
  stdlib records, the structlog allow-list never sees them. p05 lists the other families left at
  DEBUG (asyncpg, google, opentelemetry, pypdfium2, smart_open, spacy, uvicorn, …); presidio is the
  one shown to carry values.
- **Exploit/failure:** an operator sets `DPS_LOG_LEVEL=DEBUG` to troubleshoot an image built with
  `WITH_NER=1` (or the `[ner]` extra); every detect/obfuscate/synthesize/document request and every
  L2 job over free text (the worker uses the same detector) writes names, birth dates and other
  words adjacent to detected entities to container logs → log platform (wide access, long
  retention). Violates spec §10 / contract §0.1; same class as the carried M3-B4r3 (pymongo).
- **Fix:** invert the rule — keep the root logger at WARNING whatever `DPS_LOG_LEVEL` is and apply the
  configured level only to the `dps` logger hierarchy (allow-list, like `_SAFE_FIELDS`); at minimum
  add `("presidio-analyzer", logging.WARNING)` (hyphenated name) and `("presidio_analyzer", …)`,
  `("spacy", …)` to `_THIRD_PARTY_LEVELS`. Optionally a posture warning for DEBUG in staging/prod.
  **Test:** `configure_logging("DEBUG")` + `PresidioNer` (skip without a model) over a sentinel
  text next to a date → captured stderr/root records contain no sentinel; extend
  `tests/test_observability.py::test_third_party_loggers_are_capped` to assert every non-`dps`
  logger's effective level is ≥ WARNING after `configure_logging("DEBUG")`.

### F2 · MEDIUM · Caller-chosen custom entity names become unbounded label values on the anonymous `/metrics` (and unbounded process-global taxonomy entries)
- **Where:** `dps/engine/service.py:307` and `dps/engine/batch.py:898`
  (`FINDINGS.labels(<entity_type>, …)` with any inline custom `supported_entity`);
  `dps/engine/detect/custom.py:162` (`register_entity` into the process-wide `TAXONOMY`,
  `dps/engine/taxonomy.py:174-193`); `dps/layers/api/routers/ops.py:26` (`/metrics`, no auth).
- **Evidence:** p07 — a key with only the `detect` scope, 10 requests × 1,000 inline recognizers
  (one field each): `dps_findings_total` series 0 → 10,000, taxonomy 58 → 10,058 entries, anonymous
  `/metrics` body 1.5 MB, RSS +17 MB; nothing is ever freed. p09 — tenant A's custom type
  `PROJECT_FALCON_TARGET_ID` is not listed to tenant B by `GET /entities`, but an anonymous
  `GET /metrics` shows it.
- **Exploit/failure:** (a) availability: any low-privilege key adds ~1,000 never-freed series and
  taxonomy entries per request (p07: ~0.3 s per such request; no cap on `custom_recognizers`, only
  the 8 MiB body limit), so a loop of requests grows app/worker memory (~1.7 KB per series+entry
  measured) until OOM and makes every Prometheus scrape fail long before that; (b) disclosure: tenants'
  custom entity names (often project/system/customer-programme names) are readable by anyone who can
  reach port 8400 (the console port), without a key. (The cross-tenant `/entities` listing noted
  "open" in M1-DEVIATIONS is closed; the metrics path is not.)
- **Fix:** label `dps_findings_total` with a bounded set — built-in entity types as is, every custom
  type as `custom` (and detector family only); keep custom tiers per request/policy instead of
  the global `TAXONOMY` (or cap the registry and evict); cap `custom_recognizers` (e.g. ≤ 50) and
  deny-list sizes in `DetectConfig`; serve `/metrics` only on the ops port or behind a
  network policy/token in staging/prod. **Test:** N requests with distinct custom names → the
  number of `dps_findings_total` series and `len(TAXONOMY)` stay bounded; `/metrics` never contains a
  custom name.

### F3 · LOW · S11 unaddressed — no `Cache-Control: no-store` on API responses that carry DEKs, seeds, revealed originals or tenant data
- **Where:** `dps/app.py:85-106` (`_SecurityHeaders` adds nosniff/DENY/no-referrer/HSTS only); only
  some routes set the header themselves (admin key create/rotate, jobs/run, job-groups create,
  artefacts, evidence HTML/PDF, reports downloads, audit CSV).
- **Evidence:** p03 — `cache-control` absent on `POST /api/v1/obfuscate` (JSON, NDJSON body and
  NDJSON answer; body holds `key_material.dek`), `POST /api/v1/documents/obfuscate` (DEK),
  `POST /api/v1/synthesize` scope=key (`synthesis_seed`), `POST /api/v1/reveal` (all six fake
  originals in the body). p04 — absent on `GET /api/v1/policies/{name}` and `/versions/2` (deny-list
  terms), `GET /jobs`, `/jobs/{id}`, `/connections`, `/connections/{id}`, `/admin/keys`,
  `/admin/audit` (JSON), `/admin/posture`, `/evidence`, `/reports/summary`.
- **Exploit/failure:** the credential travels in `X-API-KEY`, not `Authorization`, so RFC 9111 §3.5
  (shared caches must not reuse responses to authenticated requests) does not protect these GETs; a
  shared cache/API gateway in front of DPS (e.g. Varnish's default 120 s TTL) may store tenant A's
  GET response and serve it to the next caller of the same URL, key or no key. The POST answers
  with DEKs/originals have no explicit directive either, against the plan's S11 rule.
- **Fix:** as planned — in `_SecurityHeaders`: `if path.startswith("/api/"): setdefault("Cache-Control",
  "no-store")` (optionally `Vary: X-API-KEY, X-DPS-Tenant`). **Test:** `tests/security/test_headers.py`
  (plan S11) over every `/api/v1` route kind (JSON, NDJSON stream, multipart, error, GET list).

### F4 · LOW · S2 unaddressed — `repr()`/`str()` of `Settings` print 12 secrets
- **Where:** `dps/config.py:99-100, 125-126, 147, 189-193, 201-202, 255-256` — only
  `tokenizer_token` (`:277`) has `repr=False`; M5-DEVIATIONS line 21 defers the rest to Task 17.
- **Evidence:** p01 — `repr` and `str` show `api_keys`, `bootstrap_api_key`, `coin_client_id`,
  `coin_client_secret`, `azure_vision_key`, `pg_password`, `pg_worker_password`,
  `pg_migration_password`, `job_kek`, `job_kek_previous`, `s3_access_key_id`,
  `s3_secret_access_key` (12/13 sentinels visible).
- **Exploit/failure:** no current call site logs a `Settings` object (allow-listed structlog,
  `HandlerContext.__repr__` overridden), but any `print`/`%r`/f-string/debugger/pytest assertion
  introspection/error report that renders a `Settings` (or an object whose default repr nests it)
  dumps the job KEK, DB passwords and API keys in clear.
- **Fix:** `Field(default="", repr=False)` on every secret field (and consider `SecretStr`);
  **test** `test_settings_repr_hides_secrets` exactly as planned (all 13 fields).

### F5 · LOW · S1 residual — error bodies still echo submitted values (and 422 lacks `code`)
- **Where:** `dps/app.py:220-227` (`_validation_error` keeps pydantic's `msg`);
  `dps/engine/detect/custom.py:33` (`entity type {value!r} …`), `dps/engine/policy.py:79`
  (`unknown method {value!r}`), `dps/engine/jsonpath.py:49,56` (`{path!r}`), surfaced through
  `EngineError(f"invalid policy: {exc}")` (`dps/engine/service.py:260`) and
  `validation_message`.
- **Evidence:** p08 (real app + DB): `POST /api/v1/jobs/run` and `/jobs/run/validate` with
  `target.pk_strategy.kind` = a fake SSN → 422 whose `msg` (`union_tag_invalid`) quotes it; same app:
  `POST /api/v1/detect` inline policy with that SSN in `supported_entity` → 400 quoting it. p03:
  `POST /api/v1/policies/test-recognizer` likewise (422). Plain type/literal/extra errors do not echo
  (p03, p04). No 422 carries `"code": "invalid_request"` (S1's shape; contract module
  `dps/layers/api/validation.py` absent).
- **Exploit/failure:** a value pasted into the wrong field (the S1 scenario) comes back in the
  response body and lands in client/gateway logs.
- **Fix:** in `_validation_error` emit a fixed message per `type` (or drop `msg` for
  `union_tag_invalid`, `value_error`, `assertion_error`, `get_attribute_error`, `uuid_parsing`,
  `url_*`) and add `code: "invalid_request"`; make the custom validators name the field and rule,
  not the value. **Test:** `tests/security/test_validation_errors.py` plus the two echo cases above.

### F6 · LOW · S13 unaddressed — no M5 log-scan test (contract §0.1 requires one per milestone)
- **Where:** `tests/test_no_values_in_logs_m5.py` does not exist; `tests/test_no_values_in_logs_m3.py`
  scans only M3 modules; nothing scans copula/metrics/tokenizer/auth_db/keys_cli/rekey/
  policy_store/tenants/reports/retention/pdf/netguard/egress.
- **Evidence:** file absent (`ls tests`); the M5 modules' log calls I read are allow-listed and
  value-free, and p04 found no sentinel in logs or audit rows for the M5 routes it drove — so this is
  a missing guard, not a present leak.
- **Fix/test:** add the planned `tests/test_no_values_in_logs_m5.py` (static AST scan of the M5
  modules like M3's, plus the dynamic sentinel run of plan Task 17) and include F1's third-party case.

### F7 · LOW · Inline-policy deny/allow-list terms are stored in clear in job rows and listed by `GET /jobs`
- **Where:** `dps/jobs_models.py:400-434` (`redact_spec` templates selections and drops key
  material, but keeps `policy` — an inline dict — verbatim); `dps/layers/connectors/submit.py:130-136`
  seals only the selection.
- **Evidence:** p04 — a discover job whose inline policy has a deny-list term: the term is returned by
  `POST /jobs/discover`, `GET /jobs/{id}` and `GET /jobs` and is in `dps_job.spec` (superuser scan:
  1 row). `dps/engine/detect/custom.py` itself says deny-list entries "are often the sensitive terms
  themselves" (and hides them from validation errors).
- **Exploit/failure:** a privacy officer lists named individuals (VIPs, PEPs) to always mask; the
  names persist in plaintext job rows for the retention period, in DB backups, and are listed to
  every `jobs`-scope key of the tenant — unlike selection literals, which contract §0.1 has sealed.
- **Fix:** store only the policy label/hash in `dps_job.spec` and seal an inline policy body with the
  job KEK (`spec_enc`, like `SealedSelection`), or require stored `@name[:vN]` policies for L2 jobs.
  **Test:** submit with a sentinel deny term → absent from the job row and from `GET /jobs*`.

### F8 · HIGH · (out of lens, found in passing) With NER on, a date of birth is downgraded to `DATE` (method `keep`) and returned in clear
- **Where:** `dps/engine/detect/patterns.py:413-420` (`resolve_overlaps` ranks validated → score →
  length, so a longer/higher-scored Presidio `DATE_TIME`→`DATE` span beats the pattern tier's
  `DATE_OF_BIRTH`), via `dps/engine/detect/ensemble.py:96-110`.
- **Evidence:** p06 — same engine, policy `gdpr-pseudonymise`, fake text; NER off: DOB detected as
  `DATE_OF_BIRTH` and transformed in all 4 phrasings; NER on: "date of birth 14/03/1971",
  "DOB: 14/03/1971", "birthday 14/03/1971" → `DATE` + `keep`, DOB in the output, request succeeds
  (verification status `partial`, passed). Also seen through the HTTP API in p03.
- **Exploit/failure:** any deployment that enables NER (image `WITH_NER=1`, `[ner]` extra, default
  `ner_enabled=None` = on when importable) leaks birth dates from free text, documents (KYC forms)
  and L2 free-text columns into "obfuscated" outputs — the stronger detector weakens protection.
- **Fix:** when spans overlap, prefer the more sensitive entity (taxonomy tier) before score/length,
  or never let an NER `DATE` override a T1 typed date hit (map NER dates overlapping a DOB cue to
  `DATE_OF_BIRTH`); test with NER on for the four phrasings plus a source-vs-output check.

## M5 Task 17 items in this lens

| # | Item | Status on `fsec` | Evidence |
|---|---|---|---|
| S1 | 422 never echoes input | **partly** — `input`/`ctx` dropped (M0 handler), but `msg` echoes for `union_tag_invalid` and value-quoting validators; no `code`; `validation.py` and its test absent | F5, p03, p08 |
| S2 | secret fields hidden in `repr(Settings)` | **open** (1 of 13 fields) | F4, p01 |
| S9 | audit CSV formula-safe | addressed (`dps/audit_query.py:49,107-110`, CSV `no-store`) | code + p04 |
| S10 | PDF mini-markup escaped | addressed (`dps/layers/reports/pdf.py:118-124`, every Paragraph via `pdf_text`) | code |
| S11 | `Cache-Control: no-store` on every `/api/` response | **open** (per-route only) | F3, p03, p04 |
| S12 | no docs/OpenAPI in prod; no CORS `*` | addressed (`dps/app.py:584-639`, `dps/posture.py:197-200`); `tests/test_posture_m5.py::test_docs_and_cors_follow_the_environment` passes | test run |
| S13 | M5 modules value-free in logs | **open** (test absent; code clean in probes) | F6 |

Out of this lens but noticed (for the other finders): S3 (`regex_guard.py` absent; `PatternSpecModel`
has no ReDoS check) and S5 (`netguard.check_scan_url` absent) are not implemented on `fsec`.

## Verified clean (evidence, not assumptions)
- Error mapping of PG/Oracle/Mongo drivers: class names / `ORA-`/`mongo:` codes only
  (`postgres.py:355-411,869-872`, `oracle.py:118-145`, `mongo.py:122-150`); job/partition error texts
  come from value-free domain messages.
- Unhandled exceptions: `_ValueFreeErrors` (`dps/app.py:187-217`) logs the class only; uvicorn access
  log off (`Dockerfile` CMD `--no-access-log`; worker ops server `access_log=False`).
- pymongo, botocore/boto3/s3transfer, httpx/httpcore, urllib3, sqlglot, pdf libs are capped;
  python-oracledb has no stdlib logging (p05); google-auth disables propagation itself.
- p03/p04 log scans at DEBUG with NER off: 0 sentinel hits (inline password, wrong-password
  connection test, selection literal, deny term, minted key); audit rows (6) value-free; the minted
  key and DEKs never reach logs/audit; `GET /admin/posture` value-free.
- Selection templates (`where_template`/`query_template`): 35 literal forms across PG/Oracle
  (dollar quotes, casts, arrays, regex, LIKE, intervals, N'', DATE '', TO_DATE) — no value survives
  except deliberate format strings / JSON key names; comments stripped; binds sealed.
- Key handling: API keys stored/held as SHA-256; raw key only in the 201 (no-store); `X-Request-ID`
  only echoed when an identifier; COIN/Stellar/Vertex/OCR/tokenizer errors carry status/class only;
  OCR key sent only to the same origin; tokenizer bearer token only in the client.
- Console: API key in sessionStorage unless "remember"; wizard draft drops credentials, binds,
  filters, key material; no key in URLs; build without source maps or embedded keys.

## Environment notes
- The `dps-wt-fsec` compose project is shared with the other S3 finders; it was brought down by
  another agent mid-run. I started only `dps-db` and `source-pg` (`--profile sources up -d dps-db
  source-pg`) and left them running for the others; my probe databases (`dps_probe_s3sl_*`) were
  dropped by the probes. No image built, no other project touched, no repository file changed.
