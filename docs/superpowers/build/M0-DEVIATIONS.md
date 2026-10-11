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

## Fix round 2 — review M0-G1-r2 (Tasks 1-4), blocking finding B1

- **B1, `dps/posture.py`:** `posture_problems` now enforces spec §10 "TLS-only egress" in
  staging/prod.
  - For each non-empty `stellar_base_url` and `coin_url` whose `urlparse(v).scheme` is not
    `https`, it adds `"DPS_STELLAR_BASE_URL must be https in staging/prod"` or
    `"DPS_COIN_URL must be https in staging/prod"`. The message names the setting and never echoes
    the URL, which may carry userinfo credentials.
  - Why: Task 20 POSTs the COIN client id and secret to `coin_url` (mint with `verify=False` by
    design) and calls Stellar with the bearer token. Over `http://`, both would cross the wire in
    cleartext. The plan's own Task 2 code omitted this check; the gap came from the plan.
  - The check runs whenever a URL is set, **whatever `llm_provider` is**. That is fail-closed: a
    plain-http Stellar/COIN URL left in a prod config with provider `vertex` or `""` is refused
    too. `local`/`dev` are unchanged (http stays allowed for local stubs). The scheme comparison is
    case-insensitive because `urlparse` lowercases the scheme (`HTTPS://…` passes).
  - `dps/config.py:_sane_url` is unchanged. It still accepts `http` and `https` in every
    environment; TLS is enforced by the posture guard, which is the component that knows the
    environment.
  - Impact on later tasks: none. Every later plan test that builds stellar settings (Task 2 posture
    case, Task 20 gateway `_settings`) already uses `https://` URLs.
  - Tests added in `tests/test_posture.py`:
    - two cases in `test_bad_production_is_refused`: an http `stellar_base_url`, and an http
      `coin_url`, each with `ssl_cert_file` set and the other URL https;
    - `test_good_stellar_production_passes` (https both, uppercase scheme on one);
    - `test_plain_http_egress_names_the_setting_never_the_url` (parametrised over providers
      `""`, `vertex`, `stellar`; asserts the userinfo, host and `http://` never reach the message);
    - `test_plain_http_egress_is_allowed_outside_production`.
  - Totals: 45 → 52 tests (posture +7: 2 parametrised refusal cases, 1 good case, 3 message
    cases, 1 local case).

## Task 5 — Checksum validators

- `tests/test_checksums.py`: the SIN sample is `123 456 782` / `123 456 783` (plan:
  `046 454 286` / `046 454 287`). Why: python-stdnum 2.2 refuses SINs whose first digit is `0`
  (never issued; reserved for fictitious numbers) or `8` (business numbers) with
  `InvalidComponent`, so the plan's "good" sample was rejected. The replacement is the sample in
  `stdnum.ca.sin`'s own docstring, as the plan instructs. The validator is unchanged.
- `dps/engine/detect/checksums.py`: the RFC validator calls
  `stdnum.mx.rfc.is_valid(v, validate_check_digits=True)`; `_stdnum` gained `**kwargs` to pass it.
  Why: stdnum skips the RFC check digit by default, so the plan's "bad" sample `GODE561231GR0`
  was accepted as validated. The test's intent (a wrong check digit is not "validated") is kept by
  strengthening the validator, not by changing the sample. Trade-off: stdnum notes about 1.5% of
  RFCs in the wild carry a wrong check digit; those now score as unvalidated pattern hits
  (`0.5 x 0.45`) and still pass the threshold under an `rfc` key (noisy-OR with the 0.9 structural
  candidate in Task 8). Free-text recall for them is an open item (see the report).
- `dps/engine/detect/checksums.py`: `luhn_valid` reads digits with `int(ch)` instead of
  `ord(ch) - 48`. Why: the catalogue's `\d` matches every Unicode decimal digit, and
  `ord - 48` turned an Arabic-Indic card number into garbage sums. `int()` reads any `Nd` digit by
  value, so the same card number validates in either script. ASCII behaviour is identical.
- Docstrings added to `valid_card_pan` and `valid_nino` (public functions).
- Tests added: `test_validators_never_raise` (7 odd inputs x every validator; the module contract
  says a validator never raises) and `test_luhn_reads_unicode_decimal_digits_as_their_value`
  (red before the `int(ch)` change). Totals: the plan's 16 + 8 added = 24 in the file; suite 76.

## Task 6 — Pattern catalogue (tier T1) and phone matching

- `dps/engine/detect/patterns.py`, `in_aadhaar` regex: the boundaries are
  `(?<!\d)(?<!\d[ -])(...)(?![ -]?\d)` (plan: `(?<!\d)(...)(?!\d)`). Why: in a grouped card
  number (`4141 7058 6492 0836`) the plan's pattern matched the first or last three groups as an
  Aadhaar. About 1 in 10 such 12-digit windows is Verhoeff-valid, and a validated Aadhaar at 0.98
  beat the validated card at 0.95 in `resolve_overlaps`. The card hit was then dropped, so the last
  four digits stayed in clear and the value was typed AADHAAR. A probe over 2,000 random
  Luhn-valid 4-4-4-4 cards with no context lost 215 cards before the fix; after it, 0 of 1,500 per
  region (US, IN, GB, MX) are lost, including with phones in the overlap pool. An Aadhaar on its
  own (`Aadhaar 2341 2341 2346, issued 2019`) still matches.
  **Superseded** by "Fix round 1 — review M0-G2-r1" below: these lookarounds dropped every
  Aadhaar with a number next to it, so the plan's boundaries are back and the card case is
  decided after matching.
- Docstring added to `PatternSpec`.
- Tests added:
  - `test_grouped_card_numbers_are_never_split_into_an_aadhaar` (3 cases, space and dash
    grouping; red before the regex change);
  - `test_aadhaar_still_matches_on_its_own`;
  - `test_evidence_never_carries_the_value` (hit evidence names the pattern or checksum, never
    the digits or the address).
- Count: the plan expected "22 passed"; its own tests total 19 (14 parametrised + 5). With the
  5 added tests, 24 in the file; suite 100.

## Task 7 — Structural detector (tier T0)

- `dps/engine/detect/structural.py`, `normalize_key`: the last segment now comes from
  `dps.engine.jsonpath.parse_path` (new private helper `_last_segment`) instead of the plan's
  hand-rolled split. Why: the plan's own test case `$.card.tags[1]['cc-number']` came out as
  `tags 1 cc number` (its split regex never isolated the bracketed segment), so `cc_number`-style
  keys inside arrays were classified by their parent's name. Parsing with the same grammar
  `flatten` writes keeps quoted keys whole (`$.a['b.c']` → `b c`), skips indices
  (`$.tags[3]` → `tags`), and treats a bare column name as a one-segment path
  (`customer.email` → `email`). Anything unparseable falls back to the text after the last
  `.`/`[`/`]`/`'`.
- `normalize_key`: camelCase splitting also separates an acronym from the next word
  (`userIPAddress` → `user ip address`, `customerSSNLast4` → `customer ssn last4`). With the
  plan's single rule these became `useripaddress` and `customer ssnlast4`, so `ip address` and
  `ssn` never matched as whole words. This is a recall fix only; every plan case is unchanged.
- `normalize_key`: accents are folded (NFKD, combining marks dropped) before lower-casing. Why:
  the Spanish rule phrases are accent-free (`telefono`, `direccion`, `genero`, `codigo postal`),
  but MX payloads use `teléfono` / `dirección`, which the plan split into `tel fono` /
  `direcci n` and lost. This is a recall fix only.
- Docstring added to `KeyCandidate`.
- Tests added: `test_normalize_key_edge_cases` (10 cases), `test_acronyms_and_accents_reach_the_rules`,
  `test_evidence_names_the_rule_never_the_key_value`. Count: the plan's 14 + 12 added = 26 in
  the file; suite 126.

## Task 8 — Detector ensemble over JSON and text

- `tests/test_ensemble.py`: the `SAMPLE["pan"]` card is `4532 7811 2297 4417` (plan:
  `4532 7811 2290 4417`). Why: the plan's number fails Luhn, so its card hit scored
  `0.45 x 0.45 + 0.1 = 0.30`. The ambiguous `pan` key (PAN_IN 0.5) is below the structural floor,
  so the field produced no finding at all and the test hit `KeyError: '$.pan'`. Real card numbers
  always pass Luhn. One digit changed, and the `4417` ending the `notes` field mentions is kept.
  The validator and the scoring are unchanged.
- `dps/engine/detect/ensemble.py`: the whole-value branch assigns `entity`/`score`/`detectors`/
  `evidence` in each arm instead of setting `hit = None` under two `# type: ignore`s. `_dedupe`
  takes `Iterable[Finding]` instead of `Any`. The behaviour is identical. Why: the project rule is
  "no `Any` unless unavoidable", and the ignores hid a real `None` dereference path from mypy.
- Docstrings added to `DetectorConfig` and `DetectionResult`.
- Tests added:
  - `test_custom_tier_declares_fields_and_adds_hits`: a fake `CustomDetector`; a declared path
    wins as a whole-leaf finding, and custom scan hits become spans;
  - `test_exactly_one_input_is_required`;
  - `test_results_never_carry_the_values`: the serialised `DetectionResult` for the sample
    contains none of its names, numbers, domain or dates;
  - `test_grouped_card_in_free_text_is_one_span`: end-to-end guard for the Task 6 Aadhaar fix.

  Count: the plan's 4 + 4 added = 8 in the file; suite 134.

## Task 9 — Optional Presidio NER adapter (tier T2)

- `dps/engine/detect/ner_presidio.py`: analyzer construction moved into a new private
  `PresidioNer._build() -> (analyzer, loaded_languages)`. `_engine()` now remembers a failed
  build and raises a fast `RuntimeError` afterwards instead of rebuilding. Why: the ensemble calls
  `analyze` once per JSON leaf, and in auto mode (`enabled=None`) `build_ner` returns an adapter
  whenever `presidio_analyzer` imports, even with no spaCy model installed. The plan's `_engine`
  then retried the import plus three `spacy.util.is_package` probes for every leaf of every
  request. The result is still `[]` and the warning is still logged once; a missing model is not
  a transient condition. `_engine`, `analyze`, `PresidioNer(...)` and `build_ner` keep their
  signatures.
- `analyze` picks the requested language only if a model for it was actually loaded, and
  otherwise the first loaded language. Why: with `languages=("en", "es")` and only an English
  model installed, an `es_MX` request asked Presidio for `es`. Presidio raised (no `es` NLP
  engine), so every Spanish text got no NER at all. Reading it with the English model recovers
  some names; NER is recall-only, so more recall is the safe direction.
- Docstring added to `presidio_available`.
- Tests added: `test_build_failure_is_cached_and_never_raises` and
  `test_language_falls_back_to_a_loaded_model`. Both use a fake analyzer; the second also asserts
  the Presidio-to-DPS mapping, that unmapped Presidio types are dropped, and that evidence never
  carries the text. Both were red before the change.
- The real-Presidio test (`@pytest.mark.ner`) skips because the `[ner]` extra and spaCy models
  are not installed in this venv; the plan expects that skip.
- Count: the plan's "3 passed, 1 skipped" + 2 added = 5 passed, 1 skipped; suite 139 passed, 1 skipped.

## Task 10 — Custom recognizers and declared fields (tier T4)

- `dps/engine/detect/custom.py`, declared-field globs: paths are matched by a new public helper
  `path_glob(pattern)`. In it, `[*]` means any array index, `*` means any run of characters
  inside one dotted name, and everything else is literal. The plan used `fnmatch.fnmatchcase`.
  Why: in fnmatch `[*]` is a character class matching a literal `*`, so the plan's own test
  (`$.accounts[*].iban` vs `$.accounts[3].iban`) returned `None`. Every JSON path with an index
  or quoted key (`[0]`, `['k']`) also misread as a character class. Exact paths still match by
  equality first.
- `RecognizerSpec` and `DeclaredField` refuse `sensitivity` on a **built-in** entity type at
  validation time (`ValueError`, "sensitivity cannot be set for built-in entity type …"). Why:
  this is the Task 10 follow-up recorded under Fix round 1 / B2. Since that fix,
  `register_entity` raises for built-ins. Without the validator, a policy that set `sensitivity`
  on `IBAN` would pass policy validation and then raise inside the request, mapped to a 500. A
  policy validation error is a 400. A built-in type *without* `sensitivity` is still accepted.
- The `PatternSpecModel`, `RecognizerSpec` and `DeclaredField` configs add
  `hide_input_in_errors=True`. The regex error reads `invalid regex: <re.error>` without the
  pattern text. Why: deny-list terms and patterns are often the sensitive terms themselves (code
  names, VIP names). Pydantic's model-level errors otherwise echo the whole input, deny list
  included; this was verified, and the same flag closed the matching settings leak in Task 2.
- `RecognizerSpec.deny_list` refuses blank entries, and `scan` skips zero-length regex matches.
  Why: a pattern like `\d*` or a deny-list entry `""` produced empty hits at every position. In
  an empty leaf such a hit counts as a whole-value finding, and downstream an empty span would
  insert a token into the text.
- Small additions:
  - `DENY_LIST_SCORE = 0.9` is now a named constant (same value);
  - the context window is built with an f-string;
  - `custom_detector_from` checks `isinstance` first;
  - docstrings were added to the public classes and methods.
- Tests added:
  - `test_declared_globs_follow_json_path_syntax`;
  - `test_sensitivity_on_a_builtin_entity_is_refused_at_validation`;
  - `test_validation_errors_never_echo_policy_terms`;
  - `test_empty_matches_and_blank_deny_entries_never_become_hits`.

  All four were red before the change.
- Count: the plan's 4 + 4 added = 8 in the file; suite 147 passed, 1 skipped.

## Task 11 — Policy model and presets

- `dps/engine/policy.py`: `@functools.cache` replaces `@lru_cache(maxsize=None)`. Why: ruff's
  `UP033` fails the plan's code at the lint gate.
- `load_preset(name)` accepts only the names of the bundled preset files: `preset_names()` is a
  new public helper, and a name must match `[a-z0-9][a-z0-9-]{0,63}`. Why: the plan joined the
  name straight into a path (`PRESET_DIR / f"{name}.yaml"`), and the name comes from the request
  body (`"policy": "<name>"`). With it, `../../<anything>` read any `.yaml` on the image.
  `EngineError(f"invalid policy: {exc}")` in Task 22 would then send the pydantic error, which
  echoes the file's content, back to the caller. A malformed name is not echoed in the
  `KeyError`; a well-formed unknown name still is (`unknown policy preset 'no-such-preset'`).
  `KeyError` stays the error type, as the plan's test and Task 22 expect.
- `load_preset` returns a deep copy of a cached, validated policy (cache in
  `_load_preset_cached`). Why: the plan cached and returned the same mutable `Policy` to every
  caller, and `resolve_policy(None)` handed it out directly. One request editing its policy
  (`policy.treat[...] = ...`) would change the treatment for every later request and tenant.
  `list_presets` reads the cache directly, since it only reads.
- `_deep_merge` replaces method specs whole: entries of `treat` and `synthesize.overrides`, and
  `synthesize.default`. Why: the plan merged an override's dict into the preset's dict. So
  `{"preset": "gdpr-pseudonymise", "treat": {"SSN": {"method": "fpe_digits"}}}`, which means
  "encrypt every digit", silently inherited `keep_suffix: 4`, and the last four SSN digits stayed
  in clear. A method swap (`mask_partial`) also carried the old method's params. Other sections
  still deep-merge.
- `resolve_policy` names a merged policy `<preset>+overrides` unless the override sets `name`.
  Why: the plan's `merged.setdefault("name", ...)` never fired, because the preset dump always
  has a `name`. An overridden policy was therefore reported and audited under the preset's own
  name (`report.policy`, audit `policy`). The config hash already differed; now the name does too.
- Every policy model uses `ConfigDict(extra="forbid", hide_input_in_errors=True)`. Why: inline
  policies arrive in request bodies and can carry deny lists, declared paths and dictionary
  values, and a validation error goes back to the caller and the logs. The Task 24 expectation
  (`"teleport"` appears in the 400 detail) still holds, because the method name is in the
  validator's own message.
- Docstrings added to the section models.
- Tests added:
  - `test_preset_names_cannot_reach_other_files`;
  - `test_an_override_replaces_a_method_spec_whole`;
  - `test_overridden_presets_are_named_as_such`;
  - `test_cached_presets_are_not_shared_mutable_state`;
  - `test_policy_validation_errors_never_echo_inline_values`;
  - `test_preset_entity_keys_are_known_types`, a typo guard: every `treat` and `overrides` key
    in every preset is a built-in type or `default_sensitive`.

  The first five were red before the change.
- Presets: the six YAML files are verbatim from the plan; they ship in the wheel, verified with
  `uv build --wheel`.
- Count: the plan's 4 + 6 added = 10 in the file; suite 157 passed, 1 skipped.

## Fix round 1 — review M0-G2-r1 (Tasks 5-11), blocking finding B1

- **B1, `dps/engine/detect/patterns.py`, `in_aadhaar`:** the plan's boundaries are back:
  `(?<!\d)([2-9]\d{3}[ -]?\d{4}[ -]?\d{4})(?!\d)`. The Task 6 lookarounds
  (`(?<!\d[ -])`, `(?![ -]?\d)`) rejected any Aadhaar with a space- or dash-separated number
  next to it. In `Aadhaar 2341 2341 2346 9845120377`, `Aadhaar no 2341 2341 2346 2 copies`,
  `Aadhaar 2341-2341-2346 12 Mar 2020` and `ref 1 2341 2341 2346` a validated CRITICAL
  identifier was left in clear. A probe of 3,000 random Aadhaars with a 1-10 digit number before
  or after them missed all 3,000.
- The grouped-card problem that the Task 6 lookarounds solved is now handled after matching, by
  a new private step `_settle_aadhaar_vs_card(text, hits)`. `scan_text` runs it before
  `resolve_overlaps`. The step goes beyond the review's literal fix ("drop an AADHAAR strictly
  inside a validated CARD_PAN") in two ways, because a probe of that fix found two leaks:
  1. **The card's grouping decides, not containment alone.** When a validated AADHAAR overlaps a
     validated CARD_PAN, the card wins only if it is printed in fours: 15+ digits, every group 4
     long except a last group of 1-4 (4-4-4-3, 4-4-4-4, 4-4-4-4-1..3; helper
     `_printed_in_fours`). Otherwise the card window is dropped.
     - Why: an Aadhaar next to a short number forms a Luhn-valid 13-19 digit window about one
       time in ten. Examples: `Aadhaar no 2341 2341 2346 7 copies`, `ref 7 2341 2341 2346`,
       `Aadhaar 2341 2341 2346 100005` (a PIN code).
     - Under the literal fix, those Aadhaars became CARD_PAN. The card presets
       (`fpe_digits`, `keep_prefix: 6`, `keep_suffix: 4`) would then leave up to 9 of the 12
       Aadhaar digits in clear.
     - Even without the literal fix, a context tie (`Aadhaar card no …`: both hits score 1.0)
       let the longer card window win in `resolve_overlaps`.
     - Partial overlaps are decided the same way. In `Aadhaar card 44785794 5363 3875 0044`,
       the Luhn-valid window `44785794 5363 3875` (8-4-4) used to win the tie and leave `0044`
       in clear.
  2. **`in_aadhaar` searches overlapping windows.** `PatternSpec` gained a defaulted field
     `overlapping: bool = False`, set only on `in_aadhaar`. A private `_matches` helper restarts
     the search one character after each match's start, instead of `finditer` resuming at its
     end.
     - Why: with `finditer`, a 4- or 8-digit number just before an Aadhaar consumed the
       Aadhaar's first group. In `ref 2019 2341 2341 2346`, `2019 2341 2341` was tried
       (invalid), and the real Aadhaar never was. This shadowing is also in the plan's regex.
     - With overlapping windows, a Verhoeff-valid window that straddles two adjacent cards
       printed in fours is now seen. The settle step drops it. Without that step it would
       outscore both cards (0.98 vs 0.95) and leave 20 card digits in clear; a test covers this.
  - Public interfaces are unchanged: `Hit`, `PatternSpec` (one additive defaulted field),
    `scan_text`, `scan_phones`, `resolve_overlaps`, `CATALOGUE`.
- Probe results (`scan_text`, 3,000 random Luhn-valid cards printed in fours, 15-19 digits,
  space or dash, with and without context; 3,000 random Verhoeff-valid Aadhaars with a 1-10
  digit number before or after them):

  | Variant | Cards lost | Aadhaars missed |
  |---|---|---|
  | Task 6 lookarounds | 0 | 3,000 |
  | Review's literal fix (plan regex, drop AADHAAR strictly inside a validated card) | 0 | 564 |
  | This fix | 0 | 76 |

  - The 76 remaining misses are genuinely ambiguous, and all involve a neighbour of 3 or 4
    digits:
    - In 56, the Aadhaar plus a 3- or 4-digit group is itself a Luhn-valid run printed in fours
      (`2341 2341 2346 2019`). That is indistinguishable from a card, so it stays a card with
      the whole run covered.
    - In 20, a 4-digit number before the Aadhaar makes a second Verhoeff-valid 12-digit window
      (`7030 4431-8009-3624`), and the earlier window wins.
  - At the ensemble level (`Detector`, en_US / en_IN / en_GB / es_MX, 1,500 each), no card
    printed in fours was lost, and no Aadhaar followed by a 1-2 digit number or a 10-digit phone
    was missed.
- Tests added. All were red at `2dedd02`, except the card tests, which guard the new code paths:
  - `tests/test_patterns.py`:
    - `test_aadhaar_next_to_another_number_is_still_found`: the review's four strings with no
      comma; red at `2dedd02`.
    - `test_aadhaar_beside_a_luhn_valid_window_stays_an_aadhaar` (7 cases): red at `2dedd02`, and
      all 7 red under the review's literal fix.
    - `test_random_aadhaars_next_to_short_numbers_are_always_found` (300 seeded Aadhaars, 1-, 2-,
      6- and 8-digit neighbours on either side): red at `2dedd02` and under the literal fix.
    - `test_cards_printed_in_fours_are_never_split_into_an_aadhaar` (15-19 digits x space/dash,
      300 seeded cards each).
    - `test_adjacent_cards_printed_in_fours_stay_two_cards`: red if the settle step is removed.
  - `tests/test_ensemble.py`: `test_aadhaar_next_to_another_number_is_a_finding`, the four
    strings through `Detector(locale="en_IN")`, with exact spans. For example, the first string
    yields AADHAAR plus PHONE, where before it yielded PHONE only.
  - `test_aadhaar_still_matches_on_its_own` (the comma case) is kept.
- Count: suite 157 → 184 passed, 1 skipped (patterns +23, ensemble +4).

## Task 12 — Crypto core

- `KeyMaterial` sets `hide_input_in_errors=True` and `dek` is `Field(repr=False)`. Why: the
  model validates caller-presented key material, and Task 22 turns a `ValueError` into
  `EngineError(f"invalid key material: {exc}")`, which goes back to the caller and can reach logs.
  Without the flag, pydantic echoes `input_value=` with the whole dict, DEK included, for any
  field that fails (a bad `created_at`, a non-string `kid`). `repr(km)` / `str(km)` no longer show
  the DEK either. `model_dump` is unchanged, so the response still returns it.
- `unb64u` and `unb32` are strict: any character outside the alphabet, or an impossible length,
  raises `ValueError`. Why: `base64.urlsafe_b64decode` silently drops characters outside the
  alphabet, so `"!!!" + dek` was accepted as the same key. Valid encodings decode exactly as before.
- `key_material_from_caller` error for a wrong `alg` no longer echoes the caller's value, and it
  refuses an empty `kid` (the kid is the HKDF salt).
- `subkey(km, info, length=SUBKEY_BYTES)` gained an optional `length`. A length other than 32 is
  bound into the HKDF info (`…|L64`), because HKDF output of length 32 is a byte-for-byte prefix
  of the 64-byte output for the same info. New constant `SIV_KEY_BYTES = 64`; Task 15 derives
  `det_token` keys at that length, so tokens are AES-256-SIV. The plan's 32-byte key meant
  AES-128 inside SIV, below the 256-bit DEK the spec promises. `det_encrypt` / `det_decrypt`
  accept any AES-SIV key size, so the plan's tests are unchanged.
- `wrap_key_material` refuses RSA keys below 2048 bits (`MIN_RSA_BITS`) and turns an unreadable
  PEM (`ValueError`, `TypeError`, `UnsupportedAlgorithm`) into one `ValueError` with a fixed
  message. Why: a 1024-bit wrap would protect the DEK far below its own strength, and
  `UnsupportedAlgorithm` is not a `ValueError`, so Task 22's `except ValueError` would have let
  it through as a 500.
- Docstrings added to the public helpers.
- Tests added: `test_key_material_errors_never_echo_the_key` (six malformed payloads),
  `test_wrapped_only_material_is_refused_for_reveal`, `test_wrap_refuses_weak_or_unusable_public_keys`,
  `test_longer_subkeys_are_domain_separated`, `test_key_material_repr_never_shows_the_dek`.
  Against the plan's `crypto.py`, four of the five fail (the wrapped-only refusal was already
  there).
- Count: the plan's 7 + 5 added = 12 in the file; suite 196 passed, 1 skipped.

## Task 13 — Token envelope

- `make_envelope` raises `ValueError` when its result would not match `ENVELOPE_RE` (entity not
  upper snake case, token not lower-case base32 of 2+ characters). Why: the plan built the string
  unchecked, so an entity type outside the grammar produced an "envelope" that
  `parse_envelopes` cannot see. Such a token is neither self-describing nor revealable without
  hints. Task 17 catches the error and keeps the bare token (the reveal hint still covers it).
  Entity types reach this point from findings: built-in names and Task 10's validated custom
  names all fit, except that Task 10's `^…$` with `re.match` also accepts a trailing newline
  (`"FOO\n"`, open item in the report).
- Docstrings added to `make_envelope` and `is_envelope`.
- Test added: `test_make_envelope_refuses_what_parse_cannot_read` (6 parametrised cases).
- Count: the plan's 2 + 6 = 8 in the file; suite 204 passed, 1 skipped.

## Task 14 — Optional FF1 adapter

- `Ff1` hex-encodes the tweak (`tweak.encode("utf-8").hex()`). Why: `fastfpe.ff1` takes the key
  **and the tweak** as hex strings. The plan passed the tweak raw, so `Ff1(key, tweak="SSN")`
  raised `ValueError: Invalid tweak hex` (the plan's own `test_real_ff1_round_trip` failed), and
  a tweak that happens to be valid hex (`""`, digit strings, `"CAFE"`) was silently read as other
  bytes. With the fix the adapter reproduces the NIST SP 800-38G FF1 samples 1, 2 and 7 exactly.
- `_fpe_digits` raises `ValueError` for a negative `keep_prefix` / `keep_suffix` and returns
  `None` (domain too small) when the kept digits cover the whole value. Why: a negative keep made
  Python slicing keep the wrong digits; Task 15 turns bad method params into `MethodError`.
- `tests/test_fpe.py`, `test_digits_keep_prefix_suffix_and_format`: `out[7] == "4"` (plan:
  `== "1"`). Why: `"4532 78"` is indices 0-6, so index 7 is the first *encrypted* digit; under the
  test's rotate-by-3 cipher `1` becomes `4`. The plan's assertion contradicted its own
  `keep_prefix=6`. The kept prefix and suffix are still asserted by `startswith`/`endswith`.
- Docstrings added to the public functions and `Cipher`.
- Tests added: `test_real_ff1_matches_nist_sp800_38g_samples`,
  `test_real_ff1_through_the_format_keeping_helpers` (letters at the 5-letter minimum, a card with
  kept prefix/suffix, a field-scoped tweak with quotes and spaces), `test_ff1_refuses_bad_key_sizes`,
  `test_negative_keeps_are_refused`, `test_keeps_that_cover_the_value_fall_back`,
  `test_non_ascii_letters_pass_through` (documents the v1 limit: `é ú ñ` stay in place; a name
  with no ASCII letters falls back). Against the plan's `fpe.py`, the three real-FF1 tests and
  the negative-keep test fail.
- With `uv pip install -e ".[dev,fpe]"` (fastfpe 0.2.1) the FF1 tests run for real: 10 passed in
  the file; suite 214 passed, 1 skipped (the Presidio test).

## Task 15 — The method registry

Public names and signatures are as planned (`METHODS`, `MethodError`, `TransformContext`,
`Outcome`, `SurrogateProvider`, `CipherFactory`, `run_method`, `reverse_method`).
`TransformContext.subkey(method, length=32)` gained an optional `length`. Changes:

- **`det_token` is AES-256-SIV.** Its subkey is derived at `crypto.SIV_KEY_BYTES` (64). The plan
  used 32 bytes, which is AES-128 inside SIV (see Task 12).
- **Exact reversal of `date_shift`.** The plan's version did not round-trip for several real
  inputs and in one case revealed a *different* date without any error:
  - Reverse re-detected the format from the reveal-time locale. `03/04/1988` shifted under
    `en_US` and revealed under `en_IN` (Task 22 reveals with the service's default locale) came
    back as another date. Reverse now parses with the format recorded in `reverse_params["fmt"]`;
    only a known format is accepted.
  - `datetime.fromisoformat` accepts many spellings (`19880314`, `1988-03-14 10:30`, a trailing
    `Z`) and `isoformat()` rewrote them (`1988-03-14T10:30:00`). `strptime` also accepts
    `4 Mar 1988` and `14 MAR 1988`, which `strftime` re-renders padded / title case. Reveal then
    returned a different string from the original.
  - New rule: a value is shifted only if rendering it back reproduces it exactly. ISO dates must
    be `YYYY-MM-DD`; ISO datetimes shift the date part and keep the time and zone text verbatim;
    surrounding whitespace is kept. After shifting, the method reverses its own output and
    compares. Anything that fails these checks falls back to `det_token`, which is exactly
    reversible.
  - `9999-12-31` / `0001-01-01` (common sentinel dates in bank data) raised `OverflowError` for
    about half of all anchors. `OverflowError` is not a `ValueError`, so Task 22 would have
    returned a 500. They now fall back to `det_token` with the note `fallback:date_out_of_range`.
- **Bad method parameters are `MethodError`s.** Params come from the policy, which can be inline
  in a request body, or from caller-supplied reveal hints. The plan used bare
  `int(ctx.params.get(...))`, so `{"length": "twelve"}`, `{"keep_suffix": [4]}`, `{"ranges": 7}`
  and `{"window_days": 10**12}` raised `ValueError`, `TypeError` or `OverflowError`. Task 22 only
  catches `MethodError` around `apply_plan`, so these became 500s. Now each param is checked
  against a range (`length` 4-52, keeps 0-64, `window_days` 1-36500, `percent` 0-100,
  `ranges` ascending finite numbers, `values` a list, `granularity` year/month,
  `keep_country_code` a bool). The message names the method, the parameter and the rule, never
  the value. An unknown method name is also a `MethodError` (it was a `KeyError`).
- **`email` domain modes.** The spec lists `keep / generalize / encrypt`. The plan treated every
  mode other than `generalize` as `keep`, so `domain: encrypt` silently left the domain in clear.
  `encrypt` is now implemented: the domain becomes an AES-SIV token under the RFC 2606 `.invalid`
  TLD, with associated data `<TYPE>|domain`, and it is reversible. Any other mode is a
  `MethodError`.
- **Reversing an FPE sub-method needs FPE.** `phone` (sub `fpe_digits`) and `email` (sub
  `fpe_text`) now raise `MethodError` when FPE is off at reveal time, like `fpe_digits` /
  `fpe_text` already did. Before, they constructed `Ff1` directly, which raises `ImportError`
  without the extra.
- **`det_token` reversal** catches `InvalidTag` and `ValueError` only, and raises
  `MethodError(...) from None` (no chained exception that could carry bytes).
  `TransformContext.subkey` refuses wrapped-only key material with a `MethodError`; before, a
  `KeyError` escaped.
- **`mask_partial`** masks the whole value when `keep_first + keep_last` would cover all of it.
  The plan's slice went negative: `"123"` with `keep_last: 4` became `"**3"` by accident, and
  `"4417"` with `keep_last: 4` was returned in clear.
- **`noise`** fails closed. An unparseable amount becomes `[REDACTED]` (the plan passed it
  through unchanged). `1,000` is read as 1000. The output never equals the input: if rounding
  gives the original back, it moves one unit in the last place. Without a key (synthesize mode)
  the noise is random; the plan used zero noise, which returned every amount in clear.
- **`dictionary`** without a key picks the entry by SHA-256 of the value. The plan used `hash()`,
  which is salted per process, so the choice was not stable across workers or restarts.
- The registry check is `if … raise RuntimeError` instead of `assert`, so it still holds under
  `python -O`. No `# type: ignore` was needed.
- Tests added (all after the plan's 8):
  - `test_det_token_is_aes256_siv_under_a_64_byte_subkey`;
  - `test_field_scoped_tokens_differ_per_path_and_reverse_only_there`;
  - `test_wrong_key_or_garbage_tokens_are_method_errors_without_values`;
  - `test_unknown_methods_are_method_errors`;
  - `test_bad_method_params_are_method_errors_that_never_echo_the_value` (13 cases);
  - `test_email_domain_encrypt_hides_the_domain_and_reverses`;
  - `test_reversing_an_fpe_sub_method_needs_fpe`;
  - `test_date_shift_reverses_exactly_whatever_the_input` (13 inputs x 4 anchors);
  - `test_date_shift_keeps_the_time_part_verbatim`;
  - `test_date_shift_reverse_uses_the_recorded_format_not_the_reveal_locale`;
  - `test_mask_partial_never_shows_a_whole_short_value`;
  - `test_noise_never_returns_the_original_and_works_without_a_key`;
  - `test_dictionary_without_a_key_is_stable_across_processes`;
  - `test_methods_tolerate_empty_values`.

  Against the plan's `methods.py`, 29 of these 37 test cases fail. The plan's own 8 tests pass
  unchanged.
- Count: 45 in the file; suite 259 passed, 1 skipped.
