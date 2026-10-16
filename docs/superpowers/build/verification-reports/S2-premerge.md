# S2 — M4 pre-merge: main (M5 + M3) merged into `m4`, scanner adapted, gates

**Verdict: DONE — merge committed, six adaptations (plus three integration fixes) committed test-first, every
gate green on the lane stack (two flakes analysed below), D4 proofs passing, stack down.**
Worktree `/Users/neelu/document_intelligence/dps-wt-m4`, branch `m4`, HEAD `a0e8d26`, `git status` clean.
Author on every commit: NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers. Not pushed (see "Push").

## 1. Merge

| Commit | What |
|---|---|
| `b34dd07` | `merge: main (M5 + M3) into m4 ahead of the M4 integration` — main `6fa2da5` into m4 `bdbdc0a`. The earlier agent had resolved all 23 conflicted files; I re-derived every conflict (`git merge-file` of base/ours/theirs into scratch) and checked that each conflict-side line survives in the resolution (code files: 0 lines lost; docs: only reworded lines, rows merged), fixed one formatting nit (`dps/observability.py`), and ran the unit suite before committing (1 expected adaptation failure, 2825 passed). |
| `4e55da3` | main gained `64c46e3`, `144cc65` (docs only) after the merge started → merged again, clean. main has not moved since (`144cc65`). |

Hotspots kept both sides: pyproject extras (`scanner` + `tabular`/`synth-ml`), `dps/config.py` (M4 scanner block + M5 blocks),
`dps/db.py` TENANT_TABLES (scan tables + M5 tables), log allow-list, `dps/app.py` (scans router beside admin/reports/policy-store;
browser readiness probe + M5 key refresher in the lifespan), compose (browser-worker, demo-site, tokenizer-mock, Oracle, MongoDB;
profiles docs/sources/oracle/scanner/tokenizer/full; every host port and built image a variable), console (scanner pages + M5 pages,
one `dev:fixtures` mode chaining both fixture servers), smoke (scanner block + M3 block), contract, HANDOFF, README.
Migrations: M2 001–010, M3 020, M4 030–031, M5 040–045, each number once.

## 2. Adaptations (each with a failing test first)

| Commit | Adaptation | Failing-first evidence |
|---|---|---|
| `b6f7e82` | migration 031 `ADD COLUMN IF NOT EXISTS registered_hosts` (main DBs ran M5's 040, which adds it, before 030/031 ship; runner applies pending files out of order) | new `test_m4_files_apply_after_m3_and_m5_on_an_existing_database` failed on the old 031 (duplicate column), passes after |
| `d9a1752` | **SEC-5**: `_storage_state` uses main's `resolve_secret_ref(tenant_id, ref, settings)` (own scoping removed); `validate_scan(..., tenant_id=)` runs `check_secret_ref` at accept → 403 `secret_ref_foreign_tenant`, 403 `secret_ref_not_shared`, 422 `secret_ref_invalid`; `ScanAuth` takes `SECRET_REF_PATTERN` (`_shared/` works); cross-tenant e2e (403 at accept, `secret_unavailable` at load); console wording | `tests/scanner/test_scan_secret_refs.py` 19/22 failed (TypeError: 2-arg resolver call; mypy also flagged it after the merge); API + e2e tests |
| `e9682ef` | **SEC-2**: browser-worker reads the stack's tenant-scoped `dps-secrets` volume (no `./docker/secrets` bind mount / `DPS_SECRET_DIR` override); `demo-secrets` (now also profile `scanner`) copies the committed demo session to `default/dps/ui/demo-onboarding/` | `tests/test_compose.py` 2 tests failed first; live: file present in the volume, no top-level `dps/`, smoke scan with the ref succeeded |
| `5ffab35` | **M5 `@name[:vN]` policy refs on scans**: resolved at accept (stored spec pins the body), store codes 400/404/409 audited, evidence `policy.version`, `verify_warn_disallowed` mapped at accept/run, cancel-path finalize no longer raises on a refused body | API test failed (422 `policy_invalid`); evidence test failed; worker tests raised `CodedError` |
| `a3e6751` | **M5 DB keys, scope `scan`, every scans route** (walk built from OpenAPI): scan key passes, DB key without scan 403 everywhere, no key 401, cross-tenant 404/403, disabled key 401, audit names the DB key | passed on first run — no code change needed (`require_scope` + M5 `ChainKeyStore`); recorded as a guard test |
| `fb45a44` | **M5 posture for the browser-worker role**: `assert_production_posture(settings, role="browser-worker")` keeps every M2/M4/M5 rule but not those of components it never runs (API keys, CORS, OCR, tokenizer token) — before, a browser-worker sharing the app's prod config without those mounts refused to boot | `tests/scanner/test_browser_worker_posture.py` all failed first (no `role`; `amain` refused for the OCR key) |
| `27cf286` | **Main's discovery semantics in scanner classification** (`classify_fields`): shared dictionary (role names, address lines, doc numbers); contact value next to a channel field → `MIXED_PII`; a field showing MEDIUM+ findings → its entity / `FREE_TEXT` (never "not personal"); name rule moved to `dps/engine/detect/structural.py` and shared with discovery | `test_classify.py` failed first (old "Nominee name" threshold test; then `classify_fields` missing); live crawl test on a contact-preferences page |

Integration fixes found by the gates: `666efbf` Overview "personal data fields" showed every inventoried field (126) instead of
personal ones (102) → `layers.ui.personal_fields` (API + console tests failed first); `2ba0767` two merged tests adapted to M4 as
built (M3 demo-secrets host test now maps `/seed/ui`; M5 retention test inserts M4's `template NOT NULL`); `8f4c5b0` test: hosts
registered on the M5 Admin page decide what a tenant may scan (passed first run); `c354366` popup-realm *positive controls* wait up to
30 s (one timing miss under host load; no guarded assertion touched); `e75b133` M3 console screenshots retaken by the console tests;
`a0e8d26` HANDOFF §16 (merge, adaptations, traps, verification). Deviation log: `docs/superpowers/build/M4-DEVIATIONS.md`
"M4 pre-merge" (one entry per change). Contract §3.5 (posture roles) and §9 (scan accept codes, `layers.ui`) and `docs/technical/api-m4.md` updated.

## 3. Gates (lane stack `dps-wt-m4`, `--profile full`, images `m4` rebuilt after the last code change)

| Gate | Result | Log (`~/.cache/dps-build/s2/`) |
|---|---|---|
| ruff format --check / ruff check / mypy dps | clean (425 files / 0 / 150 files) | — |
| `pytest -q` (all services up) | 3413 passed, 40 skipped; 2 failed → fixed in `2ba0767` (pass) | gate-unit.log |
| `-m "docs or ocr"` (REQUIRE=1) | 139 passed, 2 skipped (host has no tesseract) | gate-docs.log |
| `-m "db or pg_source or s3"` | 402 passed; 2 console tests failed: one ran while the app was being recreated (h-scroll 1446 px), one needs the smoke's `bank-restore-rw` → both pass after the smoke (browser gate) | gate-db.log |
| `-m "oracle or mongo"` | 162 passed, 1 skipped (Mongo has no composite keys, by design) | gate-oramongo.log |
| `-m browser` | 166 passed, 27 skipped (tesseract); 1 unguarded positive-control timing miss → 3/3 and 7/7 on rerun, wait raised (`c354366`) | gate-browser.log |
| `-m tesseract` with `DPS_TEST_TESSERACT_CMD` = app image's tesseract | 27/27 scanner OCR capture tests passed (2 docs tests need a host binary) | gate-tesseract.log |
| `-m "not browser"` (REQUIRE=1, after every change) | 3254 passed, 8 skipped (ner/ml extras, host tesseract, by-design), 1 failed = known main flake below; module rerun 7/7 | gate-final-nonbrowser.log |
| frontend `npm ci && npm test && npm run build` | 42 node + 356 Vitest passed, build + check-bundle OK | gate-npm-*.log |
| smoke `DPS_SMOKE_SCANNER=1 DPS_SMOKE_REQUIRE_OCR=1 … --m3` | **52/52** (L1, documents + OCR, M2 jobs, M3 three-engine group 3/3 consistent, M4 scanner 11) | smoke.log |

## 4. Proofs (counts only; probes in `~/.cache/dps-build/reports/probe-s2/`)

- **D4** (`d4_scan.txt`): two live scans of the demo site through the API, synthesize and obfuscate, with the demo session ref:
  both succeeded, 21 pages, 63 fields, 51 personal, 9 risk flags, 3 captures, blocked POST 60 + GET 1, `non_get_allowed` 0,
  evidence `scan.submissions` 0 and `non_get_allowed` 0; demo site `/__received` `non_get`, `form_get`, `logout_hits`, `admin_hits`
  all **+0**; the smoke's scan +0 as well. Evidence `policy.version` "1" present.
- **Captures OCR** (`ocr.txt`, app image tesseract, `--network none`): filled_synthesize 12 + filled_obfuscate 12 per scan → 0 demo
  values; original/annotated/masked_capture → only `application_ref` ×2 on `/apply/review` (M4's known non-blocking N9: not a detected
  entity; identical to M4 verification r2/r3). Plus the 27 OCR capture tests above.
- **Logs** (`log_scan.txt`): 15 containers' logs → 0 demo values, 0 session-cookie hits; the scans' keys/seeds → 0 hits in 436 lines.
- **Console** (`console_walk.json`): 11 pages of M1–M5 × light/dark × 1440/390 = 44 views: 0 console errors, 0 5xx, no
  horizontal scroll at 390 px; headings render (Overview shows the scanner layer: 2 scans, 102 personal fields after `666efbf`).
- **Source vs target** (PG target of the smoke's M3 group, 20 000 rows): personal columns full_name/national_id/tax_id/email/mobile
  → 0 values found in the source column; date of birth same row 0.

## 5. Open items for the orchestrator / next stages

1. **Main flake (not M4):** `tests/connectors/test_synthesize_copula.py::test_surrogate_job_on_the_seeded_demo_customers` failed once
   in ~8 runs with 1 synthetic `national_id` equal to a demo-source value. Root cause: `tools/seed_model.py` draws the demo SSNs
   (`engine.surrogate("SSN")` → 9xx) and Aadhaars (leading 1) from the same never-issued ranges the D-A surrogates use; the run's
   own identifier check compares with the source, so such a run is review-required (never silently written). Fix belongs on main
   (seed demo ids from sub-ranges the surrogates never draw, e.g. SSN area 666, Aadhaar leading 0, or redraw a surrogate found in
   the source column) — expect it on main too (~1 in 8 runs).
2. The **worker** role still gets every app rule (`DPS_API_KEYS_FILE` required in prod); M5's OpenShift plan gives the worker no
   API-keys mount → F2/F-deploy should mount it or call `assert_production_posture(settings, role="worker")` with a worker rule set.
3. Scanner TLS-only egress (`http` start URLs when `DPS_EGRESS_TLS_REQUIRED`) is M5 T17 S5 (`netguard.check_scan_url`) → F1 (fsec).
4. A scan policy that maps an entity to `external_tokenize` fails closed in obfuscate fills (no tokenizer in the scanner's apply
   options) — acceptable, untested.
5. Lane `.env` and `.env.lane` already carried `DPS_TOKENIZER_MOCK_PORT=8424` (demo-site holds 8420); no other port variable needed.

## 6. Push

Not run. The project `CLAUDE.md` says never to push without the owner's explicit request, and the owner's request to this run did not
ask for a push; the orchestration's own autopush loop / LaunchAgent can push `m4` (`a0e8d26`, 15 new commits since `bdbdc0a`).
