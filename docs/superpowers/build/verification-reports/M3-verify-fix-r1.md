# M3 lane verification fix round 1 (B6, B3-r3)

Branch `m3` in `/Users/neelu/document_intelligence/dps-wt-m3`. Starting HEAD was `392c1fe`. Two commits, both authored by NilenduGanguli <nilendu.ganguli@gmail.com> with no trailers:

- `de757f5` fix(m3): refuse column-list aliases, PIVOT/UNPIVOT and MATCH_RECOGNIZE in a query; render fails closed
- `da470f9` fix(m3): consistency targets key PostgreSQL objects on the resolved schema; case-fold Oracle only

Resume check: there was no earlier fix attempt. The working copy already held modified `docs/superpowers/build/m3-shots/*.png` files and an untracked `docs/screenshots/m3/`, both from the verifier's browser walk. I left them uncommitted and untouched.

## B6: query re-mapping (selection.py)

Tests came first. I added 11 shapes to `ALIAS_REFUSED`, which runs on both engines:
- column-list aliases on the object, on another source and on a CTE;
- UNPIVOT with a WHERE filter, `SELECT *` with UNPIVOT, and PIVOT;
- UNPIVOT on a parenthesised table, and UNPIVOT on a joined table.

I also added a new `ALIAS_REFUSED_ORACLE` list with 2 MATCH_RECOGNIZE shapes (PostgreSQL does not parse MATCH_RECOGNIZE). Before the fix, both engine cases failed with "DID NOT RAISE".

The fix is in `_check_query_outputs`. Before it looks at the select list, it walks the whole tree and refuses with `selection_query_alias` if it finds any of these:
- an `exp.Pivot` (covers both PIVOT and UNPIVOT);
- an `exp.MatchRecognize`;
- an `exp.TableAlias` that has a column list.

**Deviation:** the finding asked to refuse a column list on the object source only. I refuse a column list anywhere, including CTE column lists. A masking selection never needs one, and the stricter rule is simpler to audit.

### Second defect found while writing the PostgreSQL E2E test

PostgreSQL UNPIVOT still got 202 at submit after the fix above. The cause:
- sqlglot's postgres generator drops UNPIVOT. It only logs the warning "PIVOT unsupported".
- `submit._bind` validated only the rendered, bound text, and the UNPIVOT was already gone from it.
- So the stored and executed query differed from what the user wrote.

Two fixes for this:
- `_render` now generates with `unsupported_level=ErrorLevel.RAISE`. An `UnsupportedError` becomes `SelectionError("selection_parse_error", ...)` with a static message that contains no SQL. So the template or SQL can never differ silently from what was validated.
- `submit._bind` and `routers/connections._bind` now validate the text as written (with the shape) before binding.

Codes this produces:

| Where | PostgreSQL UNPIVOT is refused as |
|---|---|
| Run submit (shape known) | `selection_query_alias` |
| Discovery submit (no shape) | `selection_parse_error` |
| Count route, first shape-free pass | `selection_parse_error` |

Tests added for B6:
- `test_selection.py::test_rendering_fails_closed_on_a_construct_the_engine_cannot_express`
- PostgreSQL E2E: `test_run_modes.py::test_a_column_list_alias_or_pivot_cannot_remap_a_column`
  - 4 shapes (swapped column-list alias ×3 and UNPIVOT), each 422 `selection_query_alias` at submit;
  - `r_customers_uat` was not created (0 rows in `information_schema`);
  - the discovery job fails with `selection_query_alias`.
- Oracle E2E: `test_oracle_pipeline.py::test_unpivot_pivot_or_match_recognize_cannot_remap_a_column`
  - 5 shapes (UNPIVOT with `:s`, `SELECT *` with UNPIVOT, MATCH_RECOGNIZE, PIVOT, column-list alias), each 422 at submit;
  - `CONTACTS_PQ` was not created;
  - discovery with the UNPIVOT query fails with `selection_query_alias`.

Contract §5.2 is amended (2026-10-14).

## B3-r3: consistency target identity (consistency.py)

Tests came first. The new test `test_postgres_targets_key_on_the_resolved_schema_and_exact_name` failed before the fix: the derived check ran and returned `passed=True` for two refreshes of one PostgreSQL table.

The fix in `target_key`:
- For PostgreSQL, the object is rebuilt as `ObjectRef(owner=targets._owner(obj), name=...)`, so a missing owner becomes `public`.
- Object and path are case-folded **for Oracle only**. PostgreSQL names are quoted as given, and MongoDB is case-sensitive.

What the test asserts:
- Explicit pairs in both orders get 422 `member_duplicate`, and no connector is opened.
- `derive_members` and the derived check both get 409 `member_not_ready`.
- `public."Cust"` and `public."cust"` stay as two keys and two derived members.
- `$.Email` and `$.email` are different PostgreSQL paths.
- Oracle `crm.customers` with `$.email` still equals `CRM.CUSTOMERS` with `$.EMAIL`.

I updated the docstring, the contract member-identity wording and `M3-DEVIATIONS.md`.

Remaining limit, unchanged and documented: an Oracle object without an owner is not resolved to the connecting user's schema.

## Gates (lane stack: `docker compose --profile oracle --profile sources up --build -d`, rebuilt after the final code)

- `./lane.sh ruff format dps tests tools`: 303 files unchanged.
- `./lane.sh ruff check dps tests tools`: all passed.
- `./lane.sh mypy dps`: no issues in 109 files.
- `./lane.sh pytest -q`: **2092 passed, 8 skipped** in 7 min 18 s. The skips are the same 8 as the M3 baseline (OCR mock, host tesseract, `ner` extra and others). Log: `~/.cache/dps-build/reports/m3-verify-fix-r1-pytest.log`.
- `DPS_TEST_REQUIRE_SERVICES=1 ./lane.sh pytest -q` on the touched files (`test_no_values_in_logs_m3.py`, `test_selection.py`, `test_consistency.py`, `test_run_modes.py`, `test_oracle_pipeline.py`): **204 passed, 0 skipped**.
- No frontend file changed, so the npm gate did not apply.

### Live re-run of the verifier's probe

I ran `m3-verify-fix-r1/b6_e2e.py` (a copy of `m3-verify-r1/probes/b6_e2e.py`) against the rebuilt app. Output is in `m3-verify-fix-r1/b6_e2e.out`.

| Engine | Before (verify r1) | Now |
|---|---|---|
| PostgreSQL | 202, then 4000/4000 target city values equal to a source line1 | 422 `selection_query_alias`, target not created |
| Oracle | 202, job succeeded, 20000/20000 target CITY values equal to a source EMAIL | 422 `selection_query_alias`, target not created |

### Live count route (`m3-verify-fix-r1/count_probe.out`)

| Query | Result |
|---|---|
| PostgreSQL column-list alias | 422 `selection_query_alias`, failed check `identifiers_validated` |
| PostgreSQL UNPIVOT | 422 `selection_parse_error`, failed check `one_statement` (from the shape-free first pass) |
| Oracle UNPIVOT | 422 `selection_query_alias` |
| Oracle plain query | 200 |

### Log scan

I scanned the `app` and `worker` logs after the probes (23 lines). There were 0 lines matching pivot, unpivot, match_recognize, the probe's column list or "unsupported", so no query text was logged.

The stack was torn down with `docker compose --profile oracle --profile sources down`. Volumes are kept and 0 containers remain.

## Open issues

- The PostgreSQL UNPIVOT refusal code depends on the stage: `selection_query_alias` at run submit, `selection_parse_error` at discovery submit and in the count route. Both are 422 before anything is read. This is documented in the deviations log.
- An Oracle owner-less object is still not resolved to the user's schema in `target_key` (a pre-existing documented limit).
- The m3-shots PNG changes and `docs/screenshots/m3/` from the verifier remain uncommitted in the working copy. They are not part of this job.
