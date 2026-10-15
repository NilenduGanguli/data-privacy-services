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

## G2 review fix — L2 reveal of `external_tokenize` columns (Task 8 open item)

- L2 stores no reveal hints, so an L2 external token has no `bind` tag and L1 `/reveal` refuses it
  (SEC-4). The bound path is the key holder's: `tools/reveal_rows.py` gains `--tokenizer-url`,
  `--tokenizer-token-file`, `--tokenizer-ca-file` (the **key holder's own** credentials; https unless
  the URL is the local machine, `TokenizerUrlRefused`) and `--chunk-rows` (default 1000). DPS's
  `PrivacyEngine` in the tool is built with `tokenizer=None`, so no `DPS_TOKENIZER_*` setting is ever
  read there. The key is checked against the pack's key id **before** any service call (the per-row
  check of `reveal_row` came after the first row).
- `dps/engine/batch.py`: `external_reveal_cache(rows, *, reveal_map, shape, tokenizer)` (one detokenize
  call per entity type and profile per chunk; every answer must tokenize back to its token, else a
  value-free `MethodError`) and `reveal_row(..., external=None)`; without the cache an external column
  is refused by path. Rows are revealed per chunk and written after the chunk, so a refusal in a chunk
  writes none of its rows (before: rows up to the refused line were written).
- `main` reports `ExternalServiceError` (value-free) as a refusal instead of a traceback.
- Tests: `tests/test_reveal_rows_external.py` (6) against the counting mock: round trip in 3 chunks
  (3 detokenize requests), refusal by path without a tokenizer, another key → 0 requests, wrong
  credentials (HTTP 401) and a tampered token refused without values, the https rule, and a service
  whose answer does not tokenize back.

## G2 review fix — D-A: synthetic tables never carry linkable real identifiers

- **Process note:** `tests/test_key_pseudonyms.py` was written before `dps/engine/synth/keys.py` and
  the `BatchTransformer` change but first run after them (both were new interfaces, so the run would
  have failed on the import); the targets, integration and review tests were seen failing first
  (missing `pseudonym_columns`, `KeyError: '$.customer_id'` in the evidence, no `review_required`).
- **Which columns.** D-A names "a sensitive key (`CUSTOMER_ID`, `ACCOUNT_NUMBER`, … per taxonomy) or
  any column discovery classified as personal". As built (`targets._identifiers`): projected,
  non-free-text columns with an entity type whose synthesize method is `keep` **and** that are a key,
  a join identifier (`LINK_ENTITIES` = discovery's `JOIN_ENTITIES`, now one constant in
  `dps/engine/synth/keys.py`) or of MEDIUM+ sensitivity. Classified columns with any other synthesize
  method are not copied anyway (surrogate, null, …). ~~LOW attributes a policy keeps on purpose
  stay … the copula models them when they qualify~~ — **withdrawn in the G2 review r1 fix
  (below)**: that sentence was false (the copula never modelled a classified column) and the
  narrowing contradicted D-A; LOW attributes are now never copied.
- **Pseudonym** (`dps/engine/synth/keys.py`, pure Python): `KeyPseudonyms(seed)` maps `v →
  p⁻¹(p(v)+1 mod N)` with `p` a keyed Feistel permutation (HMAC-SHA256 rounds, counter-mode output for
  wide halves, cycle walking) keyed by the synthesis seed and entity type — injective, deterministic,
  consistent across the tables of a job group, and **without fixed points** (a plain keyed
  permutation maps about one value in N to itself, i.e. copies it). Integer tiers do not depend on
  the column type (so `integer` and `bigint` columns of one group agree): below 10⁹ → below 10⁹,
  else the same digit count (≤ 18). Text: length and ASCII character class kept per position; other
  characters kept; uuid → uuid. The constraints module keeps its own `feistel_permute` (Task 4,
  ≤ 18 digits); the new one is wider (arbitrary domains).
- `dps/engine/batch.py`: `PseudonymColumn` (value-free) and `BatchTransformer(..., pseudonyms=())`
  (synthesize only; never a planned or sampled column); applied after the row transform and before
  the tabular draw, counted as `key_pseudonym`, `incompatible_method` when the pseudonym does not fit
  the column's digits; a pseudonym equal to its source would be a `fixed_point` hit.
- `dps/layers/connectors/targets.py`: `pseudonym_columns(...)`, `RunPlan.pseudonyms`,
  `resolve_target(..., pseudonyms=)` (written in place); `validate_run` (synthesize) refuses
  `synthesize_in_place_sensitive_key` (422), an `offset`/prefix append of a sensitive key
  (`pk_strategy_invalid`; D-A said "use table, files or append" — append with `generate` stays) and
  `synthesize_pseudonym_unsupported` (422: integer columns under 9 digits — a `smallint` key; dates,
  floats). `INT_DIGITS` moved to `keys.py` (tabular re-imports it).
- `dps/layers/connectors/tabular.py` `prepare_tabular(..., pseudonyms=)`: pseudonymised columns leave
  the exact-row reject columns (a pseudonym never equals its real value, so comparing on it would let
  a row that copies every other column through).
- **Kept-column review.** The lane predates main's `review_required` (main `89a9e98`, all modes,
  console). Here, for synthesize runs only: `jobs_run.synthetic_kept_columns` (written, unclassified,
  not key, not planned, not pseudonymised, not sampled) and `kept_review` (MEDIUM+ findings; numeric
  columns only above discovery's bar, like main's `scan_written`) run on the **preflight** output
  (`plan.prepare.kept_review`, every target, files included — files have no post-write sample) and on
  the post-write sample of database targets (`post_sample.synthetic_kept`); any flagged column makes
  `verification.review_required` true, `review_columns` its names and `passed` false. At integration
  main's `verification_outcome` should absorb this (one rule, all modes).
- `dps/layers/connectors/evidence.py`: one `columns[]` entry per pseudonymised identifier (method
  `key_pseudonym`, not reversible, its value count).
- Tests changed: `tests/connectors/test_synthesize_copula.py` (the key is now the pseudonym: the
  target is re-keyed through `KeyPseudonyms` and asserted disjoint from the source keys);
  `test_resume_after_kill[append_offset_named]`, `test_run_failures::test_same_table_append_refuses_
  an_offset_inside_the_key_span`, `test_run_modes::test_append_offset_to_the_same_table` review the
  demo key as not personal (`tests.connectors.helpers.KEY_NOT_PERSONAL`) — they test offset-append
  mechanics, which D-A no longer allows on a sensitive key. New: `tests/test_key_pseudonyms.py` (7),
  `tests/test_kept_review.py` (2), `tests/connectors/test_targets.py` (+3),
  `tests/connectors/test_synthesize_identifiers.py` (3, integration: two tables of one seed, 0
  survivors per identifier column, the FK joins all 900 rows, no source value anywhere in either
  target; in-place refusal before a job exists; review_required for a PII column reviewed as not
  personal).

## G2 review r1 fix — D-A B1 (classified attributes) and B2 (unclassified keys)

Review `M5-G2-review-r1.md`: B1 — LOW attributes kept by `keep_non_sensitive` (city, postal code,
gender, age, date …) were copied row-aligned (probe: 400/400 on each) while the evidence passed;
B2 — an unclassified key (`loan_id`) was written with its real value next to the pseudonymised
`customer_id` (a re-link path). Tests were written first and seen failing (unit:
`pseudonym_columns` returned `[]` for an unclassified key, the offset append was accepted,
`country_code` had no plan, `tabular_columns` had no `personal`; integration: city, postal code,
gender and age 400/400 for both `copula` and `none`, `loan_id` 300/300 and `repayment_id` 900/900
real survivors).

- **B1, never copied.** `targets.kept_attribute(c)`: a classified, non-free-text column whose
  synthesize method is `keep` and that is not an identifier (key, `LINK_ENTITIES`, MEDIUM+).
  `build_column_plans` plans it `surrogate` in synthesize mode (every write mode, in place too).
  Single-table tabular runs model it instead when the engine can (`tabular_columns(...,
  personal=True)`, used by `prepare_tabular` and `runtime_sampler`; categorical cardinality and
  kind rules unchanged): `_prepare_tabular`'s `make` drops the surrogate plan of every kept
  attribute the fitted sampler draws, so the stored plan the partitions rebuild from is the one
  without it; anything the engine declines (high cardinality, kind, fit-time exclusion) keeps the
  surrogate. Linked generation (Task 7B) is unchanged: it leaves classified columns out entirely.
  Choice among the review's options (a)/(b): (a) where the engine can, (b) otherwise — no
  refusal, no `review_required` needed. The snapshot still shows `keep` (discovery and the
  console are unchanged); the evidence shows `surrogate` or the column in `tabular.columns`.
- **Verification.** A surrogate gender or nationality is persona-drawn and may equal the original
  (M0 `_IDENTITY_OK`; the schema sweep's `SWEEP_EXEMPT`), which the strict sweep reported as
  `fixed_point` (206 hits on 400 rows, the job failed). `batch.CHANCE_EQUAL_ENTITIES = {GENDER,
  NATIONALITY}`: an equal whole value of these two is not a fixed point (an unequal one is still
  searched for elsewhere in the row). No other rule changed; every other entity still fails on an
  unchanged value (`tests/test_batch.py`).
- **B2, keys.** `targets._identifiers` now also returns every unclassified key a `table`/`files`/
  `append` (non-`generate`) run writes, labelled `KEY|owner.table.column`, and every unclassified
  declared foreign key labelled by the key it references. `PseudonymColumn.label` (new, optional)
  keys the pseudonym (`entity_type` is `KEY` for these); classified columns keep their entity label.
  `ColumnInfo.references` (new, optional) is filled by the PostgreSQL `describe` from
  `pg_constraint` (single-column FKs; the first by constraint name); `pseudonym_columns(...,
  shape=)` reads the live shape at prepare (submit reads the snapshot's). In place: unclassified
  keys and foreign keys are not pseudonymised (the key is the row rewritten; the parent rows are
  real). Offsets: `validate_run` refuses any `offset`/prefix append in synthesize mode
  (`pk_strategy_invalid`), not only of a sensitive key — `real key + offset` links back.
- **Fail-closed guard.** `jobs_run.copied_personal_columns(plan, snapshot, sampled)` — written
  classified columns or keys that are neither planned, pseudonymised nor drawn — fails prepare with
  `leakage` before the preflight. Empty by construction; a unit test shows it names the columns of a
  wrong plan.
- **Tests changed.** The three offset-append tests (`test_resume_after_kill[append_offset_named]`,
  `test_run_failures::test_same_table_append_refuses_an_offset_inside_the_key_span`,
  `test_run_modes::test_append_offset_to_the_same_table`) now run in **obfuscate** mode with
  `IN_PLACE_REVIEW` (offset mechanics; the default obfuscate review overflows the same table's
  `varchar` columns); the last one first asserts that a synthesize offset append of a key reviewed
  as not personal is refused (422) before a job exists. New: `tests/connectors/test_targets.py`
  (+3), `tests/test_tabular_prepare.py` (+1), `tests/test_batch.py` (+1),
  `tests/test_kept_review.py` (+1), `tests/connectors/test_synthesize_identifiers.py` (+3
  integration: B1 `copula` and `none` — row-aligned equality per LOW-classified column at chance
  level, QI combination ≤ 2 rows; B2 — 0 real survivors of `loan_id`, `repayment_id` and the
  declared FK `repayments.loan_id`, the FK joins all 900 rows, both labels
  `KEY|<schema>.loans.loan_id`).
- **Limitations (open, for the privacy review).** (1) A surrogate DATE is the original shifted by
  ±30 days and a surrogate AGE stays in its 5-year band (the M0 generators): not equal, but close;
  the copula path draws them independently when it models the column. (2) An unclassified foreign
  key that references a *classified* key (or the reverse) gets a different label than the key, so it
  is pseudonymised (no leak) but no longer joins; a reference without a declared constraint is not
  known and is copied unless discovery classified it. (3) A written key or declared FK no pseudonym
  fits (smallint, date, float) refuses the run (`synthesize_pseudonym_unsupported`); a FK can be
  left out of `selection.columns`. (4) Multi-column foreign keys are not named.

## Task 9 — DB-backed API keys: rotation, expiry, disable; operator CLI

- `dps/auth_db.py`:
  - `_validate` also refuses a tenant id that is not a job-platform id or that starts with `_`
    (`422 invalid_tenant`): the plan let a wildcard creator mint a key for `_system` or for an id
    with spaces (harmless at lookup, but never meaningful). New test
    `test_reserved_and_malformed_tenants_are_refused`.
  - Listing uses one helper, `covers(actor, tenant_ids)` (same rule as the plan's inline
    expression: a wildcard key is visible only to a wildcard actor). `rotate_key` runs
    `check_grant` **before** the rotated/status checks, so a caller who does not cover the key gets
    403 rather than learning its state. New test `test_a_tenant_admin_cannot_touch_a_wider_key`.
  - `refresh()` first calls `db.init_pool(self._settings)` (the app's settings, never the process
    defaults a fresh pool would otherwise read) and puts the `last_used` ids back when the flush
    fails; `run()` catches any `Exception` (class name logged), not only `PostgresError`/`OSError`
    (an `asyncpg.InterfaceError` or a pool error would have killed the refresher, leaving the
    snapshot to go stale).
  - `_table` uses `db.schema_ident(settings)` (validated, quoted) instead of an f-string of the raw
    schema name; `GrantError` details never quote a scope, tenant or name the caller sent.
- `dps/keys_cli.py`: also logs `api_key_disabled`/`api_key_enabled` (key id only); `rotate`'s `old`
  is an `ApiKeyView` (never a key). `main()` wraps `amain()` so tests and the image entry point share it.
- `dps/app.py`: the refresher starts **after** the job platform connected and only when it is
  configured, and is stopped **before** the pools close (else its next refresh would re-create a
  pool on a closed loop). `_db_key_store()` imports `dps.auth_db` lazily (a Layer 1 install without
  the `db` extra keeps static keys only). The "no keys" warning now fires only when there are no
  static keys **and** no database.
- `tests/test_auth_db.py::test_api_accepts_a_db_key` does not use `migrated_db` (whose pools live
  on the fixture's loop and would be handed to the app's lifespan on another loop); it truncates the
  tables itself like `api_client`. `tests/test_keys_cli.py` runs the CLI with `cwd=tmp_path` and
  without the lane's other `DPS_*` variables (a `.env` in the tree cannot change its settings), and
  gains `test_refusals_exit_2_value_free_and_mutations_are_audited` (exit 2 + code; one `_system`
  audit row per mutating command, value-free).
- `tests/db/test_migrations.py::test_files_are_the_contract_set`: pinned M2's ten files exactly; it
  now pins M2's files (`< 020`) and M5's (`040–059`) separately, so each lane's migrations are checked
  by its own range (M3/M4 add theirs at integration).
- `HANDOFF.md`: the bootstrap path and the key-store behaviour went into §5 (probes and ops), where
  `/readyz` and the metrics are described.

## Task 10 — Tenants, quotas and the tenant directory

- `dps/migrations/040_tenant_quotas.sql`:
  - **`registered_hosts`** is M4's column (`031_tenant_hosts.sql`), which this lane does not have; the
    M5 tenant API edits it, so 040 adds it with `ADD COLUMN IF NOT EXISTS` (a no-op after 031 once the
    lanes are merged, since 031 sorts first). **Integration note (WP-I):** a database migrated on the
    `m5` lane before the merge would then get 031 as a pending file *after* 040; make M4's 031
    `ADD COLUMN IF NOT EXISTS` too (it is not applied anywhere outside lane test databases), or
    recreate lane databases (`docker compose down -v`).
  - The `owner_directory` policies are `TO CURRENT_USER` (the migration role that owns
    `dps_tenant_directory()`), not a hard-coded `dps_owner`: production may name the migration login
    differently (`DPS_PG_MIGRATION_USER`), and the policy must target the function's owner.
- `dps/tenants.py`:
  - `TenantSpec`/`TenantPatch` use `hide_input_in_errors` and `extra="forbid"`; a host error names the
    entry index only (`registered_hosts: entry N is not a valid host`). `normalize_host` also maps a
    malformed port or an IDNA failure to the same value-free `ValueError`.
  - `TenantPatch` writes exactly the fields present in the request (`model_fields_set`); an explicit
    `null` resets an override to the settings default (a retention reset re-stamps with the default).
    The retention re-stamp also updates `dps_evidence.expires_at` (the plan named jobs and artefacts;
    evidence rows carry the same expiry column).
  - `create_tenant`/`update_tenant` refuse a tenant the caller is not granted (403
    `tenant_forbidden`, checked before existence); `create_tenant` on an existing row (e.g. one the
    first job inserted) is 409 `tenant_exists` — change it with PATCH.
  - `check_daily_quota` reads the quota **uncached** (one primary-key lookup) instead of through the
    30 s `effective_tenant` cache: a quota an admin just set applies to the next submission on every
    replica, like the claim-side check (and tests that truncate tables never see a stale quota).
  - `TenantView.keys` is `{"active", "disabled"}` from the global key table (keys granted the tenant
    or `*`).
- `dps/jobs.py::checkpoint` now runs in a transaction and uses `UPDATE … RETURNING tenant_id` to
  learn the tenant for the usage row (it had no tenant in hand); an empty batch writes no usage row.
- `POST /jobs/run`: the quota check runs only when the request is not an idempotent replay of an
  accepted job (a replay never counts as a new submission). A refusal is audited like any
  `job.create` rejection.
- Tests: `tests/test_tenants.py` adds `test_view_counts_usage_and_keys` and a connection-only tenant
  in the directory test; `tests/test_quota.py` builds partitions through M2's queue functions and the
  API test runs through the `api_client` fixture (a `RunSpec` that is valid but names unknown ids:
  the quota refusal comes first, a tenant without a quota fails later on lookup). Shared fixtures:
  `tests/fixtures/db.py::_truncate` truncates the M5 tables when they exist; `tests/db/test_rls.py`
  seeds one row per M5 tenant table (`M5_SEEDS`) so the per-table isolation test covers them.

## Task 11 — Admin API: keys, tenants, audit (with CSV), queue

- `dps/layers/api/routers/admin.py`:
  - Audit events go through M2's `deps.audit_action` (route template, `action`, outcome, status,
    integer counts) instead of a second helper (`audit_admin` in the plan): one audit code path,
    the same strict-sink 503 behaviour. Counts: `{tenants, scopes}` for key create, `{grace_hours}`
    for rotate, `{hosts}` for tenant create, `{fields}` for tenant update — never an id or a name.
  - `POST /admin/keys` and `/enable` refresh this replica's key snapshot after the change (the plan
    refreshed only on rotate/disable/enable): a key created through the console works at once on
    the replica that answered; other replicas follow within `DPS_KEY_CACHE_SECONDS`.
  - Rotation `grace_hours` above `DPS_KEY_ROTATION_MAX_GRACE_HOURS` is refused with 422
    `invalid_grace` (the plan's body allowed 0–168 and `rotate_key` silently clamped).
  - `GET /admin/queue` does **not** call M2's `queue_stats()`: that runs on the worker pool (a
    cross-tenant `worker_claim` read) which the API process must not open. The route aggregates
    per-tenant counts under each tenant's GUC for the tenants the caller covers (directory +
    explicit grants; `_system` for wildcard keys) and reads the global worker registry; the shape is
    `queue_stats()`'s rows (`table, state, n, oldest_s`) plus the workers.
  - Audit `tenants=*` is the tenant **directory** (tenants with a record, a job or a connection) plus
    the caller's explicit grants, filtered by grants — a tenant that only ever called Layer 1 has
    audit rows but no directory entry; name it explicitly or create its record
    (`POST /admin/tenants`). Reading `_system` audit rows (CLI and retention events) is allowed for
    wildcard keys only (explicitly or through `*`); contract §8.1 forbids *acting as* `_system`,
    not reading its value-free audit rows.
  - Bad cursors are 422 `invalid_cursor`; a window longer than 400 days, inverted, or naive is 422
    `invalid_window`; `action` is matched as `action = x OR action LIKE x.% ` with `x` LIKE-escaped.
- `dps/audit_query.py`: `query_audit` takes the tenant per query as its own parameter; the CSV is
  produced by `stream_audit_csv` in pages of 1000 through `query_audit` (as planned), `methods`
  rendered sorted (`email=1;fpe_digits=3`).
- Tests (`tests/test_admin_api.py`): the fixture mints DB keys on the app's loop (portal) and
  truncates afterwards (no `migrated_db`, see Task 9); added `test_a_tenant_admin_cannot_escalate_
  over_http`, window/cursor refusals, the CSV `methods` cell and a 422 for a host with credentials
  that never echoes them. The audit fixture creates tenant records for `a` and `b` (see `tenants=*`).

## Task 12 — Report roll-ups and the reports API

- `dps/migrations/041_report_rollups.sql`: no `CREATE INDEX dps_audit_tenant_ts` — 007 already
  creates an index of that name on `(tenant_id, ts DESC)`; the plan's `IF NOT EXISTS` would have been
  a silent no-op.
- `dps/reports.py` roll-up statements:
  - The job statement also skips `values_by_method` keys containing `:` (`col:<name>`,
    `fallback:<method>`, `verify:hits` — M2's per-column/fallback/verification counters, see
    `evidence._split_counters`); the plan summed them as transformed values and would have counted
    every value two or three times.
  - JSON numbers are cast `::numeric::bigint` (a `3.0` in a pack or progress map would make the
    plan's `::bigint` cast fail the whole refresh); evidence `columns` elements must be objects.
  - `rollup_lock_key(tenant)` includes the schema (`dps.rollup.<schema>.<tenant>`), so two schemas
    on one server never share a lock.
- `summary`:
  - `trend` splits by mode from the row's action or mode (`obfuscate`, `document.obfuscate`, a
    `job.run` row whose mode is `synthesize` …).
  - `posture.policies_latest_pct` is 100 until the policy store exists (Task 15 replaces it), as is
    `key_held_by_caller_pct` by design; `snapshots_reviewed_pct` counts accepted **or superseded**
    snapshots (a superseded snapshot was accepted when the job ran).
  - `layers.ui.risk_flags` reads M4's `dps_scan_page` only when the table exists (this lane has no
    030); 0 otherwise. `last_scan.personas` is the scan spec's `fill.personas`.
  - The LLM tiles are constant 0 as planned (Task 16B adds migration 044).
- `jobs_report`: every day × layer (`api`, `conn`, `ui`) is present (zeros filled) so the chart needs
  no gap handling; failed sweeps are capped at 100 per kind; the API ones are one per (day, route).
- `evidence_list`: `q` matches the job id, ticket or policy name exactly (case-insensitive) and the
  source object as a substring (LIKE-escaped); `layer=conn|ui` only (admin/api layers have no packs).
  The summary also carries `policy_version` (the pack's `policy.version`, M5 Task 6).
- Routes: `/reports/evidence` and `/reports/api-rollups/{day}` refresh their window first like the
  others; the `{day}` document's `sha256` is over the canonical JSON of the document **without**
  `generated_at` (so the hash attests the numbers, not the time of download); the document's
  `entities` are the day's API-layer entity counts (entity counts are not kept per action).
- Tests: the HTTP tests seed through the app's portal (no `migrated_db`, see Task 9); the summary
  test also checks the trend split, the layer numbers and that no fixture sentinel (a job `error`,
  an audit `trace_id`, a `reveal_map` entry) reaches any answer.

## Task 13 — Retention: extend M2's retention job

- **Counter keys.** The plan both adds `retention:rollup_rows`/`retention:usage_rows` to
  `values_by_method` and requires M2's `tests/db/test_retention.py` to stay green unchanged — whose
  dry-run test pins the counter map with `==` to M2's five keys. Both hold: the M5 keys are written
  only when non-zero (`RetentionCounts.counters()`); M2's five are always there; the full counts are
  in `progress.metrics.retention`. Contract §3.3 says so.
- `run_retention(*, settings, artefacts, dry_run, on_tenant=None)`: the optional `on_tenant` callback
  carries M2's fenced per-tenant progress update (`advance_job` → `False` = lease lost, the run
  stops), so the refactor keeps M2's behaviour. A `PostgresError` in one tenant's pass is logged
  (`retention_tenant_failed`, class name only) and the next tenant runs, as planned; an object-store
  error still requeues the whole (idempotent) run like M2.
- `dps.tenants.tenant_directory` gains `worker: bool = False` (read through the worker pool — the
  handler runs in a worker process, which should not open an API pool).
- Roll-ups are refreshed (today and the two days before) for tenants with a job created or finished
  in those days, never for pseudo-tenants and never on a dry run. The `retention.run` audit event
  (route `retention`, key id `system`, outcome `ok`/`dry_run`, the `RetentionCounts` as counts) is
  written through `ctx.audit`; a sink failure is logged, never fails the job.
- `POST /admin/retention/run` additionally requires a key granted **every** tenant (403
  `tenant_forbidden` otherwise): the run deletes across all tenants, so a tenant-scoped admin must
  not be able to start it. The body is optional (`{}` = not a dry run).
- Scan rows: this lane has no M4 030, so the scan-cascade part of
  `test_expired_jobs_their_objects_and_scan_rows_are_deleted` runs only when `dps_scan_page` exists
  (it will after the merge); the "no `dps_scan` statement in `dps/retention.py`" assertion always runs.
- `test_audit_log_is_never_touched` creates (and drops) audit partitions for older months on the
  owner connection: the fixture database only has partitions from the current month on.
- `HANDOFF.md` §5's worker bullet describes the M5 additions and the on-demand route.

## Task 14 — Evidence PDF and API roll-up PDF

- `dps/layers/reports/pdf.py`: every section of the plan's list is rendered, but **defensively**: the
  plan's `f"{value:.3f}"` formatting raised on a missing or non-numeric field (an older pack, a
  linked pack, `None`), so numbers go through `_num()` (number → formatted, anything else → text,
  missing → "—"). New test `test_malformed_numbers_never_break_the_render`.
- Linked and DP packs (deferred from Task 7B): a `fidelity.tables` map (`parent`/`child`) renders one
  column table + correlation matrix per table, `tabular.linked` renders as a line, and
  `privacy.dp`/`tabular.dp` adds a "Differential privacy" row. Test
  `test_linked_and_dp_packs_render_both_tables`; scan packs: `test_scan_pack_renders_the_ui_scan_section`.
- The footer is drawn on the canvas (no markup parser), so it uses the Latin-1 fold **without** XML
  escaping (`pdf_text` would have printed `&amp;`); `pdf_text` = escape(fold) for every `Paragraph`.
  A few more typographic characters are folded (curly quotes, thin spaces).
- The regulatory note's wording is fixed text per label family (pseudonymised: still personal data;
  synthetic: measurements, not a claim of anonymity) after the pack's own `label`.
- Routes: the evidence PDF route lives in `dps/layers/api/routers/reports.py` as
  `evidence_pdf_router` (prefix `/api/v1`, the evidence scope rule reused from M2's evidence router's
  public helpers), included by `create_app` next to M2's routers. The first render links the artefact
  with `UPDATE … WHERE pdf_artefact_id IS NULL`; a concurrent first request that loses removes its own
  object and row and serves the winner's bytes, so exactly one `evidence_pdf` artefact exists. The
  audit event `evidence.pdf` carries `{"bytes"}` and the job id. Both PDF responses are
  `Cache-Control: no-store`.
- The route test seeds a complete pack (`reveal_map`, `errors`): M2's `get_evidence` validates packs
  with `EvidencePack`, so the plan's abbreviated `BASE` would have been a 500 there. Added a route
  test for the roll-up PDF.
- `HANDOFF.md`: the `DPS_REPORT_PDF_ENABLED` row is in §4's M5 settings table (the plan said §3).

## G3 review r1 fix — B1: the evidence PDF for wide synthetic tables (Task 14)

- The review found that `_corr` gave each correlation-matrix column `120 / (n - 1)` mm. From 58
  numeric columns on, that is narrower than the 6 pt cell padding, so reportlab raised "negative
  availWidth" and `GET /evidence/{id}/pdf` answered 500. `metrics.py` puts every numeric column into
  `fidelity.corr_matrix`, so a wide bank table (monthly balances) triggered it. The Task 14 note
  that the renderer "never trusts the pack's shape" was false for wide matrices.
- `dps/layers/reports/pdf.py`: the lower triangle prints only up to `_CORR_MATRIX_MAX = 16`
  columns. That is the widest matrix whose 15 value columns fit 8 mm each in the 120 mm beside the
  40 mm name column. The review said "about 20", but 19 columns of 8 mm would overflow the 180 mm
  A4 frame. Value columns are clamped to `_CORR_MIN_WIDTH = 8` mm as a backstop.
- A wider matrix prints a summary line instead: the column count, the mean pair delta
  (`corr_delta`), and the largest delta (`corr_max`, or the computed maximum when the pack lacks it).
  After the line comes a table of the `_CORR_TOP_PAIRS = 10` largest pair deltas, using names and
  numbers only. Cells that are not finite numbers (text, `None`, NaN) and rows that are not lists are
  skipped. The same rule applies to each table of a linked `fidelity.tables` pack. The pack format
  and the contract are unchanged.
- New tests in `tests/test_evidence_pdf.py`:
  - an 80-column pack, checking the summary and the largest pair;
  - a linked parent/child pack of the same width;
  - widths 2, 8, 16, 17, 20, 57, 58 and 120;
  - a wide matrix with malformed cells;
  - a DB route test showing that an 80-column pack gets 200 with a PDF.
  Before the fix, the 58, 80 and 120 cases raised `ValueError`. The reviewer's probes
  `p8_isolate.py` and `p8b_threshold.py` now report every count as ok and no failing threshold.

## Task 15 — Policy store: tenant policies, drafts, immutable versions, `@name` references

- `dps/layers/api/policy_refs.py`:
  - Errors are `CodedHTTPException` (M1's coded HTTP error, the same `{"detail", "code"}` wire shape)
    instead of M2's `DpsHttpError`: the L1 routes map exceptions through `run_engine`/
    `map_engine_error`, which knows coded HTTP errors but would turn a `DpsHttpError` into a 500, and
    importing `dps.layers.api.errors` would pull asyncpg into a Layer 1 install. The store itself is
    imported only when a reference (`@…`) is used.
  - A resolved body is a `StoredPolicy` (a `dict` subclass made only from a stored row), which
    `policy_label` names `<name>@v<N>`. A caller's inline dict carrying `name`/`version` is never one,
    so it cannot pass itself off as a stored version in the audit trail.
  - Two helpers: `resolve(request, ref)` (no audit — for code paths whose error is audited by their
    caller: `run_engine` streams, jobs' `rejected()`, documents' refusal) and
    `resolved_policy(request, ref, *, mode=)` (audits a refusal as one event of `mode` with the code
    as its outcome — the plan's signature plus `mode`). Configured-but-down database → 503
    `db_unavailable` (the plan named only the unconfigured case, 400 `policy_store_unavailable`).
  - The NDJSON paths resolve too: a JSON array streamed as NDJSON (the envelope keeps the resolved
    object: assigned after construction so pydantic does not copy it into a plain dict) and an
    NDJSON request body (resolved after the envelope line is read, before the policy is used).
- `dps/policy_store.py`:
  - `publish(..., settings=None)`: the `verify_warn_allowed` check needs the app's settings; the
    route passes `request.app.state.settings` (default `db.active_settings()`).
  - Version 1 of an adopted preset is the preset with `name` set to the tenant policy's name and
    `version` 1 (every body carries the policy's own name), so `active_hash` equals the preset's hash
    when the policy keeps the preset's name (the plan's test) and differs otherwise.
  - `save_draft` stores the **validated, resolved** body (a draft sent as `{"preset", ...overrides}`
    is stored as the full policy), so `publish` and the diffs never see the override form.
  - Additional value-free codes: 409 `policy_exists` (create on an existing name), 422
    `invalid_preset` (base preset not shipped), 404 `version_not_found` (versions route), 422
    `invalid_change_ticket` / `invalid_note` (043's length CHECKs, checked before the transaction).
  - `diff_versions()` and `active_versions()` added for the diff route and the reports.
  - `PresetView.jobs` counts the tenant's run jobs whose `spec.policy` is that preset name (a string);
    a tenant policy's `jobs` and each version's `jobs_pinned` count `spec.policy` objects by
    name/version.
- `dps/layers/api/routers/policy_store.py`: `DELETE /draft` is audited `admin.policy.discard`; the
  diff route's `to` is `draft` or a positive integer (422 otherwise). `dps/app.py` registers it last in
  `_include_m2_routers` (after M0's policies router) and CORS allows `PUT` (the draft route).
- Router call sites: M0 `detect`/`obfuscate`/`synthesize` (JSON + NDJSON), `policies/validate`
  (now async; a `CodedError` such as `verify_warn_disallowed` is answered with its code instead of a
  500) and `policies/plan`; M1 `documents/*` (a refused reference is audited like any refusal);
  M2 `jobs/discover` and `jobs/run` (inside the audited try block, before `validate_run`). M1
  `schema/*` takes no `policy` (nothing to change). M4 `scans` is not on this lane — **integration
  note (WP-I):** `POST /scans` must call `body.policy = await resolve(request, body.policy)` like
  `jobs/run` when the lanes merge.
- `dps/reports.py`: `posture.policies_latest_pct` is now computed (Task 12 left it at 100 "until the
  policy store"): run jobs in the window whose `spec.policy` is a tenant policy at its active
  version, over all run jobs; presets, inline policies and unknown names count as latest.
- Tests: `tests/test_policy_diff.py` has 4 tests (plus `if_` → `if` in YAML); `tests/test_policy_store.py`
  has 10 (plus `test_resolve_ref_grammar_and_states`; the HTTP test also covers `/detect`, both
  NDJSON paths and the audit labels; the run test also checks the job's stored spec through the
  owner connection and `policies_latest_pct` before/after a newer version; the isolation test also
  checks the admin scope and that `/policies/presets` keeps its meaning). The immutability test runs
  the owner's UPDATE/DELETE with the tenant GUC bound (FORCE RLS would otherwise match no rows and
  the trigger would never fire) and also asserts the app role has no UPDATE privilege.
  `tests/db/test_rls.py` seeds both tables (both are append-only for the API: no DELETE grant);
  `tests/db/test_migrations.py` pins `043_policies.sql`; `tests/fixtures/db.py` truncates both.
- Contract: §2.2 (043), new §5.5 "Policy references", §9 rows for the eight routes.

## Task 16 — Posture tightening: strict verification at boot, secrets from files, TLS-only egress, KEK rotation, runtime posture report

- `dps/posture.py`:
  - The KEK rule ("must be set") applies only with `DPS_PG_HOST` (contract §10: "wherever a worker
    runs"); the plan's `_m5_problems` required it for every deployment, which would refuse an L1-only
    deployment that M0/M2 explicitly boot. The "decodes to 32 bytes" check stays as a backstop but
    can never fire: `Settings` already refuses a KEK that is not base64 of 32 bytes (so the plan's
    parametrised case `job_kek=base64("short")` raises `ValueError` at construction —
    `tests/test_posture_m5.py` checks that instead, plus `job_kek=""` → "must be set").
  - The plan's audit (`DPS_AUDIT_ENABLED` and `DPS_AUDIT_STRICT`) and embedded-worker rules are
    M2's (with `DPS_PG_HOST`; `audit_strict` only affects the DB audit sink) and M0's (audit on);
    `_m5_problems` does not repeat them. `posture_problems` de-duplicates the joined list.
  - `non_strict_policies` iterates `preset_names()` (the plan's `list_presets()` returns dicts) and
    skips a default policy that names no preset (that is a configuration error every request reports,
    not a posture item). URL checks use `urlparse(...).scheme`, like M0's.
  - `posture_report(settings, *, environ=None)`: as planned; `api_keys_from_file` passes with DB keys
    (`DPS_PG_HOST`) or `DPS_API_KEYS_FILE`, and fails when a literal `DPS_API_KEYS` is set;
    `leakage_strict` adds every directory tenant's active policy whose stored body is not strict
    (`<tenant>/<name>`), reading the store; `keys_rotated` lists `"<name> is <n> days old"`. Checks
    have `enforced_at_boot` true where a boot rule exists (all but `keys_rotated` and
    `migrations_verified`).
  - Request-time checks: `PrivacyEngine.policy` raises `CodedError("verify_warn_disallowed", 422)`
    as planned; `validate_run` refuses `options.verify="warn"` **and** a policy whose resolved
    leakage check is `warn` (contract §5.3 row). `/policies/validate` maps the `CodedError` to its
    code (it previously caught only `EngineError`).
- TLS-only connections: `require_tls_connection(spec, settings, *, secret=None)` accepts anything
  with `engine` and `params` (a `ConnectionSpec` or a stored `Connection`), so `PATCH` checks the
  connection with its new params and `open_connector` checks the stored row. At use it raises
  `ConnectorError("tls_required")` (not retryable; `dps/layers/api/errors.py` maps it to 422) —
  M2's connector error, so a job fails with that code instead of an `internal_error` retry.
  `open_target_store` (s3 `files` targets) is checked too (the plan named `open_connector` only, which
  never opens s3). `open_connector` now refuses an engine without a connector (`engine_unavailable`)
  **before** opening credentials, then checks TLS for every engine; **integration note (WP-I):** M3's
  oracle/mongo branches go after the TLS check (`_CONNECTORS` lists the engines).
- `dps/app.py`: no `CORSMiddleware` at all when `DPS_CORS_ORIGINS` is empty; `PUT` allowed (Task 15).
  `/docs` and `/api/v1/openapi.json` are off in staging/prod.
- `GET /admin/posture` needs `admin` but **not** the database (the report says "database
  unavailable" instead of the route answering 503).
- `dps/rekey.py`: counts are per sealed **blob** (`rows` = blobs examined); a blob a worker rewrote
  between the read and the optimistic update counts as `already_current`. `amain(argv, *, settings)`
  closes the pools it opened; exit 2 for a configuration error (no `DPS_PG_HOST` or no KEK). The
  `_system` tenant's connections are included (the directory with `include_system=True`).
- `HANDOFF.md`: the configuration rows already existed in §4 (Task 1); the posture, TLS-only
  connection rules, posture report and the **KEK rotation runbook** went into §6 "Security posture"
  (§9 is "Next milestones" in this file). The CORS bullet now describes the M5 behaviour.
- Tests: `tests/test_posture_m5.py` (24: the plan's cases plus a mixed `*` origin list, the
  tokenizer token rule, a short-KEK construction refusal, every preset strict, request-time refusals
  over HTTP and for runs, a good production report, `GET /admin/posture` scope, and a `db` test of the
  key-age and tenant-policy checks); the API-keys file of the plan lacked `tenant_ids`/`scopes`, which
  the static key store requires; `_files` clears every `SECRET_ENV` name from the environment first.
  `tests/test_egress_tls.py` has the 12 cases (each also passing with the rule off), an
  allow-list test and a `db` test of create/patch/use over HTTP. `tests/test_rekey.py` (2, `db`)
  seals the connection through M2's `create_connection` and opens it with `load_credentials`.
  M0's `tests/test_posture.py` and M2's `tests/test_posture_m2.py` `_prod()` helpers gained the M5
  settings a good production needs (`egress_tls_required`, explicit `cors_origins`,
  `verify_warn_allowed=False`) — the rules are not relaxed.
- Contract: §5.1 (`protocol` option + the TLS rule), §5.3 (`tls_required` row), §9 (the posture
  report shape), §10 (secret files, TLS egress, CORS, docs, `dps.rekey`).

## Task 16B — Per-tenant LLM exposure counter, egress verifier and alerts

- `dps/llm/egress.py`: `residual_values(messages, *, protected=(), region="US")` instead of the
  plan's `detector: Detector` argument — the gateway holds no detector, and the full `Detector`
  (structural keys, name cues, NER) over-matches prompt text. It runs M0's T1 scanners
  (`scan_text`, `scan_phones`) and counts only self-identifying hits (checksum-validated, `EMAIL`,
  `PHONE`) outside `{TYPE_N}` placeholders. Two false-positive guards keep M1's labelling and
  spec-proposal prompts sendable under `strict`: a match made only of value-shape characters
  (`x`/`X`/`9` + punctuation — e.g. the e-mail shape `xxx.x@x.xx`) is not a value, and a protected
  original of exactly 3 characters must be a whole word (4+ characters match anywhere in the
  normalised text, like M0's leak guard). Each distinct value counts once. `LlmEgressBlocked`
  lives in `dps/llm/egress.py`; the gateway imports it lazily (egress imports the gateway's
  `LlmCallError`).
- `dps/llm/gateway.py`: a blocked prompt is also counted as `dps_llm_calls_total{outcome="blocked"}`
  and logged `llm_egress_blocked` (purpose, count); a sent prompt with raw values (warn) is logged
  `llm_egress_raw_values_sent` at error level. The verifier runs after the availability check (an
  unconfigured provider sends nothing either way).
- `dps/observability.py`: `record_llm(blocked=True)` counts a blocked prompt only (it is not a
  call); `LlmStats.counts()` gives the audit/job keys `llm_calls`, `llm_raw_values`, `llm_blocked`.
- Tenant binding: in `begin_call` (`dps/layers/api/common.py`) rather than in `run_engine` only, so
  NDJSON streams, refusals and idempotency paths bind it too; `_audit_event` adds the three counts
  **only when the request reached the gateway** (calls or blocked > 0), so every other audit row is
  unchanged. `Consumer._guard(..., tenant_id=)` binds the job's or partition's tenant.
- Callers: M0 `synthesize_text`/`rewrite_placeholders` pass the text's originals; M1's spec proposer
  is `dps/engine/synth/schema_profile.py::propose_spec` (the plan said `schema_gen.py`) and passes
  `profile_originals`; M1's `label` passes its `protected`. Each handles `LlmEgressBlocked` before
  `LlmCallError`: `llm_calls` 0 and `fallback: egress_blocked` (labelling counts the fields
  `blocked`).
- Jobs: no L2 handler calls the LLM on this lane (T3 labelling in discovery is Task 8B, deferred);
  roll-up statement 3 already sums `progress.metrics.llm.{calls,raw_values,blocked}` and the
  `_guard` binding is in place for it. The engine report's `llm.raw_pii_sent` stays the constant 0
  it was in M0 (only a local `warn` deployment can send one; the per-tenant counter and the audit
  counts carry the real figure) — open item for the final stage.
- Tests (`tests/test_llm_egress.py`, 8): the plan's six plus `record_llm` without a tenant/stats and
  a log scan of both egress decisions. The `warn` test counts M0's process counter into a private
  `Counter` (monkeypatched module attribute): M0's tests assert `dps_llm_raw_pii_sent` is 0 for the
  whole session. The blocked-fallback test forces the leak by wrapping `rewrite_placeholders` (the
  plan's `to_placeholders` patch would leave the original in the deterministic fill too, which the
  leakage sweep then refuses — a different failure). The audit test also checks that a request that
  never reached the gateway carries no LLM counts. `tests/test_reports.py` gains
  `test_summary_llm_tiles_are_per_tenant` (audit rows and a job's `progress.metrics.llm`, a
  non-numeric count ignored). The gateway doubles in `tests/test_no_values_in_logs_m1.py` and
  `tests/test_schema_profile.py` accept the new `protected` argument.
- `HANDOFF.md` §5 (metrics bullet) describes the counters, the verifier, the alerts and the
  roll-ups; contract §2.2 (044) and §8.3 ("as built").

## G4 review r1 fix — B1: the posture report is scoped to the caller and covers policy-only tenants (Task 16)

- (a) `GET /admin/posture` listed every tenant's stale DB keys and every tenant's non-strict policies
  as `<tenant>/<name>`, so a key that is admin for t1 only read t2's key and policy names. Contract
  §9 scopes admin views to the caller's tenants. `posture_report(settings, *, environ=None,
  principal=None)` now takes the caller: `_tenant_warn_policies` keeps only tenants the principal
  `can_access`, and `_stale_keys` keeps only keys the principal `covers` (the `GET /admin/keys`
  rule, so a key granted `*` is hidden from a non-wildcard admin). The route passes
  `principal_of(request)`. A wildcard key, or `principal=None` (internal callers), keeps the full
  platform view. Consequence, by design: a single-tenant admin's `leakage_strict` and
  `keys_rotated` are judged on that admin's tenants only; the deployment-wide checks (settings,
  RLS, migrations) are unchanged. The report shape is unchanged (m5ui `PostureReport` holds).
- (b) `leakage_strict` reported "strict everywhere" while a policy-only tenant had an active warn
  version: `dps_tenant_directory()` (040) unioned `dps_tenant`, `dps_job` and `dps_connection` but
  not `dps_policy`. New migration `045_policy_directory.sql` (inside M5's 040–059 range) adds an
  owner SELECT-only `owner_directory` policy on `dps_policy` (FORCE RLS applies to the owner) and
  redefines the function with `UNION SELECT tenant_id FROM dps_policy`; it still returns ids only.
  `ALLOWED_EXTRA_POLICIES["dps_policy"] = {"owner_directory"}` so the RLS posture check accepts it.
  Side effect: every directory reader (admin tenant list and `*` audit expansion, retention, rekey)
  now also sees policy-only tenants, which is correct for all of them.
- Tests (both failed before the fix): `tests/test_posture_m5.py::
  test_a_policy_only_tenants_warn_version_fails_leakage_strict` (the policy-only tenant is in the
  directory and fails `leakage_strict`) and `test_posture_shows_a_single_tenant_admin_only_its_tenants`
  (over HTTP: a t1-only admin sees `t1-ledger-recon` and `t1/t1-merger-project` and nothing of t2;
  the wildcard key sees both tenants; no raw key in either body). `tests/test_tenants.py` adds a
  policy-only tenant to the directory test and the new owner policy to the RLS-posture test;
  `tests/db/test_migrations.py` lists 045. Contract §1 (M5 migration files), §2.1 (`ALLOWED_EXTRA_POLICIES`), §2.2 (045) and
  §9 (posture scope) updated. The reviewer's probes p1/p3/p5 now show no t2 names and
  `leakage_strict` failing on the warn version.

## Lane verification r1 fix — copula on a declared foreign key; no real key in synthetic targets (D-A)

Findings (`~/.cache/dps-build/reports/M5-verify-r1.md`, blocking 1 and 2):

1. **Copula `bank.customers` died in `prepare`.** `customers.relationship_manager_id` is a declared
   foreign key (unclassified `int`): `targets.pseudonym_columns` pseudonymises it (G2 r1 B2), and
   `tabular.tabular_columns` modelled it too, so `BatchTransformer` refused the overlap with a
   `ValueError` that `jobs_run.prepare` retried as `internal_error` until the job was dead.
   - `tabular_columns(..., pseudonyms=())`: columns the run pseudonymises are never modelled;
     `prepare_tabular` passes the run's pseudonym columns. They are not listed in `tabular.excluded`
     (that list is for columns that keep their source values).
   - `batch.PlanConflict(ValueError)` for the plan contradictions `BatchTransformer` refuses (a plan or
     pseudonym naming a missing column, a sampler writing a key/planned column, a pseudonym also
     planned or sampled, tabular without a key); `jobs_run.prepare` fails such a job once with the
     value-free `plan_conflict` (new code) instead of retrying `internal_error`.
2. **A real key in synthetic targets.** The integer pseudonym was a keyed cycle through `[0, 10⁹)` for
   values below 10⁹, which contains the source's own keys: another row's real key came out with
   probability ~n²/10⁹ (observed: 1 `customer_id` in 20,000 rows, 1 `account_id` in 30,000; a
   300,000-key probe hit 85 and 92 under two seeds).
   - `KeyPseudonyms.integer` now maps into bands no accepted source value reaches: `|v| < 10⁹` →
     `[10⁹, 2·10⁹)` (fits `integer`: 2·10⁹ − 1 < 2³¹), 10 to 18 digits → `[10¹⁸, 2·10¹⁸)` (fits
     `bigint`); sign kept; > 18 digits refused as before. Each tier is a plain keyed Feistel
     permutation onto its band (injective, deterministic per seed and label, type-independent so a key
     and its foreign keys agree); no fixed point is possible since the bands are disjoint from the
     inputs. Labels changed (`<entity>|int-band|<tier>|<sign>`), so pseudonyms differ from earlier
     runs of the same seed — re-run every table of a job group under this build.
   - **Residual (documented, not hidden):** a column holding keys below 10⁹ *and* keys in
     `[10⁹, 2·10⁹)` can still see a small-tier pseudonym equal a real key of the second range — the
     `integer` type forces the small band there. A cycle-walk past the group's source key set was
     considered and rejected: walking forward to the next non-source value is not injective (two
     source keys on one run of the cycle reach the same value), and the group has no shared state
     at which the key set could be known consistently for every table.
   - Text and UUID pseudonyms are unchanged (shape-keeping cycles): a collision with another real key
     of the same shape has probability ~n²/|shape| — nil for UUIDs and long mixed keys, not for short
     digit-only text keys at very large n (open owner decision; the demo's `account_number`/`iban`
     showed 0).
   - `PseudonymColumn.limit` (largest magnitude the column holds; `max_magnitude()` falls back to
     `10**digits − 1`); `targets._pseudonym_of` refuses an integer identifier whose column cannot hold
     2·10⁹ − 1 (`smallint`, **`numeric(p ≤ 9, 0)` — newly refused**, was accepted at p = 9) with
     `synthesize_pseudonym_unsupported`; a `numeric(10..18, 0)` key with values ≥ 10⁹ now fails closed
     at the write (`incompatible_method`, the 19-digit band does not fit) where it used to keep its
     digit count.
- Tests: `tests/test_key_pseudonyms.py` (banded tiers; `test_integer_pseudonyms_never_land_on_a_real_key_at_realistic_n`:
  300,000 random 9-digit ids, a 1..150,000 sequence, 50,000 random 12-digit and 20,000 random
  10–18-digit ids, two seeds — 0 overlap; an `int4` key holds its pseudonym), `tests/connectors/test_targets.py`
  (`integer`/`numeric(10,0)` accepted, `numeric(9,0)` refused), `tests/test_tabular_prepare.py`
  (a pseudonymised FK is not modelled; `prepare_tabular` on a table with one succeeds and its preview
  holds no real key — `PlanConflict` before the fix), `tests/connectors/test_synthesize_copula.py`
  (`test_copula_job_on_a_table_with_a_declared_foreign_key`: 9-digit keys + FK to `employees`;
  `test_copula_job_on_the_seeded_demo_customers`: the verifier's reproduction on the seeded
  `bank.customers` — fails with `plan_conflict` with the exclusion reverted; both assert success,
  `exact_row_matches` 0, 0 real customer/employee keys in the target and FK pseudonyms equal to the
  referenced key's; `test_a_plan_conflict_fails_prepare_without_retrying`). Contract §5.3 D-A
  paragraph and HANDOFF §8 updated.

## Lane verification r2 fix — real national ids in synthetic targets (D-A, programme §2.4)

Finding (`~/.cache/dps-build/reports/M5-verify-r2.md`, blocker): synthesize jobs on the demo
`bank.customers` (copula and surrogate) wrote 1, 1, 2, 0, 2 and 3 real source `national_id` values
(all NINOs, each unique in the source) onto unrelated target rows. `NATIONAL_ID`, `NINO`, `CURP`,
`PASSPORT` and the other government identifiers without a generator fell back to the `Reshaper`, a
keyed derangement of the *issued* value space that keeps the NINO prefix: it maps real ids onto
other real ids (~n²/|space| per column; at bank scale most outputs would be issued to somebody).

- **`dps/engine/synth/unissued.py` (new):** generators and the `never_issued(entity, value)`
  predicate for every government identifier with a range its issuer never allocates —
  NINO (a prefix with a letter HMRC never uses; **not `QQ`**: deviation from mockup 05's "QQ prefix",
  because test fixtures, the demo seed included, use `QQ`, so a `QQ` surrogate can equal a source
  value — the predicate still counts `QQ` and BG/GB/NK/KN/NT/ZZ as never issued), CURP and personal
  RFC (first four letters an "inconvenient word" RENAPO/SAT never issue; CURP check digit valid), NHS
  (`999` test range, mod-11 valid), EIN (unassigned campus prefixes), PAN (sequence `0000`, which
  stdnum also rejects), SSN (9xx with groups 01–49: no ITIN/ATIN either; was any group, i.e. could
  equal a real ITIN), ITIN (group 89 only; 93 is the ATINs', issued), and a generic `NATIONAL_ID`
  by recognised sub-shape (12 digits → leading `1`, Verhoeff-valid; 9 digits → `000…`, Luhn-valid,
  never an SSN area nor a SIN; 10 digits → NHS `999`; NINO, CURP, RFC shapes as above). Shapes and
  types with no such range (PASSPORT, DRIVER_LICENSE, VOTER_ID, INE_CLAVE, BN, MRZ document numbers,
  company RFCs, other national-id shapes) are still reshaped and **fail closed to review** (below).
  `SurrogateEngine` routes NINO, CURP, RFC, NHS_NUMBER, EIN and NATIONAL_ID to them. Applies in
  every mode that uses the `surrogate` method (masking too), not only synthesize: a real id mapped
  onto another real id is as wrong there.
- **Post-write identifier check (`dps/layers/connectors/identifier_check.py`, `jobs_run._identifier_check`):**
  after a successful synthesize to a database target the run reads **every** written value of every
  written government-ID-family or financial-identifier column (`CARD_PAN`, `IBAN`, `ACCOUNT_NUMBER`,
  `CRYPTO_ADDRESS`) through the connector (keyset pages of 5,000, projection of those columns), then
  every source value of the same columns, and counts `in_source` (distinct target values found in the
  source column, compared as keyed 64-bit BLAKE2b hashes of the leakage sweep's normalised form under a
  fresh random key; numpy-sorted when installed, 8 bytes a value) and `issuable` (government ids not
  in a never-issued range). Either above 0 → the column joins `review_columns`, `review_required`
  true, `passed` false. A check that errors flags every identifier column (fail closed). In place:
  the ranges only (`source_compared: false`). A same-table append: `not_run`. Files targets:
  `not_run: files_target` (the generator guarantee holds; reading the written objects back is left
  open). Value-free: column names, entity types and counts. The finding suggested an SQL join; a
  connector-level stream works across servers and credentials (the source connection is read-only and
  need not see the target schema) — at the cost of two full scans per run.
- **Tools that need rule-valid PANs** no longer take them from the engine: `tools/seed_sources.py`
  draws customers' and applicants' PANs itself (per-customer generator, no other seeded value moves;
  `tests/test_seed_sources.py` now also validates them), and `dps.evaluation.corpus.rule_valid_pan`
  reproduces the engine's former draw exactly, so the committed eval corpus and demo documents are
  byte-identical (checked by regenerating both).
- Console: the Policies matrix's synthesize hints for PAN_IN, NINO, CURP, NHS_NUMBER, NATIONAL_ID and
  PASSPORT describe the new ranges / review.
- Tests (written first): `tests/test_unissued.py` (20,000 demo-style QQ NINOs → 0 surrogates among
  them, as NINO and as NATIONAL_ID — the old reshape kept QQ; real-format NINOs map outside the
  issued space; CURP; sub-shapes; SSN/ITIN groups; NHS/EIN/PAN/RFC; no-range types never claimed
  unissued; the demo's mixed column → 0 survivors, 0 issuable), `tests/test_identifier_check.py`,
  `tests/connectors/test_synthesize_copula.py` (`test_copula_job_on_the_seeded_demo_customers` and the
  new `test_surrogate_job_on_the_seeded_demo_customers`: 0 target `national_id`/`tax_id` in the source
  by SQL on the normalised value, and the run's own check agrees: checked = rows with a value,
  `in_source` 0, `issuable` 0; with the r2 fix reverted for NATIONAL_ID they fail: 2 and 1 real ids,
  18,044 issuable), `tests/connectors/test_synthesize_identifiers.py`
  (`test_a_real_identifier_in_the_target_needs_review`: a deliberately leaky generator → the check's
  `in_source` equals the source-vs-target count and the pack needs review), `tests/test_surrogates.py` (PAN now
  `0000`, invalid by design).

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
  `{parent, child}` shapes of `tabular.columns` and `fidelity.tables` (PDF: done in Task 14).
- Task 8 (open): ~~`reveal_row` does not detokenize `external_tokenize` columns~~ (closed in the G2
  review fix above); an L2 policy that maps a column to `external_tokenize` on a deployment without
  an adapter (or without `DPS_JOB_KEK` on the worker — always set for jobs) fails at the preflight
  with an `internal_error` retry (`MethodError`) instead of a coded refusal at submit. The `tokenizer-mock`
  container itself runs once an `m5` image exists (Task 24 smoke).
- Task 16B → Task 25 (WP-F2, review G4 N6): the `DpsRawPiiSentToLlm` / `DpsLlmExposureBlocked`
  `PrometheusRule` manifest belongs to the OpenShift manifests of Task 25; the alert names and
  semantics are documented in HANDOFF §5 and contract §8.3.

## Task 24 — Images, local stack, smoke-test extension, flow report (finish stage, lane `fimg`, 2026-10-14)

Finish-stage work (programme plan §2.6 F2, orchestration stream S4), built on `main` as merged so far
(M0–M3 + M5; M4 not yet merged) in worktree `dps-wt-fimg`, branch `fimg`. Proofs and measurements:
`~/.cache/dps-build/reports/S4-impl.md`.

- **`Dockerfile` — `EXTRAS`:** `ARG EXTRAS=""` adds `${EXTRAS:+,$EXTRAS}` to **both** install lines
  (the plan shows one; the `WITH_NER=1` variant keeps its own line), so `--build-arg EXTRAS=synth-ml`
  works with and without NER. Added: a `case` guard stops the build (exit 2, the rule named, the
  argument never echoed) when `EXTRAS` holds anything but lower-case extra names, digits, `-` and
  `,` — the argument is spliced into a shell command. Closes the Task 7 → Task 24 deferred item (the
  MOSTLY SDK itself stays unverified: no `synth-ml` image was built — torch ≈ 2 GB). The stale
  "`[tabular]` arrives with M5" comment is gone (Task 1 deferred item).
- **`Dockerfile` — arbitrary UID, deviates from the plan's two lines.** The plan's `chgrp -R 0 /app &&
  chmod -R g=u /app` would make the code (root-owned, `rw` for its owner) writable by every UID of
  group 0, i.e. by every OpenShift UID — contrary to the image's "`/app` read-only to the process"
  property. As built: `chgrp -R 0 /app && chmod -R g+rX,g-w /app && chmod -R g=u /app/var` (group 0
  reads all of `/app`, writes only `/app/var`, whatever the build context's modes) and `ENV
  HOME=/tmp`, as planned. **Measured gap, then fixed (test first):** with only that, artefacts that
  uid 10001 wrote into the shared volume (directories `10001:10001 0755`) refused a later arbitrary
  UID, and the smoke's `m2 parquet export` died with `storage_error` (`finalize_failed StorageError`
  in the worker log). So also: the image user's primary group is 0 (`useradd --uid 10001 --gid 0`),
  `/app/var` directories are setgid (`find /app/var -type d -exec chmod g+s {} +`), and
  `docker/entrypoint.sh` sets `umask 0002` — directories any UID creates there are `2775`, group 0,
  and objects are `0660`, group 0 (the store's own mode, see the review fix below).
  Not in the plan's file list: `docker/entrypoint.sh` (one line + comment). OpenShift manifests must
  keep the image `ENTRYPOINT` (`args`, not `command`) — HANDOFF §8.
- **Review fix (S4 r1 B1, N2, N3) — the store's objects were `0600`.** `LocalObjectStore` spooled
  every object through `tempfile.mkstemp`, which always creates `0600` whatever the umask, so the
  objects themselves stayed `600 <uid>:0` and the claim above held for directories only. Measured
  by the review on the lane stack after a UID change on the same volumes: every earlier artefact
  answered 500 (`GET /artefacts/{id}`, an unmapped `PermissionError`) and every earlier Evidence
  pack 502 `storage_error` (`GET /evidence/{id}/html`, the console's Evidence page). The runtime
  test that pinned the property wrote with `Path.write_bytes` (which honours the umask), never
  through the store. Fixed test first: `dps/storage.py` creates the spool file — which becomes the
  object by `os.replace` — with `os.open(…, O_WRONLY|O_CREAT|O_EXCL|O_NOFOLLOW, 0o660)`
  (`OBJECT_MODE`), so the kernel applies the process umask: `0660` under the image's `0002`, while
  an operator's stricter umask still narrows it (`0022` → `0640`, `0077` → `0600`) and other users
  never get access (not even under umask 0). **Deviation from the review's suggested
  `os.fchmod(fd, 0o660)` after `mkstemp`:** that mode would ignore the umask; creating the file
  with the mode gives the same `0660` in the image without overriding a stricter umask, and no
  moment exists where the file has another mode. The spool of an S3 upload (the system temp
  directory, shared on a host) stays owner-only (`0600`, unit-tested). N2: `LocalObjectStore.stream`
  (at open and mid-stream) and `exists` now map every `OSError` to `storage_error` (class name only,
  never the path), as `get` did — an unreadable object is a 502 on `/artefacts/{id}`, not a 500.
  `tests/test_image_runtime.py::test_a_new_uid_reads_and_replaces_what_the_previous_one_stored`
  replaces the old `write_bytes` test: through `LocalObjectStore` on an artefact and an export
  volume, uid 10001 stores an object (`0660` group 0 in `2775` directories), uid 1000770000:0 reads,
  streams and replaces it and writes and deletes next to it, and uid 10001 reads back what the new
  UID stored. It failed on the image of `74625d6` (`0o600`; the new UID's `get` raised
  `storage_error: PermissionError`). Objects stored by earlier images keep `0600`: HANDOFF §8's
  one-time re-permission (`chmod -R g+rwX`) covers them. N3: HANDOFF §6 now states which compose
  roles have a `/tmp` tmpfs and that the local `demo-secrets` one-shot runs as root.
- **`Dockerfile` — console stage on `$BUILDPLATFORM`** (not in the plan): `FROM
  --platform=$BUILDPLATFORM node:22-slim AS frontend`. The dist is platform-free; an amd64 build on
  the arm64 Mac then reuses the native console layers instead of running npm under emulation (the
  amd64 build log shows every `frontend` step `CACHED` from the native build).
- **`Dockerfile.browser` (M4) is not on `main`**, so its "same two lines" cannot be added here
  without an add/add conflict at the M4 merge. Instead `tests/test_images.py::
  test_the_browser_image_is_arbitrary_uid_safe` (skipped while the file is absent) pins the rules for
  it, and HANDOFF §8 gives the recipe (`--gid 0`, the entrypoint with its umask, `chgrp -R 0 /app &&
  chmod -R g+rX,g-w /app && chmod -R g=u /app/var && find … chmod g+s`, `ENV HOME=/tmp`).
  **Deviation:** no `chgrp`/`chmod -R` of `/ms-playwright` (the plan's third line): the Playwright
  base ships it `0777` throughout, so every UID can already run the browsers, and the recursive
  change copies every browser file into a new layer — measured on a probe image built from branch
  `m4` with the plan's line: +1.46 GB (4.24 → 6.2 GB). Without it the permissions layer is 20 kB and
  the probe runs Chromium as uid 10001 and as 1000770000:0 (read-only root, `cap_drop ALL`) and
  writes the artefact volume, which `m4`'s own image cannot do as an arbitrary UID (measured). The
  browsers' 0777 makes a read-only root mandatory for the browser-worker; `tests/test_compose_m5.py`
  pins it for compose (`extends: worker` inherits `read_only: true`), Task 25 for OpenShift. The
  guard fails on `m4`'s file and on the heavy variant, and passes on the recipe.
- **`docker-compose.yml`:** the three M5 settings go into the shared `x-dps-env` anchor (every role of
  the app image — app, worker, migrate — and M4's `browser-worker`, which `extends: worker`), each
  overridable (`${DPS_SYNTH_TABULAR_ENGINE:-copula}`, `${DPS_KEY_CACHE_SECONDS:-5}`,
  `${DPS_REPORT_PDF_ENABLED:-true}`); the plan wrote literals on app and worker. Added: `app`
  `depends_on: tokenizer-mock` (`service_healthy`, `required: false`, like the OCR mock) — `/readyz`
  probes the tokenizer once at startup, so under `full` it read "down" when the app won the race and
  the smoke's tokenizer check skipped. `full` already named every service; `tests/test_compose_m5.py`
  now pins that every profiled service carries `full` (M4's services slot in the same way).
- **`.gitignore`:** `var/` was already there (twice); `reports/` is added **anchored** as `/reports/`
  — unanchored, it would also ignore new files under the tracked package `dps/layers/reports/`
  (test with `git check-ignore`). `.dockerignore` gains `reports` (not in the plan: tool reports and
  screenshots never reach a build context).
- **Smoke M5 block (`tools/smoke_test.py`):** eight checks as the plan names them, whose code holds
  seven: the eighth is `db key disabled` (the disabled key answers 401 — the lifecycle's end).
  `time.sleep(6)` became a poll of the new key (`DPS_SMOKE_KEY_WAIT_SECONDS`, default 12 s; the
  creating replica refreshes at once). `policies with hashes` checks 64 lower-case hex digits, not
  only the length. `DPS_SMOKE_REQUIRE_M5=1` turns an M5 skip into a failure (the DoD's "none SKIP"),
  like `DPS_SMOKE_REQUIRE_OCR`. **Measured on the `full` stack:** every check passes except the last,
  `api responses are not cacheable`: `Cache-Control: no-store` on every `/api/` response is M5 Task
  17 S11 (security review, lane `fsec`), not yet on `main` — the check stays as the plan writes it.
- **`tools/demo_client.py`:** the plan's signatures exactly (checked by a test). Additions:
  `route_template(path)` (ids, numbers, days → `{id}`, `{n}`, `{day}`), `DpsClient.url_path`
  (paths are under `/api/v1` unless they start with `/api/` or name `/health`, `/readyz`, `/metrics`,
  `/demo/`, `/docs`), `base_url`/`tenant` properties, `close()` and context-manager use, and a
  `_transport` class attribute for tests (no constructor change). `DemoError(method, path, status,
  code=None)`: the message is method, route template, `HTTP <status>` (or `not reached`) and the
  response's error code when it is a value-free identifier; transport errors are `DemoError` with
  status 0 and code `unreachable` (the URL is never shown); `wait_job` raises code `wait_timeout`
  and, for `kind="scans"`, returns the scan with its `summary`. `load_keys` takes a flat
  `{name: key}` object or a `{"keys": {…}}` wrapper and refuses a file other users can read **or
  write** (the plan: world-readable).
- **`tools/flow_report.py`:** `EXPECT` has 85 rows — every route `main` serves (75; a test compares
  the table with the app's routes, so a new route without a row fails) plus M4's 10 scanner routes
  as built on branch `m4` (allowed to be missing from the app only until M4 merges; then the coverage
  test needs their templates to match). The scanner rows run only when `/readyz` reports the browser
  worker, else they are reported `skipped` (not a failure). Job controls on a finished run expect
  their documented 409s (`job_not_pausable`, `job_not_paused`, `job_terminal`,
  `job_not_retryable`). The run is **two** grouped `table` runs of 500 rows (one would make the
  consistency check answer 409 `member_not_ready`: it needs two targets). Besides statuses the flow
  checks the outcomes it relies on (both runs `succeeded`, the connection test `ok`, the run spec
  valid, the consistency check `passed`) and names error codes of refusals — value-free. Keys: by
  default `DPS_API_KEY` (else the local bootstrap literal), taken as an operator key; the plan's
  "demo keys" are `--demo-keys [PATH]` (`--key-name`, default `demo-console`): a tenant-scoped key
  cannot start the cross-tenant retention job or create a tenant, so those two rows then expect
  403 (`TENANT_KEY_EXPECT`). The demo keys are opt-in, not the default, so a report run never fills
  the demo tenant's pages with flow artefacts. The flow's connections use the compose stack's local
  demo logins inline (`dps_reader`, `dps_writer`; any tenant), names carry a UTC timestamp, the two
  connections are deleted at the end; tenant policies and tenant records cannot be deleted and stay.
  Like `smoke_test.py`, the tools import `tools.demo_client` with the repository root on `sys.path`
  (the plan: `tools/` on `sys.path`); `demo_client` itself imports nothing from the repository, so
  F3's tools may use either form.
- **M4 slot-in (merge of `m4` into `fimg`, 2026-10-15; report
  `~/.cache/dps-build/reports/FIMG-merge-m4.md`).** `m4` (`cbbac38`: M4 with main up to `144cc65`
  merged into it and S2 fix rounds 1–2) merged with `--no-ff` (`83bc5f3`); one conflict
  (`HANDOFF.md`), both sides kept: M4's §15/§16 keep their numbers, this task's record moved to §17
  (§18 since S4b below: main took §17).
  Then, test first:
  - `Dockerfile.browser` gets the app image's arbitrary-UID lines (the HANDOFF §8 recipe:
    `--gid 0`, `docker/entrypoint.sh` with `umask 0002` as `ENTRYPOINT`, `chgrp -R 0 /app && chmod
    -R g+rX,g-w /app && chmod -R g=u /app/var`, setgid `/app/var`, `ENV HOME=/tmp`; `chown -R
    10001:0 /app/var` as in the app image). `test_the_browser_image_is_arbitrary_uid_safe` failed on
    the merged file and passes now. **Added (not in the recipe):** uv leaves `/opt/venv/.lock` mode
    `0666` (world-writable, found by the new runtime test); the permissions step drops group and
    other write bits in the virtualenv (`find /opt/venv -perm /022 … chmod go-w`, touching only that
    0-byte file: the layer is 86 kB). `/ms-playwright` stays untouched (0777, +1.46 GB otherwise),
    so the read-only root stays mandatory for the browser-worker.
  - New `tests/test_browser_image_runtime.py` (opt-in `DPS_TEST_BROWSER_IMAGE`; with `DPS_TEST_IMAGE`
    also the app image): as uid 1000770000:0, read-only, `cap_drop ALL`, Chromium starts through the
    scanner's own `get_browser` and takes a screenshot; `HOME=/tmp`, umask `0002`, the extras import,
    `/app`, `/opt/venv` and `/ms-playwright` are not writable, the volumes are; through
    `LocalObjectStore` a new UID reads, streams and replaces what uid 10001 stored, and the app image
    does the same with what the browser image stored. Red on `m4`'s image (umask `0022`;
    `storage_error: PermissionError` for the second UID), green on the rebuilt one.
  - **Smoke (deviation from M4's opt-in switch):** M4 ran the eleven scanner checks only under
    `DPS_SMOKE_SCANNER=1`, so a plain `python tools/smoke_test.py` on `--profile full` (DoD 5) never
    covered the scanner. `DPS_SMOKE_SCANNER` now works like `DPS_SMOKE_M2`: `auto` (default) runs
    the block when the demo site answers `/health` at `DPS_TEST_DEMO_SITE` (only the `scanner` and
    `full` profiles run it), `on` (or M4's `1`) runs it whatever the stack (a missing browser worker
    or demo site fails), `off` (or `0`) skips it; any other value stops the run. M4's
    `test_scanner_block_runs_only_when_asked` became `test_the_scanner_block_follows_the_switch_and_the_stack`
    plus a probe test. The `full` gate is `DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1
    DPS_SMOKE_SCANNER=on python tools/smoke_test.py --m3`. `scanner_block` (`78f0017`) wraps the
    decision: under `on` a demo site that does not answer is the failed check `demo site answers`
    (M4's `=1` raised a `ConnectError` traceback there); the success path keeps eleven checks.
  - **Flow report (review S4 r2 N5):** `--require-scanner` (the `full` gate) turns the scanner rows
    into "not run" failures when no browser worker is online, instead of skips. The scanner block
    reads `/readyz` fresh (unrecorded) and re-reads it every 3 s for up to
    `DPS_FLOW_BROWSER_WAIT_SECONDS` (60) while it says `unknown` (a freshly started app, HANDOFF §15
    trap 8) or, under the switch, until it is ok; a stack without the `scanner` profile ("none
    online") is not waited for. The coverage test no longer allows rows the app does not serve (M4
    is merged: the ten scanner templates match the app's routes), and it leaves out the console's
    `GET /` and SPA fallback, which the app registers only when `frontend/dist` is built (`58fd122`:
    the first run of the whole suite on this lane after `npm run build` failed on them; the test had
    only ever run without a dist).
  - `tests/test_compose_m5.py`: `full` must hold `browser-worker` and `demo-site`; the browser-worker
    test no longer skips. Review S4 r2 R2-2: `.dockerignore` keeps `tools/dev` (the owner's push
    automation and LaunchAgent) out of the image (test first). R2-3: the Dockerfile comment and the
    runtime test's docstring no longer claim Kubernetes emptyDirs inherit the image's ownership
    (kubelet creates them, group-owned by the pod's `fsGroup`). R2-1: HANDOFF §8's re-permission
    command (the one-time fix for `0600` objects of older images, above) is now the review's measured
    one (`--cap-add CHOWN --cap-add FOWNER`, `find` limited to the two volumes); the old one stopped
    with `Operation not permitted` under the worker's `cap_drop: [ALL]`.
- **S4b — the scanner in the `full` stack (finish-stage work, orchestration stream S4b, 2026-10-15;
  report `~/.cache/dps-build/reports/S4b-impl.md`, HANDOFF §18.2).** Merges `4eeac2a` (main up to
  `40d8b34`: the M3 post-merge and M4 integration fixes the images now ship) and `5c54dda` (main's
  docs commit `8ea220a`). Main took HANDOFF §17 for its own M4 integration record, so this task's
  record (the M4 slot-in above says §17) is §18 since `93d4b4d`. Then, test first:
  - **The smoke's D4 check gets two positive controls (`f39df5b`; not in the plan or M4's Task 24).**
    "The demo site's counters do not move and `non_get_allowed` is 0" also holds for the counters of
    a site the scan never visited (the smoke reads `DPS_TEST_DEMO_SITE` from the host, the
    browser-worker scans `DPS_SMOKE_DEMO_URL` inside the network; a stale port or another lane's
    demo site breaks the link silently) and for a scan whose pages never tried to send. Added: the
    demo site counts the GETs it served (`get`: pages and files, `/health` and `/__received`
    excluded — an additive key; every reader compares named keys, checked across `tests/` and
    `tools/`), and the check needs `get` to move by at least the scan's `pages_visited` (and ≥ 1;
    the site sends `no-store`, so each visit fetches its page) and the scan summary's
    `blocked_requests` to hold at least one non-GET request. A site without `get` fails the check.
    Red first: four cases in `tests/test_tools_m4.py` (then a fifth, `20 < 21 visited`, when the
    control was tightened from "moved" to "≥ pages visited") and
    `tests/scanner/test_demo_site.py::test_received_counts_the_pages_and_files_it_served`.
    Measured on every smoke run: `pages served +175 (21 visited)`, 60–61 POSTs blocked.
  - **The local mocks run unprivileged (`7561e89`; review S4 r2 R2-4, not in the plan).** The OCR
    mock's image declares no user (root): compose runs it as uid 10001 in group 0 (like the app
    image) with a read-only root, a 128 MiB `/tmp` tmpfs for pytesseract, `cap_drop: [ALL]` and
    `no-new-privileges`. The demo site runs as the `node` image's own user (uid 1000; it writes
    nothing and shares no volume, so group 0 is not needed) with the same hardening. The data
    stores keep their images' users (they own their data directories; Oracle Free needs uid 54321)
    and the `demo-secrets` one-shot stays root (it writes the secrets volume). Red first:
    `tests/test_compose_m5.py::test_the_local_mocks_run_unprivileged_with_a_read_only_root`.
  - **The image runtime tests check the architecture they run (`9db127f`; not in the plan).** Under
    `DPS_TEST_IMAGE_PLATFORM` the probes of both images must report `platform.machine()` `x86_64`
    (`linux/amd64`) or `aarch64` (`linux/arm64`), so an amd64 claim cannot rest on a native image
    that answered instead (the classic image store runs a mismatched image with only a warning).
    Red first: the probes did not report the machine (`KeyError`).
  - Verified at `5c54dda` (HANDOFF §18.2): both images built for amd64; the runtime tests 6/6 per
    architecture; the whole suite with every service up 3688 passed; the `full` stack as arm64 uid
    10001, as the amd64 builds under uid 1000770000:0 (read-only, `cap_drop ALL`) on the same
    volumes, and back — smoke 59/60 each (only S11, lane `fsec`), flow `--require-scanner` PASS
    92/0, every artefact and pack readable across the UID changes.
  - Left open: S11 (`no-store` on `/api/`) is red until `fsec` merges; `mypy tools/smoke_test.py`
    still reports 3 pre-existing errors (2 from main, 1 in M4's scanner block; the gate is
    `mypy dps`).
- **Contract:** unchanged (the plan lists no contract change for Task 24; HANDOFF §2, §3, §6 and §8
  carry the build and run facts).

## Task 26B — The showcase deck (finish stage S6a, worktree `dps-wt-fdemo`, branch `fdemo`)

Finish-stage work (programme plan §2.6 F3). S6a builds the deck tool, its test and the `deck` extra
without a real rehearsal; S6b (after merging `fimg`'s Task 24 and `m4`) writes `tools/e2e_demo.py`
with `reports/e2e-demo-report.json`, runs the rehearsal, builds and commits
`docs/demo/dps-showcase.pptx` and does the Keynote QA.

- **No `.pptx` committed here.** A deck built from fixtures would show fixture numbers as if they were
  measured; the plan's file `docs/demo/dps-showcase.pptx` arrives with S6b's rehearsal.
- **Input contract fixed here** (e2e_demo.py does not exist yet; it lives in the `build_deck.py`
  docstring): report schema `dps.e2e-demo.v1` — top level `generated_at`, `dps_version`, `commit`,
  `tenant`, `total_seconds`, `steps[]` of `{id, title, status, seconds, facts}` (status `PASS`, `FAIL`,
  `ERROR`, `NOT RUN` or `SKIP`; every scripted step S1–S11 is listed, a skipped one as `SKIP`, the
  ones after a failure as `NOT RUN`; S12 when `--console` ran — see the S6a review r1 fix below).
  Facts the deck reads mirror the API's own key paths so S6b can copy them: S1 `raw_pii_sent_to_llm`; S2 `findings`,
  `entity_types[]`, `reveal_identical`, `raw_pii_sent_to_llm`; S4 `ocr_pages`, `findings`,
  `extractable_chars`, `originals_left`; S6 `jobs[]` of `{engine, counts.rows_written,
  counts.partitions, timings.rows_per_s, verification.passed}` (evidence pack keys); S7 `members[].engine`,
  `pairs[].{left,right,checked,matched,consistent}`, `sampled`, `all_members_matched`, `passed`
  (consistency-check result keys); S8 `counts.rows_written`, `fidelity.score`,
  `privacy.exact_row_matches`, `privacy.nndr.{ratio,target,measured}`,
  `privacy.membership_inference.{run,auc,target}`, `utility.{run,ratio,metric}` (synthetic pack keys);
  S9 `scan.{pages,fields,personas,submissions,non_get_allowed}` (scan evidence keys),
  `received.{non_get,form_get,logout_hits,admin_hits,get}` (how far the demo site's own `/__received`
  counters moved across the scan) and `sends_blocked` (changed at the `fimg` merge from a bare
  `posts_received`; see "fimg merged into fdemo" below); S10 `packs_listed`, `audit_rows`, `pdf_ok`; S11 `posture.{passed,total,enforced}`
  and optionally `posture.env` (`GET /admin/posture`'s own keys), `rotated_key_status`,
  `retention_state`. Screenshots: `reports/demo-screens/<page>-light.png` for
  `playground`, `job`, `scan-parallel`, `evidence` (S12's walk writes `<page>-<theme>.png`). Only a
  step with status `PASS` contributes figures.
- **Speaker notes** come from the `#### Slide N` blocks of `docs/demo/DEMO-SCRIPT.md` (the plan: "taken
  from the matching DEMO-SCRIPT beat"); a missing block falls back to built-in notes and is listed as a
  missing input.
- **Fonts.** A PPTX run cannot name a fallback font, so every run names `Helvetica Neue` and the theme
  font scheme (what anything un-styled inherits) names `Arial`; the template's per-script theme fonts
  are removed, so no other face is named anywhere in the package. The theme colours are the Vault tokens.
- **Keynote safety beyond the plan:** no `p:graphicFrame` at all (the D1–D14 status table on slide 12
  is pills and text boxes, not a table part); every shape drops its `p:style` (no inherited shadow,
  outline or white text); solid fills; the template's stale thumbnail is dropped; the same inputs give a
  byte-identical file (fixed zip timestamps, document dates from the report) so a rebuild without new
  inputs makes no diff.
- **Figures.** Nothing is derived (no sums, averages or list counts), so every number on a slide is an
  input value as formatted; titles avoid free-standing digits ("Layer 2" became "Databases: …"); labels
  glued to letters (S8, L2, D4) are names, not figures. The plan's test checks slides 7–11; a second
  test checks slides 5–11 with a complete report.
- **Missing inputs** render named slate placeholders (`placeholder:<key>`), are listed on stderr, and
  `--strict` exits 1 without writing (an existing deck stays); an unreadable or wrong-schema report exits
  2. Only name-shaped strings from the report are ever printed (entity types, allow-listed engines,
  states and metrics, a tenant slug, a git sha, a semver).
- **Bench bars:** the newest file per configuration (engine, mode, write mode, workers, parallelism), at
  most eight; the host line appears only when every file reports the same CPU count.
- **Tests:** `tests/test_deck.py` has 12 tests (the plan says `1 passed`): the plan's fixture test plus
  slide 9 without MIA/utility, a complete report, no inputs and `--strict`, a failed step, string
  filtering, malformed facts, a wrong-schema report, notes from the script, the notes parser, the
  notes fallback, the CLI.
- **Keynote QA not done here:** three `osascript` exports of a fixture preview timed out on `open`
  (AppleEvent -1712); Keynote 14.4 received the file (it stamped its quarantine attribute) but showed
  no document or window to AppleScript — most likely a modal dialog that needs a person. Visual QA used
  LibreOffice headless renders of all twelve slides (complete inputs and no inputs). The Keynote PDF
  review stays with S6b and the owner.
- Contract §1 (extras sentence) and HANDOFF §1 (extras of the `dps` package) name the `deck` extra.

## Task 26 — Presenter script draft (finish stage S6a, worktree `dps-wt-fdemo`)

Finish-stage work. `docs/demo/DEMO-SCRIPT.md` is drafted from the plan's outline and spec §9; S6b
finalises it after the rehearsal together with `tools/demo_seed.py`, `tools/e2e_demo.py` and
`tests/console/test_console_pages.py`.

- Labels are the console's as built (main plus the `m4` scanner pages): the Playground heading is "Try
  the engine", the Data sources page heading is "Jobs" (the sidebar reads "Data sources & jobs"),
  documents use "Load demo KYC scan" and "Download redacted PDF", the parallel view toggles "Filled with:
  Synthetic persona / Obfuscated tokens" and shows "No submissions · POST interception on". For S6b's
  console test: Task 26 expects the headings "Playground" and "Data sources & jobs", which the pages do
  not render as their `h1`.
- Each beat carries `#### Slide N` blocks (the deck's notes); the timeline table and the "Slides and
  beats" line map slides to beats (Task 26B's one line). Slides 1–4 are an optional opening before the
  clock; Policies has no slide.
- The script quotes no figure: beats point at the number on screen or on the slide built from the
  rehearsal. Commands and flags (`--skip-oracle`, `--reset-jobs`, `--console`) are the plan's; S6b
  confirms them against the tools it writes.

## S6a review r1 fix — B1: slide 1 claims an end-to-end run only for a complete rehearsal; B2: the posture count in its context (Tasks 26B, 26)

- **B1.** `_slide_title` printed "the scripted demo ran end to end in m:ss" whenever `generated_at`
  and `total_seconds` existed, so a rehearsal that failed at S5 (S6–S11 `NOT RUN`) still claimed an
  end-to-end run, timed to its failure. Now the sentence needs a complete rehearsal
  (`Report.incomplete()` is `None`): every scripted step S1–S11 listed and every listed step `PASS` or
  `SKIP`; skipped steps are named ("… in 7:12 (S3 skipped)."). Otherwise slide 1 shows
  `placeholder:rehearsal` — "a complete rehearsal (first failure: S5)", else an entry without a step
  id, else "S11 not run", else "no step S4 in the report" — listed as missing, so `--strict` refuses
  the build. Only step ids shaped `S<n>` and known statuses are printed (anything else is
  "unknown"); a status that is not a string no longer crashes `_step` (it was a set lookup).
  Contract addition for S6b's `e2e_demo.py`: list every scripted step, a skipped one as `SKIP`
  (never left out).
- **B2.** `GET /admin/posture` evaluates its eleven checks as if in production in every environment
  and enforces them only in staging and prod (`enforced`), so the local demo stack fails the
  production-only checks by design (as the M5 verifications measured: bootstrap key, stub LLM,
  literal KEK, http egress, verify-warn allowed); the demo seed needs the bootstrap key, so the
  stack can never show every check passing.
  - Deck: the S11 contract adds `posture.enforced` (required: without it, or not a boolean, the
    count is a placeholder listed as missing) and `posture.env` (optional; printed only when it
    agrees with `enforced`: `local`/`dev` when not enforced, `staging`/`prod` when enforced). The
    value is emerald when every check passed, **amber** when a check failed where it is not enforced
    and rose only for an enforced failure; the caption says where it is enforced ("local stack,
    evaluated as if production; staging and production refuse to boot when a check enforced at boot
    fails" / "enforced on this production stack: it refuses to boot when …"). The posture card grew
    to 1.4 in and the LLM card shrank to 1.15 in (its caption is one line), so the column still ends
    above the footer.
  - Wording: "refuse to boot when a posture check fails" (slide 11's lede, the built-in notes) and the
    reviewer's suggested "refuse to boot unless all pass" both overclaim — `keys_rotated` and
    `migrations_verified` are not enforced at boot (`dps/posture.py` `CHECKS`) — so the deck and the
    script use the console's own rule (`PostureList`): staging and production refuse to boot when a
    check marked "enforced at boot" fails.
  - `docs/demo/DEMO-SCRIPT.md`: the timeline's proof and the Reports and Admin beat's say-line, proof
    and slide 11 notes no longer promise every check passing; they say the local stack only reports
    the checks and fails the production-only ones by design, quote no count, and state the boot rule
    as above. The Overview beat names the **Posture score** card (the Overview has no checklist; the
    checks are on the Admin page).
- **Non-blocking review items taken:** N1 (slide 8's summary reads "Found in every system of the
  group", true for any member count — `all_members_matched` is the intersection of every member; the
  script says a `--skip-oracle` rehearsal cannot rebuild the deck under `--strict`, so keep the last
  full rehearsal's deck), N5 (bench caption "on one host with N CPUs"; slide 7 notes "one machine":
  the bench JSON records CPUs and the machine type only), N6 (D14 as spec §12 words it: payload size,
  rows per job, concurrent jobs, docs per day), N7 (slide 1's m:ss is now tested against
  `total_seconds`), N9 (MIA caption "a coin toss scores about half": the proxy's AUC is not
  symmetrised), N10 (`python-pptx>=1.0,<2`: the tool uses package internals; contract §1 too), N13
  (slide 1 notes name the bench runs as the other measured source).
- **Not taken (owner / S6b):** N2 (strict by default for the committed deck path — S6b decides when it
  commits the deck), N3 (`reports/` ignore rule — S6b, with the rehearsal outputs), N4 (Keynote PDF
  review), N8 (chip-row height limit; the KYC sample's types fit), N11 (no action), N12 (the release
  venv installs every extra).
- Tests: `tests/test_deck.py` 12 → 30 — slide 1 states the measured time (431.6 s → 7:12) and
  `--strict` builds; no claim and a placeholder for `FAIL` then `NOT RUN`, `ERROR`, an unknown
  status, `null`, a list, and a lone `NOT RUN` (each also refused by `--strict`); skipped steps named;
  a scripted step missing or an entry without a step id; six posture contexts (value, colour,
  caption); `enforced` absent, a string or a number. `FULL_FACTS` gains S3 and S5 (a complete
  S1–S11 report) and the local stack's posture shape (`enforced: false`, `env: local`). On the code
  before this fix 17 of the 18 new cases fail (the list status with `TypeError: unhashable type`);
  the 12 earlier tests and the positive control (slide 1's measured time) pass on both
  (`~/.cache/dps-build/reports/s6a-fix-r1/`).

## fimg merged into fdemo (2026-10-15) — Task 24 and the scanner under the demo tools (Tasks 26, 26B)

Finish-stage work on lane `fdemo`: `git merge --no-ff fimg` (`fimg` `3f8af02`: Task 24 with S4b, and
M4 as merged on `main` `5959a75` with the integrate fixes up to `8ea220a`), so S6b's demo tools
(`tools/demo_seed.py`, `tools/e2e_demo.py`) can build on `tools/demo_client.py`, the `full` stack
with the browser-worker and the demo site, the smoke's scanner block and the console's scanner pages.

- **Conflicts, both sides kept.** HANDOFF §1, the `dps` package row: one row naming `fimg`'s
  `scanner` extra and this lane's `deck` extra. This log: `fimg`'s Task 24 section first, then this
  lane's Task 26B, Task 26 and S6a fix sections. Auto-merged and checked: `pyproject.toml` (both
  extras) and contract §1's extras sentence (both).
- **The deck's S9 contract follows the D4 proof that came in.** The S6a contract read
  `posts_received`, a key the demo site does not have: `/__received` counts `non_get`, `form_get`,
  `logout_hits`, `admin_hits` and, since S4b, `get` (pages and files served). A bare zero also proves
  nothing when the counters read are not those of the site the browser-worker scanned, which is why
  S4b gave the smoke's D4 check two positive controls. S9 now carries the smoke's own figures:
  `scan.{pages,fields,personas,submissions,non_get_allowed}` (the pack's scan block),
  `received.{non_get,form_get,logout_hits,admin_hits,get}` (each counter after the scan less before
  it) and `sends_blocked` (the pack's `scan.blocked_requests` less GET and HEAD, the smoke's
  `_sends_blocked`). Slide 10's card reads "Non-GET requests received by the site" and shows the
  count only when both controls hold (`received.get >= max(1, scan.pages)`, `sends_blocked >= 1`),
  with the served GETs and the blocked sends in its caption; otherwise it is the placeholder
  `S9 D4 positive controls`, listed as missing, so `--strict` refuses the deck. The count is emerald
  only when no counter moved and `scan.non_get_allowed` is 0, rose otherwise. **For S6b:**
  `e2e_demo.py` passes S9 only on the smoke's rule (the same four counters, `non_get_allowed` 0 and
  both controls) and records these facts; `/__received` is read from the host at
  `DPS_TEST_DEMO_SITE`, the scan reaches the site in-network at `http://demo-site:8410`.
- **The presenter script** (`docs/demo/DEMO-SCRIPT.md`): every console label it names was checked
  again in the merged console source (Playground, Policies, Data sources, Reports, Admin, the top
  bar and the scanner pages: "UI scanner", "Start URL", "Synthesize persona fill", "Start crawl",
  "Pages with forms", "Parallel view", "Filled with", "Synthetic persona", "Obfuscated tokens",
  "Export inventory", "No submissions · POST interception on"); all are present. The status line says
  so; the 7:00 timeline proof, the beat's **Proof** and slide 10's notes now speak of non-GET requests
  and forms sent by GET, received 0 while the site served the scan, as the slide does. The scanner
  beat's first step follows the New scan page's defaults (`frontend/src/lib/scans.ts`
  `DEFAULT_CONFIG`): the demo URL and the stored session `dps/ui/demo-onboarding` are already filled
  in, and Personas (default 20) is set to 3, the plan's S9 count.
- **S6a review N3 closed by Task 24:** `.gitignore` ignores `/reports/` (the e2e reports, demo
  screenshots and flow report) and `var/` (demo keys and the group file).
- **Tests:** `tests/test_deck.py` 30 → 42: the complete report's slide 10 shows the emerald 0 with
  its controls and builds under `--strict`; seven cases without proof are placeholders that
  `--strict` refuses (no GET served, fewer GETs than pages, no send blocked, the old
  `posts_received` shape, a counter that is not a number, `non_get_allowed` not a number,
  `sends_blocked` absent); four moved counts are rose (`non_get` 2, `form_get` 1, `admin_hits` 1,
  `non_get_allowed` 1). On the code before the change 20 of the 42 failed (the new cases and every
  test that builds from the complete fixture report).

## Task 26 — The ten-minute demo: seed, automated run, console check, deck (finish stage S6b, lane `fdemo`, 2026-10-15)

Finish-stage work (programme plan §2.6 F3) on lane `fdemo` after the `fimg` merge (Task 24, the
scanner in the `full` stack). Rehearsal on this lane's `--profile full` stack: `tools/demo_seed.py`,
then `tools/e2e_demo.py --console` at `37ec781`: S1–S12 all PASS, the whole run 3:25 (the API path
S1–S11 3:12); `docs/demo/dps-showcase.pptx` built `--strict` from that report, its screenshots and
the bench files (no placeholder).

- **The demo tenant needs its own secret refs.** Refs are tenant-scoped (SEC-2, SEC-5), so the
  plan's connections and the scan's stored session cannot resolve for `retail-banking-apac` from
  the `default/` folder. The compose `demo-secrets` one-shot now copies the whole demo tree
  (`default/dps/…`, incl. `dps/ui/demo-onboarding`) to `retail-banking-apac/dps/…`; a test runs the
  one-shot's script and resolves every demo connection's ref and the session for the demo tenant.
  A running stack takes it with `docker compose --profile full up -d demo-secrets`.
- **Connections as built.** Step 5's single `crm-oracle` (read as `dps_ro`, write as `crm_masked`)
  and `profiles-mongo` (source + target) cannot exist: one connection has one credential, `DPS_RO`
  cannot write and `CRM_MASKED` cannot read `CRM`, and a MongoDB connection names one database. The
  seed makes seven, one per role, with the compose demo refs: `bank-pg` / `bank-uat`, `crm-oracle` /
  `crm-masked`, `profiles-mongo` / `profiles-uat`, `exports-minio`. Sources are tagged `production`.
- **`--rows` defaults to 20 000** (plan: 2 000): the compose `seed-sources` one-shot loads 20 000
  customers with seed 7 and `seed_sources.py` refuses other parameters on a seeded schema (exit 3).
  `--reseed` (beyond the plan) reloads (`--reset`) a source seeded with other parameters — the test
  suite leaves 200 rows in Oracle and MongoDB; without it the seed stops with that remedy.
- **Keys.** `demo-console` has every scope (`*`) for the demo tenant (the plan's "(tenant, `*`)");
  keys last 90 days. A key in the file that no longer authenticates (401) is replaced and the other
  active keys of its name are disabled (nobody holds them any more). The job group's file holds
  `group_id`, `name`, `tenant`, `key_material` and `synthesis_seed`; a group whose file is lost
  cannot be used again (DPS keeps no key), so a new `crm-uat-refresh-<n>` is made.
- **Policies.** The recognizer's "deny `EMP-000000`" is the console's allow list (the matched texts
  it never reports, as the Policies fixture and mockup 05's live test treat the seeded id); the
  declared field keeps the mockup's path `LOANS.APPLICANTS.EMPLOYER_NAME`. v2 is published once
  (`CHG-48213`); a rerun reuses it.
- **`--reset-jobs`.** Retention's floor is one day, so it removes only demo jobs older than a day;
  the seed also cancels the tenant's unfinished jobs. A retention job belongs to the system tenant,
  which no tenant's `/jobs/{id}` reads (404, and `_system` is refused as reserved): the seed and S11
  wait for the job's `retention.run` audit row instead (`outcome` `ok` / `dry_run`).
- **e2e_demo as built.**
  - The demo runs as the `demo-console` key with `X-DPS-Tenant: retail-banking-apac`; only S11's
    retention run uses `--admin-key` (it sweeps every tenant, so it needs a key for every tenant).
  - S2 uses the console's own KYC sample (`frontend/src/lib/samples.json`, as the Playground) and
    checks the synthetic record against the values the detector found (non-sensitive fields such as
    `risk_score` are kept by design).
  - S4 uses the console's committed synthetic scan `demo/documents/kyc-scanned.pdf` ("Load demo KYC
    scan") instead of rendering one; its checks are the plan's (OCR pages, `%PDF`, 0 extractable
    characters, 0 originals in the OCR re-read).
  - S5 discovers under `@retail-kyc`. **Product gap found:** a run without `policy` resolves its
    snapshot's policy *name* as a preset, so a snapshot discovered under a tenant policy fails
    every run with `policy_invalid`; the demo's runs name `@retail-kyc` again (the API resolves it
    to the same stored version). Left for the owner (not fixed here: L2 + Task 15, outside the demo).
  - S6 runs with `partition_rows` 5 000 (four partitions per job, two at a time) into
    `bank_uat.customers_demo`, `crm_uat.profiles_demo` and `CRM_MASKED.CUSTOMERS_DEMO`; partitions
    report `done`. Besides the plan's checks every pack must carry the group's key id.
  - S8 writes `bank_uat.customers_synthetic` (plan: schema `synth_demo`, which the demo source does
    not have; `dps_writer` owns `bank_uat`), with the inline policy `synthetic-realistic` plus
    `synthesize.utility_label` `$.segment`, so the pack reports utility and the MIA proxy (slide 9);
    `privacy.nndr.measured` is a count (≥ 1), not a flag.
  - S9 passes only on the smoke's D4 rule with both positive controls (as the `fimg` merge asked):
    no counter moved, `non_get_allowed` 0, `get` moved ≥ pages visited, ≥ 1 send blocked; facts
    `scan.*`, `received.*`, `sends_blocked`. It also checks the persona CSV export holds none of
    the demo site's values.
  - S10 also scans the report, audit and PDF bodies for the S2 record, the SSN and the demo site's
    values (0 required).
  - S12's pages are recognised by their own heading as built (`main h1`; the evidence page by its
    eyebrow): "Protection posture", "Try the engine", "Policies", "Jobs", the job title (with `→`),
    "Scan …", "Evidence and audit", "evidence pack", "Admin" — the sidebar names every section, so
    matching any text would pass on any page. The key goes to `sessionStorage['dps.apiKey']`
    (the console's default, O5), the tenant and theme to `localStorage`. The Playground screenshot
    runs **Obfuscate** (slide 5 speaks of tokens and the key card) and masks the card's data-key
    text with an ink box: no screenshot or deck carries key material.
  - The JSON report lists every scripted step (a skipped one `SKIP`, the steps after a failure
    `NOT RUN`) and S12 only with `--console`; the Markdown's last line is the whole run's wall time,
    and its verdict line gives the API path (S1–S11) separately.
- **`tests/console/test_console_pages.py`** opens the demo tenant's newest succeeded run, scan (its
  first page with both filled screens) and evidence pack, read through the API, so it does not
  depend on a report file; it also fails on any request to another origin.
- **Deck.** Keynote 14.4 could not be driven headless here: `open` through AppleScript returned no
  document or timed out (AppleEvent -1712) and `open -a Keynote` opened nothing, with or without a
  restart of Keynote — as in S6a, most likely a dialog that needs a person (osascript has no
  assistive access to look). Visual QA instead: LibreOffice headless → PDF → page images, all
  twelve read (no overflow, every bar and diagram drawn, fonts as named). `tests/test_deck.py`
  gains a test of the committed deck itself (twelve slides, notes equal the script's Slide blocks,
  no placeholder, no chart part or graphic frame, only Helvetica Neue and Arial, no CRITICAL entity,
  under 20 MB) — S6a's N2: a deck that is not a complete `--strict` build cannot be committed. The
  Keynote PDF review stays with the owner.
- **Slide 1** shows "v0.2.0": the version the stack reports (`api-core`); rebuild after Task 28
  sets 1.0.0 (rehearse, then `build_deck.py --strict`).
- Tests: `tests/test_demo_seed.py` 10 (idempotent second run, key replacement and retirement, a
  lost group file, skip flags, a failed and a reseeded source, reset, an unreachable stack, the
  demo tenant's refs resolving), `tests/test_e2e_demo.py` 22 (the D4 rule and its controls, the
  driver, the report contract read by `tools/build_deck.py`, the Markdown's last line, helpers, the
  page list, a missing seed); on mutated code (the D4 controls dropped, the driver not stopping)
  5 fail. `tests/console/test_console_pages.py` 9 on the stack; `tests/test_deck.py` 42 → 43.

### S6b review round 1 fixes (lane `fdemo`, 2026-10-16)

- **B1 — MongoDB `$sample` (product, `dps/layers/connectors/mongo.py`).** The server's pseudo-random
  cursor (`$sample` first, N < 5 % of a collection of > 100 documents) fails with 28799 on a
  collection that is mostly deleted records: a `table` target emptied by `truncate_insert`
  (`delete_many`) and refilled by every run. Its random descent lands among the deleted records and
  walks to the same live document, so a `$sample` of 500 fails every time while 200 and 2 000 (≥ 5 %,
  no random cursor) pass. `describe` (each run's target check at prepare, the consistency check) and
  the samples now try the random cursor twice, then sample through `sample_pipeline(…,
  random_cursor=False)`: a leading `$match {_id: {$exists: true}}` (an empty `$match` is optimised
  away and the random cursor comes back) with the hint `{$natural: 1}` (a collection scan, not an
  `_id`-index fetch of every document), under the same `maxTimeMS` and comment. Logged value-free
  (`mongo_sample_retry` with the driver code, `mongo_sample_scan`). Measured on this lane's MongoDB
  7.0.40 with `explain`: the plain pipeline plans `$sampleFromRandomCursor`, the fallback a
  `COLLSCAN` and `$sample`. Tests: `tests/connectors/test_mongo_sample.py` (offline, a fake
  collection raising 28799 from the aggregate or a getMore: one retry, the fallback, no retry for
  another code, a failing fallback stays a value-free `source_error`, `describe` through the
  fallback) and the `mongo` integration test
  `test_describe_and_sample_survive_a_collection_of_deleted_records` (3 000 × 17 KB documents
  inserted and deleted next to 12 000 live ones reproduces 28799; `describe`, `sample(500)` and
  `sample_keys(500)` succeed and the profiler shows each one's scan), which fails on the old code
  with `source_error: mongo:28799`.
- **B2 — S2's synthetic probes.** `detected_values(record, findings)` probes every string leaf of
  every top-level field a finding points into (the nested `card.pan` and `card.expiry`), never a
  `str()` of a dict; the haystack is the synthetic record's leaves and its JSON without ASCII
  escapes. Tests: the probe set, a passing synthetic record (0 of 9), and an echoed card number,
  expiry or e-mail fails S2.
- **B3 — S7 joins this run's jobs.** S5 records each accepted snapshot's customer-id path
  (`customer_id_path`: the `CUSTOMER_ID` column the server would join — not `keep`, not free text;
  key, anchor, shortest path). S7 posts explicit `pairs`, one per S6 job (`consistency_pairs`: the
  last submitted, Oracle on a full run, first as the anchor) and fails unless the result's member
  job ids are exactly the S6 job ids; the plan's "customer id path per job". The members' paths are
  in the report. A `--skip-oracle` rehearsal after a full one, and an older run of the group, no
  longer change what S7 checks.
- **N1 — the driver.** Any other exception of a step body (Playwright's `TimeoutError` in S12) is an
  `ERROR` with its class name only, so the run still writes its reports instead of leaving the
  previous ones for `build_deck.py --strict`.
