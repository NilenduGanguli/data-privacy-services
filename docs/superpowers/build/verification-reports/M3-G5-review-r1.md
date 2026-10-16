# M3 G5 review, round 1: Tasks 18 and 18b (console)

**Reviewer:** independent review agent · **Date:** 2026-10-14 · **Worktree:** `/Users/neelu/document_intelligence/dps-wt-m3` (branch `m3`)
**Commits reviewed:** `529b1da` (T18 job groups), `276ea3a` (T18b Oracle/MongoDB wizard), `436ae69` (T18 browser test). HEAD `436ae69`. The tree was clean except for the untracked `docs/superpowers/build/m3-shots/`. My test runs rewrote those screenshots, which the plan intends; Task 21 commits them.
**Verdict: FAIL.** One blocking defect, described in B1. Every gate is green, and every other acceptance item for G5 was verified in the running stack.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 301 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues in 109 source files |
| `./lane.sh pytest -q` (lane stack `--profile oracle --profile sources` up, image rebuilt from HEAD with `up --build`) | **2056 passed, 8 skipped**, 0 failed (423 s) |
| `tests/console/test_console_m3_wizard.py` + `test_console_m3_groups.py` run again on their own with `-v -rs` | **6 passed**, 0 skipped (58 s) |
| `cd frontend && npm ci && npm test && npm run build` | ci ok; node tests pass; Vitest **90 passed** (engines 12, groups 12, state 29, jobs 37); `tsc --noEmit && vite build` clean |
| Stack | brought down afterwards (`docker compose --profile oracle --profile sources down`); no other project was touched |

The impl report's single M2 console failure, caused by a missing `bank-restore-rw` connection, did not occur on this run.

## 2. Independent probes (`/Users/neelu/.cache/dps-build/reports/probe-m3-G5/`)

All probes used the private Playwright 1.55.0 venv at `/Users/neelu/.cache/dps-build/pw-m3`, which I created for this review. Every probe reports counts and names only.

- **P1 · the wizard creates Oracle and MongoDB connections** (`p1_wizard_create.py`). The test module does not cover this programme acceptance item, because its harness registers the `smoke-*` connections through the API. I drove New connection in the browser:
  - **Oracle:** host `oracle`, port 1521, service name `FREEPDB1`, secret ref → "2 of 2 checks passed" and "Oracle Database 23.26.3.0.0". Step 2 Owner lists `CRM` and `LOANS`. It was registered with `service_name` set and `database` null.
  - **MongoDB:** host, port, database `crm`, Replica set `rs0` → "2 of 2 checks passed", "MongoDB 7.0.40" and the chip "transactions: yes". Step 2 shows "Database: crm" and the collections `dps_seed_info` and `profiles`. It was registered with `options: {replicaSet: rs0}`.
  - No page errors. Both probe connections were deleted afterwards (204).
  - The first attempt, made while the full suite was reseeding MongoDB, showed an empty collection list. Two later runs and the API listing were correct, so I attribute that attempt to the concurrent reseed.
- **P2 · evidence pack of a filtered MongoDB job** (`p2_evidence_selection.py`). For the job the wizard created (`{"segment":"premium"}` → `profiles_console`):
  - The job spec and the evidence JSON both carry `mongo_filter_template {"segment": "?"}`. The literal is absent from the evidence JSON.
  - The console's Evidence pack page reads **"Selection all rows · 0 bound parameters"**. See B1.
- **P4 · source vs target, counts only, any document rather than the same document** (`p4_survivors_and_logs.py`). This strengthens the test, which compares Mongo values only within the same document.
  - Every value in `crm_uat.profiles_console` was compared with the set of all source values.
  - 0 survivors for `profile.fullName`, `contact.email`, `contact.mobile`, `customerId`, `kyc.taxId`, `contact.addresses[*].line1`, `contact.addresses[*].pin`, `accounts[*].iban` and `notes`.
  - `profile.dob` (date_shift) shows 38/424 and later 5/31 matches against *other* customers' dates of birth. These are chance collisions in a small date space; same-document DOB survivors are **0/424**.
  - The Oracle test already compares against every source value, set-wise.
- **P5 · log scan.** `docker compose logs app worker` after all console runs (99 KB) was searched for 9,560 seeded values: names, e-mails, mobile numbers and national ids from Oracle `CRM.CUSTOMERS` plus MongoDB `crm.profiles`. **0 found.**
- **P6 · tenant isolation of the group routes the console uses** (`p6_tenant.py`).
  - `GET /job-groups/{id}` and `POST …/consistency-check` from another tenant (`X-DPS-Tenant`) → 404 `group_not_found`, and the list does not contain the group.
  - A `pairs` body naming foreign jobs or an injection-shaped path → 422 `member_not_in_group`, with no value echoed.
- **Visual check against the mockups** (screenshots from this run): `07-jobs-group-desktop`, `03-job-detail-group-desktop` and `06-wizard-mongo-2-select-desktop`.
  - Mockup 07: ghead, key node with the entity-scoped chip, wires, three `.sys` cards (ORA/MDB/PG, state chip, object → target, job id + rows, field chips), and `.gsplit` with the verdict (passed, 3 / 3, three pairs at 500/500, anchor line) beside the join-coverage tables and the prefixed-digits callout. The structure matches.
  - Mockup 03: crumb with the group link, subtitle naming the other two targets, the **Open consistency check** primary button, and the "Cross-system joins 3 / 3 consistent" tile. These match.
  - The tests assert no horizontal scroll at 390 px. No value is visible: the wizard rail shows `"segment": "<bound>"`.

## 3. Blocking

**B1 · The console's evidence pack says "all rows · 0 bound parameters" for a MongoDB job that ran with a filter.**
- **Where:** `frontend/src/pages/EvidencePack.tsx:159`. It renders `sel.where_template || sel.query_template || 'all rows'` and never reads `sel.mongo_filter_template`, which the server writes (`dps/layers/connectors/evidence.py:235`).
- **Effect:** every MongoDB job submitted with a filter, which Task 18b now makes the console's normal path, shows auditors a selection scope that is false. Evidence pack `j_01M4VB3EEC…` covers 31 of 200 documents but states "all rows". The binds count is also 0 although the filter has one bound literal.
- **Why the tests miss it:** `test_mongo_wizard_filters_discovers_and_runs` opens exactly this page but asserts only that sections 1–5 are visible.
- **Why it blocks:** this contradicts spec P6 (an evidence pack records what ran) and the M3 scope ("the Oracle and MongoDB paths of the console").
- **Fix:**
  - When `sel.mongo_filter_template` is set, show it as compact JSON with its `?` placeholders, and the number of bound literals, for example "Filter `{"segment":"?"}` · 1 bound literal". Use "documents" for MongoDB.
  - Add a Vitest for the selection text and a browser assertion in the Mongo wizard test, for example that the evidence page contains `mongo_filter` / `"segment"` and does not contain "all rows".

## 4. Plan, contract and programme acceptance

| Item | Status |
|---|---|
| T18 types/api (`CheckMember`… `ConsistencyCheckResult` field for field with `consistency.py`; `api.groups.consistencyCheck`; JobGroup `last_check`/`last_check_at`) | met; `api.groups.get` was added because the hook needs it |
| groups.ts helpers + Vitest cases of Step 2b | met (12 cases; free text excluded, as on the server) |
| useJobGroup (dedup cached connections/snapshots, `{sample: 500}`, result stored in place, 2 s poll while non-terminal, cleared on unmount) | met |
| Jobs page group card, `.grp` badges, New group dialog (kid, seed fingerprint, .dpskey/.dpsseed via blob URL revoked, stored-checkbox before Done, nothing in storage) | met; downloads and the storage scan are asserted in the browser |
| Job detail crumb/subtitle/button/joins tile | met |
| Wizard `?group=` presets the step 5 group | met; it opens on the farthest reachable step, not step 5 (logged deviation, accepted) |
| T18b engine cards enabled, per-engine fields, version + transactions chip, Oracle owners, Mongo database line/collection/Filter (JSON), Check → count, 422 shown without echo, Mongo paths in heat-map, Oracle schema / Mongo collection targets, `transactions_unsupported` gate, rail template | met (code + tests + P1) |
| Draft never stores `mongoFilterText` | met (TRANSIENT list, `restore`, Vitest) |
| D3 typed confirmation | not bypassed. Mongo/Oracle in_place/append use the same `needsConfirmation`/`isWriteConfirmed`/`confirmationKey` (which includes the parsed `mongo_filter`). An unparseable filter cannot be counted (`check()` refuses before any request), and `canLeave(2)` needs a count when the filter text is non-empty, so an invalid filter cannot silently widen to "all documents". `confirm_write` is sent only when confirmed. `?group=` does not touch the confirmation. |
| Programme §2.2 G5 (wizard creates Oracle/Mongo connections, owner listing, JSON filter step, job-group panel, browser check vs 03/06/07) | met, apart from B1. The connection creation is verified by P1 rather than by the test module. |
| Contract names for other lanes | additive only (`JobGroup` optional fields, `JobGroupOut`, new M3 types, `Selection.mongo_filter`, `api.groups.get/consistencyCheck`); nothing renamed or removed |

## 5. Deviation log, entry by entry

**Task 18**
- Free text never counted as a shared entity: **accepted**. It mirrors the server's `_joinable`.
- `api.groups.get` added: **accepted**. It is required and mirrors the route.
- Extra tested helpers (`pickGroup`, `joinsText`, `checkErrorText`, `groupKeyFiles`…): **accepted**. The `.dpskey` shape `{group, key_material}` is read by `parseKeyFile` (`obj(o.key_material)`).
- `checkError` kept separate from `error`: **accepted**, and better: a refused check does not hide the group.
- The card shows every run and the check is disabled below two members: **accepted**. Note that the server collapses same-target runs, so a two-run group on one target still answers 409, which is shown as a callout.
- Coverage tables instead of the masked-row sample: **accepted**. This follows the contract's counts-only rule and open question 3.
- Crumb row and the joins tile in place of Partitions: **accepted**. It matches mockup 03.
- `?group=` cannot open step 5: **accepted**. Steps 1–4 are prerequisites.
- Styles in `styles/*.css`: **accepted**. Vault tokens only; no raw colours in the diff; 1200/980 px media rules are present.
- The dialog's only exit after Create is Done: **accepted**, and stricter.

**Task 18b**
- Draft fields `serviceName`, `connOptions`, `selMode: 'mongo'` and the `engine` action: **accepted**. `connOptions` keys are limited to the server allow-list, so no `uri` or password lands in the stored draft.
- `connectionParams` makes host, port and service optional with a secret ref on Oracle/Mongo: **accepted**. The connectors read `dsn`/`uri` from the secret.
- `versionLabel` and `columnLabel` helpers: **accepted**.
- Client-side refusal of `$where/$expr/$function/$accumulator`: **accepted**. The test also exercises the server 422 through `$jsonSchema`.
- Mongo transactions gate in the reducer: **accepted**.
- Same-engine target filter: **accepted**. It follows the plan text.
- Mongo Fields list grouped by top-level field: **accepted**. It matches the projection semantics.
- Test harness registers `smoke-*` connections through the API: **accepted for the test**, but the "wizard creates connections" acceptance was not exercised by any test. P1 covers it now (see N2).
- Oracle filter `'retail'` (lower-case seed): **accepted**.
- Step 8 implemented as a test module: **accepted**, and better than a manual pass.

## 6. Non-blocking findings and nits

- **N1.** The MongoDB **Check** result lists the server's SQL-flavoured selection checklist ("SELECT only", "No INTO, procedure calls, database links or FOR UPDATE", "Literals bound as parameters 0") and "0 bound parameters" for `{"segment":"premium"}`. The source is server-generic (`selection.py` `_CHECKS`) together with the console labels in `lib/jobs.ts:250`. Recommend engine-aware labels for Mongo, such as operator allow-list, no JavaScript and literal count. It is misleading, not a leak.
- **N2.** No automated test drives New connection for Oracle or MongoDB. Add a browser case with a throw-away connection name, deleted after, along the lines of P1.
- **N3.** Mongo survivor check in the test (`_mongo_survivors`):
  - It compares values within the same document only, and only for 4 paths.
  - Consider the set-wise comparison and the remaining leaves: `kyc.taxId`, `contact.addresses[*].*`, `accounts[*].iban`, `notes`. P4 found 0 survivors for all of them.
- **N4.** `assert '"$where": "1"' not in page.inner_text("body")` is vacuous. A textarea's value is never in `inner_text`, and the message never contains the filter by construction. The `kycSentinel42` assertion is the meaningful one.
- **N5.** `checkErrorText` has no plain-words entry for the route's `path_unknown` (422). It falls back to the API message plus the code, which is acceptable.
- **N6.** In the Step 5 "Ready to run" summary, the "Rows" line still says "rows · columns" for MongoDB. Step 2 and the stepper say documents and fields.
- **N7.** A restored draft drops the Mongo filter by design, and the run then reads every document unless the operator re-enters it. This is M2 behaviour with an amber callout, and in-place/append still require a fresh count, so it is not a regression.
- **N8.** Full-page screenshots show the fixed header and sidebar repeated mid-page. This is a capture artefact, not a layout bug.

## 7. Privacy evidence summary (counts only)

- No seeded value is visible on any wizard, job, group or evidence page (tests: 25+25 names and e-mails per page).
- MongoDB console target: 0 any-document survivors on 9 sensitive paths. DOB has 0 same-document survivors.
- Oracle console target: 0 survivors set-wise on 5 columns (test).
- Logs: 0 of 9,560 seeded values in the app and worker logs.
- Evidence and job JSON hold `mongo_filter` only as a template (`"?"`), with no literal.
- The New-group dialog leaves no key, seed or kid in local or session storage (test).
- Tenant isolation of the group routes holds (P6).
