# M3 G3 fix, round 2 (Tasks 9-14): R2-B1, R2-B2

Worktree `/Users/neelu/document_intelligence/dps-wt-m3`, branch `m3`. Start: HEAD `a183f52`, tree clean, so
there was no earlier partial attempt. One commit: **`d5b734b`** `fix(m3): Mongo reads project whole top-level
fields so in-place array positions are exact; upserts never set an empty sub-document over a match`.
Author: NilenduGanguli <nilendu.ganguli@gmail.com>. No trailer. Explicit pathspec.

## R2-B1: projected in-place write through a mixed array (privacy leak). Fixed.

**Root cause.** `mongo._projection` passed `Selection.columns` to the server as dotted paths. A dotted inclusion
projection drops the non-document elements of every array it crosses. Positional `$set` paths from
`leaf_updates` (`accounts.2.iban`) then addressed the wrong stored element.

**Fix.** I took the review's first option and applied it to every write mode:

* `_projection` projects the **top-level field** of each `columns` entry: `accounts.iban` becomes `accounts`,
  and `_id` is always included. Array positions read are therefore the stored positions.
* I applied it to every mode, not only `in_place`, because a read does not know its write mode (the connector
  is stateless). It also makes the read equal `read_projection`, as contract §5.2 requires.
* `selection.read_projection` now folds a dotted MongoDB `columns` entry to its top-level field (new
  `_fold_column`; the Oracle upper-case fold is unchanged). The plan therefore covers every leaf the
  connector reads. This also closes R2-N1's "dotted columns plan nothing".
* `jobs_run._prepare` de-duplicates projection names (`dict.fromkeys`). This is a no-op for SQL.

**Behaviour change (documented):** a dotted Mongo `columns` entry no longer narrows *within* a top-level
field. `profile.fullName` now reads, plans and writes all of `profile`. Every leaf of `profile` is planned, so
nothing unreviewed passes in clear. This change also closes the sparse read (`profile: {}`).

**Tests:**

* New: `test_mongo_write_fidelity.py::test_a_projected_in_place_write_keeps_array_positions`.
  * Before the fix it failed with `target_error: mongo:28`: the shifted path hit a scalar element.
  * Setup: 20 documents, each with
    * `accounts: [{iban}, "note", {bal}, {iban}]`
    * `matrix: [[{iban}, "x"], "y", [7, {iban}]]`
    * `columns=["accounts.iban","matrix.iban"]`
    * `det_token` on `$.accounts[*].iban` and on `$.matrix[*][*].iban`
    * in-place write
  * Result: **0/80 source IBANs survive**. **0/20 documents change structure** (element counts, element kinds,
    keys and every non-IBAN scalar are compared). There are 4 tokens per document.
* New offline test: `test_selection.py::test_read_projection_folds_mongo_paths_to_top_level_fields`. On the
  HEAD code, `read_projection` returned `['$._id']` only. I proved this by loading the HEAD module from a
  scratch copy.
* Updated to the new rule. No masking or kept-field assertion was removed.
  * `test_mongo_connector.py::test_projection_reads_the_top_level_fields_of_selected_paths` (renamed).
  * `test_mongo_writer.py::test_in_place_sets_leaves_only` (the read now holds `profile.dob`).
  * The r1 B3 fidelity test (inserted documents now hold `fullName` and `dob`).
* Reviewer probe `r2/probe_r2c.py` after the fix:
  * mixed document: surviving source IBANs 1 before, **0 now**; element types are `dict,str,dict,dict` with
    the original keys, and element 2 no longer gains an `iban`;
  * regular documents: 0/30 survivors.

## R2-B2: projected upsert set an empty sub-document over a match. Fixed.

**Fix.** The new `mongo_docs.upsert_update(doc)` returns the whole update document:

* `$set` goes through sub-documents, as before.
* **Empty sub-documents go to `$setOnInsert`.** `$setOnInsert` is omitted when there are none. The `$set` and
  `$setOnInsert` paths are disjoint leaves, so they never conflict.
* `upsert_fields` keeps its name and returns the `$set` part.
* `MongoWriter._table_ops` sends `upsert_update`.

**Tests:**

* New: `test_mongo_write_fidelity.py::test_a_projected_upsert_keeps_matched_fields_a_sparse_row_lacks`.
  * Before the fix it failed with `KeyError: 'fullName'`.
  * The sparse source `{profile:{dob}}` keeps the target's `fullName` and a nested sub-document.
  * The empty source `{profile:{}}` keeps the target's whole `profile`.
  * The inserted document gets `profile: {}`.
* New offline test: `test_mongo_docs.py::test_upsert_update_never_sets_an_empty_sub_document_over_a_match`.
* Updated: `test_upsert_fields_set_through_sub_documents_only` (`empty` moved to `$setOnInsert`; it also checks
  `upsert_update` and its bad-name cases).
* Reviewer probe `r2/probe_r2b.py`: target A keeps `fullName: TF`, and target C keeps all its fields. Target A's
  `dob` is now the source's, because the row carries the whole `profile` (by design, see R2-B1).
* `probe_r2.py` C:
  * S0 (`profile: {}`) no longer loses anything.
  * S1/S2 (`profile: null` / `"flat"`) and S4 now carry their top-level `profile` value, so it is set. This is
    the same as a projected SQL column that holds NULL.
  * S3: the array is set whole, as R2-N2 documents.

## Contract and deviation log (same commit)

* Contract §4.2 amended: Mongo `table` upsert with `$setOnInsert` for empty sub-documents (`upsert_update`), and
  Mongo reads project top-level fields.
* Contract §5.2 amended: `read_projection` folds MongoDB dotted `columns` to their top-level field.
* `docs/superpowers/build/M3-DEVIATIONS.md` has a new section, "Review G3 round 2 fixes (Tasks 9-14)".

## Gates (all through `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format dps tests tools` / `--check` | 284 files unchanged / already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success: no issues found in 108 source files |
| `pytest -q` (mongo only up) | 1731 passed, 257 skipped |
| `pytest -q` (lane `--profile sources --profile oracle` up: dps-db, source-pg, minio, mongo, oracle, app, worker) | **1977 passed, 11 skipped** |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "oracle or mongo"` | **141 passed, 1 skipped** |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m mongo tests/connectors` (mongo only) | 55 passed, 1 skipped, plus 1 error from `test_tokens_agree_across_engines`, which needs Oracle. It passed in the full run above. |

The 11 skips in the full run are:

* the Mongo composite-key capability case;
* 3 `test_console_m2` cases, which need the demo connections from `tools/smoke_test.py`;
* the docs/OCR-mock cases (`--profile docs` was not started);
* tesseract cases (no binary on the host);
* the presidio/spaCy `ner` extra, which is not installed.

None of these touch G3. No frontend files changed, so the npm gates do not apply. Docker: I brought this lane's
stack up and then ran `docker compose --profile sources --profile oracle down` on this lane's project only.

## Open issues (not blocking, unchanged)

* R2-N2: a `table` upsert overwrites a matched document's array whole (documented).
* R2-N3: `InvalidBSON` from an out-of-range date is not mapped to `source_error`.
* R2-N4: the engine's DATE span leaves the time part in clear.
* R2-N5: `fpe_digits` on a short int fails closed, which is correct.
* For T15/T19: a key-less `selection.sample` count in `_scan_ids` (r1 open item).
* For T15/T19: the merger must keep the new top-level rule when Mongo enters `PIPELINE_ENGINES`. Elsewhere,
  plan columns are matched by name, and every leaf column of a top-level field shares that name, so they are
  consistent.
