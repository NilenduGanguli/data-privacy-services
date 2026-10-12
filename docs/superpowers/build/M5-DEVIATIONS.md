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

## Deferred to final stage

- Task 1: append the M5 variables to `.env.example` (blocked by permissions in this lane).
- Task 1: `Dockerfile`'s comment still says the `[tabular]` extra "arrives with M5" and is skipped;
  the install line already names the extra, so the image now gets numpy + scipy. The comment and the
  image size measurement belong to Task 24 (images), which this lane does not run.
