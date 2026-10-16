# M3 G2 fix, round 2 (review `M3-G2-review-r2.md`: B3, B4, B5)

Worktree `/Users/neelu/document_intelligence/dps-wt-m3`, branch `m3`. Start: HEAD `bd18b15` (fix r1), tree
clean, no earlier r2 attempt (nothing to resume). Commit: **`d565836`** `fix(m3): refuse key-less upserts,
check methods over the run's own projection, bind query outputs to their own columns`. Author
NilenduGanguli <nilendu.ganguli@gmail.com>, no trailer, explicit pathspec. Tree clean afterwards. Lane
containers were brought down after the integration runs (this project only).

Files: `dps/layers/connectors/{selection,targets,jobs_run,oracle}.py`;
`tests/connectors/test_{selection,targets,oracle_writer,oracle_pipeline,run_modes}.py`; contract
`docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (§4.2, §5.2, §5.3); `docs/superpowers/build/M3-DEVIATIONS.md`
(new section "Review G2 round 2 fixes").

## B3: a key-less Oracle `upsert` appended on every run

**Fix (both layers):**
- **Submit:** `targets.validate_run` refuses `write_mode: table` + `table_load: upsert` when
  `snapshot.shape.key_source` is `rowid` or `none`. It raises `no_key` · 422 ("upsert needs a key: a key-less
  table loads with truncate_insert"), placed after the target checks.
- **Prepare:** `OracleWriter._prepare_sync` raises `PrepareFailure("no_key", …)` for the same combination. It
  does this first, before it describes, creates, truncates or ledgers anything.
- **Docs:** the `OracleWriter` docstring, contract §4.2 ("key-less `table` loads are `truncate_insert` only")
  and the §5.3 validation table (new row) were updated.

**Tests written first, each failing before the fix:**

| Test | Before | After |
|---|---|---|
| `test_targets.py::test_a_keyless_upsert_is_refused_at_submit` | DID NOT RAISE | `no_key` 422. Key-less `truncate_insert` and keyed `upsert` are still accepted |
| `test_oracle_writer.py::test_a_keyless_upsert_is_refused_and_writes_nothing` | failed (no raise) | `no_key` for an existing target (real run and dry run) and for a new one. The existing target keeps its 7 rows, no target is created, and `truncate_insert` still prepares |
| `test_oracle_pipeline.py::test_a_keyless_upsert_is_refused_and_truncate_insert_reloads_exactly` (500-row `LEADS`, the probe's scenario) | DID NOT RAISE | `upsert` gives 422 `no_key` at submit. `truncate_insert` run twice leaves **500 rows each time**, **0 duplicated `LEAD_NO` groups** and **0 surviving source e-mails** |

## B4: lower-case Oracle `columns` bypassed the submit-time method checks

**Fix:**
- **Shared projection:** `targets._projection(spec, snapshot, engine)` now returns
  `read_projection(snapshot.shape, selection, engine)`. That is the run's own projection: the query's select
  list, `columns` folded per engine, and the keys. If a selection does not parse, it returns every column (the
  strictest set); `_bind` then refuses the selection itself.
- **Shared method rules:** the rules moved into the new public `targets.check_projected_methods(snapshot, mode,
  projection)`.
  - `validate_run` calls it at submit.
  - `jobs_run._prepare` calls it again over the final projection, as defence in depth. A `SpecError` there
    becomes a `PrepareFailure` with the same code.

**Tests written first, each failing before the fix:**

| Test | Before | After |
|---|---|---|
| `test_targets.py::test_submit_method_checks_cover_what_the_run_reads` | DID NOT RAISE at `columns=['notes']` | Oracle `NOTES` / `notes` / `Notes` with kept free-text NOTES gives `text_method_invalid` 422. Synthesize-mode `FULL_NAME` / `full_name` with `det_token` gives `method_needs_key` 422. The PG query subset is refused with `notes` and accepted without it |
| `test_targets.py::test_projected_method_checks_are_shared_with_prepare` | ImportError | pass |
| `test_oracle_pipeline.py::test_lower_case_columns_meet_the_same_method_checks` (the probe's scenario, a `NOTES → keep` override) | DID NOT RAISE | all 3 spellings give 422 `text_method_invalid`, and no target table is created |

## B5: a query alias put a sensitive column under a kept column's name

**Fix in `selection.py` (both engines):**
- **The rule:** the new `_check_query_outputs` runs inside `validate_selection` whenever a shape is given. A
  select-list item whose folded output name is a column of the object must be a plain `exp.Column` reference
  to that same column:
  - unaliased, or aliased to itself;
  - qualified only by the object's own `FROM` alias (or by its table name when it has no alias);
  - unqualified only when the object is the query's only `FROM` source.
- **Stars:** a bare `*` needs the object as the only source. `t.*` needs `t` to be the object's qualifier.
- **What counts as the object:** a plain table with the object's name and, when qualified, its owner. For PG,
  an object with no owner is in `public`. An unqualified name that a `WITH` clause defines is the CTE, not the
  object.
- **Unchanged:** outputs with other names are still dropped from the read.
- **Error code:** new code `selection_query_alias`, checklist entry `identifiers_validated`.
- **Where it is enforced:**
  - submit (`_bind`);
  - the discovery job;
  - `jobs_run._prepare`;
  - `OracleConnector._compile`;
  - the `runs_as` preview.
- **Docs:** contract §5.2 amended, replacing r1's "output names are the column identities" reading.

**Tests written first, each failing before the fix:**

| Test | Before | After |
|---|---|---|
| `test_selection.py::test_a_query_output_named_like_a_column_must_be_that_column[postgres, oracle]` | failed (no raise) | Per engine:<br>- **15 refused shapes:** an alias, an expression, a concatenation, an alias-qualified alias, a scalar subquery, another table's column (qualified and unqualified in a join), a foreign table aliased as the object, the wrong table, the wrong schema, a derived table through `*` and through a qualifier, a CTE that shadows the object, `*` over a join, and another source's `.*`.<br>- **9 allowed shapes:** plain columns, a self-alias, alias-qualified and table-qualified columns, an unqualified table, `*`, the object's `.*` in a join, qualified columns in a join, and a non-column expression output.<br>- The checklist reports `identifiers_validated` failed with `selection_query_alias`.<br>- Without a shape, no check is made. |
| `test_oracle_pipeline.py::test_a_query_alias_cannot_put_a_column_under_a_kept_columns_name` (`CONTACTS`, 400 rows, CHANNEL kept: the probe's scenario) | DID NOT RAISE | The 3 alias queries give 422 at submit, and no target is created. A discovery job with the alias query ends `failed selection_query_alias`. The plain query `SELECT contact_id, email, channel` gives:<br>- 400 rows;<br>- **0 surviving source e-mails**;<br>- 400/400 CHANNEL equal to the source row's CHANNEL (kept as reviewed);<br>- **0 CHANNEL values equal to a source e-mail**;<br>- evidence passed. |
| `test_run_modes.py::test_a_query_alias_cannot_put_a_column_under_a_kept_columns_name` (PG, `segment` kept) | DID NOT RAISE | The 3 alias queries give 422, no target table is created, and the discovery job ends `failed selection_query_alias`. The plain subset query still runs: the existing r1 test passes |

**How "before" was shown:** for the integration tests, I reverse-applied the uncommitted `dps/` diff, ran the 4
tests (4 failed, each with "DID NOT RAISE SpecError"), and re-applied the diff. For the unit tests, I ran them
before the implementation.

## Gates (lane wrapper; lane containers dps-db, oracle, source-pg, minio and minio-init; brought down afterwards)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 268 files already formatted |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 106 source files |
| `./lane.sh pytest -q` (services up) | **1799 passed, 11 skipped** (r1: 1789; +10 new) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "(db or pg_source or s3 or oracle) and not browser and not mongo"` | **235 passed** (r1: 230) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "slow and not browser and not mongo"` (incl. M2 kill/resume ×6, HTTP e2e) | **7 passed** |
| Frontend | not touched (not run) |

The 11 skips are unchanged from r1. None of them cover the touched code:
- 3 console tests: the app container is not started.
- 3 OCR-mock tests: docs profile.
- 1 Tesseract test: no binary.
- 1 Mongo stack test: Mongo is G3.
- 2 `[ner]` tests: the extra is not installed in this lane.

**Log scan:** I ran the 4 new integration tests at DEBUG with `-s` and live logs, capturing 135 lines. The
scan found:
- 0 `@example.(com|org)` strings;
- 0 `lead.N`, `contact.N` or `Person N` values;
- 0 password or key strings.

The new error messages carry codes and rules only, never SQL text or values.

## Deviations and behaviour changes (all logged in M3-DEVIATIONS.md)

- **PG key-less `upsert`:** it is now refused at submit (422 `no_key`) rather than failing `no_key` at
  prepare.
- **Submit-time method checks:** they now follow the read projection. A query that leaves a column out no
  longer has that column's method checked, because the column is neither read nor written. This reverses r1's
  "targets._projection not changed" entry, which the reviewer rejected.
- **Queries refused by B5, on both engines:** queries that alias or compute a value under an object column's
  name, take that name from another source, or use `*` over joins, derived tables or a shadowing CTE are now
  refused. This also applies to M2 PostgreSQL.

## Open issues

- **Residual of B5 (logged):** an *unqualified* table name in a query is treated as the object when its name
  matches. The database, however, resolves it through the PG `search_path` or the Oracle login's schema and
  synonyms. So a query could read a same-named table in another schema under the object's reviewed methods.
  Qualifying the table, as the tests and the console do, avoids this. Refusing unqualified names would break
  plain queries. Options: require qualification whenever the object has an owner, or bind the snapshot to the
  discovery query.
- The review's non-blocking items (r1 N1–N8, r2 N9–N11) are not addressed in this round.
