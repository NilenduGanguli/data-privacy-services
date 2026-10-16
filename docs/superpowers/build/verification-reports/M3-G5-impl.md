# M3 G5 implementation report — Tasks 18, 18b (console: job groups, consistency panel; Oracle/MongoDB wizard)

Worktree `/Users/neelu/document_intelligence/dps-wt-m3`, branch `m3`. Author NilenduGanguli
<nilendu.ganguli@gmail.com>, no trailers. Resume check at start: tree clean, last commit `ac710bf`
(G4 fix r2); no earlier G5 work, no earlier G5 report.

## Commits

| Commit | Message |
|---|---|
| `529b1da` | feat(console): job groups, key node, member systems and the consistency verdict on Jobs and Job detail (Task 18) |
| `276ea3a` | feat(console): Oracle and MongoDB in the new-job wizard - connection fields, owners, JSON filter, Mongo targets (Task 18b) |
| `436ae69` | test(console): job-group card, consistency check and job-detail joins against the lane stack (Task 18 step 8 as a test) |

## What was built

### Task 18 (mockups 07, 03)
- `frontend/src/lib/types.ts`: `CheckMember`, `ConsistencyCheckRequest`, `JoinFormKind`, `MemberResult`, `PairResult`,
  `AttributeResult`, `CheckProblem`, `ConsistencyCheckResult` (mirror `consistency.py`); `JobGroup.last_check?`,
  `last_check_at?`; `JobGroupOut`.
- `lib/api.ts`: `api.groups.get` (new; the hook needs it) and `api.groups.consistencyCheck`.
- `lib/groups.ts` (+ `groups.test.ts`, 12 Vitest cases): `JOIN_PREFERENCE`, `ENGINE_BADGE`, `sharedEntities`,
  `memberFields`, `formLabel`, `pairLabel`, `problemText`, `targetOf` (plan) + `displayPath`, `engineLabel`,
  `checkErrorText`, `pickGroup`, `joinsText`, `groupKeyFiles`.
- `hooks/useJobGroup.ts`: group + run jobs + (cached, parallel) connections and snapshots; 2 s re-read while a member
  is non-terminal; `runCheck` posts `{sample: 500}` and stores the answer as `last_check`.
- `components/groups/{ConsistencyVerdict,CoverageTable,JobGroupCard,NewGroupDialog,JobGroupsSection}.tsx`.
- Pages: Jobs (group section `id="group"`, `.grp` key badge with the group name, linking to the group), JobDetail
  (crumb, subtitle "consistency key shared with …", **Open consistency check**, "Cross-system joins" tile), JobWizard
  (`?group=` presets step 5's group and shows a callout); Step 5 / retry accept `.dpskey`/`.dpsseed`.
- Styles ported from mockup 07 into `styles/jobs.css` (+ dialog in `components.css`, crumb in `job-detail.css`).

### Task 18b (mockup 06)
- `lib/engines.ts` (+ `engines.test.ts`, 12 cases): `connectionFields`, `connectionParams`, `defaultPort`,
  `secretRefHelp`, `ownerLabel`, `objectLabel`, `filterModes`, `versionLabel`, `parseMongoFilter`,
  `mongoFilterTemplate`, `needsTransactions`, `columnLabel`.
- `lib/jobs.ts`: draft `serviceName`, `connOptions`, `mongoFilterText` (transient), `selMode: 'mongo'`;
  `selectionOf` emits `mongo_filter`; rail shows it via `mongoFilterTemplate`. `Selection.mongo_filter` in types.
- `pages/wizard/state.ts`: `engine` action, `filterTextOf`, `transactionsMissing` gate in `targetReady`.
- Steps: Connect (3 engines enabled, per-engine fields, version label, Mongo `transactions: yes|no` chip),
  Select (Oracle owners + `:name` hint; Mongo database line, Collection, Filter (JSON), Fields grouped by top-level),
  Discover / heat-map / job-detail show document paths, Target (same-engine targets; Oracle destination schema;
  Mongo destination collection; amber `transactions_unsupported` callout), Run summary engine name.
- `jobs.test.ts`: +6 cases (Mongo/Oracle run specs, storage, count/engine reducer, saved-connection engine,
  transactions rule).

## Gates (final state, all on the commits above)
- `./lane.sh ruff format --check dps tests tools` → 301 files already formatted; `ruff check` → All checks passed;
  `mypy dps` → Success, 109 source files.
- `./lane.sh pytest -q` with the lane stack up (oracle + sources): **2055 passed, 8 skipped, 1 failed** —
  the failure was `tests/console/test_console_m2.py::test_in_place_on_a_table_with_an_entity_typed_key`, an
  environment precondition (M2 demo connection `bank-restore-rw` not registered on this freshly-up stack; the M2
  smoke registers it). After registering the M2 demo connections (`tools.m2_api.Client().ensure_connections()`),
  `tests/console/test_console_m2.py` → **3 passed** (also proves the PostgreSQL wizard path still works). Before the
  stack/console existed: 1726 passed, 332 skipped.
- Frontend: `npm run build` (tsc + vite) clean; `npm test` → node tests 42 pass, Vitest **90 passed** (groups 12,
  engines 12, jobs 37, state 29).
- Browser (Playwright 1.55 from the lane venv `e2e` extra, against the rebuilt lane image on :8413):
  - `tests/console/test_console_m3_wizard.py` → **3 passed** (engine cards enabled/no "M3" chip; Oracle wizard to
    evidence pack; Mongo wizard: valid filter count, `$where` refused client-side, `$jsonSchema` refused by server
    422 `selection_operator_denied`, no echo of a sentinel literal, heat-map has `profile.*` paths, run to evidence).
  - `tests/console/test_console_m3_groups.py` → **3 passed** (group card + check 3/3 passed, coverage, callout,
    stored verdict after reload, `.grp` badges, Add-a-job preset; job detail crumb/subtitle/button/joins tile;
    New group dialog downloads + storage scan).
  - TDD red steps seen: groups.test (module missing), engines.test (module missing), jobs.test 4 failing M3 cases,
    transactions case, columnLabel case.

## Privacy evidence (counts only)
- Source vs target (wizard runs): Oracle `CRM.CUSTOMERS WHERE SEGMENT='retail'` → `CRM_MASKED.CUSTOMERS_CONSOLE`:
  9,824 / 9,824 rows (later run on a 200-row reseed: 97 / 97); survivors FULL_NAME 0, EMAIL 0, MOBILE 0,
  NATIONAL_ID 0, TAX_ID 0. MongoDB `crm.profiles {segment: premium}` → `crm_uat.profiles_console`: 3,469 / 3,469
  documents (later 31 / 31); same-document survivors profile.fullName 0, contact.email 0, contact.mobile 0,
  customerId 0. (Both asserted in the tests.)
- Consistency check through the console on the three-member group (2,400 Oracle/Mongo, 20,000 PG): passed 3/3,
  500/500 per pair, members found 500/500; attributes EMAIL, PERSON_NAME, PHONE, PAN_IN, NATIONAL_ID,
  STREET_ADDRESS agreed 100 %, DATE_OF_BIRTH (date_shift) and POSTAL_CODE informational.
- No seeded value on any console page (25 names + e-mails per engine checked in body text: 0).
- Log scan of the app + worker container logs after the console runs: 12,000 seeded values (e-mail, name, mobile,
  national id, CUST-id of 2,400 customers) → 0 found (46 KB of logs).
- New-group dialog: no `key_material`, `synthesis_seed` or kid in local/session storage.

## Deviations
All recorded in `docs/superpowers/build/M3-DEVIATIONS.md` (sections "Task 18", "Task 18b", "Task 18 browser
check"). Main ones: free text never counts as a shared entity (mirrors the server); `api.groups.get` added; extra
tested helpers; `checkError` separate from `error`; group card shows every run, check disabled with < 2 members;
coverage tables in place of the masked-row sample (contract counts-only); joins tile replaces the partitions tile for
grouped jobs; `?group=` presets step 5 but cannot open step 5 on an empty draft; styles in `styles/*.css` not
`styles.css`; dialog's only exit after Create is Done; client-side refusal of JS operators; Mongo transactions gate in
the reducer; same-engine target filter; Oracle filter `'retail'` (seed is lower-case); step 8 implemented as a test
module; `smoke-*` connections registered by the test harness (Task 21's `smoke --m3` not yet built). No contract name
changed.

## Environment notes / open issues
- The lane's PostgreSQL `bank` had been seeded by M2's older generator (attributes disagreed with Oracle/Mongo):
  reseeded with `tools/seed_sources.py --target postgres --rows 20000 --seed 7 --restore-copy --reset`. The full
  pytest run reseeds Oracle/Mongo to 200 rows (other tests); the group test reseeds 2,400 itself.
- The group check leaves `bank_uat.customers_grp_<hex>`, `crm_uat.profiles_grp_<hex>` and (dropped next run only for
  its own suffix) `CRM_MASKED.CUSTOMERS_GRP_<HEX>` per run in the lane stack (Oracle has no volume; `down` resets it).
- `lane.sh` exports `.env.lane` after the caller's environment, so `DPS_TEST_APP_URL=… ./lane.sh pytest` is
  overridden; use `./lane.sh env DPS_TEST_APP_URL=… pytest` (used for the Vite-backed iteration).
- Screenshots in `docs/superpowers/build/m3-shots/` (06-wizard-oracle-*, 06-wizard-mongo-*, 07-jobs-group-*,
  03-job-detail-group-*, mockups) are untracked, to be committed by Task 21 per the plan. Visual comparison:
  mockup 07 structure matched (ghead, keynode, wires, sys3, gsplit verdict + table); mockup 03 header/tiles matched;
  390 px without horizontal scroll (one overflow found and fixed: Mongo nested-path column list).
- Not done here (other groups): Task 21 `smoke --m3`, README/HANDOFF/API docs, committing screenshots.
- Stack brought down (`docker compose --profile oracle --profile sources down`); Vite dev server stopped.
