# RESUME HERE — Data Privacy Services (DPS) development handoff

> Read this first in any new session, on any account. It lets a fresh agent (or engineer) continue the
> programme exactly where it stopped, without the original chat. It complements, and points into, the
> binding documents listed in §2. **Do not trust the snapshot in §4 blindly — run §3 first.**

**Last updated:** 2026-10-13 22:00 IST by the orchestrating session (chat `d1f0660d…`, workflow `wf_c77f2c08-9ae`).
**Goal:** tag `v1.0.0` on `main` with every Definition-of-Done gate green (M0–M5 shipped).

---

## 0. TL;DR

- **What:** DPS is an enterprise data-obfuscation and synthetic-data platform for a large bank: one Privacy Engine
  (detect → plan → transform → verify → report), three entry points (L1 stateless API + documents, L2 database
  connectors PostgreSQL/Oracle/MongoDB, L3 UI scanner), a React "Privacy Console", hard rules: no LLM ever sees a raw
  value; keys are returned to the caller and never stored; the scanner never submits anything.
- **Done and tagged on main:** `v0.1.0-m0` (engine, L1 API, Playground), `v0.2.0-m1` (documents, true PDF redaction,
  schema synthesis), `v0.3.0-m2` (metadata DB, job queue, PG connector, four write modes, evidence, wizard), plus
  verified hardening (SEC-2, SEC-3 and four fixes).
- **In flight (2026-10-13):** three milestone lanes in git worktrees — M3 (Oracle + MongoDB), M4 (UI scanner), M5
  (synthetic engine + hardening) — then serialised merges onto main, a finish stage (security review, images +
  OpenShift, demo + deck, docs) and the release.
- **Next action for a new session:** §5.

## 1. Where everything lives

| What | Where |
|---|---|
| Main repo (branch `main`) | `/Users/neelu/document_intelligence/data-privacy-services` — its own git repo, nested inside `/Users/neelu/document_intelligence` (a **different** repo; never run git against the parent) |
| Remote | `origin` = `https://github.com/NilenduGanguli/data-privacy-services` (private). `origin/main` = `48d9425` (M0 only). **Push only when the owner explicitly asks.** |
| Lane worktrees | `/Users/neelu/document_intelligence/dps-wt-{m3,m4,m4ui,m5,m5ui}` on branches `m3`, `m4`, `m4ui`, `m5`, `m5ui`; finish stage adds `dps-wt-fsec` (`fsec`) and `dps-wt-fdeploy` (`fdeploy`) |
| Per-worktree local files (git-ignored via `info/exclude`) | `.venv/` (Python 3.12), `.env` (compose image tag + host ports), `.env.lane` (exports `DPS_TEST_*` endpoints), `lane.sh` (wrapper: activates `.venv`, exports `.env.lane`, execs args) |
| Build reports (durable) | `~/.cache/dps-build/reports/` — `<LANE>-<GROUP>-{impl,review-rN,fix-rN}.md`, `<LANE>-verify-rN.md`, `I-<lane>*.md`, `F-*.md`, `release.md`, `final-verify-rN.md`, probe folders. (`/private/tmp` was wiped once — never rely on it.) |
| Orchestration script (as run) | `docs/superpowers/build/orchestration/dps-to-v1.workflow.js` |
| Workflow journal of the running orchestration (same Mac) | `~/.claude/projects/-Users-neelu-document-intelligence/d1f0660d-411d-4447-9d4e-0331e8df3a77/subagents/workflows/wf_c77f2c08-9ae/journal.jsonl` (one `result` line per finished agent; earlier runs: `wf_8168f882-c04` M0, `wf_b5f84136-a43` M1+M2, `wf_7d9b2cbe-385` lanes interrupted by an outage, `wf_6bca69a3-f4c` hardening) |
| Claude memory for this project | `~/.claude/projects/-Users-neelu-document-intelligence/memory/data-privacy-services.md` |
| Status script | `tools/dev/resume_status.sh` (read-only) |

Host ports per stack (from each worktree's `.env`; compose host ports are variables with the contract defaults):

| Stack | app | dps-db | source-pg | MinIO / console | Oracle | Mongo | OCR mock | demo site |
|---|---|---|---|---|---|---|---|---|
| main | 8400 | 5441 | 5442 | 9010 / 9011 | 1522 | 27018 | 5016 | 8410 |
| m3 | 8413 | 5453 | 5454 | 9040 / 9041 | 1523 | 27019 | 5036 | 8423 |
| m4 | 8414 | 5463 | 5464 | 9050 / 9051 | 1524 | 27020 | 5046 | 8420 |
| m5 | 8415 | 5473 | 5474 | 9060 / 9061 | 1525 | 27021 | 5056 | 8425 |
| fsec / fdeploy | 8416 / 8417 | 5483 / 5493 | 5484 / 5494 | 9070-71 / 9080-81 | 1526 / 1527 | 27022 / 27023 | 5066 / 5076 | 8426 / 8427 |
| console sub-lanes | Vite dev servers m4ui 5184, m5ui 5185 (no Docker) | | | | | | | |

## 2. Read-first documents (binding, in this order)

1. `docs/superpowers/plans/2026-10-13-remaining-work-plan.md` — **programme plan**: state inventory, work packages
   WP-H/M3/M4/M5/I/F, decisions D-A..D-D, schedule, operational rules, verification matrix, risks.
2. `docs/specs/2026-10-11-data-privacy-services-design.md` — the spec (§12 decision record D1–D14).
3. `docs/superpowers/plans/2026-10-11-m1-m5-contract.md` — engineering contract (names, DDL, migrations ranges,
   routes, settings, ports, compose; §0 amendment 2026-10-12 "lane-safe compose").
4. Milestone plans `docs/superpowers/plans/2026-10-11-m{3,4,5}-*.md` — TDD task lists with code; each ends with a
   Definition of Done (DoD).
5. Deviation logs `docs/superpowers/build/M{0..5}-DEVIATIONS*.md` — **where the code differs from the plans; the code
   as built wins.** Also the per-lane **task ledger** (one `## Task N` section per finished task).
6. `HANDOFF.md` — build/deploy facts (kept current with every build/deploy change).

## 3. How to find the current state (do this first, every time)

```bash
/Users/neelu/document_intelligence/data-privacy-services/tools/dev/resume_status.sh
```
It prints: worktrees; tags; main's last commits, uncommitted files and whether a merge is in progress; for every lane
branch whether it is merged, its last commits, its **task ledger** (from its deviation log) and its uncommitted files;
the newest review/verification verdicts in `~/.cache/dps-build/reports`; Docker compose projects; activity in the last
10 minutes.

Interpretation rules:
- A task is **done** when it has a `## Task N` section in the lane's deviation log **and** a commit on the branch.
  A group is **accepted** when its newest `*-review-rN.md` says PASS — or, after round 3, its FAIL items are carried
  into the lane verification (that is by design; they must be fixed before merge).
- **Uncommitted files in a worktree = work of an agent that is running or that died.** If files changed in the last
  10 minutes, an agent is probably still working — do not touch that worktree. Otherwise treat the files as a starting
  point: review, finish, test and commit them; never discard them silently.
- Milestone tags on main mark merged + verified milestones: `v0.4.0-m3`, `v0.5.0-m4` (M5 has no milestone tag; it
  ships as `v1.0.0`).
- If `docker compose ls` fails: `open -a Docker` and wait for `docker info`.

## 4. Snapshot as of 2026-10-13 22:00 IST (re-check with §3)

| Stream | Done | In progress / next | Remaining after that |
|---|---|---|---|
| main | M0, M1, M2 tagged; hardening (SEC-2 `e25209f`, SEC-3 `34f4aa3`, N3, wizard step-5 server check, length-safe methods, fallback label, `length_overflow` text `4615e3d`) **verified PASS**; programme plan; this guide | idle until the first lane merges | merges, finish, release |
| **M3** `m3` | T1–T17 (Oracle connector + writer + pitfalls, pipeline wiring, MongoDB rules/filters/connector/writer, engine bridge, conformance on Oracle+Mongo, three-engine customer space, consistency check + API, migration 020) | G4 review/fix (T15, T16, T17, T19 — T19 = three-way join e2e + resume-after-kill on Oracle/Mongo) | T18, T18b (console), T20, T21, lane verification |
| **M4** `m4` (+`m4ui`) | T1–T18 (URL toolkit, scan tables 030–031, models, store, request guard + scope proxy, demo site, browser runtime, classification, risk flags, personas, captures, fill, crawler, no-submission proof, scan handlers + evidence, browser worker, scans API); console T21–T23 done on `m4ui` (review PASS) | G4 fix round (finalize fencing, deny/allow on scanner-caused navigations) | T19, T20, merge `m4ui` into `m4` + wire to API, T24, T25, lane verification |
| **M5** `m5` (+`m5ui`) | T1–T8 + SEC-4 (D-B) + D-A + L2 reveal for `external_tokenize`; T9–T16B (DB keys, tenants/quotas, admin API, roll-ups/reports, retention, evidence PDF, policy store, posture tightening, per-tenant LLM exposure); console T18–T23 merged into `m5` (`c238390`) | wiring the console to the real API | lane verification |
| Integration | — | — | merge order = lane completion order (expected M5 → M3 → M4) |
| Finish / release | — | — | F1 security ∥ (F2 deploy → F3 demo + deck) → merge + F4 polish + F5 docs → F6 release + final verification |

**Open findings carried into lane verification (must be fixed before merge; each needs a failing test first):**

| Id | Lane | Finding | Fix direction |
|---|---|---|---|
| M3-B6 | M3 | `selection.py` `_check_query_outputs` bypassed by FROM-clause column re-mapping: a PostgreSQL alias column list (`FROM contacts AS c(a, b, c)`) or an Oracle `UNPIVOT` puts e-mails under a kept column name; run passes with a "verified" pack | in `_query_sources` a table counts as the object only when it is referenced without a column alias list / UNPIVOT / PIVOT; otherwise refuse the query (`selection_column_remap`) or treat every output column as unknown → `needs a decision`; tests for both dialects + e2e source-vs-target |
| M3-G3-B1 | M3 | `engine/batch.py` `_unclassified`: a top-level Mongo field sampled as a scalar but holding a sub-document/array in other documents is never searched → nested PII passes | run the detector over every string leaf of such fields (by actual runtime type, not sampled shape); test with mixed-shape documents |
| M3-G3-B2 | M3 | a field name containing `[digits]` breaks the path machinery → a planned column silently untransformed | escape such names in `docpaths.py` (bracket-quoted segments) or refuse the collection at discovery with a clear code; test |
| M4-G2-B1 | M4 | popups opened while the first `arm` runs are neither snapshotted nor locked; their worker sends the fill value over `wss://` (D4) | register `LOCK_JS` before taking the page snapshot on the first `arm`; HTTPS counting-server test (continuous `about:blank` popups each with a worker) expecting 0 upgrades / 0 `/leak-*`, with an unguarded positive control |
| M4-G2-B2 | M4 | short dates with separators other than `- . / space` survive `redact_url` and the persisted `template` | let any single non-alphanumeric character separate date parts; add rows to `test_redact_url_hides_…`, `test_the_persisted_template_is_value_free`, `test_a_page_record_refuses_a_raw_template` with both classifiers |
| M4-G3-B4 | M4 | text under a `display: contents` parent is neither classified nor in the capture census → shows in "masked" captures | fix `visible`/`textOk` in `dom.py` `_TEXT_HELPERS_JS` and `CENSUS_JS`; OCR probe on masked PNGs → 0 source values |
| M4-G3-B5 | M4 | strings the browser draws from attributes (placeholder, value, alt/title tooltips rendered, `::before` content via `attr()`) are not in the census | add them to `CENSUS_JS` and to classification; OCR probe |
| M4-G4-B1 | M4 | `finalize_scan` neither fenced nor idempotent → two contradictory evidence packs after cancel/crash during finalize | move the job into the finalize phase through the fence (`advance_job(phase=finalize, state=running)`; stop on False; `cancel` answers `job_finalizing`), make artefacts/evidence idempotent per job; crash/cancel tests |
| M4-G4-B2 | M4 | deny/allow lists not enforced on navigations the scanner itself causes (SPA discovery clicks on `href="#"`/`javascript:`/`role=link`, redirect hops); records claim "skipped / deny" while `/logout` was hit | enforce the path verdict in the guard/proxy for every request a scan causes (not only in `ScanRunner._consider`); never click elements whose resolved navigation is denied; counting-server test with `deny=("/logout","/admin")` → 0 hits |
| SEC-5 | M4 merge | scanner resolves storage-state secrets with its own copy of tenant scoping | at the M4 merge use main's `resolve_secret_ref(tenant_id, ref, settings)`; validate `storage_state_ref` at ScanSpec accept; cross-tenant test |

## 5. How to continue

### 5.1 If the orchestrating chat is still alive
The workflow `wf_c77f2c08-9ae` runs everything to `v1.0.0` by itself (with automatic retries). Just wait; ask it for status.
Same session only: `Workflow({scriptPath: "<script path printed at launch>", resumeFromRunId: "wf_c77f2c08-9ae"})`
replays finished agents from cache after an interruption.

### 5.2 New chat or new account (the old run is gone)
Workflow runs cannot be resumed across sessions. When the old chat ends, its running agents end too.
1. **Make sure nothing is still running:** `tools/dev/resume_status.sh` — no files modified in the last 10 minutes and
   no other Claude session working in these paths. If something is active, wait or ask the owner.
2. **Recover uncommitted work** in each worktree: read the diff, finish it, run the lane gates (§9), commit with an
   explicit pathspec. Read the newest report in `~/.cache/dps-build/reports` for that lane to know what the agent was doing.
3. **Decide what is left** per lane from the task ledger (§3) against §6 below.
4. **Relaunch the orchestration** (needs the owner's opt-in to multi-agent workflows, e.g. "use a workflow" / ultracode):
   copy `docs/superpowers/build/orchestration/dps-to-v1.workflow.js` and edit only the data, not the machinery:
   - `M3_GROUPS`, `M4_GROUPS`, `M5_GROUPS`: delete groups whose tasks are done and accepted; for a group that is
     half-done, keep it (its prompt tells the agent to continue from the current state).
   - If a console sub-lane is already merged into its lane (e.g. `m5` has `merge: M5 console`), replace that lane's
     `uiLane(...)` promise with `Promise.resolve({ verdict: 'PASS', merged: true })` and the `mergeUi` step becomes a
     "wire the console to the API" step (its prompt already covers wiring).
   - Already-merged lanes: remove them from the lane list; keep `withMain` + `integrate` for the rest.
   - Add the §4 open findings to each lane's verification `carried` list (or as an extra first group).
   - Keep `run()` (retries), `judge()`, `withMain()` (serialised merges), the finish and release sections.
   Launch with the Workflow tool (`script` inline) and record the new run id at the top of this file.
5. **Without workflows:** execute the same steps sequentially with single subagents (Agent tool) or by hand: for each
   group implement → independent review → fix (≤ 2 rounds) → lane verification → merge (§7) → finish (§8).

## 6. Remaining work in detail

Execute plan tasks exactly as their milestone plan writes them (TDD: failing test first; gates; the plan's commit
message; deviation log entry). Below: what is left and what proves it.

### 6.1 M3 — Oracle + MongoDB (`dps-wt-m3`, plan `…-m3-oracle-and-mongodb.md`)
- **G4 (T15, T16, T17, T19)** — review/fix. Acceptance: one customer space across PG/Oracle/Mongo; consistency check
  returns overlap > 0 for every pair with counts only; T19 three-way join through API + worker; resume-after-kill on
  Oracle and Mongo for every write mode.
- **G5 (T18, T18b)** — console: job groups + consistency panel on Jobs (mockup 07) and Job detail (mockup 03); Oracle
  and MongoDB in the new-job wizard (mockup 06): connection forms, owner listing, JSON filter step; the D3 typed
  confirmation can never be bypassed. Browser check vs mockups.
- **G6 (T20, T21)** — M3 log-scan test; smoke extension, benchmark, HANDOFF, README, API reference, final gates.
- **Lane verification** — M3 DoD + programme plan §2.2: per-column source≠target check on every sensitive column of
  every Oracle/Mongo target (0 survivors); fix M3-B6, M3-G3-B1, M3-G3-B2 first.

### 6.2 M4 — UI scanner (`dps-wt-m4`, plan `…-m4-ui-scanner.md`)
- **G4 fix** — M4-G4-B1 (finalize fencing/idempotency), M4-G4-B2 (deny/allow on scanner-caused navigations), then re-review.
- **G5 (T19, T20)** — M4 log scan; browser-worker image (`mcr.microsoft.com/playwright:v1.55.0-noble` base) and compose services.
- **Merge `m4ui`** (T21–T23 done) into `m4`, wire to the real scans API, browser check vs mockups 12 and 04.
- **G7 (T24, T25)** — smoke extension + compose-level scanner gate; documentation as built.
- **Lane verification** — live scan of the demo onboarding site; parallel view in synthetic and obfuscated mode;
  captures OCR-checked (masked values unreadable); demo site `/__received` POST count **0**; fix M4-G2-B1/B2,
  M4-G3-B4/B5 first.

### 6.3 M5 — hardening + showcase (`dps-wt-m5`, plan `…-m5-hardening-and-showcase.md`)
- **Wire the merged console** (T18–T23) to the real API; Vitest + build; browser walk.
- **Lane verification** — in-scope DoD; Overview, Reports & audit (roll-ups, audit filters, CSV), Evidence pack
  (synthetic section, PDF), Admin (key shown once, rotate/disable, tenants/quotas, posture), Policies (versions);
  one L2 synthesize job: exact-row matches 0, no sensitive source value or real key in the target (D-A).
- **Deferred to the finish stage:** T0 (contract-surface test), T8B (T3 labelling in L2 + L3), T17 (security review),
  T24 (images, compose `full`, smoke M5 block, `flow_report.py`, `demo_client.py`), T25 (OpenShift), T26 (demo seed +
  e2e demo + script), T26B (deck), T27 (docs), T28 (release).

## 7. Integration onto main (WP-I) — one lane at a time

For each finished lane (completion order):
1. `main` clean, no merge in progress → `git merge --no-ff <lane> -m "merge: <LANE> into main"`. Keep both sides of
   every conflict. Hotspots: `pyproject.toml` extras (`connectors`, `scanner`, `tabular`, `synth-ml`, `deck`),
   `dps/config.py`, `dps/posture.py`, `dps/app.py`, `docker-compose.yml` (profiles `docs`, `sources`, `oracle`,
   `scanner`, `tokenizer`, `full`; every host port/image a variable), `Dockerfile`, `tests/conftest.py`,
   `frontend/src/{App.tsx,components/Layout.tsx,lib/api.ts,lib/types.ts}`, contract, HANDOFF, README, `.env.example`.
   Migration ranges: M2 001–019, M3 020–029, M4 030–039, M5 040–059 — no duplicates.
2. Adapt lane code to main's newer behaviour (each with a test): SEC-2 tenant-scoped secret refs (lane demo secrets and
   seeds move under the demo tenant), SEC-3 endpoint-param list covers Oracle/Mongo/S3, the discovery fix (mixed-PII
   columns never auto-kept; post-write kept-column hits → review required) for Oracle/Mongo snapshots and scanner
   classification, SEC-5 for M4.
3. Gates on the merged tree (fresh venv with all extras; every integration marker merged so far on one main stack;
   frontend; smoke). Independent verification; then tag `v0.4.0-m3` / `v0.5.0-m4` (M5: no tag).

## 8. Finish and release

| Step | Content (M5 plan tasks) | Proof |
|---|---|---|
| F1 security (`dps-wt-fsec`) | T0 contract-surface test; T8B; T17 S1–S13; adversarial sweep (auth/scopes, tenancy, injection incl. selections in 3 engines + Mongo filters + regexes + CSV + PDF, SSRF scanner/tokenizer/OCR/Stellar, secrets & logs, crypto & key custody, D4); confirm SEC-2..SEC-5 | `docs/security/2026-10-11-m5-security-review.md` (finding → fix → test); `tests/security/*` green |
| F2 deploy (`dps-wt-fdeploy`) | T24 images (`--platform linux/amd64`, arbitrary UID, read-only root), compose `full` + tokenizer-mock, smoke M5 block, `flow_report.py`, `demo_client.py`; T25 OpenShift kustomize base/components/dev+prod overlays (Wega layout, release placeholders) | `kustomize build` dev + prod and kubeconform clean; images build; smoke all checks |
| F3 demo (after F2, same worktree) | T26 `demo_seed.py`, `e2e_demo.py --console`, `docs/demo/DEMO-SCRIPT.md`, console page test; T26B deck `docs/demo/dps-showcase.pptx` built by `tools/build_deck.py` from measured outputs | e2e demo all steps PASS < 10:00; deck test; **Keynote** export to PDF and page review (owner uses Keynote: no native chart parts, Helvetica Neue/Arial) |
| Merge + F4 + F5 (main) | merge `fsec`, `fdeploy`; polish (Policies stale preset response, stale demo group file, `console_check` overwriting tracked shots, cheap non-blocking items from reports; the rest → CHANGELOG "Known limitations"); T27 HANDOFF final shape (generated settings table), README, `docs/technical/api-m5.md`, `CHANGELOG.md` | `tests/test_handoff.py` |
| F6 release (main) | T28: version 1.0.0 (pyproject, `dps/__init__.py`, frontend package + lock); clean stack `docker compose --profile full down -v && … up --build -d`; DoD 1–7; HANDOFF "Last verified"; commit `chore(release): v1.0.0` | independent final verification (≤ 3 rounds) → `git tag -a v1.0.0`; local images tagged `1.0.0`; worktrees removed after confirming branches merged; **no push** unless the owner asks |

## 9. Test and verification cheat sheet

Lane (always via the wrapper, from the worktree):
```bash
./lane.sh ruff format --check dps tests tools && ./lane.sh ruff check dps tests tools && ./lane.sh mypy dps
./lane.sh pytest -q                                  # unit (service tests skip without containers)
docker compose --profile <needed> up -d              # reads .env: lane ports + image tag; never --wait (one-shots)
DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -m "db or pg_source or s3"   # M2 regression
DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -m "oracle or mongo"        # M3
DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -m browser                  # M4
DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q -m "docs or ocr"            # M1
./lane.sh python tools/smoke_test.py
cd frontend && npm ci && npm test && npm run build
docker compose down                                   # free memory when the group is done
```
Main: same commands from the main checkout with `source .venv/bin/activate` and the `DPS_TEST_*` defaults of contract
§12.2 (ports 8400/5441/5442/9010/5016/1522/27018/8410). Full release stack: `docker compose --profile full up --build -d`,
then poll `curl -fsS localhost:8400/readyz` until `"ready": true` (Oracle Free needs ~3 min on first start, ~2.7 GB).

Proofs that every verifier must produce (programme plan §5 rule 6 / §6):
- **Source vs target:** for every target written (PG/Oracle/Mongo/files/synthetic), count values of each sensitive
  column that equal a source value of the same column → must be 0 (counts only, never print values).
- **D4:** the demo site's `/__received` counters (and a counting server for probes): POSTs/non-GET == 0.
- **Redaction:** redacted PDF has 0 extractable characters and an OCR re-read finds 0 originals; masked scanner
  captures OCR-checked.
- **Logs:** scan every container's logs for the demo values used and key material → 0 hits;
  `dps_llm_raw_pii_sent_total` 0 (per tenant from M5); audit rows value-free.
- **Console:** private Playwright (`playwright==1.55.0` in a venv under `~/.cache/dps-build/pw-<name>`), every page vs
  `docs/design/mockups/01..12`, 0 console errors, no horizontal scroll at 390 px.
Other tools: `tools/demo_m2.py` (10-table PG demo), `tools/bench_connector.py`, `tools/eval_detectors.py`,
`tools/console_check.py`, `tools/reveal_rows.py`; M5 adds `tools/demo_seed.py`, `tools/e2e_demo.py`, `tools/flow_report.py`,
`tools/build_deck.py`.

## 10. Knowledge you need

**Architecture.** One image, roles `app` (API + console, port 8400), `worker` (L2 jobs, ops 9400), `browser-worker`
(L3, Chromium, ops 9401), `migrate` (DDL as `dps_owner`); metadata PostgreSQL schema `dps` with tenant RLS; durable
Postgres queue (SKIP LOCKED, leases, reaper); artefacts in a local dir or S3/MinIO; LLM via Stellar (COIN OAuth2) /
Vertex / stub — **placeholders only**; OCR via the Azure Read v3.2 contract (mock in dev) or Tesseract.

**Owner decisions** (spec §12): D1 repo nested in `document_intelligence`; D2 stateless key custody (DEK returned,
optional RSA wrap, caller passes it back; DPS stores no key — the L1 idempotency cache keeps no key material);
D3 L2 write modes `in_place`, `append` (PK strategy generate/offset/token + collision pre-check), `table`, `files`,
in-place/append need typed confirmation + admin on production connections; D4 the scanner never submits anything and
renders a parallel view (original vs filled with synthetic or obfuscated data) + inventory + obfuscated captures;
D5–D14 at spec defaults (jurisdictions US/CA/MX/IN/UK+EU, NER EN/ES, Stellar/Vertex/stub, surrogates + copula with
MOSTLY optional and SDV excluded, AES-SIV tokens by default with FF1 via `fastfpe` behind a license review —
`DPS_FPE_ENABLED`, OpenShift via kustomize, 30-day retention, X-API-KEY with tenant grants + scopes).

**Engineering decisions** (programme plan §3): D-A synthetic tables never carry linkable real identifiers;
D-B external tokens HMAC-bound to the caller's key before detokenization; D-C scanner network policy below the page
(scope proxy every hop, beacon/worker/socket APIs removed on filled pages, abort-all before close, fail-closed
captures); D-D nothing pushed without the owner's go-ahead.

**Security log:** SEC-1 COIN token mint verified TLS by default (insecure only by explicit, posture-gated opt-in) —
main; SEC-2 tenant-scoped secret refs — main `e25209f`; SEC-3 admin for any production-connection change, endpoint
changes need credentials — main `34f4aa3`; SEC-4 external-token binding — `m5`; SEC-5 scanner secret-ref scoping —
at the M4 merge.

**Lessons and traps.**
- A "verified / 0 leaks" label must be backed by a source-vs-target comparison (stage A found raw PII in a "verified"
  UAT copy: discovery auto-kept mixed-type and unnamed PII columns; fixed on main `89a9e98`).
- Always use `./lane.sh`; two runs without `.env.lane` once hit main's stack.
- `/private/tmp` can be wiped; reports live in `~/.cache/dps-build/reports`. Workflow journals survive in `~/.claude`.
- A network outage killed 34 agents once; agents must resume from git state, never redo or discard.
- Docker Desktop may need `open -a Docker`; never `docker system prune`; never touch another compose project.
- Oracle Free: ~3 min first start, 2.7 GB; run it only where needed. Never `docker compose --wait` with one-shot services.
- Never symlink `node_modules` into worktrees (`npm ci` in each).
- Kustomize lessons (from the Wega project): generator/component ordering, JSON6902 patches guarded by tests,
  warn-placeholder + `--fail-on-warn` release overlays; Oracle Free needs uid 54321.
- Decks: Keynote drops native chart parts and lacks Cambria Bold — draw charts as shapes, QA via Keynote PDF export.

**Owner preferences.** Plain language; every commit authored `NilenduGanguli <nilendu.ganguli@gmail.com>` with **no
`Co-Authored-By` or any trailer** (check for a local `.git/config` override before the first commit); conventional
commits; explicit-pathspec commits; ask before pushing or any outward-facing action; raise decisions explicitly.

## 11. Keeping this file current
Update §4 (snapshot), the "Last updated" line and §5.1 (run id) whenever a lane merges, a stage finishes or a new
orchestration run starts. The status script (§3) is the source of truth between updates.
