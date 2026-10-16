# S8-idcheck — the D-A post-write identifier check reads every row on every engine, each column as spelt

**Status: DONE** (resumed run, 2026-10-15 22:05-22:50). Branch `fidcheck`
(`/Users/neelu/document_intelligence/dps-wt-fidcheck`, feeder of `fllm`):

| Commit | What |
|---|---|
| `9fe68f4` | (first run) `fix(connectors): the D-A identifier check reads every row on every engine and says what it read` |
| `794007e` | (first run) `docs(connectors): the identifier-check fix's gates and live counts in the M3 deviation log` |
| `762418d` | (this run) `fix(connectors): the D-A identifier check reads each column as the object spells it` |

All authored `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer, explicit pathspecs. Not pushed by me
(the repo's CLAUDE.md: never push without the owner's request; the owner's autopush LaunchAgent pushes every
10 min). Worktree clean. Lane stack stopped (all profiles). Open blocker S1-M3 engines r3 B1 closed (first run);
the review r1 probe findings closed (this run, §9).

## 0. This run in short (resume)

- Found the job already committed (`9fe68f4`, `794007e`) and an **interrupted review r1** (no report; probes in
  `S8-idcheck-review-r1/`) whose probes showed three remaining defects of the same check, all fail-open or
  fail-hard: (1) quoted mixed-/lower-case **Oracle** identifier columns were never read (`checked` 0,
  `whole_table`, `passed` even with a planted real identifier); (2) names the selection validator refuses
  (Oracle `"Tax Id"`, MongoDB `tax-id`) raised `SelectionError`, which finalize's `_best_effort` did not
  catch: finalize failed until the job was `dead`, no evidence pack (`j_01M506EKR5N1KCPSB65QE9GZF5`);
  (3) a MongoDB field name with a dot was projected as the field before the dot (`checked` 0).
- Fixed test-first (9 unit + 2 engine tests failed as built), gates green, live product proofs, committed `762418d`.
  Details in §9; the first run's report follows unchanged as §1-§8.



## 9. Resumed run — review r1 probe findings: each column read as the object spells it (`762418d`)

### 9.1 Root cause

`identifier_check._every_row` read with `Selection(columns=fields)` — a person's selection:
- Oracle: `selection.read_projection` folds a listed column to upper case and keeps shape columns whose name
  *equals* the folded one, while `validate_selection` accepts it (compares upper-cased) → `"NationalId"`,
  `"acctNo"`, `"acct_no"` silently dropped from the projection; rows carried only the key; `column_values`
  found nothing → `checked` 0, nothing flagged, `whole_table`, `passed`.
- `quote_ident` refuses names outside `ORACLE_IDENT`/`MONGO_PATH` (space, hyphen) → `SelectionError` (a
  `ValueError`, not a `ConnectorError`) → `_best_effort` re-raised → `finalize_failed error=SelectionError`
  on every retry → job `dead`, no pack, no key purge (seen live in the lane worker log, 3 retries then dead).
- MongoDB `_projection` splits a listed path on `.` → a top-level field `acct.no` was projected as `acct`.

### 9.2 Fix (`dps/layers/connectors/identifier_check.py`, `jobs_run.py`)

1. `_read_request`: a SQL read lists **no** column — it reads the shape narrowed (`model_copy`) to its key and the
   identifier columns (catalogue names; Oracle quotes them verbatim via `qcol`); MongoDB projects only plain
   top-level names (`_plain_field`: one `MONGO_PATH` segment, no dot), any other field reads whole documents.
2. `_projected`: a SQL row without a requested column raises `IncompleteCheck` (fail closed; a SQL row holds every
   projected column, NULL included) — a projection mismatch can never again count as a clean column.
3. `_best_effort` also records `SelectionError` as `{"error": code}` → every written identifier column reviewed,
   pack written, key purged; no finalize retry loop.
Docs: HANDOFF §8 paragraph, contract §7.1 evidence comment, `M3-DEVIATIONS.md` new last section
("S8-idcheck fix round 1").

### 9.3 Test-first evidence

| Test | As built (`794007e`) | Fixed |
|---|---|---|
| `tests/test_identifier_check.py` (fake now reads as each engine's connector: SQL `validate_selection` + `read_projection`, MongoDB validator + top-level projection) — Oracle `NationalId` / `acct_no` / `Tax Id` / `NATIONAL_ID`+`acctNo`, key `ID` and mixed-case `RowKey` | 3x `checked` 0 != 9, 1x `SelectionError` | pass |
| `test_a_sql_read_without_an_identifier_column_fails_closed` | did not raise (counted clean) | pass |
| MongoDB `tax-id`, `acct.no`, mixed list | 2x `SelectionError`, 1x `checked` 0 | pass |
| `test_a_refused_selection_reaches_the_evidence_as_an_error` | `SelectionError` raised | pass |
| `tests/connectors/test_identifier_check_engines.py::test_oracle_reads_columns_as_the_catalogue_spells_them` (20 000-row `"RowKey"`/`"acctNo"`/`"National Id"`, hits at rows 15 000 / 17 500, key_floor, append after floor 5 000) | `SelectionError` | pass |
| `...::test_mongo_reads_fields_a_projection_cannot_name` (20 000 docs, `acct.no` / `tax-id`) | `SelectionError` | pass |

Logs: `~/.cache/dps-build/s8-idcheck/logs/r1-engines-before-fix.log`, `r1-engines-after-fix.log` (7 passed:
5 engine + 2 run tests, `DPS_TEST_REQUIRE_SERVICES=1`); unit 25 passed (incl. documents file).

### 9.4 Gates (lane stack, `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format --check`, `ruff check` (dps tests tools), `mypy dps` | clean (437 files, 150 sources) |
| Whole suite, lane services up (`r1-suite.log`) | **3 569 passed, 86 skipped, 0 failed** (24:23) |
| Identifier engine + run tests, services required | 7 passed |
| `tools/smoke_test.py --m3` against the rebuilt lane app (`r1-smoke-m3.log`) | **39 passed**, 2 skipped (OCR not up) |

The suite's console tests overwrote 54 tracked PNGs in `docs/superpowers/build/m3-shots/` (known): restored with
`git restore` (my run's artefacts; the tree was clean apart from my 4 files before the suite).

### 9.5 Live proofs (counts only; `~/.cache/dps-build/reports/S8-idcheck-fix-r1/`)

Image `data-privacy-services:fidcheck` rebuilt; app and worker `identifier_check.py` / `jobs_run.py` sha256 =
the tree's.

a. `probe_fix_r1_oracle_case_runs.py` (the review's probe + a spaced-name case; scratch `CRM.S8R_*` /
   `CRM_MASKED.S8R_*_SYN`, 400 generated rows, all dropped: `scratch_tables_left` 0):

| Case | run 1 `truncate_insert`: checked / independent target values | run 2 `upsert` after planting 1 source national id + account number: checked, in_source, issuable / independent | pack |
|---|---|---|---|
| mixed `"NationalId"`, `"acctNo"` | 400 / 400 (review r1, as built: **0** / 400) | 801, 1, 1 (acct 801, 1, 0) / 801, 1 (as built: **0, 0, 0**, passed) | run 2 `review_required`, not passed |
| spaced `"National Id"`, `"Acct No"` | 400 / 400 | 801, 1, 1 (acct 801, 1, 0) / 801, 1 | run 2 `review_required`, not passed |
| upper `NATIONALID`, `ACCTNO` (control) | 400 / 400 | 801, 1, 1 / 801, 1 | unchanged |

   `rows_read` 400/400 then 801/400 in every pack; `scope: whole_table`.
b. `probe_fix_r1_mongo_hyphen_run.py` (the review's probe; `crm.s8r_hyphen` -> `crm_uat.s8r_hyphen_syn`, 300 docs,
   both dropped): job `j_01M507APJMSWMN2QZ8VPWV6E2A` `succeeded` with pack `ev_01M507APREWHN4PSYP2ZN4XREQ`,
   `$['tax-id']` checked 300 = 300 docs, `rows_read` 300/300 (as built: `finalize_failed` x3, `dead`, no pack).
c. Required proof re-run on the fixed code (`S8-idcheck/probe_planted_hit.fixed-r1.out`): 20 000-row Oracle and
   MongoDB targets, real account number at target row 15 000 and issuable national id at 17 500 — both flagged,
   `checked` = `rows_read` = 20 000 / 20 000, key_floor 20 000; scratch user and database dropped.
d. `logscan_fix_r1.py`: app + worker logs since the fixed image started — 0 hits of 92 192 demo source values and
   800 probe source values, 7/7 probe job ids found, 0 tracebacks, 0 `finalize_failed`.

### 9.6 Observations outside this job

- `dps/layers/connectors/consistency.py:559,567` builds `Selection(columns=[join.name])` from catalogue names the
  same way: an Oracle quoted mixed-case join column would likely be folded out of the consistency read
  (same mechanism; not verified, not D-A). `read_projection`/`validate_selection` disagree on Oracle case
  for any person-typed column list (validation accepts what the projection then drops).
- The review r1 hyphen probe's job `j_01M506EKR5N1KCPSB65QE9GZF5` stays `dead` in the lane's job table
  (history; its scratch collections were dropped by my rerun of the probe).
- Cross-engine PostgreSQL -> Oracle `table` run is refused `target_invalid` (review probe
  `probe_r1_pg_to_oracle`): unrelated to the check.

### 9.7 Deviations (also in `M3-DEVIATIONS.md`)

- A MongoDB check of a field with a hyphen or dot reads whole documents (more bytes); plain names unchanged.
- PostgreSQL names `PG_IDENT` refuses still cannot be read (the run's own read fails first); now recorded as
  `{"error": "selection_identifier_invalid"}` (review) instead of a dead job.
- New possible pack error codes `selection_*` under `identifier_check.error` (contract §7.1 amended).

### 9.8 State left behind

Lane stack `dps-wt-fidcheck` stopped (`docker compose --profile '*' stop`, volumes kept); image `fidcheck` = `762418d`
for `dps/`. No scratch objects left by this run. Logs: `~/.cache/dps-build/s8-idcheck/logs/r1-*`.

---

# First run (unchanged)

## 1. Starting state

- `fidcheck` = `5cef802` (= `fllm`'s tip), no own commit, clean, no report. No `.env`/`.env.lane`/`lane.sh`/
  `.venv`: created (git-ignored; `info/exclude` already lists them). Ports 8601/5601/5602/9601-02/1601/
  27601/5603/8602/8603 (+ 9603 in `.env` only), `COMPOSE_PROJECT_NAME=dps-wt-fidcheck`, image tag `fidcheck`;
  venv: every extra but `synth-ml`.
- **Root cause confirmed in the code** (as S1 found): `identifier_check.py` called `rows.clear()` on every
  page it had counted (`key_floor`, `_tally_target`, the source loop). Oracle `read_partition` did
  `yield rows` and then `if len(rows) < size: return` / `after = rows[-1]…`; MongoDB (`read_partition` and
  `_read_unkeyed`) yielded `docs` itself and then tested `len(docs) < batch`. The emptied list looked like a
  short last page: one 5 000-row page per side, reported `scope: whole_table`. PostgreSQL yielded a fresh list.

## 2. The fix (three layers + one gap beyond the finding)

1. **Connectors** (`oracle.py`, `mongo.py`; protocol docstring in `base.py`; contract §12.3): a page's length
   and the next keyset bound are taken *before* the page is yielded — a yielded page is the consumer's to
   empty or rewrite.
2. **The check pages each side itself** (`identifier_check._every_row`): every read starts after the last key
   read (`after_key`), and a side ends only when a read after that key returns no row; a connector's own
   paging is never taken as the end of the object (one extra, empty read per side). `key_floor` reads the key
   column the same way. The check no longer empties pages.
3. **It reports what it read and fails closed**: the result carries `rows_read: {target, source}` (`source`
   null in place; `checked` still counts values read). A read that cannot finish raises `IncompleteCheck`
   (`ConnectorError` code `identifier_check_incomplete`, value-free detail): a read that does not advance past
   its start key, a row without its key, a key-less MongoDB collection (one cursor, no `after_key`) read short
   of `count()`, a SQL column the object does not list. Finalize's `_best_effort` records
   `{"error": "identifier_check_incomplete"}` → every written identifier column is reviewed, no scope claimed;
   in prepare (`key_floor` of a synthesize append) the job fails before writing.
4. **Beyond the finding**: a MongoDB shape comes from a 500-document sample; a written identifier leaf the
   target's (or source's) sample missed was silently skipped (`checked` 0, never flagged). It is now read as
   the other side describes it, else as its key names it. SQL shapes (catalogue) get no fallback (→ 3).

Docs: HANDOFF §8 (identifier-check paragraph: every row, `rows_read`, the error code, image rebuild needed, no
setting/migration), contract §7.1 (evidence shape) and §12.3 (conformance rule), `M3-DEVIATIONS.md` last
section (fix, deviations, tests, verification).

## 3. Test-first evidence

New/changed tests were run against an export of `5cef802` (the tree as built, `git archive`, imported first
on `PYTHONPATH`): **21 failed + 2 failed** (`~/.cache/dps-build/s8-idcheck/logs/before-fix-all-at-HEAD.log`,
`before-fix-runs-at-HEAD.log`); all pass on the fix (`after-fix-final-targeted.log`: 73 passed, 2 known skips).

| Test | As built (`5cef802`) | Fixed |
|---|---|---|
| `tests/test_identifier_check.py` (fake now aliases its pages like Oracle did; one-page reads; no-progress reads; row without key; key-less count; sampled-leaf fallback; SQL column missing; `rows_read`; `key_floor`; `_best_effort` → error) | 11 failed | 16 passed (with documents file) |
| `tests/test_identifier_check_documents.py` (fake honours `after_key`; `rows_read`) | failed | passed |
| `conformance.py::test_paging_does_not_depend_on_what_the_consumer_does_with_a_page` (int/text/key-less, consumer empties rows and page) | Oracle 3/3 and MongoDB 3/3 failed; PG passed (key-less skip `no_key`) | 8 passed, 1 skip |
| `test_identifier_check_engines.py` (scratch 20 000 rows, real `CHECK_BATCH`, PG/Oracle/MongoDB, + ROWID-paged Oracle target, key-less MongoDB collection, append after floor 5 000) | Oracle and MongoDB: `checked` 5 000, `in_source` 0; PG: no `rows_read` | 3 passed |
| `test_identifier_check_runs.py` (in-process synthesize → created table → finalize, `CHECK_BATCH` 128) | Oracle `TAX_ID` checked 71 of 1 821; MongoDB `$.kyc.taxId` 68 of 355 | 2 passed |

## 4. Proofs (counts only; files in `~/.cache/dps-build/reports/S8-idcheck/`)

**a. The requested scratch proof** (`probe_planted_hit.py` → `.as_built.json` / `.fixed.json`): probe-owned
Oracle user and MongoDB database (both dropped, confirmed), target and source of 20 000 rows each; one source
ACCOUNT_NUMBER planted at target row 15 000 (source row 18 000), one issuable source NATIONAL_ID at target
row 17 500 (source row 19 000); MongoDB target keyed by ObjectIds, leaves `$.accounts[*].number`,
`$.kyc.nationalId`.

| Engine | As built: checked / in_source / issuable / review_columns / key_floor | Fixed |
|---|---|---|
| Oracle | 5 000 / 0 / 0 / [] / 5 000 (both columns) | ACCOUNT_NUMBER 20 000 / 1 / 0; NATIONAL_ID 20 000 / 1 / 1; review both; `rows_read` 20 000 / 20 000; key_floor 20 000 |
| MongoDB | 5 000 / 0 / 0 / [] / 5 000 | same counts on the two leaves; review both; `rows_read` 20 000 / 20 000; key_floor 20 000 |

**b. Deployed image at HEAD** (`data-privacy-services:fidcheck`, app and worker: sha256 of 190 tracked
`dps/` + `tools/` files identical to `9fe68f4`, `app_vs_head.diff`/`worker_vs_head.diff` empty).
`probe_product_runs.py` (tag `s8b`): 20 000-row synthesize `table` runs through the API, every pack checked
against an independent count of the target (and of `in_source`):

| Run (all `succeeded`, `passed`, `scope: whole_table`, `rows_read` 20 000 / 20 000) | Column: pack `checked` = target values | As built (S1 r3 packs) |
|---|---|---|
| Oracle `CRM.CUSTOMERS` → `CRM_MASKED.CUSTOMERS_SYN_S8B` | `NATIONAL_ID` 20 000 = 20 000; `TAX_ID` 11 989 = 11 989 | 5 000; 2 994 |
| Oracle `CRM.KYC_DOCUMENTS` → `KYC_SYN_S8B` (CHAR key) | `DOC_NUMBER` 20 000 = 20 000 | 5 000 |
| MongoDB `crm.profiles` → `crm_uat.profiles_syn_s8b` | `$.accounts[*].iban` 20 000 = 20 000; `$.kyc.taxId` 11 989 = 11 989 | 5 000; 3 002 |
| PostgreSQL `bank.customers` (control) | `national_id` 20 000; `tax_id` 11 989 | unchanged |

`in_source` 0 everywhere = the independent count (0). (A first pass, `probe_product_runs.200rows-after-gate-reseed.json`,
ran on 200-row sources: the M3 gate's tests reseed the shared demo with 200 rows; reseeded to 20 000 and rerun.)

`probe_product_plant.py`: copies of those targets plus ONE planted row after every pseudonymised key (Oracle key
3·10⁹ with the source's last `NATIONAL_ID`; MongoDB `_id` ffff… with the source's last IBAN), then a synthesize
`table_load: upsert` of the same source → 40 001-row targets: Oracle `NATIONAL_ID` checked 40 001, `in_source` 1,
`issuable` 1; MongoDB `$.accounts[*].iban` checked 40 001, `in_source` 1; both packs `review_required: true`,
`passed: false`, `rows_read` 40 001 / 20 000.

**c. Logs** (`logscan.py` → `logscan.json`): app + worker logs since the HEAD image started: 0 hits of 109 792
demo source values (PG customers, Oracle customers/KYC, MongoDB IBANs/tax ids), 6/6 probe job ids found
(control), 0 tracebacks.

## 5. Gates (lane stack, `./lane.sh`, `DPS_TEST_REQUIRE_SERVICES=1` for the markers)

| Gate | Result |
|---|---|
| `ruff format --check`, `ruff check` (dps tests tools), `mypy dps` | clean (437 files, 150 sources) |
| Whole suite (services up, not required) | 3 540 passed, 98 skipped, 6 failed — none from this change: `test_config_m2` was my `.env.lane` exporting `DPS_WORKER_METRICS_PORT` (removed; passes); 5 fail only with the spaCy English model installed, **identically at `5cef802`**, and pass/skip without it (§6a) |
| M3 gate `-m "oracle or mongo"` | **188 passed, 1 skipped** (MongoDB composite), 6:23 |
| M2 gate `-m "db or pg_source or s3"` | run 1: 438 passed, 1 failed — a console test needing the smoke's `bank-restore-rw` connection (precondition; passes after smoke). Run 2: 437 passed, 2 setup errors — one intermittent fail-closed `leakage` of a PG **obfuscate** run in the console M3-groups fixture (§6b); module 3/3 passed in two reruns |
| Conformance on all three engines | in the gates: PG, Oracle, MongoDB incl. the new rule |
| `tools/smoke_test.py --m3` against the lane app | **39 passed**, 2 skipped (OCR not up) |

## 6. Observations outside this job (not caused by it)

a. **spaCy model installed → 5 failures** (also at `5cef802`): `tests/scanner/test_capture.py::test_obfuscated_capture_tokens_the_dom_and_reveals_with_the_key`,
   `test_capture_residual.py::test_masked_capture_boxes_every_copy_of_a_detected_value`,
   `test_crawler.py::test_full_demo_crawl_in_memory`, `::test_dialogs_dismissed_and_popups_closed` (captures
   `withheld: leakage`), `tests/test_playground_samples.py::test_text_sample_with_ner_on_still_never_returns_the_name`
   (`partial`, not `passed`). The gates elsewhere run without the model ("installed nowhere"); `fllm`'s venv has
   it. If production may run NER-on, worth a look. I removed the model from this venv afterwards.
b. **Intermittent `leakage` on PostgreSQL `bank.customers` obfuscate** (job `j_01M5043CJ1RZFQ9VVMJR1H72RC`:
   15 batches committed, batch 16 failed closed, 0 hits recorded in the pack) — the same symptom S2 console r3
   saw on main (`j_01M4YPD09E5MX92DEKJS9MC8TA`). PostgreSQL obfuscate paths are untouched by this change.
c. The M3 gate's console tests overwrite 54+ tracked PNGs in `docs/superpowers/build/m3-shots/` (known polish
   item): restored with `git restore` (my run's artefacts). The M3/M2 gate tests reseed the shared demo Oracle/
   MongoDB with 200 rows.

## 7. Deviations (also in `M3-DEVIATIONS.md`)

- New evidence key `rows_read: {target, source}` (contract §7.1 amended); packs sealed before keep their counts.
- A key-less MongoDB collection that gains documents while the check reads it fails closed (review): a count
  cannot tell a short read from a concurrent insert. Keyed objects need no count (the empty read after the last
  key is the proof).
- `key_floor` failing to read to its end fails the synthesize append at prepare (before any write).

## 8. State left behind

- Lane stack `dps-wt-fidcheck` **stopped** (`docker compose stop`; volumes kept; Oracle has no volume, the
  stopped container keeps its data). Image `data-privacy-services:fidcheck` = `9fe68f4`.
- Targets in the lane's databases: `bank_uat.customers_syn_s8{,b}`, `CRM_MASKED.{CUSTOMERS,KYC}_SYN_S8{,B}`,
  `CRM_MASKED.CUSTOMERS_PLANT_S8`, `crm_uat.profiles_syn_s8{,b}`, `crm_uat.profiles_plant_s8`, plus the
  smoke's objects. Demo sources: 20 000 customers (seed 7, model 2) on all three engines. Scratch Oracle users
  `DPS_T_S8*` and MongoDB databases `dps_t_s8*` dropped (confirmed).
- `.env.lane` no longer exports `DPS_WORKER_METRICS_PORT` (it collided with `Settings.worker_metrics_port`).
- Logs: `~/.cache/dps-build/s8-idcheck/logs/` (one pytest traceback that printed synthetic demo values was
  redacted). Probes and outputs: `~/.cache/dps-build/reports/S8-idcheck/`.
