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

## Deferred to final stage

- Task 1: append the M5 variables to `.env.example` (blocked by permissions in this lane).
- Task 1: `Dockerfile`'s comment still says the `[tabular]` extra "arrives with M5" and is skipped;
  the install line already names the extra, so the image now gets numpy + scipy. The comment and the
  image size measurement belong to Task 24 (images), which this lane does not run.
