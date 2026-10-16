# M3 G5 review, round 2: Tasks 18 and 18b (console)

**Reviewer:** independent review agent (round 2) · **Date:** 2026-10-14 · **Worktree:** `/Users/neelu/document_intelligence/dps-wt-m3` (branch `m3`)
**HEAD reviewed:** `4c1fd3f fix(m3): evidence pack page states a MongoDB job's filter template, bound literals and documents`, on top of `529b1da` (T18), `276ea3a` (T18b) and `436ae69` (T18 browser test).
**Tree:** clean apart from the untracked `docs/superpowers/build/m3-shots/`. My browser runs rewrote those screenshots, which is intended; Task 21 commits them. I modified no repository file.
**Verdict: PASS.** Every gate is green. Round-1 blocker B1 is fixed and the fix was verified in the running stack. This round found no new blocking defect.

## 1. Gates (all run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 301 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 109 source files |
| `cd frontend && npm ci && npm test && npm run build` | All exit 0. Node tests pass. Vitest **91 passed** (engines 12, groups 12, state 29, jobs 38). `tsc` + `vite build` clean. |
| Lane stack | `docker compose --profile oracle --profile sources up -d --build`, so the image was rebuilt from HEAD. All services healthy. |
| `tests/console/test_console_m3_groups.py` + `test_console_m3_wizard.py` (`-v -rs -s`) | **6 passed, 0 skipped** (61 s). Printed counts: `M3_WIZARD_ORACLE 1148 1148` with 0 survivors in all 5 columns; `M3_WIZARD_MONGO 424 424` with 0 survivors on all 4 paths. |
| `./lane.sh pytest -q` (full suite, same stack) | **2056 passed, 8 skipped**, 0 failed (432 s) |
| Stack afterwards | `docker compose --profile oracle --profile sources down` (rc 0). No container of another project was touched. |

Logs are in `/Users/neelu/.cache/dps-build/reports/probe-m3-G5/r2/`: `npm-*.log`, `up.log`, `console-m3.log`, `pytest-full.log` and `down.log`.

## 2. B1 re-check (round-1 blocker): evidence pack selection for MongoDB

- **Code.** `packSelection(sel, engine)` and `recordNoun` were added to `frontend/src/lib/jobs.ts`, and `EvidencePack.tsx` uses them.
  - For MongoDB the page shows `JSON.stringify(mongo_filter_template)`, with "all documents" when the template is null or `{}`, plus "N bound literal(s)" counted from the `?` leaves.
  - The server's `mongo_filter_template` (`dps/layers/connectors/selection.py:1112`) replaces every scalar with `"?"`. The page therefore shows only a value-free structure, and the literal count equals the number of scalars in the filter.
  - SQL engines keep their previous wording.
- **Tests.**
  - The new Vitest case covers SQL where/query/none and Mongo single, nested (`$or`/`$in`/`$gte`), null and `{}`.
  - The browser assertion in `test_mongo_wizard_filters_discovers_and_runs` is not vacuous. Before the fix the line read "Selection all rows · 0 bound parameters", which fails `'{"segment":"?"}' in selection`.
- **P7 (my probe, `r2/p7_mongo_complex_filter.py`).** I submitted, through the API, a MongoDB run with a nested filter: `$or` of `$in` with 2 literals and an Extended-JSON `$date` with `$gte`, plus a top-level `$ne`.
  - The run succeeded: 154 documents read and 154 written.
  - The job spec template is `{"$or":[{"segment":{"$in":["?","?"]}},{"profile.dob":{"$gte":{"$date":"?"}}}],"segment":{"$ne":"?"}}`.
  - The evidence page line reads `Selection {…same template…} · 4 bound literals`. Four is correct.
  - The summary says "DPS read 154 documents from MongoDB".
  - None of the 4 filter literals appears in the job JSON, the evidence JSON, the evidence page or the job detail page: 0 of 4 in each.
  - "all rows" is absent and there were no page errors.
- **P9.** The same evidence page at 390 px has 0 px horizontal overflow; the long template wraps inside the card.
- **P13 (SQL regression).** The Oracle console job's evidence page shows `Selection "SEGMENT" = :l1 · 1 bound parameter`. The summary says "rows from Oracle" and the literal is not on the page.

**B1: fixed.**

## 3. Independent probes (counts only; scripts under `probe-m3-G5/r2/`)

| Probe | Result |
|---|---|
| **D3** (`d3/d3_mongo.ts`, bundled with the frontend's esbuild against the real reducer and helpers): 27 cases | **27/27 pass.** For both Mongo `in_place` and `append`: confirm after count + typed name works, and the spec carries `confirm_write: true, expected_rows 31`. Each of these lapses the confirmation and blocks step 4: a new filter, a filter that is no longer JSON, a cleared filter, an engine switch, a changed field subset, a draft restore. A broken filter sends no `mongo_filter` and cannot stay confirmed. `$where` blocks step 2. No-transactions blocks step 4. Oracle `in_place`: the confirm word is `CRM.CUSTOMERS` and a WHERE change lapses the confirmation. **Gap:** Vitest has no Mongo D3 case (see N9). |
| **P1** (round-1 script, re-run): the wizard creates connections | **Oracle:** host/port/service name + secret ref give "2 of 2 checks passed" and "Oracle Database 23.26.3.0.0". Owners `CRM` and `LOANS`. Registered with `service_name` set and `database` null. **MongoDB:** "2 of 2 checks passed", "MongoDB 7.0.40", "transactions: yes", "Database: crm", collections `dps_seed_info` and `profiles`. Registered with `options.replicaSet=rs0`. Both connections deleted (204). No page errors. |
| **P8**: visual check vs mockups 07 and 03, light and dark, 1440 and 390 px | **Group card:** ghead, key node with the kid and the entity-scoped chip, wires, and three `.sys` cards (ORA, MDB, PG) with status chips, object → target, rows/documents and field chips. The verdict shows "3 / 3 consistent, passed" with pair rows `CUSTOMER_ID = customerId` etc. at 500 / 500. The join-coverage table carries the prefixed-id callout. **Job detail:** crumb "Data sources & jobs / Job … · Job group …", subtitle "consistency key shared with …", "Open consistency check" and Evidence pack buttons. **Mobile:** 0 px overflow on both pages. The deliberate departure from the mockup is coverage tables in place of the mockup's masked-value sample table; this is accepted (counts-only rule). |
| **P10/P11**: source vs target, after re-seeding the full 2,400-row seed 7 (the full suite had left a 200-row seed) | **Mongo** `crm_uat.profiles_console` (424 docs) and the P7 target (154 docs): **0 any-document survivors** on 9 paths (`fullName`, `email`, `mobile`, `customerId`, `kyc.taxId`, `addresses[*].line1`, `addresses[*].pin`, `accounts[*].iban`, `notes`), from 3,645 and 1,322 target values. **Oracle**, every `CRM_MASKED` table against all 2,400 source rows set-wise: `CUSTOMERS_CONSOLE` 1,148 rows, `CUSTOMERS_E2E` 2,400, and both `CUSTOMERS_GRP_*` 2,400 each. **0 survivors** in `FULL_NAME`, `EMAIL`, `MOBILE`, `NATIONAL_ID`, `TAX_ID`, `ADDR_LINE1` and `NOTES`. |
| **P10 log scan**: `docker compose logs app worker` (84.8 KB) | **0 of 13,408** seeded values (Oracle names, e-mails, mobiles and national ids; Mongo names, e-mails, mobiles, tax ids and IBANs) plus the P7 filter literal. |
| **P12**: job-group key custody | Group created (201). Its key-material and seed strings (2) appear in neither the later `GET /job-groups/{id}` and list answers nor the app/worker logs: 0 and 0. |
| **P6** (round-1 script, re-run): tenant isolation | From two foreign tenants, `GET /job-groups/{id}` returns 404 and `POST …/consistency-check` returns 404 `group_not_found`; the group is absent from their lists. A `pairs` body with foreign jobs and an injection-shaped path returns 422 `member_not_in_group`, with no echo. |
| **Server D3** (P7 side-check) | Unconfirmed and wrong-count `in_place` bodies on the source-only Mongo connection were refused 422 `connection_role` before any write, with no literal echoed. `confirm_write_required` itself is engine-agnostic in `dps/layers/connectors/targets.py:364` and is covered by `tests/connectors/test_targets.py`. |

## 4. Plan, contract and programme acceptance (§2.2 row G5)

| Item | Status |
|---|---|
| Wizard creates Oracle and MongoDB connections | met (P1; no automated test, see N2) |
| Owner listing (Oracle owners; Mongo database line and collections) | met (P1, test module) |
| JSON filter step (operator hints, `$where`/`$expr`/`$function`/`$accumulator` refused on the client, server 422 shown without echo, never stored in the draft, `<bound>` in the rail) | met (tests, D3 probe) |
| Job-group panel (Jobs card, New group dialog with once-only `.dpskey`/`.dpsseed`, nothing in storage, check → `last_check`) | met (tests, P8) |
| Job detail of a grouped job (crumb, subtitle, Open consistency check, joins tile) | met (tests, P8) |
| Browser check against mockups 03, 06 and 07 | met (P8 plus the test screenshots, desktop and mobile, light and dark) |
| Evidence pack states what ran (spec P6) for Mongo and Oracle | met after the B1 fix (P7, P13) |
| D3 typed confirmation not bypassable from the wizard on Oracle/Mongo | met (D3 probe; server-side refusal intact) |
| Contract names for other lanes | additive only. `JobGroup.last_check`/`last_check_at` are optional; added `JobGroupOut`, the M3 check types (field for field with `consistency.py`, `FormKind` literal included), `Selection.mongo_filter`, `api.groups.get`/`consistencyCheck`. The fix added only page helpers. No server, schema or contract change; the sealed pack hash is unaffected. |

## 5. Deviation log, entry by entry

- **T18 and T18b entries.** I re-read all of them and agree with round 1: every one is **accepted**. In brief:
  - free text is never a join;
  - `api.groups.get` added;
  - extra tested helpers;
  - `checkError` kept separate;
  - the check is disabled below two members;
  - coverage tables in place of the masked sample;
  - crumb and joins tile;
  - `?group=` opens the farthest reachable step;
  - styles kept in `styles/*.css`;
  - Done is the only exit after Create;
  - the draft fields `serviceName`, `connOptions` and `selMode: 'mongo'`;
  - optional host, port and service with a secret ref;
  - a client-side JavaScript-operator hint, with the server authoritative;
  - the transactions gate;
  - same-engine table targets;
  - the Mongo field list grouped by top-level field;
  - `smoke-*` connections registered through the API in the harness;
  - the lower-case Oracle filter;
  - step 8 as a test module.
- **Fix round 1, bound-literal count derived on the page from the template's `?` leaves:** **accepted**.
  - The server keeps `binds = len(params)`, which is 0 for inline Mongo literals, and changing it would alter sealed pack content.
  - The `?` count matches the server template's scalar count exactly. P7 confirmed this: 4 literals shown for 4 in the filter.
- **Fix round 1, "documents" only in the summary and the selection line; tiles and check rows keep "rows":** **accepted** as wording. Listed again as N6/N10.
- **Fix round 1, no new `.sel` class:** **accepted**, because it avoids a CSS collision.

## 6. Non-blocking findings and nits

- **N1 (open from round 1).** The step-2 **Check** result for MongoDB still lists the SQL-flavoured checklist ("SELECT only", "No INTO … database links …") and "0 bound parameters". It is misleading wording, not a leak.
- **N2 (open).** No automated test drives *New connection* for Oracle or MongoDB. It is covered only by probe P1.
- **N3 (open).** The test's Mongo survivor check compares within the same document only, and on 4 paths.
  - P10 compared every target value with every source value on 9 paths and found 0 survivors.
  - Widening the test would make this evidence permanent.
- **N4 (open).** `assert '"$where": "1"' not in page.inner_text("body")` is vacuous, because a textarea's value is never in `inner_text`.
- **N5 (open).** `checkErrorText` has no plain-words entry for `path_unknown` or `field_scoped`. It falls back to the server's fixed message plus the code, which is acceptable.
- **N6 (open).** The step-5 "Ready to run" summary row "Rows" still says "N rows · N columns" for MongoDB; steps 2 and 4 and the stepper say documents and fields.
- **N7 (open, by design).** A restored draft drops the Mongo filter, with an amber callout.
- **N8.** Full-page screenshots repeat the fixed header and sidebar. This is a capture artefact.
- **N9 (new).** No Vitest case covers D3 on a MongoDB draft: filter change, broken JSON or engine switch after a confirmation.
  - The behaviour is correct (27/27 in my probe).
  - Suggest porting the probe's cases into `state.test.ts`.
- **N10 (new, cosmetic).** On the evidence page, a MongoDB pack's stat tiles and "Row counts" check row still say "rows". This is documented in the fix deviation.
- **N11 (new, outside G5).** `tests/e2e/test_m3_three_way.py::test_three_way_consistency_join` (line 344) uses an "anywhere" survivor assertion on the key path `$.customerId`.
  - The fix agent measured this as flaky, at about 6.7 % per run.
  - The cause is chance FF1 collisions in a 9×10^7 domain with 2,400 ids, for which the birthday estimate is about 6 %. The arithmetic is plausible.
  - It passed in my full run.
  - Recommendation for the T17/G4 owner: assert same-document survivors for key columns, as is already done for the Oracle `CUSTOMER_ID`.
- **N12 (observation).** The full suite leaves Oracle and MongoDB seeded with 200 rows, while the console checks seed 2,400.
  - Any ad-hoc source-vs-target comparison after a full run must re-seed first, as I did.
  - Otherwise the comparison silently covers only part of the source.

## 7. Privacy evidence summary (counts only)

- **Seeded values on pages.** None on any wizard, job, group or evidence page. The tests check 25 names and 25 e-mails per page; P7 and P13 checked the filter literals on the evidence and detail pages.
- **Mongo targets.** 0 any-document survivors on 9 paths, in 2 targets.
- **Oracle targets.** 0 set-wise survivors on 7 columns, in 4 `CRM_MASKED` tables.
- **Logs.** 0 of 13,408 seeded values in the app and worker logs; 0 group key or seed strings.
- **Job spec and evidence JSON.** Both hold the Mongo filter only as a `?` template. The rail shows `<bound>`.
- **Tenant isolation.** The group routes hold (P6).
