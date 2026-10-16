# M3 G2 review, round 3 (Tasks 5–8 after fix round 2)

Reviewer: independent agent · Date: 2026-10-13 · Worktree `/Users/neelu/document_intelligence/dps-wt-m3`,
branch `m3`, HEAD `d565836` (fix r2), on top of `bd18b15` (fix r1) and T5–T8 (`6366422`, `2b8eb25`, `e6272d4`,
`f795666`). The tree was clean before and after; no repository file was modified. Probes live in
`/Users/neelu/.cache/dps-build/reports/probe-m3-G2/r3/`. I did not rely on `M3-G2-impl.md` or `M3-G2-fix-r2.md`;
every claim below was read in the code or run.

**Verdict: FAIL — 1 blocking finding (B6: the B5 fix is bypassable on both engines; raw e-mails reach the
target under a `keep` column with `verification.passed = true`).** B3 and B4 are fixed and independently
verified. All gates are green. The pitfalls, no-lock reads and kill fencing still hold at HEAD.

---

## 1. Gates (run by the reviewer via the lane wrapper; lane containers dps-db, oracle, source-pg, minio, minio-init)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 268 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 106 source files |
| `./lane.sh pytest -q` | **1799 passed, 11 skipped** (176.6 s) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "(db or pg_source or s3 or oracle) and not browser and not mongo"` | **235 passed** (163.5 s) — includes `test_oracle_pitfalls.py`, `test_oracle_pipeline.py`, `test_oracle_writer.py`, `test_oracle_connector.py`, `test_run_modes.py` |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "slow and not browser and not mongo"` | **7 passed** (M2 kill/resume ×6, HTTP e2e) |
| Frontend | not touched by T5–T8 or fixes r1/r2 (not run) |

The 11 skips are the same as r2 (3 console tests without the app container, 3 OCR-mock, 1 Tesseract, 1 Mongo
stack test (G3), 2 `[ner]` tests: extra not installed in this lane). None covers code touched by T5–T8 or
the fixes. Afterwards `docker compose --profile sources --profile oracle down` for this project only.

## 2. Round-2 blockers: verification

### B3 (key-less Oracle `upsert` appended on every run): FIXED

- Code: `targets.validate_run` refuses `table` + `upsert` when `snapshot.shape.key_source in ("rowid","none")`
  (`no_key` 422); `OracleWriter._prepare_sync` refuses the same first thing, before describe/create/truncate/
  ledger.
- Re-run of the r2 probe `test_r2_keyless_upsert_twice` at HEAD: submit now raises `SpecError no_key`
  ("upsert needs a key: a key-less table loads with truncate_insert") — the job is never created.
- Repository tests read and run: `test_targets.py::test_a_keyless_upsert_is_refused_at_submit`,
  `test_oracle_writer.py::test_a_keyless_upsert_is_refused_and_writes_nothing` (existing target keeps its 7
  rows; nothing created), `test_oracle_pipeline.py::test_a_keyless_upsert_is_refused_and_truncate_insert_
  reloads_exactly` (500 rows twice, 0 duplicated groups, 0 surviving e-mails). The assertions do test the claim.
- Key-less resume (B2) re-proved at HEAD (`r3/r2rerun/r2_p6_keyless_resume.json`): two lost checkpoints,
  1200 written = 1200 in target = 1200 distinct, 0 duplicate groups, 0 surviving e-mails, 0 ledger rows left.

### B4 (lower-case Oracle `columns` bypassed submit-time method checks): FIXED

- Code: `targets._projection` is now `read_projection(snapshot.shape, selection, engine)` (the run's own
  projection); rules live in `check_projected_methods`, called by `validate_run` and again by
  `jobs_run._prepare` over the final projection (`SpecError` → `PrepareFailure`, same code, value-free detail).
- Re-run of the r2 probe (`r3/r2rerun/r2_lowercase_columns.json`): `NOTES → keep` (free text) — `["NOTES"]`
  and `["notes"]` both refused `text_method_invalid` at submit.
- Subset queries still work (`r3/r2rerun/r2_p3_subset.json`): exact target columns, 0 NULLs, 0 surviving
  `CUSTOMER_ID`/`FULL_NAME`/`EMAIL`.

### B5 (query alias puts a sensitive column under a kept column's name): PARTIALLY FIXED → see B6

- The r2 probe query `SELECT contact_id, email AS channel …` is now refused `selection_query_alias` at
  submit (r2 probe `test_r2_alias_swap_fitting` re-run: SpecError). The 15 refused / 9 allowed unit shapes
  are sound for what they cover.
- But the check only inspects the select list and treats any `exp.Table` with the object's name as "the
  object", ignoring FROM-clause constructs that rename or re-map columns. See B6.

## 3. Blocking finding

### B6: `_check_query_outputs` is bypassed by FROM-clause column re-mapping — a PostgreSQL alias column list and an Oracle `UNPIVOT` put e-mail addresses under CHANNEL (`keep`); the run succeeds with a passing evidence pack

- **Where:** `dps/layers/connectors/selection.py` `_query_sources` (lines ~635–663). A source counts as the
  object when `isinstance(src, exp.Table)` and its name/owner match; it does not look at
  `src.args["alias"].columns` (a `TableAlias` column list) or `src.args["pivots"]` (PIVOT/UNPIVOT), nor at
  `match_recognize`. `_own_column` then accepts an output whose *name* is CHANNEL and whose qualifier is
  that source — but the source's CHANNEL is not the table's CHANNEL.
- **Validator probe** (`r3/p_sel.py`, `r3/p_sel2.py`, shapes `PG_CONTACTS`/`ORA_CONTACTS` from the repo tests):
  accepted by `validate_selection` with a shape:
  - PG: `SELECT c.contact_id, c.channel FROM crm.contacts AS c(contact_id, channel, email)`;
    the same unqualified; `SELECT * FROM crm.contacts AS c(contact_id, channel, email)`; `SELECT c.* …`;
    `… FROM crm.contacts c(contact_id, channel)`; `… FROM ONLY crm.contacts AS c(…)`.
  - Oracle: `SELECT contact_id, channel FROM crm.contacts UNPIVOT (channel FOR src IN (email, channel))
    WHERE src = :s` (also `UNPIVOT INCLUDE NULLS`, `SELECT * … UNPIVOT …`), and
    `… FROM crm.contacts MATCH_RECOGNIZE (… MEASURES email AS channel …)` (validator only, not run E2E).
- **End-to-end proof (counts only; `r3/test_probe_r3.py`):** 400-row `CONTACTS(CONTACT_ID pk, EMAIL, CHANNEL)`,
  snapshot discovered and accepted plainly: `EMAIL → email`, `CHANNEL → keep`. `write_mode: table`.
  - PostgreSQL (`r3/r3_pg_alias_list.json`):
    - `SELECT c.contact_id, c.channel FROM "<s>".contacts AS c(contact_id, channel, email)` → submit
      accepted, job `succeeded`, 400 rows, **400/400 target `channel` values equal a source e-mail**,
      evidence `verification.passed = true`.
    - `SELECT * FROM "<s>".contacts AS c(contact_id, channel, email)` → same: **400/400**, evidence passed.
  - Oracle (`r3/r3_ora_unpivot.json`): `SELECT contact_id, channel FROM "<owner>".contacts UNPIVOT (channel
    FOR src IN (email, channel)) WHERE src = :s` with `params {s: "EMAIL"}` → submit accepted, job
    `succeeded`, target columns `CONTACT_ID, CHANNEL`, 400 rows, **400/400 `CHANNEL` equal a source e-mail**,
    evidence passed.
- **Why blocking:** a leak of raw customer values into a target with a false masking claim in the evidence
  pack; it contradicts contract §5.2 as amended in `d565836` ("A FROM source is the object when it is a
  *plain table* with the object's name … an output named like a column must be that column itself") and the
  round-2 blocker it was meant to close.
- **Fix (test-first, `selection.py`, both engines):** in `_query_sources`, a table counts as the object only
  when it is a plain reference: no `TableAlias` column list (`src.args["alias"]` with non-empty `.columns`),
  no `pivots`, no `match_recognize`, no `laterals`/table-function args (`exp.Table` with `this` not an
  `Identifier` is already skipped), no `sample`-that-renames, and (Oracle) no flashback/`AS OF` once parsed.
  Simplest robust rule: refuse the whole query (`selection_query_alias`) whenever any FROM/JOIN source that
  matches the object carries `alias.columns`, `pivots` or `match_recognize`, or when any node of those types
  appears anywhere in the statement (`exp.Pivot`, `exp.MatchRecognize`, `exp.TableAlias` with columns).
  Defence in depth worth considering: after the read, the connector could compare the result's column
  types/lengths against the shape (`cursor.description`), but the parse-level refusal is the fix.
  Add the shapes above to `ALIAS_REFUSED` for each engine, and one E2E per engine (the probes above as
  repository tests: 422 at submit, no target created; discovery fails `selection_query_alias`).
  Also amend contract §5.2 to name "no column alias list, PIVOT/UNPIVOT or MATCH_RECOGNIZE on the object".

## 4. Programme-plan acceptance (§2.2 G2-finish, G2-review)

| Criterion | Result | Evidence |
|---|---|---|
| Oracle flows through registry, discovery and finalize seams | yes (B6 aside) | `test_oracle_pipeline.py` green; `test_registry_m3.py`, `test_pipeline_seams_m3.py`, `test_discovery_m3.py` green in the full run |
| Wildcard doc paths | yes | `test_docpaths.py`, `test_discovery_m3.py` green |
| CLOB UTF-16 offsets, CHAR binds, TIMESTAMP(7–9) paging on Oracle Free | yes | `test_oracle_pitfalls.py` green at HEAD (unchanged since r1, where each was read and judged) |
| Reads never `FOR UPDATE` | yes | `r3/kill/p5_report.json`: 7 `read_partition` pages, 0 locks and 0 transactions on the tagged session (`module=dps`, `action=partitions`); 0 `FOR UPDATE` among 5 `dps:` statements in `v$sql` |
| Ledger fencing survives kill | yes | `r3/kill/p2_report.json`: batch 2 SIGKILLed mid-`executemany` → 0 batch-2 rows, ledger 1; stale replay of batch 1 → `LedgerConflict`, nothing written; resume wrote 29,000 exactly once, ledger 2. `p2b_report.json`: 50k/150k batches complete |

## 5. Deviation-log judgements (new section "Review G2 round 2 fixes")

| Entry | Judgement |
|---|---|
| B3 refused twice (submit + Oracle prepare), contract §4.2/§5.3 | Accept |
| Behaviour change: PG key-less `upsert` refused at submit (was prepare `no_key`) | Accept (earlier, same code) |
| B4 one projection at submit and run; `check_projected_methods` shared; prepare re-checks | Accept |
| Behaviour change: a column a query leaves out is no longer method-checked at submit | Accept (it is neither read nor written; prepare re-checks what is) |
| B5 `_check_query_outputs`, new code `selection_query_alias`, contract §5.2 | **Reject as complete** — the mechanism is right, but "a plain table with the object's name" is not enforced (B6) |
| Behaviour change (M2 too): alias/expression/other-source/`*`-over-join queries refused | Accept |
| Residual: an unqualified table name is taken to be the object although the DB resolves it via `search_path` / login schema / synonyms | Accept as an open, non-blocking issue: the jobs-scope caller can already discover and accept another object; recommend requiring qualification whenever the object has an owner (cheap, the console always qualifies) |

## 6. Privacy checks (counts only)

- Source-vs-target comparisons: §2 and §3, all from SQL joins in the probes; no value printed or stored.
- Log scan: the r3 probes plus the new repository integration tests (`-k "unpivot or alias or keyless or
  lower_case"` over `test_oracle_pipeline.py`, `test_run_modes.py`) at `--log-cli-level=DEBUG -s`, 229 lines
  (`r3/logscan.out`): 0 `@example.(com|org)`, 0 `contact.N`/`lead.N`/`Person NNNNN`, 0 password/secret/key
  strings. The one `partition_failed error=ConnectionError` line comes from the repo's lost-checkpoint test
  and carries no value.
- New error texts (`no_key`, `selection_query_alias`, prepare re-check) carry codes and rules only.

## 7. Non-blocking findings

- Carried forward, unaddressed: r1 N1–N8 and r2 N9–N11 (see `M3-G2-review-r2.md` §7).
- **N12 (new, style):** `oracle.py:986` (the `OracleWriter` docstring line edited for B3) is 176 characters;
  several other lines in `oracle.py` exceed the 100-char standard (ruff's E501 is not enforced here).
- **N13 (new, test data):** the Oracle `CUSTOMERS` fixture's `TAX_ID` values (`ABCDE0001`, 9 chars) match no
  detector, so discovery classifies `TAX_ID → keep`; no pipeline test pushes a *masked* `CHAR(10)` column
  through the writer (the CHAR-bind pitfall is covered in `test_oracle_pitfalls.py`, but an end-to-end masked
  CHAR column would add confidence). Consider a PAN-shaped 10-char value.
- **N14 (new):** `_projection`'s fallback on an unparsable selection returns snapshot ∪ shape columns, then
  `_bind` refuses; fine, but the order makes a `method_needs_key`/`text_method_invalid` 422 win over the real
  `selection_*` code for a broken query. Cosmetic.

## 8. Probe index (`/Users/neelu/.cache/dps-build/reports/probe-m3-G2/r3/`)

- `gates.out` — format/lint/mypy/pytest output.
- `p_sel.py`, `p_sel2.py` — validator probes (accept/refuse per query).
- `test_probe_r3.py` → `r3_pg_alias_list.json`, `r3_ora_unpivot.json` (**B6**).
- `test_probe_r2_rerun.py` → `r2rerun/` (B3/B4/B5 re-checks, subset, key-less resume).
- `kill/` — kill-fencing and no-lock probes re-run at HEAD (`p2_report.json`, `p2b_report.json`, `p5_report.json`).
- `logscan.out`.

Run from the worktree: `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -p no:cacheprovider -c pyproject.toml
-p tests.conftest --rootdir=. <probe>` (needs dps-db, oracle and source-pg).
