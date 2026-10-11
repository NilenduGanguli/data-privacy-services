# M0 build — deviations from the plan

Plan: `docs/superpowers/plans/2026-10-11-m0-foundation-engine-api-playground.md`.
One section per task: file, what changed, why. Public names and signatures that later tasks rely on
are preserved unless a section says otherwise.

## Task 1 — Repository scaffold and toolchain

- `pyproject.toml`: added `[tool.ruff.lint.isort] known-first-party = ["dps"]`.
  Why: ruff 0.17 classifies `from dps.config import ...` as third-party while `dps/config.py`
  does not exist yet (it resolves the full submodule path against `src`), so the plan's
  `tests/conftest.py` failed `I001` at Task 1 and the import order would have flipped once Task 2
  landed. Declaring the package first-party makes isort sorting deterministic in every task.
- `pyproject.toml` installs with the `fpe` extra as well (`uv pip install -e ".[dev,fpe]"`,
  fastfpe 0.2.1 wheel) so the FF1 tests in Task 14 run for real instead of skipping.
- Gate note: as the plan states, a full `pytest -q` is red at the end of Task 1 because
  `tests/conftest.py` imports `dps.config` (created in Task 2). The Task 1 commit was gated on
  `ruff format`, `ruff check`, `mypy dps` and
  `pytest -q -p no:cacheprovider tests/test_package.py --noconftest` (1 passed); the full suite is
  green from Task 2 onward.
- `ruff format` reformatted `tests/conftest.py` (blank line after the module docstring) —
  formatting only.

## Task 2 — Settings and production posture

- `dps/config.py`: `settings_customise_sources` is fully typed (`type[BaseSettings]`,
  `PydanticBaseSettingsSource` x4 → `tuple[PydanticBaseSettingsSource, ...]`) instead of the plan's
  untyped signature with `# type: ignore[override]`. Why: project rule "type hints on every
  function"; the typed signature matches pydantic-settings 2.15 exactly, so no ignore is needed.
- `dps/config.py`: added `hide_input_in_errors=True` to `model_config`. Why: settings carry
  secrets (COIN client id/secret, API keys); pydantic's `ValidationError` otherwise echoes
  `input_value=...` for any field that fails validation. Strengthens the "never put a secret in an
  exception message" rule; no field, default or validator changed.
- `dps/config.py`: `_sane_url` error message no longer echoes the URL (`got {v!r}` dropped) —
  a URL may carry userinfo credentials. The validator itself is unchanged (still refuses
  non-http(s) schemes and missing hosts).
- `tests/test_config.py`: added `test_validation_errors_never_echo_the_input` (asserts a password
  in a bad URL and a bad int field never appears in the error text; verified red with
  `hide_input_in_errors=False`).
- Count: the plan's expected "14 passed" is an arithmetic slip; the plan's own tests total 16
  (1 package + 7 config + 8 posture); with the added test, 17.

## Task 3 — Observability

- `dps/observability.py`: `_drop_value_fields` is typed with `structlog.typing.EventDict`
  (`MutableMapping[str, Any]`) for the event dict and return value instead of `dict[str, Any]`.
  Why: mypy rejected the plan's signature as a structlog processor (`list-item` error: structlog's
  `Processor` type takes a `MutableMapping`). Behaviour is identical; the test still passes a dict.
- `tests/test_observability.py`: added `test_configured_pipeline_drops_values_end_to_end`, which
  configures the real structlog pipeline, logs `value=` and `token=` fields and asserts neither
  reaches stdout while `entity_type` does. Why: the plan only unit-tested the processor; this proves
  it is wired into `configure_logging`.

## Task 4 — Taxonomy, findings model, JSON path helpers

- `dps/engine/taxonomy.py`: `DRIVER_LICENSE` and `VOTER_ID` are `CRITICAL` (plan: `HIGH`).
  Why: the spec (§5.2) says "any government or card identifier CRITICAL"; both identify a natural
  person. `EIN` and `BN` stay `HIGH` because they identify businesses (open question for the
  privacy office, see the report). No later M0 test depends on these two tiers — sensitivity only
  drives `keep_non_sensitive` (LOW) and the reported record tier, both unaffected. This raises
  protection; it never lowers it. `DRIVER_LICENSE` also gained a description.
- `dps/engine/jsonpath.py`: `parse_path` declares `out: list[str | int] = []` instead of the
  plan's `rest, out, pos = path[1:], [], 0` + `out_list` alias. Why: mypy rejects the plan's line
  (`var-annotated: Need type annotation for "out"`). Behaviour is identical; the plan's quoted-key
  unescape is kept verbatim (a brute-force round trip over every key of length 1-5 drawn from
  `a \ ' .` confirms `_segment` → `parse_path` is lossless).
- `dps/engine/findings.py`: the `hash_value` docstring now states plainly that the salt-free hash
  does not hide low-entropy values (an SSN can be recovered by enumerating 10^9 candidates), so it
  must only travel where the caller is entitled to the finding. Code unchanged. Flagged as an open
  issue in the report.
- Docstrings added to public properties/methods (`Sensitivity.rank`, `Sensitivity.max_of`,
  `Family`, `Locator.whole`, `Plan.reversible_count`, `Plan.methods_used`) per the conventions.
- Tests added: `test_identifiers_of_a_natural_person_are_critical` (taxonomy, fixed list),
  `test_every_flattened_path_reads_back_including_odd_keys` and `test_malformed_paths_are_refused`
  (jsonpath). Totals: the plan expected "27 passed"; the plan's own tests total 29 after Task 4
  (package 1 + config 7 + posture 8 + observability 3 + taxonomy 4 + findings 3 + jsonpath 3 —
  an arithmetic slip in the plan); with the 5 tests added in Tasks 2-4, 34.

## Fix round 1 — review M0-G1-r1 (Tasks 1-4), blocking findings B1-B3

- **B1, `dps/engine/jsonpath.py`:** `_segment` now uses `_IDENT.fullmatch(key)` (the pattern
  lost its `^…$` anchors). Before, `re.match` with `$` accepted a trailing newline, so the key
  `"name\n"` was written as the bare segment `.name\n` and `parse_path` could not read it back.
  Such keys are now quoted (`$['name\n']`). Also, `_TOKEN` takes `[0-9]+` instead of `\d+` for
  indices. `\d` accepted non-ASCII digits (`$[٣]` read as index 3), so one element had two
  spellings. Paths that `flatten` emits are unchanged for every ASCII identifier key. Tests added:
  `test_keys_that_merely_look_like_identifiers_are_quoted`, plus
  `test_brute_force_round_trip_over_awkward_alphabet`. The second covers every key of length 0-3
  over `a _ 0 \ ' . \n ] [ $ space é` and round-trips flatten → get_path → set_path. Also `$[٣]`
  was added to `test_malformed_paths_are_refused`.
- **B2, `dps/engine/taxonomy.py`:** the registry now only ever raises protection.
  - Built-in entries sit in a read-only `_BUILTIN` mapping. `TAXONOMY` is still the public
    `dict[str, EntityInfo]` (built-ins plus custom entries), and `entity_info` consults `_BUILTIN`
    first. A direct write such as `TAXONOMY["SSN"] = …` therefore cannot downgrade a built-in.
  - `register_entity(info)` keeps its signature but has new behaviour:
    - For a built-in name it raises `ValueError("built-in entity type 'SSN' cannot be
      re-registered")`. That includes a registration at a *higher* tier, because the review asked
      to refuse replacement outright.
    - For a custom name it stores `max(current effective tier, requested tier)`. Family and
      description come from the latest call.
  - **Interpretation:** the "current effective tier" of a name nobody has registered is the
    `MEDIUM` default that `entity_info` returns to every caller. A first registration at `LOW` is
    therefore held at `MEDIUM`. Otherwise, one tenant could register `LOAN_REF` at LOW and
    downgrade every tenant that uses the default for `LOAN_REF`, which is the same cross-tenant
    attack as B2. Nothing in the M0 plan registers a LOW custom entity. Allowing a LOW custom tier
    needs request-scoped tiers (see the open item for Task 10 below).
  - The read-modify-write runs under a lock.
  - New additive helper `is_builtin_entity(name) -> bool`.
  - Tests added:
    - `test_builtin_entities_cannot_be_replaced_or_downgraded`: SSN at LOW raises and stays
      CRITICAL, and `record_sensitivity(["SSN"])` stays CRITICAL. EMAIL at CRITICAL also raises.
    - `test_builtins_survive_direct_registry_mutation`.
    - `test_custom_tiers_only_ever_rise`.
    - `test_custom_entity_cannot_go_below_the_unregistered_default`.
  - A `restore_taxonomy` fixture now undoes registrations, and the existing
    `test_register_custom_entity` uses it (review N9).
  - **Impact on Task 10:** `PolicyCustomDetector.__init__` calls `register_entity` when a recognizer
    or declared field carries `sensitivity`. A policy that sets `sensitivity` on a built-in entity
    (`IBAN`, `EMPLOYEE_ID`, …) will now raise `ValueError`. Task 22's `_detector` does not wrap
    `custom_detector_from`, so `run_engine` would map that `ValueError` to a 500. This fails
    closed, but it is the wrong status. Task 10 should reject `sensitivity` on a built-in name at
    validation time: add a `RecognizerSpec`/`DeclaredField` validator using `is_builtin_entity`,
    which the policy loader already maps to "invalid policy" (400). Alternatively, Task 22 can
    wrap `custom_detector_from` in `EngineError`. The plan's own Task 10 tests are unaffected:
    EMPLOYEE_ID carries no sensitivity, and PROJECT_CODE at HIGH gives max(MEDIUM, HIGH) = HIGH.
- **B3, `dps/observability.py`:** `_drop_value_fields` keeps its name and signature but is now an
  **allow-list** processor (spec §10).
  - It keeps only the keys in `_SAFE_FIELDS` and drops every other key. The deny-list
    `_VALUE_FIELDS` is gone.
  - `_SAFE_FIELDS` covers:
    - structlog's own keys;
    - correlation ids (`trace_id`, `request_id`, `tenant_id`, `key_id`, `route`, `component`);
    - outcome fields (`outcome`, `status`, `status_code`, `duration_ms`, `attempt`, `reason`,
      `error`, `error_code`, `version`);
    - engine vocabulary (`mode`, `method`, `policy`, `entity_type`, `detector`, `count`, `counts`,
      `hits`, `findings_total`, `findings_by_type`, `methods_used`, `reversible_values`,
      `verification_passed`);
    - gateway and startup fields (`provider`, `purpose`, `model`, `env`, `auth`);
    - `ts`, from AuditEvent.

    That set is every key the M0 plan's call sites log: `ner_unavailable`,
    `verification_failed`, `audit` (all AuditEvent fields), `frontend_disabled`, `startup`,
    `engine_error`, and the bound contextvars `trace_id`/`route`.
  - Two hardenings go beyond the review's fix:
    1. An exception object in an allowed field is reduced to its class name (`error=exc` →
       `"KeyError"`), because the exception message may quote a value. `exc_info`/`stack_info` are
       not allow-listed, so tracebacks never reach the log.
    2. `event` must be a snake_case name (`[a-z][a-z0-9_.]{0,63}` with no run of 4+ digits).
       Anything else, such as free text or an f-string with an interpolated value, becomes
       `"event_redacted"`. The reviewer's probe also logged `info("customer 111-22-3333
       processed")`, which no key filter can catch. All five plan event names conform.
  - The plan's test (`value`/`dek` dropped, `event`/`entity_type`/`count` kept) passes unchanged.
  - Tests added:
    - `test_only_allow_listed_fields_survive`: the probe's field names `ssn`, `email`,
      `access_token`, `client_secret`, `Value`, `authorization`, `dek_b64`, `name` and
      `exc_info`.
    - `test_configured_pipeline_drops_unknown_fields_end_to_end`.
    - `test_exceptions_in_allowed_fields_log_their_class_only`.
    - `test_fields_the_m0_call_sites_log_are_kept`: guards against the allow-list silently gutting
      the audit sink.
    - `test_free_text_event_messages_are_replaced`.
  - Later milestones extend `_SAFE_FIELDS`, as the M1-M5 contract §0.1 already says.
- Totals: 34 → 45 tests (jsonpath +2, taxonomy +4, observability +5).
