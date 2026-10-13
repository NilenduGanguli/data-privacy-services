# M5 — deviations from the plan (`docs/superpowers/plans/2026-10-11-m5-hardening-and-showcase.md`)

Per task: file, change, why. The code as built wins over the plan; the contract was amended in the
same commit wherever a contract name or section changed.

This lane (worktree `dps-wt-m5`, branch `m5`) builds the M5 tasks that need no M3/M4 code. Work
paths are the worktree, not `data-privacy-services` (the plan's "Conventions" line).

## Task 1 — M5 settings, extras, markers, log fields

- `tests/test_observability.py` (`test_m5_log_fields_are_allowed`): the plan's forbidden set held
  `"model"`, which M0 already allow-lists — the LLM gateway logs the model **name**
  (`gemini-2.5-flash`), never a fitted model. Removing it would break M0 logging, so the test forbids
  `"tabular_model"` (the `JobSecret` field holding the sealed fitted model) instead and keeps the other
  names. It also asserts `key_count` and `grace_hours`, and a second test
  (`test_m5_metrics_exist`) checks the three new metrics reach the exposition.
- `dps/config.py`: numeric M5 fields carry `Field` bounds beyond the plan's validators (e.g.
  `copula_fit_rows ≥ 1`, `copula_holdout_rows ≥ 0`, `fidelity_column_limit` in [0, 1],
  `privacy_nndr_target > 0`, `tokenizer_retries` in [0, 10], `mia_auc_target` in (0, 1]) — additive
  validation, defaults unchanged. `tokenizer_token` is `repr=False` already (the plan does this for
  secret fields in Task 17). `synth_tabular_engine` is de-duplicated (`copula,copula` → `copula`).
  `TABULAR_ENGINES = ("copula", "mostly")` is a module constant beside the M0 ones.
- `tests/test_config_m5.py`: one extra test, `test_url_errors_never_echo_the_url` (the
  `tokenizer_url` validator reuses M0's `_sane_url`, whose message never quotes the URL).
- `.env.example` **not changed**: this session's permission settings deny reading or writing
  `.env*` files. The three variables the plan appends (`DPS_SYNTH_TABULAR_ENGINE`,
  `DPS_TOKENIZER_PROVIDER`/`_URL`/`_TOKEN_FILE`) are documented in `HANDOFF.md` §4 instead
  (deferred to final stage, below).
- `HANDOFF.md`: the plan says §3 (configuration table); the configuration table is §4 — the M5 rows
  went there as their own "M5 settings" table. §2 notes that the image's `tabular` extra now resolves
  (numpy + scipy) and that `synth-ml` never enters an image.
- Contract §1: besides the extras sentence, the M5 rows added are the plan's file-structure paths not
  already listed (constraints, connectors `tabular.py`, `linked.py`, `external.py`, tokenizer mock,
  keys CLI, audit query, reports, policy store, netguard, rekey, egress, regex guard, validation,
  policy refs, migrations 040–044, console components/pages, tools, docs). §10 rows and §12.1
  `deploy`/`ml` markers as the plan lists them.

## Task 2 — Fidelity and privacy metrics

- `dps/engine/metrics.py` `_nearest`: the plan took `sqrt(min(|q|² + |r|² − 2q·r))`. For values of
  realistic magnitude (balances around 1e6) that expansion loses the exact zero of many copied rows
  (cancellation leaves a residue up to ~1e-3), so `zero_distance_share` under-counts verbatim copies
  (0.82 instead of 1.0 for the new test's data). The nearest row is still found on the expanded form,
  but its distance is recomputed directly (`‖q − r‖`), so a copy measures exactly 0. New test
  `test_nndr_counts_exact_copies_of_large_values_as_zero_distance` (fails on the plan's form).
- `to_float` returns `None` for an int too large for a float (`OverflowError`) instead of raising;
  `qi_unique_overlap` returns 0 without quasi-identifiers (the plan counted every synthetic row as
  matching the empty key); `fidelity_report` adds `corr_within_limit` (additive; the evidence shows
  the correlation limit like the column limits) and casts `within_limit` to a plain `bool`.
- `tests/test_metrics.py`: `numpy` is taken from `pytest.importorskip` too (the module imports it
  at the top), plus assertions for `to_float("12")`, NaN, bytes and dates in `canonical`.

## Task 3 — Gaussian-copula engine

- `dps/engine/synth/copula.py` `_subkey`: keyed BLAKE2b (`key=seed`, the label as the message)
  instead of hashing the seed as data with the label as a 16-byte salt — the standard keyed-KDF form,
  no truncated labels; same interface and properties (the seed decides every stream).
- `fit` also refuses a path without a kind (`no kind given for $.X`) and columns of unequal length
  (`every column must have the same number of rows`) with value-free `CopulaError`s; the plan raised a
  `KeyError` / sampled misaligned rows.
- `from_bytes` refuses a bare `.npy` array (not an npz archive), an unknown marginal kind and a
  Cholesky factor of the wrong shape, and maps `TypeError`/`AttributeError`/`EOFError` to
  `CopulaError` too (the plan's `with np.load(...)` raised `AttributeError` on a `.npy`). The blob is
  sealed (authenticated) in Task 6, so this is robustness, not a security boundary.
- `Marginal._typed` caps a date's ordinal at `date.max` and a decimal scale at ≥ 0;
  `Marginal.inverse` raises `CopulaError` (not `assert`) on a malformed marginal.
- `to_bytes`: `np.savez_compressed(buffer, **arrays)` without `allow_pickle` (the keyword exists
  only from numpy 2.1; the extra's floor is 2.0, where it would be stored as an array). The arrays
  are numeric, so nothing is pickled; reading keeps `allow_pickle=False`.
- `tests/test_copula.py`: added a prefix-stream property of `uniforms`, a timezone-aware timestamp
  test, `test_serialisation_keeps_the_reject_set`, truncated/empty blobs, and the new fit errors.

## Task 4 — Tabular constraints

- `dps/engine/policy.py` `Policy.config_hash`: the plan accepted that every preset's hash changes
  once ("nothing pins a literal hash"), but `tests/test_policy.py::test_dpdp_policy_hash_is_unchanged_
  by_the_optional_locale` pins the v0.1.0-m0 hash, and M2 snapshots and evidence packs record policy
  hashes. The two new `synthesize` lists are left out of the hash while empty
  (`_OPTIONAL_SYNTHESIZE`), so every existing hash still matches and a policy that sets a constraint
  or a quasi-identifier hashes differently (`test_constraints_round_trip_and_change_the_policy_hash`).
- `WhenRule` always dumps its condition as `"if"` (a wrap `model_serializer`), not `"if_"`, so a
  dumped policy is the input form (API responses, `resolve_policy`'s merge, YAML in the console);
  `populate_by_name` still accepts `if_`. The new models use M0's `hide_input_in_errors` config
  (inline policies arrive in request bodies). `quasi_identifiers` entries are validated as column
  paths (`$.NAME`), like constraint paths (`COLUMN_PATH`/`ColumnPath`, public names).
- `dps/engine/synth/constraints.py`:
  - a range bound or a `then` value that does not fit the column kind fails at construction with a
    value-free `ConstraintError` naming the path (the plan would fail mid-batch with a `ValueError`
    quoting the literal); a malformed date literal in a `when` condition never matches;
  - a `when` rule whose condition column the table lacks is skipped
    (`{"path", "reason": "no_condition_column"}`) instead of always reading that column as null;
  - a unique rule on an integer/`numeric(p, 0)` column whose `precision` is under 6 digits is refused
    ("at least 6 digits"; the plan used `max(6, precision)` and could overflow the column);
    `ColumnSpec.precision` is decimal digits — **Task 6 must pass 4/9/18 for
    `smallint`/`integer`/`bigint`** (PostgreSQL integer columns carry no precision; the default 15
    digits would overflow `integer`);
  - the Feistel round input is the half-block in its own byte width (plan: 32 bytes) and the
    positive-clip value is a helper (`_smallest_positive`) instead of the nested conditional the plan
    flagged.
- `tests/test_constraints.py`: `pytest.importorskip("scipy")` (the module imports
  `dps.engine.metrics`, which needs the `tabular` extra); extra tests for seed-keyed uniqueness,
  positivity on decimals, value-free range/then errors, date/text/null conditions, the skipped
  condition column and the hash/alias round trip.

## Task 5 — Tabular sampling and exact-row rejection in `BatchTransformer`

- `dps/engine/batch.py` `LeakageError`: M2's class takes a hit count (`LeakageError(hits)`, message
  "leakage: N original value(s) …"; `jobs_run` reads `.code`/`str(exc)`). It gains a keyword
  `rule`; `LeakageError(1, rule="exact_row_match")` has the message `exact_row_match` (the plan's
  `LeakageError("exact_row_match")` would have put a string into `hits`). Code stays `leakage`.
- **Tabular anchor**: the plan reused the anchor M2 hands `ApplyOptions`, which with an entity anchor
  column is `<ENTITY>:<hash>` — shared by every row of the same customer (e.g. all accounts of one
  `CUSTOMER_ID`), so those rows would have been drawn identically (a fidelity defect and duplicate
  synthetic rows). The sampler is keyed by the row's key anchor (`row:<hash(key)>`, §6.2's no-anchor
  form), unique per row; with no anchor column it is the same string as the plan's
  (`test_rows_sharing_an_entity_anchor_still_get_their_own_draws`). Contract §6.6 says so.
- `BatchTransformer.__init__` refuses (value-free `ValueError`) a sampler outside synthesize mode, on
  a table without a key, or one that would write a key column, a planned column or a column the
  table lacks (contract §6.6: the sampler covers non-key, non-sensitive columns only; overwriting a
  key would break partitions and the ledger); and a negative `max_resample`.
- `TabularSampler.engine` and `.columns` are read-only properties in the protocol, so
  `GaussianCopula.engine: ClassVar[str]` satisfies it under mypy (checked); the other members are the
  plan's. `ConstraintSet` is imported under `TYPE_CHECKING` only (`batch.py` stays importable without
  the `tabular` extra).
- Counters: `values_by_method["<engine>"]` (non-null sampled values, also into `TRANSFORMS` with the
  engine as the method label), `"<engine>_resample"` only when there were redraws (into
  `dps_tabular_resamples_total` only), and `values_by_column` counts the sampled columns (additive;
  M2's evidence reads only planned columns, Task 6 can show the rest). The integer key for `unique`
  constraints is read only when a sampler is set.
- `tests/test_batch_tabular.py`: `ColumnPlan` needs M2's `name` field; tests added for fit/holdout
  rejection, unique constraints receiving the key, the sampler-column refusals, the anchor choice,
  and three with the real `GaussianCopula` (no output row equals a real row and the output is
  deterministic; the copula's own reject set forces redraws; a table whose every value combination is
  real fails closed with `exact_row_match`).
- Contract §6.4 lists M2's existing `anchor`/`verify` keywords beside the two new ones; §6.6 carries
  the extended protocol, the `fit` keywords of Task 3 and the behaviour sentence.

## Task 6 — L2 synthesize jobs: fit, preview, measure, gate, seal; evidence

- **`validate_run` lives in `dps/layers/connectors/targets.py`** (M2 as built), not
  `dps/jobs_models.py`; the tabular rule went there (`_check_tabular`): synthesize mode, a `table` or
  `files` target, an engine in `DPS_SYNTH_TABULAR_ENGINE`; `mostly` is refused ("the synth-ml extra
  is not installed") until Task 7 wires `mostly_available()`. `tests/connectors/test_targets.py`'s
  M2 assertion ("tabular arrives with M5") became the M5 accept/refuse matrix.
- `dps/layers/connectors/tabular.py`:
  - **Reproducibility.** PostgreSQL's `sample` is `ORDER BY random()` (whole table when it is small),
    so the plan's split and fit depended on the order the database returned: the same seed fitted a
    different model and `test_same_seed_reproduces_the_same_target` could not hold. The sampled rows
    are put in a keyed, order-independent order first (`keyed_order`: HMAC of the seed and each row
    anchor), then split and fitted (`test_prepare_is_deterministic_whatever_order_the_sample_comes_in`).
    For a table larger than `fit + holdout` rows the sample itself is random, so the model (and the
    output) is reproducible only when the whole selection is sampled — HANDOFF material for Task 27.
  - `tabular_columns` takes an optional `projection` (the run's projected columns; others are neither
    modelled nor written) and gives integer columns without a catalogue precision the digits their
    type holds (`INT_DIGITS`: smallint 4, integer 9, bigint 18 — the Task 4 note), so a `unique` rule
    never draws a number its column cannot store. The reject columns are the projected non-key columns.
  - Columns the engine itself declines (copula: `all_null`, `no_frequent_category`) keep their source
    values and are added to `tabular.excluded` with that reason; `tabular.columns`, the metrics and the
    constraints cover the columns the sampler writes only (the plan built constraints over every
    eligible column, so a `when` rule could have rewritten a source-kept value).
  - New helpers: `keyed_order`, `check_constraints` (a `unique` rule needs one integer key —
    `incompatible_method` at prepare instead of an `internal_error` retry mid-partition, the Task 5
    note), `runtime_sampler` (the sealed model plus its constraints, shared by the preflight and
    partitions), `GATE_CODES`. `fit_sampler`/`load_sampler` gain the MOSTLY branch in Task 7.
  - A preview that cannot avoid real rows (`LeakageError(rule="exact_row_match")`) fails with
    `leakage` and the detail "every redraw … equalled a real row (too few distinct value
    combinations to synthesize)" (Task 5 open note 1; `test_a_table_whose_every_combination_is_real_
    fails_closed`). The privacy block also records `engine`, `measured: true` and `nndr_enforced`.
- `dps/layers/connectors/jobs_run.py`: the plan's `_make_transformer` is M2's `_transformer` with
  keyword `tabular`/`constraints` (and `max_resample=DPS_COPULA_MAX_RESAMPLE`); `_prepare_tabular`
  runs between the run plan and the preflight (so the preflight also runs the sampler), re-seals with
  `seal_json` and `jobs.reseal_secret` (lease lost → stop), and stores `progress.metrics` on the
  partitions/dry-run transition. Partitions rebuild the sampler in `RuntimeCache._build`
  (`_runtime_tabular`, which loads the accepted snapshot for the constraint column specs); an
  unreadable model is `tabular_invalid`, a constraint error `incompatible_method` (prepare and
  partitions, `CONSTRAINT_ERRORS`, imported lazily so M2 runs need no `tabular` extra).
  `TabularGateError`s become `PrepareFailure`s (M2's `_fail_to_finalize`, failed evidence pack).
- `dps/layers/connectors/evidence.py`: `columns[*]` gain `data_type`, `confidence`, `override` but
  **not** the snapshot's `params` (M2 prints params for reversible methods only — a one-way
  `dictionary`/`redact` may hold literals; that rule is kept). `policy.version` comes from
  `EvidenceInputs.policy_version` (finalize resolves the run policy; best effort). `<engine>_resample`
  counters are not methods (`RESAMPLE_KEYS`; they no longer inflate `values_transformed`) and are
  summed into `privacy.exact_row_resamples_run`. A synthesize pack whose prepare failed before the
  preview carries `privacy = {engine, measured: false, reason}`. The HTML section 6 prints utility and
  the tabular block too (Task 14/21 render them properly).
- `JobProgress.metrics` uses `Field(default_factory=dict)` (M2 style).
- Tests: `tests/test_tabular_prepare.py` — the plan's `ColumnPlan` needs M2's `name`; the holdout is
  ~20 % by a keyed hash, so the size assertions are bounds (the plan's `fit_rows == 400` held for one
  seed in three); added the integer digits, order-independence, runtime constraints,
  every-combination-real and unique-without-integer-key tests (11; plan 6). `tests/test_evidence.py`
  +2, `tests/db/test_jobs_queue.py` +1 (`reseal_secret` fencing), `tests/connectors/test_targets.py`
  matrix. **Integration test path**: `tests/connectors/test_synthesize_copula.py` (the repo has no
  `tests/integration/`; M2's run suites live in `tests/connectors/` and drive jobs in process with
  `tests.connectors.helpers.drive` rather than through the API and a `Consumer`); the targets are
  tables in the per-test schema (dropped by the fixture) instead of `synth_<hex>` schemas. Three tests
  (plan 2): the measured run, same-seed reproduction, and a privacy gate that fails prepare before the
  target exists. Stage A lesson 1: the measured run compares every ground-truth sensitive column with
  the source — no row keeps its own value (all of them, `date_of_birth` included), no source national
  id, tax id, e-mail or mobile number appears anywhere in the target (any column, free text
  included), and no target row equals a source row on all non-key columns. `full_name` allows
  chance coincidences across rows (≤ 3 %; measured 16 of 1 494): the demo rows were generated by the
  same persona name pools as the surrogates. The key `customer_id` keeps its values by the
  synthesize-mode key rule (contract §5.3) — see the report's open issues.

## Task 6B — Utility (train on synthetic, test on real) and the membership-inference proxy

- `dps/engine/metrics.py`: `membership_inference_proxy` returns `{"run": False, "reason"}` when
  there is nothing to compare (no holdout or no synthetic row) instead of an AUC over empty sets; the
  synthetic reference is capped at 10 000 rows like NNDR's. `utility_tstr` compares class labels by
  their canonical text (`1`, `1.0` and `Decimal("1.00")` are one label) and reads regression labels
  through `to_float`; R² of a constant test label is 0. The evidence's `utility` block is the one
  Task 6 already copies from `progress.metrics` (no `evidence.py` change in this task).
- `dps/layers/connectors/tabular.py` `_utility`: the real training side is the **preview source**
  (the fit rows the synthetic preview was drawn for), not the whole fit sample, so both k-NNs train
  on the same number of rows; the plan's reasons are kept and one is added ("the label column is
  null in one of the samples"); the result carries `task` besides the plan's `label`. The
  `privacy_gate` for the MIA proxy reads "the membership-inference proxy AUC is above the configured
  target" (separate detail from the NNDR gate).
- `dps/engine/policy.py`: `utility_label: ColumnPath | None` is validated as a column path and,
  like Task 4's lists, left out of `config_hash` while unset (no recorded hash changes).
- Tests: the plan's 6 plus `test_an_enforced_privacy_gate_also_checks_the_mia_proxy`,
  `test_utility_label_is_hashed_only_when_set`, a regression label and a non-modelled label in
  `test_measure_reports_utility_and_mia` (9). The fixture is a self-contained accounts table (the
  plan reused Task 6's), imported helpers from `tests/test_tabular_prepare.py`; Task 6's
  `test_prepare_returns_a_sealable_model_and_value_free_metrics` now expects the MIA proxy to run.

## Task 7 — Optional MOSTLY AI engine (`synth-ml` extra)

- `dps/engine/synth/mostly.py`: the plan's pool sampler, with these changes (each tested offline):
  - generated values that their column cannot hold (`datetime.fromisoformat` / `float()` errors,
    whose messages quote the value) become a value-free `TabularUnavailable`
    (`coerce_rows`; `test_bad_generator_output_and_blobs_fail_without_values`); `coerce` is public
    (Task 7B's linked pool uses it) and also reads `"NaN"`/`"None"` and a float NaN as null;
  - `from_bytes` maps `zlib.error`/JSON/shape errors to `TabularUnavailable("unsupported pool
    format")` (the blob is sealed, so this is robustness, not a security boundary);
  - `mostly_available()` also treats an `ImportError`/`ValueError` from `find_spec` (a broken
    parent package) as "not available".
- `dps/layers/connectors/tabular.py`: `fit_sampler`/`load_sampler` gain the MOSTLY branch (lazy import),
  `prepare_tabular` maps `TabularUnavailable` to `tabular_invalid` and records `tabular.pool_rows`;
  `dps/layers/connectors/jobs_run.py` maps an unreadable pool to `tabular_invalid` like an unreadable
  copula; `dps/layers/connectors/targets.py` refuses `mostly` with `tabular_invalid` unless
  `mostly_available()` (Task 6 refused it unconditionally).
- The SDK call is the plan's (`MostlyAI(local=True, local_dir=…)`, `train(config=…, start=True,
  wait=True)`, `generate(…).data()`), **not verified against a real SDK in this lane**: the extra
  (torch ≈ 2 GB) is not installed, so `test_real_sdk_trains_and_generates` (`ml`) skips. Run
  `uv pip install -e ".[synth-ml]" && pytest -q -m ml tests/test_mostly.py` on a machine with the
  extra before enabling the engine anywhere.
- Tests: the plan's 4 plus `test_values_are_coerced_to_their_column_kind`,
  `test_bad_generator_output_and_blobs_fail_without_values`,
  `test_prepare_with_the_mostly_engine_records_the_pool` (prepare with the engine's trainer
  monkeypatched) and `test_validation_needs_the_extra` (8; 1 skips without the extra).
  `tests/test_tabular_prepare.py::_prepare` takes `engine_name`.
- `HANDOFF.md` §2: the `synth-ml` opt-in sentence. The plan's `docker build --build-arg
  EXTRAS=synth-ml` does not exist (the Dockerfile has `WITH_NER` only); adding a build argument is
  an image change — deferred to the images task (below).

## Task 7B — MOSTLY differential privacy and linked (multi-table, sequential) generation

- **Process note:** `dps/engine/synth/linked.py` was written before its offline tests in this task;
  the validation, `prepare_linked`, job-flow and DP-threading layers were test-first (each test seen
  failing for the missing interface first).
- `dps/engine/synth/mostly.py`: `DpConfig` (frozen), `model_configuration(minutes, dp)` and
  `dp_report(dp)` beside the plan's `_dp_configuration`; the `Trainer` takes the DP budget as a
  fourth argument (Task 7's fakes take it too); `MostlySampler.dp` records the evidence block and is
  sealed with the pool. `achieved_epsilon` is always `null`: reading it needs the real SDK, whose
  API for it is not verified in this lane.
- `dps/engine/synth/linked.py` — interface changed for two tables that may share column names:
  `LinkedPool.fit(parent: LinkedTable, child: LinkedTable, *, layout, subjects, seed,
  max_training_minutes, dp, trainer=None, locale)`; a `LinkedTable` carries the in-memory sample of
  its modelled and link columns, the kinds and scales, the output columns, the key column and kind,
  and its `SensitiveColumn`s. The pool holds the **output** rows: new keys (parent `1..n` or
  `S00000001…`, child `1..m` or `C00000001…`), the child's foreign key = the generated parent key,
  modelled values, surrogates, and null for every column that is neither modelled nor sensitive
  (free text included). Surrogates are anchored on the generated **subject**
  (`linked:parent:<generated id>`) for both tables (plan: `linked:<table>:<id>`), so one subject's
  persona is coherent across its parent and child rows; their input is a value-shaped placeholder
  (`_pseudo_original`: the column's value shape, or a date for date columns), never a real value;
  a surrogate that does not read as its column's type is null. `from_bytes(data, seed)` keeps the
  sampler signature (the seed is unused: the pool is already generated). `new_key`,
  `PARENT_INDEX`, `sequence_lengths(rows, fk, keys=None)` (zeros counted when `keys` is given).
- Layout rules (prepare, `tabular_invalid`): one key column in **each** table, `parent_key` = the
  parent's key, the link columns present, the foreign key not the child's key, integer or text keys
  (others cannot take generated ids), and `order_by` neither sensitive, free text nor
  non-orderable. A **sensitive foreign key is allowed** (plan: refused): discovery classifies the
  usual link column (`customer_id`) as `CUSTOMER_ID`, and its values are re-numbered before
  training and replaced in the output, so they reach neither the trainer nor the files.
- `validate_run(..., child_snapshot=None)` (`targets.py`, contract §5.3) and `submit_run` loads the
  child snapshot. `tabular_dp: false` is always allowed (it only switches a deployment default off);
  `true` (or `linked`) without `tabular="mostly"` is `tabular_invalid`.
- `dps/layers/connectors/tabular.py` `prepare_linked`: a keyed 20 % of the sampled parents is held
  out; their children are the membership-inference non-members. NNDR and the MIA proxy run on the
  child's modelled columns (`privacy.nndr_table = "child"`); **exact rows are rejected** (the plan
  named no rule for linked output): a generated row equal to a sampled real row on every written
  non-key, non-link column (sensitive and null columns included) is dropped — a dropped parent takes
  its children — and counted in `privacy.exact_row_rejections`, so `exact_row_matches` is 0; when
  every generated row of a table is rejected, prepare fails with `leakage`. Like the copula's reject
  set, this knows only the sampled rows (`test_generated_rows_equal_to_real_rows_are_rejected`;
  the integration run found a rejected transaction about one run in three). `fidelity = {engine, score (mean),
  tables: {parent, child}, measured_on}`; `utility` is not run for linked generation and there is
  no quasi-identifier block; `tabular.columns` is `{parent, child}` (a list for the row path) — the
  console/PDF tasks must read both shapes. The single-table MOSTLY prepare records `tabular.dp`
  too (`prepare_tabular(..., dp=)`, `jobs_run._dp_config`).
- `dps/layers/connectors/jobs_run.py`: the linked branch runs in `prepare` right after the source
  checks (the 1:1 projection, count, plans and preflight do not apply); the two tables' value-free
  descriptions (`object`, `dir`, `columns` with types, `rows`) travel in
  `RunPlan.prepare["linked"]`. `_run_linked_partition` bypasses the runtime cache: each call has its
  own writer and spool folder, a restarted partition resets its progress and rewrites its
  (deterministic) objects; the checkpoint's `last_key` is a row position, never a value;
  `values_by_method["mostly"]` counts rows written. Layout: `<connection prefix>/<files.prefix>/
  <table>/<job_id>/part-…` (plan: `<prefix>/<table>/<job_id>/…`, which put the table above the
  files prefix) and one manifest at `<connection prefix>/<files.prefix>/<job_id>/manifest.json`
  with `linked: true` and `tables: {parent, child: {object, dir, columns, objects, rows_total}}`.
  A dry run measures and stops.
- `dps/config.py` `mostly_linked_child_rows = 200_000` (`tests/test_config_m5.py`, HANDOFF §4 row,
  contract §10 row). The HANDOFF trap went to §8 "Traps" (the plan says §11; §11 here is the M1+M2
  integration record) together with two tabular traps found in Task 6.
- Tests: `tests/test_mostly_linked.py` 14 offline + 1 `ml` (plan 6 + 1) and an **integration test**
  not in the plan, `tests/connectors/test_linked_files.py`: customers + transactions in the lane's
  `source-pg` → a PVC files target through the real job flow, with the SDK trainer replaced by a
  stand-in that draws each column independently from the training sample (rare text categories
  left out, as MOSTLY's rare-category protection does) because the `synth-ml` extra is not
  installed. It compares the output with the source: no source key, name, e-mail, national id, tax
  id, mobile number or memo reaches the files, keys are new, referential integrity and sequence
  order hold, and the pack records DP and the linked block. Generated names may coincide with
  source names by chance (≤ 10 % allowed; same persona pools, placeholder input — see Task 6).

## Task 8 — `external_tokenize`: the bank tokenization adapter, a mock, batch pre-passes

- Routers: **no change** — M0/M1 as built already run `detect`, `obfuscate`, `plan` and `reveal`
  off the event loop (`run_in_threadpool`), which is what the plan's `asyncio.to_thread` edit was for.
- `dps/engine/transform/external.py`: a CA bundle path becomes an `ssl.SSLContext` (httpx 0.28
  deprecates `verify=<path>`); `HttpTokenizer.__repr__` shows the base URL only (the bearer token
  lives in the client's headers); a non-JSON 200 is counted as `malformed` too.
- `dps/engine/transform/apply.py`: the pre-passes skip entries/hints whose path does not fit the
  document (the transform/reveal loops report those, as before) and pass `profile` as a string; the
  detokenize pre-pass runs after the kid/kcv checks, so a wrong key never calls the service.
- `dps/engine/service.py`: `reveal` wraps `ExternalServiceError` as `TokenizationUnavailable` too;
  `PrivacyEngine(tokenizer="auto")` raises `ValueError` at construction for an http URL while
  egress TLS is required (`build_tokenizer`). `dps/layers/api/common.py` maps it to a plain 502
  `{"detail": "tokenization service unavailable"}` (no `code`, as the plan says), audit outcome
  `upstream_error`. `dps/app.py`: the readiness probe runs `health()` in a thread (blocking HTTP),
  detail `up`/`down`/`unconfigured`, `adapter`; never a required component.
- `dps/engine/batch.py` `_pretokenize`: one call per entity type and profile per batch for the
  whole values (nulls skipped); free-text spans still use the per-row pre-pass.
- `dps/layers/connectors/jobs_run.py` (not in the plan): an `ExternalServiceError` during prepare or
  a partition is retried with the new code `tokenization_unavailable` (a prepare that exhausts its
  attempts fails with it) instead of `internal_error`; contract §5.3 lists the code and the L1 502.
- `docker-compose.yml`: the tokenizer variables are in the shared `x-dps-env` anchor (app **and**
  worker transform), each overridable (`${DPS_TOKENIZER_URL:-http://tokenizer-mock:8420}`); the mock
  uses the app image with `pull_policy: never` (plan: `build: .`, a second build of the same image),
  the lane-safe port `${DPS_TOKENIZER_MOCK_PORT:-8420}` (contract §0 amendment), a read-only root,
  `cap_drop: [ALL]` and the labels `tests/test_compose.py` requires. The service was **not run in a
  container** here (this lane has no `m5` image; building images is Task 24): `docker compose config`
  validates it and the script's CLI entry point was smoke-run on the host (health 200, 401 without
  the bearer token, a `tk_` token with it, request lines only in its log).
- `docker/tokenizer-mock/server.py`: the plan's mock, with `do_GET`/`do_POST` returning after each
  response explicitly.
- `HANDOFF.md`: §3 service row, §8 "Traps" carries the wire contract for the bank's integration team
  (the plan said §8; the configuration rows were already in §4 from Task 1).
- Tests: `tests/test_external_tokenize.py` 11 (plan 7): the plan's, plus the bearer token is sent
  and never shown, `build_tokenizer` follows the settings (TLS rule, CA bundle), free-text spans are
  tokenized and revealed, the 502 carries no value and `/readyz` reports the adapter, nulls in a
  batch, and the compose service. `ColumnPlan` needs M2's `name`. The M0 suites (`test_methods`,
  `test_apply`, `test_service`, `test_api`, `test_planner`, `test_batch`) pass unchanged.

## G2 review fix — SEC-4: external tokens bound to the caller's key (programme plan D-B)

- **Formula changed from the plan's.** D-B's tag `HMAC(subkey(key, "external_tokenize|<entity>|
  <profile>"), token)` is keyed by the caller's DEK alone — and the caller holds its DEK (L1 returns
  it, or the caller brings its own), so anyone could forge a tag for any token and the confused deputy
  stayed open. The tag is `base32(HMAC(HMAC(B, subkey(...)), token))[:26]` with `B` a server-held key
  (`crypto.binding_key_from_kek`: HKDF of the job KEK, label `dps.v1|xtb`). No new setting: the
  binding keys are the KEK ring (`secrets.kek_ring`: `DPS_JOB_KEK`, then `DPS_JOB_KEK_PREVIOUS`), so a
  KEK rotation keeps old tags verifiable while the previous KEK is kept (HANDOFF §8 trap). The tag
  travels as `reverse_params.bind` of the hint (value-free: computed under keys).
- `dps/engine/transform/methods.py`: `TokenNotBound(MethodError)` (`code external_token_not_bound`),
  `TransformContext.binding`, `external_tag(ctx, token, binding=None)`; `_external` returns an
  `Outcome` with the tag in obfuscate mode. Without a KEK, obfuscation with `external_tokenize` fails
  closed (`MethodError` → 400) and `build_plan(..., external_bound=False)` marks the entry
  `|external:unbound` (planner).
- `dps/engine/transform/apply.py`: `ApplyOptions.binding_keys` and `on_detokenize`;
  `_detokenize_prepass` checks **every** external hint's tag (constant time, against each binding
  key) before the first call — one unbound token refuses the whole request — then calls the audit hook
  once per (entity type, profile) batch before that batch's call. The check runs even without an
  adapter.
- `dps/engine/service.py`: `PrivacyEngine.token_binding`; `reveal(..., on_detokenize=None)`; a
  `TokenNotBound` becomes `CodedError("external_token_not_bound", status=403)` (both the JSON route's
  `map_engine_error` and NDJSON's `_failure` already render a `CodedError` with its status and code).
- `dps/layers/api/common.py` `detokenize_auditor(request)`: the hook writes one `AuditEvent`
  (`action external.detokenize`, `outcome requested`, tenant, API key id, `findings_by_type {entity:
  n}`, `counts {tokens: n}`, the call's trace id) through the app's sink **on the event loop** (the
  engine runs on a worker thread and `DbAuditSink`'s queue is loop-bound), waiting for it; a strict
  refusal is `CodedError` 503 `audit_unavailable` and nothing is sent. Wired into `/reveal` (JSON),
  the JSON-array stream and the NDJSON-body stream. "Key id" in D-B is read as the API key id (the
  accountable caller); the DEK's kid is not audited (it is in the hints, not a secret, but not needed).
- The tag binds caller key, entity type and profile — not the tenant or the path: a tenant holding
  another tenant's key material and hints can reveal everything else of that output anyway.
- `tests/test_external_tokenize.py`: `_settings` sets a test `job_kek` (obfuscation now needs it);
  `tests/test_api.py`'s `_BrokenEngine.reveal` double takes the new keyword (same assertions).
- Tests: `tests/test_external_binding.py` (7) against the mock with a server-side request counter:
  forged hints (no tag, the plan's DEK-only tag, made-up, replayed, non-string, another profile, one
  forged among legit ones, a tag of another deployment) → 403 and **0** detokenize requests received;
  rotation; no KEK; API audit per batch (value-free dump), NDJSON stream refusal, strict sink → 503
  with 0 requests.

## Deferred to final stage

- Task 1: append the M5 variables to `.env.example` (blocked by permissions in this lane).
- Task 1: `Dockerfile`'s comment still says the `[tabular]` extra "arrives with M5" and is skipped;
  the install line already names the extra, so the image now gets numpy + scipy. The comment and the
  image size measurement belong to Task 24 (images), which this lane does not run.
- Task 5 → Task 6 (closed in Task 6): a table whose every non-key value combination is real now
  fails prepare with `leakage` and a detail that says why; whether to reject only matches of
  rare/unique real rows instead stays a privacy decision for the final stage (not taken here).
- Task 5 → Task 6 (closed in Task 6): `ConstraintError` maps to `incompatible_method` at prepare
  (with the one-integer-key pre-check) and in partitions; integer precision 4/9/18 is passed.
- Task 7 → Task 24: an `EXTRAS` (or `WITH_SYNTH_ML`) build argument for a bank-built image with the
  `synth-ml` extra; the MOSTLY SDK call (`_sdk_trainer`) has never run against the real SDK here —
  run the `ml` tests where the extra is installed.
- Task 7B: the MOSTLY linked SDK call (`_sdk_linked_trainer`: table config keys, `generate(...)
  .data()` returning both tables, the context foreign key) and `achieved_epsilon` are unverified
  without the extra — run `pytest -m ml tests/test_mostly_linked.py` where it is installed. The
  console (Task 21) and PDF (Task 14) must render `tabular.linked`, `tabular.dp` and the
  `{parent, child}` shapes of `tabular.columns` and `fidelity.tables`.
- Task 8 (open): `reveal_row` (L2 reveal, `tools/reveal_rows.py`) does not detokenize
  `external_tokenize` columns (it has no tokenizer in its candidate loop); an L2 policy that maps a
  column to `external_tokenize` on a deployment without an adapter fails at the preflight with an
  `internal_error` retry (`MethodError`) instead of a coded refusal at submit. The `tokenizer-mock`
  container itself runs once an `m5` image exists (Task 24 smoke).
