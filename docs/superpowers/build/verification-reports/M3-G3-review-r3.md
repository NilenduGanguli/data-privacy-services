# M3 G3 review, round 3 (Tasks 9-14): MongoDB rules, filter validation, MongoConnector, MongoWriter, engine bridge, conformance

Reviewer: independent agent, 2026-10-13.

* Working copy: `/Users/neelu/document_intelligence/dps-wt-m3`, branch `m3`.
* HEAD: `d5b734b` (the round-2 fix). The tree was clean before and after the review.
* I did not use `M3-G3-impl.md` or the fix reports as evidence. Every result below comes from reading the code
  and running it.
* No repository file was modified.
* Round-3 probes and logs are in `~/.cache/dps-build/reports/probe-m3-G3/r3/`. They use synthetic data only,
  and each probe drops its own database.

**Verdict: FAIL.** There are 2 blockers. Both are silent leaks in the document path of the engine bridge
(Task 9 shape inference and the Task 13 bridge). In both cases a job would report success with verification
passed. All gates are green. The round-2 blockers R2-B1 and R2-B2 are fixed.

## 1. Gates (run by me through `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 284 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success: no issues found in 108 source files |
| `pytest -q` (lane stack up via `--profile sources --profile oracle`: dps-db, source-pg, minio, mongo, oracle, app, worker) | **1977 passed, 11 skipped** (log `r3/gates_full.log`) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "oracle or mongo"` | **141 passed, 1 skipped** (`r3/gates_oracle_mongo.log`) |
| G3 file set with `DPS_TEST_REQUIRE_SERVICES=1` | **342 passed, 1 skipped** (`r3/gates_g3.log`) |
| Conformance suite per engine (`test_conformance_{postgres,oracle,mongo}.py`) | postgres 10/10, oracle 10/10, mongo 9 passed + 1 skip |

The G3 file set is: `mongo_docs`, `selection_mongo/oracle`, `selection`, `mongo_connector`, `mongo_writer`,
`mongo_write_fidelity`, `engine_bridge_m3`, `batch_documents_m3`, the three conformance files, `append_bound_m3`,
`docpaths`, `registry_m3` and `pipeline_seams_m3`.

The Mongo conformance skip is `mongo: no composite keys` (the capability flag).

The 11 skips in the full run:

* the Mongo composite-key capability case;
* 3 `test_console_m2` cases that need the demo connections from `tools/smoke_test.py`;
* the azure-ocr-mock cases (`--profile docs` not started);
* 2 tesseract cases (no binary on the host);
* presidio and the spaCy `ner` extra, which are not installed.

None of these touch G3. No frontend files changed, so the npm gates do not apply.

Docker: I started this lane's stack with `--no-build`, using the existing `data-privacy-services:m3` image. I
then ran `docker compose --profile sources --profile oracle down` on this lane's project only.

## 2. Acceptance (programme plan §2.2 row G3)

| Criterion | Evidence | State |
|---|---|---|
| `$where`/`$function`/`$accumulator`/JS operators rejected | `r3/probe_r3_filters.py`: 42/42 denied shapes refused before the server. Covered positions: top level, inside `$and`/`$or`/`$nor`, `$not`, `$elemMatch` (operator and document form), `$in`/`$all` items, `$eq` literals, embedded literal documents and arrays, wrappers holding operators, `$code`/`$ref`/`$binary` wrappers, `$options`, `$comment`, `$expr`, `$jsonSchema`, `$text`, geo, `$mod`, bit operators, `$sampleRate`, depth, size and regex length. 8/8 allowed filters ran on the lane's server with the profiler on: 8 profiled commands, **0 containing `$where`/`$function`/`$accumulator`**. The r1/r2 filter probes still refuse 31/31 and 19/21 (the 2 accepted are legitimate, see N5). | met |
| nested arrays transformed | r1 `probe_nested.py` rerun: `$.phones[*][*]` 30/30, `$.accounts[*].cards[*].pan` 30/30, `$.accounts[*].iban` 10/10, `$.emails[*]` 20/20 transformed, 0 survivors, 0 verification hits. r2 `probe_r2c.py` (mixed scalar/sub-document array, projected in place): 0 surviving source IBANs, element kinds and keys unchanged. **But see B1 and B2:** in two document shapes a value is not transformed at all. | met for well-formed paths; B1/B2 open |
| untouched values byte-identical | r1 `probe_untouched.py` rerun: `table` and `in_place` 0 retyped, 0 altered; drift `"0042"`/`"007"` stay strings. r2 `probe_r2.py` A: 8 of 9 drift types are byte-identical; the 9th is the engine's DATE span (R2-N4, unchanged). | met |
| M2 conformance suite green on Oracle and Mongo | as in §1; the suite's assertions are unchanged (judged in §4) | met |

Other checks:

* **No half-apply.** `r3/probe_r3_atomic.py` makes one document's `$set` path impossible by a concurrent
  change. `in_place` raises `target_error mongo:28`, **0/9 other documents changed**, the ledger is not
  advanced, and no source value appears in the error. An `append` batch with a duplicate `_id` raises
  `PkCollision`, with 0 documents inserted and no ledger.
* **Logs.** I ran the Mongo connector, writer, fidelity, bridge and conformance tests with `-s` and scanned the
  output for `GB00TEST`, `GB82WEST`, `@example.com`, `Person n`, `CUST-1` and `sentinel`: **0 hits**. The
  only events were `mongo_call_failed` with `op` and `driver_code` (`r3/logscan_mongo.log`).
* **Key bounds.** r2 `probe_r2d.py` rerun: for int, string, UUID, date and ObjectId keys, 2500/2500 distinct
  documents over 4 partitions. r1 `probe_idtypes.py`: keyed 0/5 when the sample misses a minority type.
  `key_type_mixed` is value-free.
* **Upsert.** r1 `probe_upsert.py`: 0/10 matched documents lost fields. r2 S0 (`profile: {}`) loses nothing.

## 3. Blocking findings

### B1: a top-level field sampled as a scalar but holding a sub-document or array is never searched by `unclassified`, so its nested PII passes in clear

**Where:** `dps/engine/batch.py:512`, in `BatchTransformer._unclassified`:

```python
for name, value in row.items():
    if name in self.flat_names:
        continue
```

`flat_names` holds every top-level field that is a column of the shape. A field the discovery sample saw only
as a scalar (`$.note`, text) is in `flat_names`. When an out-of-sample document holds a **sub-document or an
array** under that name, every nested leaf is skipped. That includes `$.note.text` and `$.note[*]`, whose
generalised paths the shape does not list.

**Contract.** Contract §6.4 says `unclassified="detect"` runs the detector "over string leaves whose
generalised path the shape does not list", and `"redact"` replaces such scalars. The module docstring says the
same. So does Task 13 behaviour 3: "string leaves of a Mongo document that the snapshot does not list". The
code contradicts all three.

**Evidence.**

* `r3/probe_r3_bridge.py` P1, offline: shape `$._id, $.name, $.note`, `note` not planned. Drift documents are
  `note: {text: "mail <email> now", iban: <IBAN>}` and `note: ["mail <email> now"]`. With
  `unclassified=detect` **and** with `=redact`, the surviving sentinels per document are **[2, 1]**. The
  control, an unknown top-level field `extra`, has 0.
* `r3/probe_r3_e2e.py` E1, end to end on the lane's MongoDB:
  * Setup: 30 documents with string `note` give the discovery shape; 6 drift documents follow.
  * Run: read, transform with `detect`, `in_place` write.
  * Result: **6/6 drift e-mails survive in the target**, and `verification.passed = True`.

Schema drift in a document store is exactly what `unclassified` (default `detect`) exists for. Each document is
also re-checked only against its own, empty, set of planned outputs, so nothing fails closed.

The path is latent until T15/T19 put `mongo` in `PIPELINE_ENGINES`. It is the same basis on which r2 judged
R2-B1 blocking.

**Fix direction.**

* For document rows (for example when `leaf_restore` is set or the engine is mongo), stop skipping whole
  top-level names. Iterate `flatten(value, child_path("$", name))` and skip only the leaves whose generalised
  path is in `by_path`. A scalar under a flat name yields exactly `$.name` and is still skipped. SQL rows keep
  the current `flat_names` rule, so a PostgreSQL jsonb column stays untouched.
* Add a test: a field sampled as a string that later holds a sub-document and an array with an e-mail, under
  `detect` and `redact`, with 0 survivors.

### B2: a field name containing `[digits]` breaks the path machinery, so a planned column is silently left untransformed

**Where:**

* `docpaths.generalise` (`dps/layers/connectors/docpaths.py:18-26`) applies the regex `\[\d+\]` to the whole
  path string, **including quoted segments**. A field named `iban[1]` (`$['iban[1]']`) generalises to
  `$['iban[*]']`.
* `mongo_docs.top_level` (`dps/layers/connectors/mongo_docs.py:81-83`) then names that column `iban[0]`, which
  is not the field's name.
* `expand_wildcards` splits `$['iban[*]']` at the `[*]` inside the quotes. `parse_path("$['iban")` raises
  `ValueError`, which `_ABSENT` swallows, so it returns `[]`.

**Consequence.**

* `BatchTransformer._values` finds no value for the planned path, so it builds no unit. With no units there are
  no originals, and verification has nothing to check.
* `_unclassified` skips the leaf because `generalise(path)` *is* in `by_path`.

The result is that a column discovery classified, and a reviewer planned (e.g. `IBAN` `det_token`), is written
back unchanged, with `verification.passed = True`. Field names with brackets are legal in MongoDB, and common
in documents imported from form posts, e.g. `phone[0]`.

**Evidence.**

* `r3/probe_r3_bridge.py` P2, offline: keys `iban[1]` and `acct[0]x`, and a nested `acct.no[2]`, each leave
  **20/20 values unchanged**. Keys with quotes, backslashes or spaces transform correctly (0/20 unchanged).
* `r3/probe_r3_e2e.py` E2, end to end:
  * Setup: 20 documents `{_id, "iban[1]": <IBAN>, name}`. `describe` gives the path `$['iban[*]']` with name
    `iban[0]`.
  * Run: plan `det_token`, then an `in_place` write.
  * Result: **20/20 source IBANs survive in the target**, and verification passed with 0 hits.

**Fix direction.** Either option works:

* Make `generalise`, `expand_wildcards` and `top_level` segment-aware. Work on `parse_path` parts and
  generalise only integer parts; rebuild with `child_path` and a `[*]` token that cannot occur inside a quoted
  key.
* Or fail closed: `infer_shape` marks such fields `path_unsupported`, and the bridge refuses a plan whose path
  addresses nothing in a document where the field is present.

Add a test with `iban[1]`, `a[0]x` and a nested bracket key, with 0 survivors.

## 4. Deviation log (`docs/superpowers/build/M3-DEVIATIONS.md`, Tasks 9-14 and the two fix rounds)

* **Task 9.** Accept, all entries:
  * range key only for codec-round-tripping `_id` labels;
  * `is_key` only when keyed;
  * `binData` not restored;
  * `make_bson_safe` copies.

  `coerce_bson` was later removed in r1. No other lane references it: I grepped `dps-wt-m4`, `dps-wt-m5` and
  main.
* **Task 10.** Accept:
  * wrappers checked in the validator itself, which makes submit-time refusals pure Python;
  * depth counts nesting levels;
  * a wrapper under `$not` and an empty `$elemMatch` are invalid;
  * checklist mapping for the new codes.
* **Task 11.** Accept, all entries:
  * No `timeoutMS`. The measured reason is sound, and every read carries `maxTimeMS`.
  * Error mapping follows the Oracle split.
  * A single cursor for key-less collections.
  * `sample_keys` uses `$match` before `$sample` plus a server sort.
  * HMAC sampling in `count` and `key_at_offset`.
  * Name and `list_objects` rules.
* **Task 12.** Accept:
  * the ledger guard on the caller's mark;
  * `PrepareFailure` and `PkCollision` for errors;
  * the stricter append key rules and bounds;
  * write concerns;
  * the registry branch;
  * `PIPELINE_ENGINES` staying `{postgres, oracle}`;
  * the `append_read_bound` M2 touch point.

  The entry "`table` always `ReplaceOne`" is superseded by r1 B3 and r2 B2.
* **Task 13.** Accept as built:
  * document-path read and write, with by-reference untouched values;
  * the `ts_precision` M2 touch point;
  * per-test markers.

  However, behaviour 3 ("unclassified leaves are detected") is **incomplete** for nested leaves under a
  scalar-sampled field (B1). The deviation entry says "top-level keys that are flat shape columns are never
  searched, so SQL rows are untouched". That rule is right for SQL rows and wrong for documents.
* **Task 14.** Accept:
  * The suite gained defaulted hooks (`names`, `keyless`, `composite_keys`, `equals_selection`).
  * PostgreSQL's binding is unchanged.
  * No assertion was weakened. I checked the diff: every assertion is the same with engine names mapped, the
    composite cases skip only through the capability flag, and one assertion was added (count == 1).
  * The plan's "registration list" did not exist in M2's suite, so the deviation is forced.
* **Review r1 fixes** (`leaf_restore` in the transformer, `id_bounds` plus `key_type_mixed`, `$set` upserts).
  Accept. Re-verified by the rerun probes in §2.
* **Review r2 fixes** (top-level-field projection plus the `read_projection` fold plus deduplicated
  projection names; `upsert_update` with `$setOnInsert`). Accept. r2 `probe_r2c.py` now shows 0 survivors and
  intact element kinds. The behaviour change (a dotted `columns` entry reads the whole top-level field, every
  leaf planned) is safer and is documented in contract §4.2/§5.2.

## 5. Non-blocking notes

* **N1 (drift; a planned scalar column holding a container).** When a planned `$.iban` (`det_token`) holds an
  array or a sub-document in an out-of-sample document, the container is JSON-texted, tokenised and written
  back as **one string**. There is no leak (0 survivors, `r3/probe_r3_drift2.log`), but the document's
  structure changes.
  * For `fpe_digits` on an int column the same drift fails closed (`incompatible_method`), which is good.
  * Consider failing closed for whole-value plans over containers, too.
* **N2 (TOCTOU).** `in_place` reads outside the transaction and `$set`s positional paths inside it. If an
  application inserts an element at the front of an array between the read and the write, the masked value
  lands on a shifted element. This is the analogue of SQL in-place lost updates. Optionally guard each
  `UpdateOne` with the read's array lengths.
* **N3.** A huge int literal in a filter (`2**70`) passes validation and fails at encode time with
  `OverflowError`. That is not a `PyMongoError`, so it surfaces as an unexpected error, by class name only.
  Consider bounding int literals to int64 in `_literal`.
* **N4.** R2-N2 (a matched array is set whole by `table` upsert), R2-N3 (`InvalidBSON` for out-of-range dates
  not mapped), R2-N4 (the engine's DATE span leaves the time in clear) and R2-N5 (fail-closed `fpe_digits`
  fallback) are unchanged.
* **N5.** The key-less `selection.sample` count in `_scan_ids` (an r1 open item) is unchanged. It only affects
  a discovery count, because the run handler refuses key-less sources.
* **N6 (style).** `probe_filters2` accepts `{"a": {"$not": {"$regex": {...}}}}`-like shapes only when they
  are legitimate; this is not an issue. The SSRF host guard does not cover a Mongo `uri` secret, but no lane
  has a netguard yet (M5 scope).

## 6. Probes (round 3, under `probe-m3-G3/r3/`)

| Probe | Result |
|---|---|
| `probe_r3_bridge.py` | P1: nested PII under a scalar-sampled, unplanned field survives `detect`/`redact` ([2, 1] sentinels) (**B1**). P2: bracket field names leave a planned column 20/20 unchanged (**B2**); quotes, backslashes and spaces are fine. |
| `probe_r3_e2e.py` | E1: 6/6 drift e-mails survive an `in_place` run with `detect`, verification passed (**B1**). E2: 20/20 IBANs survive under field `iban[1]`, verification passed (**B2**). |
| `probe_r3_filters.py` | 42/42 denied; 8/8 allowed ran; 0 JS operators in the server profiler |
| `probe_r3_atomic.py` | `in_place` failure applies 0/9 and leaves the ledger untouched; append duplicate gives `PkCollision` with 0 inserted; value-free |
| `probe_r3_drift2.py` | planned scalar holding a container: tokenised to a string (N1); `fpe_digits` fails closed |
| `r1_probes_rerun.log`, `r2_probes_rerun.log` | all earlier blockers stay fixed (counts in §2) |
