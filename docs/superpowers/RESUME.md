# RESUME HERE — Data Privacy Services (DPS) development handoff

> Read this first in any new session, on any account. It lets a fresh agent (or engineer) continue the
> programme exactly where it stopped, without the original chat. It complements, and points into, the
> binding documents listed in §2. **Do not trust the snapshot in §4 blindly — run §3 first.**

**Last updated:** 2026-10-16 (release merge: every finish branch merged into main, version 1.0.0 committed; the tag waits for the final verification). **Active orchestration: workflow `wf_c6c9b6f6-603` "dps-finish-fast"** (script copy: `docs/superpowers/build/orchestration/dps-finish-fast.workflow.js`); the earlier `wf_c77f2c08-9ae` "dps-to-v1" and `wf_4dc1989c-3ce` were stopped at 16:45.
**Goal:** tag `v1.0.0` on `main` with every Definition-of-Done gate green (M0–M5 shipped).

---

## 0. TL;DR

- **What:** DPS is an enterprise data-obfuscation and synthetic-data platform for a large bank: one Privacy Engine
  (detect → plan → transform → verify → report), three entry points (L1 stateless API + documents, L2 database
  connectors PostgreSQL/Oracle/MongoDB, L3 UI scanner), a React "Privacy Console", hard rules: no LLM ever sees a raw
  value; keys are returned to the caller and never stored; the scanner never submits anything.
- **Merged since the snapshot below:** M5 is merged into main (`8378da7`) with integration fixes;
  M4 (`5959a75`) and its post-merge fixes; on 2026-10-16 the finish branches `fsec`, `fsecc`, `fimg`,
  `fos`, `fdemo`, `fdocs` and `fllm` (with `fpersona`, `fspec`, `fidcheck`) and the release commit
  `chore(release): v1.0.0` (HANDOFF §14.9, report `~/.cache/dps-build/reports/release.md`). Next: the
  final verification of the Definition of Done on the clean `full` stack, then `git tag -a v1.0.0`.
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
| Remote | `origin` = `https://github.com/NilenduGanguli/data-privacy-services` (private). **Owner instruction (2026-10-14): keep everything committed and pushed** — `main`, every lane branch and every tag. See §1.1. |
| Lane worktrees | `/Users/neelu/document_intelligence/dps-wt-{m3,m4,m4ui,m5,m5ui}` on branches `m3`, `m4`, `m4ui`, `m5`, `m5ui`; finish stage adds `dps-wt-fsec` (`fsec`) and `dps-wt-fdeploy` (`fdeploy`) |
| Per-worktree local files (git-ignored via `info/exclude`) | `.venv/` (Python 3.12), `.env` (compose image tag + host ports), `.env.lane` (exports `DPS_TEST_*` endpoints), `lane.sh` (wrapper: activates `.venv`, exports `.env.lane`, execs args) |
| Build reports (durable) | `~/.cache/dps-build/reports/` — `<LANE>-<GROUP>-{impl,review-rN,fix-rN}.md`, `<LANE>-verify-rN.md`, `I-<lane>*.md`, `F-*.md`, `release.md`, `final-verify-rN.md`, probe folders. (`/private/tmp` was wiped once — never rely on it.) |
| Orchestration script (as run) | `docs/superpowers/build/orchestration/dps-to-v1.workflow.js` |
| Workflow journal of the running orchestration (same Mac) | `~/.claude/projects/-Users-neelu-document-intelligence/d1f0660d-411d-4447-9d4e-0331e8df3a77/subagents/workflows/wf_c77f2c08-9ae/journal.jsonl` (one `result` line per finished agent; earlier runs: `wf_8168f882-c04` M0, `wf_b5f84136-a43` M1+M2, `wf_7d9b2cbe-385` lanes interrupted by an outage, `wf_6bca69a3-f4c` hardening) |
| Claude memory for this project | `~/.claude/projects/-Users-neelu-document-intelligence/memory/data-privacy-services.md` |
| Status script | `tools/dev/resume_status.sh` (read-only) |

### 1.1 Pushing to GitHub (owner instruction 2026-10-14: keep everything committed and pushed)

- `tools/dev/autopush.sh` pushes every local branch and every tag to `origin`, fast-forward only, after checking the
  new commits: author `nilendu.ganguli@gmail.com`, no `Co-Authored-By`/`Signed-off-by` trailer, and a **gitleaks**
  scan (Docker image `zricethezav/gitleaks`) with no finding. Reviewed false positives (test fixtures, doc examples)
  are listed by fingerprint in `.gitleaksignore`; anything new holds that branch. A diverged branch is never
  force-pushed. A tag is pushed only when its commit is already on a pushed branch. Log: `~/.cache/dps-build/autopush.log`.
- **Running now:** inside the orchestrating chat as a background loop (every 10 min). It stops when that chat ends.
- **To keep pushing without any chat**, the owner can install the LaunchAgent (agents may not install persistent
  jobs themselves): `cp tools/dev/com.nilendu.dps-autopush.plist ~/Library/LaunchAgents/ && launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.nilendu.dps-autopush.plist`
  (remove: `launchctl bootout gui/$(id -u)/com.nilendu.dps-autopush`). A new session can also simply run the loop:
  `while true; do tools/dev/autopush.sh; sleep 600; done` in a background shell.
- Commits: agents commit their own finished work continuously; uncommitted files in a worktree mean an agent is
  mid-task (never commit another agent's in-progress files). Remote branches of merged lanes stay on GitHub after the
  local worktrees are removed at release.
- First full push: 2026-10-14 01:04 — `main` `f7bc7eb`, `m3`, `m4`, `m4ui`, `m5`, `m5ui`, tags `v0.2.0-m1`, `v0.3.0-m2`
  (237 commits audited: all authored by the owner, no trailers, gitleaks clean after review).

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

## 4. Snapshot (re-check with §3)

### 4.00 Orchestration since 2026-10-14 17:00 IST — "dps-finish-fast" (owner: finish fast, max effort, max parallelism)

Docker Desktop raised to **16 CPUs / 36 GiB** (backup of the old settings: `~/.cache/dps-build/docker-settings-store.backup.json`).
Seven streams run at once (every agent at max effort, verifications as parallel panels by lens):

| Stream | Worktree / branch | Work | Then |
|---|---|---|---|
| S1 | main | M3 post-merge verification panel → tag `v0.4.0-m3` | after S2: merge `m4` → panel → tag `v0.5.0-m4` |
| S2 | `dps-wt-m4` / `m4` | finish the in-progress merge of main into `m4`, adapt the scanner (SEC-5, SEC-2, M5 keys/posture/policy refs, discovery semantics), panel review | feeds S1, S3b, S4b, S6b |
| S3 | `dps-wt-fsec` / `fsec` | security: 5 parallel finders by lens → adversarial refutation → T0 + T17 + confirmed fixes | after S2: merge `m4`, T8B + scanner SSRF/D4 |
| S4 | `dps-wt-fimg` / `fimg` | T24 images, compose `full`, smoke M5 block, flow report, demo client | after S2: scanner in images/compose/smoke |
| S5 | `dps-wt-fos` / `fos` | T25 OpenShift manifests | — |
| S6 | `dps-wt-fdemo` / `fdemo` | T26B deck builder + test, demo script draft | after S4 + S2: T26 demo seed + e2e demo run, deck from real outputs, Keynote QA |
| S7 | `dps-wt-fdocs` / `fdocs` | T27 docs tooling and drafts, polish items | — |
| Release | main | merge fsec, fimg, fos, fdemo, fdocs → T28 → final verification panel (8 lenses) → `v1.0.0`, worktree cleanup | |

Finish-stream ports: fsec 8416…, fimg 8417…, fdemo 8418…, fos 8419…, fdocs 8422… (each worktree's `.env`).
Reports: `~/.cache/dps-build/reports/S*-*.md`, `FINAL-*`, `release*.md`.

### 4.0 Latest progress — 2026-10-15 10:00 IST (newest first; the table in 4.1 is the older detailed snapshot)

| Stream | State |
|---|---|
| **M5** | lane verification PASS (r3, after fixing real national ids in synthetic targets); **merged into main** `8378da7`; post-merge verification r1 FAIL → fixed → **r2 PASS** (no milestone tag by design; ships as v1.0.0) |
| **M3** | all tasks done; lane verification r1 FAIL → fixed (incl. M3-B6 alias/UNPIVOT, B3r3, B4r3) → **r2 PASS** (`13875fd`); **merging into main now** (integration adapting MongoDB/Oracle paths to main's D-A and synthesize gates: ObjectId pseudonyms, identifier check by document path, tabular synthesis refused for Mongo) → then post-merge verification → tag `v0.4.0-m3` |
| **M4 (2026-10-15)** | **merged into main** `5959a75` (S1 stream, after S2 fix rounds 1–2 on `m4`); S2 r3's open findings fixed on main test-first (`932075a` stopped-run wording, the select-drawn masking commit after it); gates on the merged tree and the live stack in `~/.cache/dps-build/reports/S1-merge-m4.md` → post-merge verification panel → tag `v0.5.0-m4` |
| M4 (history) | **pre-merge in progress, now driven by workflow `dps-finish-fast`** (started 2026-10-14 ~16:50; earlier run `wf_4dc1989c-3ce` stopped mid-merge: main merged into `m4` in its worktree + scanner adapted + reviewed; the main workflow's M4 merge waits for `~/.cache/dps-build/PRE-m4.done`, programme plan §2.5.1); all tasks done, `m4ui` merged and wired; lane verification r1, r2 FAIL → fixed → **r3 PASS** (`bdbdc0a`); waits for the main lock → merge (incl. SEC-5) → verification → tag `v0.5.0-m4` |
| Finish / release | not started; begins after the M4 merge is verified |
| GitHub | everything pushed (`tools/dev/autopush.sh`); one held push on 2026-10-14 was a reviewed false positive (`DPS_FPE_ENABLED=true` in HANDOFF), now in `.gitleaksignore` |

Measured durations so far (for planning): a lane verification round 1–2 h; the M5 merge took ~10 h end to end
(its post-merge fix round alone ~7.5 h). Expect the M3 and M4 merges to take 3–10 h each, the finish stage 8–14 h,
release + final verification 3–8 h.

### 4.1 Detailed snapshot as of 2026-10-13 23:59 IST

| Stream | Done (branch head) | In progress now | Still to do |
|---|---|---|---|
| main (`3dc0540`) | M0, M1, M2 tagged; hardening verified PASS; programme plan; this guide | idle — waits for the first lane to finish | merges (§7), finish + release (§8) |
| **M5** `m5` (`0d43420`) | T1–T16B, SEC-4, D-A, console T18–T23 merged (`c238390`) and wired to the API (`5694280`); lane verification r1 FAIL → fixed `ad886e8` (no pseudonymised FK modelled; integer pseudonyms from bands no real key reaches); r2 FAIL → fixed `0d43420` (government-id surrogates from never-issued ranges + post-write check of every written identifier against the source) | **lane verification round 3** (screenshots being written to `docs/screenshots/m5/`) | if PASS: first merge onto main |
| **M3** `m3` (`529b1da`) | T1–T17, T19 (three-way join e2e, resume-after-kill on Oracle/Mongo `b725cc8`), MongoDB end to end (`30e0595`), G4 fixes (`188914f`, `ac710bf`); console **T18** (`529b1da` job groups + consistency verdict on Jobs/Job detail) | **T18b** (Oracle and MongoDB in the new-job wizard; `frontend/src/lib/engines.ts` being written) | G5 review, G6 (T20 log scan, T21 smoke/bench/docs), lane verification, merge, tag `v0.4.0-m3` |
| **M4** `m4` (`87a80af`) | T1–T20: everything through the scans API, G4 fixes (`6eac062` fenced idempotent finalize + guard enforces deny/allow on every hop; `0cf0140` discovery never clicks a denied target), T19 log scan (`21bd69c`), T20 browser-worker image + compose scanner profile (`87a80af`); console T21–T23 done on `m4ui` (`e7a5188`, review PASS) | G5 review (T19, T20) | merge `m4ui` + wire, G7 (T24 smoke, T25 docs), lane verification, merge, tag `v0.5.0-m4` |
| Finish / release | — | — | F1 security ∥ (F2 deploy → F3 demo + deck) → merge + F4 polish + F5 docs → F6 release + final verification → `v1.0.0` |

**Open findings carried into lane verification** (each must be fixed — failing test first — before its lane merges;
"status" is what is known at the snapshot; the lane verifier re-checks every row):

| Id | Lane | Status | Finding | Fix direction |
|---|---|---|---|---|
| M5-V2 | M5 | fix committed `0d43420`, re-verifying (r3) | synthesize jobs wrote 0–3 real national ids (NINO etc.) per run: government ids without a generator fell back to a keyed derangement of the issued space | never-issued ranges per id type; post-write identifier check → review required on any hit |
| M3-B6 | M3 | open | `selection.py` `_check_query_outputs` bypassed by FROM-clause column re-mapping (PostgreSQL alias column list `FROM contacts AS c(a,b,c)`, Oracle `UNPIVOT`): e-mails land under a kept column; run passes "verified" | a table counts as the object only without a column alias list / UNPIVOT / PIVOT, else refuse (`selection_column_remap`) or treat outputs as unknown; tests both dialects + e2e source-vs-target |
| M3-B3r3 | M3 | open | `consistency.py` `target_key`: one PostgreSQL table spelled `t` and `public.t` counts as two distinct targets | resolve the default owner (`public`) before building the key, as `targets._owner` does; test |
| M3-B4r3 | M3 | open | pymongo DEBUG command logging (`pymongo.command`, `.connection`, `.serverSelection`, `.topology`) writes customer values when `DPS_LOG_LEVEL=DEBUG`; the M2 log gate is flaky | cap pymongo (and oracledb) loggers in `observability._THIRD_PARTY_LEVELS` at WARNING regardless of `DPS_LOG_LEVEL`; deterministic log-scan test at DEBUG |
| M3-G3-B1/B2 | M3 | probably fixed in `30e0595` (segment-aware paths, nested drift searched) — confirm | nested PII under a field sampled as scalar; `[digits]` in a field name breaking paths | — |
| M4-R3-B1 | M4 | open | a discovery click whose handler routes to a denied path (in-document `location.hash`, `history.pushState`) runs that route while the records say "skipped / deny" | record such pages as visited-then-denied (`denied_after_click`) and stop interacting with them, or block in-document navigation to denied routes via the init script; counting/route test |
| M4-G2-B1 | M4 | confirm | popups opened while the first `arm` runs escape the lock; their worker can send the fill value over `wss://` | register `LOCK_JS` before the page snapshot; HTTPS counting-server test → 0 upgrades |
| M4-G2-B2 | M4 | confirm | short dates with unusual separators survive `redact_url`/`template` | any single non-alphanumeric separator; tests with both classifiers |
| M4-G3-B4/B5 | M4 | open | text under `display: contents` parents and attribute-drawn strings (placeholder, value, alt/title, `attr()` content) missing from the capture census → visible in "masked" captures | fix `_TEXT_HELPERS_JS` / `CENSUS_JS`; OCR masked PNGs → 0 source values |
| SEC-5 | M4 merge | open | scanner resolves storage-state secrets with its own copy of tenant scoping | use main's `resolve_secret_ref(tenant_id, ref, settings)`; validate at ScanSpec accept; cross-tenant test |

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
message; deviation log entry). Status markers: ✅ done · ⏳ in progress at the snapshot · ⬜ not started.

### 6.1 M3 — Oracle + MongoDB (`dps-wt-m3`, plan `…-m3-oracle-and-mongodb.md`)
- ✅ G4 (T15, T16, T17, T19): one customer space across PG/Oracle/Mongo; consistency check (counts only) + API +
  migration 020; three-way join e2e; resume-after-kill on Oracle and Mongo. Review r3 FAIL items carried (M3-B3r3, M3-B4r3).
- ✅ T18 console job groups + consistency panel (mockups 07, 03). ⏳ **T18b** Oracle and MongoDB in the new-job wizard
  (mockup 06): connection forms, owner listing, JSON filter step; the D3 typed confirmation never bypassed.
- ⬜ G6 (T20 M3 log-scan test; T21 smoke extension, benchmark, HANDOFF, README, API reference, final gates).
- ⬜ Lane verification — M3 DoD + programme plan §2.2; first fix M3-B6, M3-B3r3, M3-B4r3; per-column source≠target
  check on every sensitive column of every Oracle/Mongo target (0 survivors).

### 6.2 M4 — UI scanner (`dps-wt-m4`, plan `…-m4-ui-scanner.md`)
- ✅ T1–T20 (incl. G4 fixes: fenced idempotent finalize, deny/allow enforced by the guard on every hop). ⏳ G5 review.
- ⬜ Merge `m4ui` (T21–T23, review PASS) into `m4`, wire to the real scans API, browser check vs mockups 12 and 04.
- ⬜ G7 (T24 smoke extension + compose-level scanner gate; T25 documentation as built).
- ⬜ Lane verification — live scan of the demo onboarding site; parallel view in both modes; captures OCR-checked;
  demo site `/__received` non-GET count **0**; first fix M4-R3-B1, M4-G3-B4/B5 and confirm M4-G2-B1/B2.

### 6.3 M5 — hardening + showcase (`dps-wt-m5`, plan `…-m5-hardening-and-showcase.md`)
- ✅ T1–T16B, SEC-4, D-A, console T18–T23 merged and wired. ⏳ Lane verification round 3 (after the national-id fix).
- **Deferred to the finish stage (§8):** T0 (contract-surface test), T8B (T3 labelling in L2 + L3), T17 (security
  review), T24 (images, compose `full`, smoke M5 block, `flow_report.py`, `demo_client.py`), T25 (OpenShift), T26
  (demo seed + e2e demo + script), T26B (deck), T27 (docs), T28 (release).

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
