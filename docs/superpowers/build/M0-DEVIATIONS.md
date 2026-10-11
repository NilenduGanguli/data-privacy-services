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

## Task 16 — Planner

- `dps/engine/planner.py` follows the plan; behaviour is unchanged. `Plan(mode=...)` uses
  `typing.cast` to the `Literal` instead of `# type: ignore[arg-type]`, after the explicit mode
  check. Docstrings now state the `ValueError`.
- Tests added: `test_unknown_modes_are_refused`,
  `test_span_findings_explain_their_span_and_entries_keep_finding_order` (it also asserts that a
  finding's `value_hash` never reaches the plan rows),
  `test_synthesize_fallbacks_and_reversibility_follow_the_registry` (an FPE override in
  synthesize mode falls back without FPE; an explicit override beats `keep_non_sensitive`; the
  `reversible` flag comes from the registry), and `test_empty_findings_give_an_empty_low_plan`.
- Count: the plan's 3 + 4 added = 7 in the file; suite 266 passed, 1 skipped.

## Task 17 — Apply a plan; reveal hints; reveal

Public names: `ApplyOptions`, `ApplyResult`, `RevealHint`, `apply_plan`, `reveal` as planned.
`anchor_for(document, plan)` is **public already**. Task 22's step 3 ("rename `_anchor_for` →
`anchor_for`") is therefore already done; there is no `_anchor_for`.

- **Reveal hints no longer leak the anchor.** In the plan, every hint carried
  `anchor = "CUSTOMER_ID:" + sha256(customer_id)[:16]`, or a salt-free hash of the whole record,
  and `date_shift` repeated it in `reverse_params`. Hints are meant to travel with the obfuscated
  output, and spec §5.5 says they contain "locators, methods and types but never originals".
  A 6-digit customer id falls to a 10^6-candidate enumeration of that hash, which undoes the
  customer-id pseudonymisation; a small record's hash falls the same way. Spec §5.5 also says
  "the anchor is the record's own token".
  - `apply_plan` now uses `a_` + base32(HMAC(subkey(key, "anchor"), anchor_for(...)))[:26]`
    whenever key material is present.
  - The anchor stays stable per (key, business key), so `consistency: key` still shifts the same
    customer's dates the same way across requests.
  - In synthesize mode (no key) the raw anchor is used, but it only feeds the surrogate engine
    and no hint is emitted.
- **`RevealHint` gained `kid` and `field_scoped`** (additive, defaulted). `original_type` is now
  `Literal["str", "int", "float"]`, and the model sets `hide_input_in_errors=True`.
  - `reveal` refuses hints whose `kid` differs from the presented key. Why: FPE has no
    authentication, so a wrong key silently decrypted FPE values to garbage.
  - The reveal context takes `field_scoped` from the hint. Why: Task 22 reveals with the
    *default* policy, so a field-scoped obfuscation could not be revealed.
- **Numbers stay numbers only when exact.** A reversible output becomes an int or float only if
  `str(number)` gives the emitted token back. Why: `_typed("0735…", "int")` returned `735…`, which
  lost a digit, and reveal then failed. FF1 output starts with `0` about 10% of the time, so this
  broke roughly one int field in ten. Irreversible outputs (`noise`, `bucket`) convert leniently,
  as before.
- **`tests/test_apply.py` sample and assertion.** The plan's
  `isinstance(out["ssn_int"], int)` cannot hold. `gdpr-pseudonymise` gives `SSN` `fpe_digits`
  with `keep_suffix: 4`, which leaves 5 free digits of a 9-digit SSN. That is below the 10^6
  domain guard, so the value falls back to a `det_token` string. The plan's own test fails
  against the plan's own code. The intent ("numbers stay numbers", plus a reversible SSN) is now
  tested by two things:
  - a new field `account_number: 50100234567812` (ACCOUNT_NUMBER, `fpe_digits`, 14 free digits),
    asserted to stay an int;
  - `ssn_int`, asserted to be a string token, with the note `fallback:fpe_domain_too_small`
    counted once and reveal restoring the int.

  The validator and the guard are unchanged. The policy question of keeping the SSN last 4 is
  listed as an open issue.
- **Reveal robustness** (caller-supplied hints):
  - a missing path, a path into a string (`TypeError` before), a malformed path, a span outside
    its value, a non-scalar target, and an unknown or irreversible method are all
    `MethodError`s. `TypeError` used to escape Task 22's `except` clause as a 500.
  - `reveal` no longer mutates the caller's `ApplyOptions` (`dataclasses.replace`).
- **Envelope walk.**
  - It runs over `flatten(document)`, so each envelope is decrypted with its real JSON path. The
    plan used `"$"`, which broke field-scoped envelopes inside JSON.
  - Without hints, every envelope must decrypt, so a wrong key fails loudly. With hints, an
    envelope that does not authenticate is left untouched. Why: input that already held envelopes
    from another key (re-obfuscating tokenised data) made every reveal fail.
  - `make_envelope` refusals (Task 13) keep the bare token; the hint still covers it.
- **Apply robustness.**
  - A span on a numeric leaf is transformed on `str(value)` and typed back. The plan skipped it
    silently, leaving the value in clear.
  - A plan span outside its value, or a whole-value entry on a non-scalar, raises `MethodError`.
- Test edit: the envelope test's first `model_copy(update={"treat": <dicts>})` line is dropped.
  It made pydantic emit a serializer warning, and the next statement overwrote it anyway.
- Tests added:
  - `test_reveal_hints_carry_no_value_derived_data`;
  - `test_the_anchor_is_keyed_and_stable_per_key_and_business_key`;
  - `test_int_tokens_that_would_lose_a_leading_zero_stay_strings`;
  - `test_reveal_refuses_hints_from_another_key`;
  - `test_malformed_hints_are_method_errors` (8 cases);
  - `test_whole_value_hints_must_point_at_a_scalar`;
  - `test_reveal_does_not_mutate_the_callers_options`;
  - `test_foreign_envelopes_survive_with_hints_and_fail_loudly_without`;
  - `test_field_scoped_tokens_reveal_by_hint_whatever_the_reveal_policy`;
  - `test_envelope_walk_reads_field_scoped_tokens_at_their_json_path`;
  - `test_spans_on_numeric_leaves_are_transformed_not_skipped`;
  - `test_plan_spans_that_do_not_fit_the_document_are_refused`;
  - `test_envelopes_are_only_made_for_names_the_parser_can_read`;
  - `test_reveal_hints_validate_and_never_echo_their_input`;
  - `test_whole_value_entries_must_point_at_a_scalar`.

  Against the plan's `apply.py` (with `anchor_for` aliased), 15 of the first 21 added cases fail
  (the 22nd was written after that run), and so does the plan's own first test.
- Count: the plan's 3 + 22 = 25 in the file; suite 291 passed, 1 skipped.

## Task 18 — Verifier and report

- **Hit semantics (the plan's own test failed against the plan's code).**
  `test_verify_passes_clean_output_and_catches_leaks` expects:
  - `bad.hits == [{"path": "$.notes", …}]` for an output whose notes field leaks the e-mail;
  - `verify_output(…, kept={("$.notes", 5, 26)}).passed`.

  The plan searched one global haystack and reported each *finding's* path. The `$.email`
  finding's value is found in the leaky notes, so it reported two hits (`$.email`, `$.notes`) and
  still failed with the notes span kept. The rules are now:
  - each output string is searched on its own, and a hit names **where in the output** the
    original was found, once per (path, entity type);
  - a kept locator is skipped, and its **value is allowed wherever it appears**.

  The second rule matters in practice. The apply sample keeps a `DATE` in the notes that equals
  the date-shifted DOB, and under the plan's rules that request would fail strict verification
  every time.
- **Normalisation is Unicode-aware.** The plan kept `[a-z0-9]` only, so every non-Latin original
  normalised to `""` and was skipped as "too short": a Devanagari name leaked verbatim passed the
  sweep. Accented letters were also dropped (`México` → `mxico`). `normalise` now:
  1. applies NFKD;
  2. case-folds;
  3. drops combining marks (accents);
  4. reads any Unicode decimal digit as its ASCII digit;
  5. keeps letters and digits in every script.

  Full-width or compatibility forms (`１２３-４５-６７８９`, `Ⅻ`) and accent-stripped copies
  (`MEXICO`) are now caught. Every ASCII case behaves as before.
- **JSON escaping no longer hides a leak.** The plan normalised `json.dumps(output)`. In that
  serialisation a newline or tab inside a value becomes `\n` / `\t`, so `12 Main St\nSpringfield`
  leaked verbatim normalised to `…stnspringfield` and did not match. The output is now walked:
  every scalar leaf and every **object key** is searched separately. Keys were inside the plan's
  dumped haystack too, so they stay covered. `None` and booleans are skipped.
- `report.py` is as planned, plus docstrings.
- Tests added:
  - `test_non_latin_originals_are_checked_too`;
  - `test_values_with_escaped_characters_are_still_found`;
  - `test_full_width_and_compatibility_forms_are_folded`;
  - `test_a_value_kept_on_purpose_is_not_a_leak_wherever_it_appears`;
  - `test_originals_in_output_keys_are_found`;
  - `test_text_output_and_numeric_leaves`;
  - `test_hits_are_reported_once_per_output_location`;
  - `test_detect_reports_without_a_plan`.

  Against the plan's `verify.py`, 6 of them fail, as does the plan's own
  `test_verify_passes_clean_output_and_catches_leaks`.
- Not done (spec §5.7 also says "plus detectors re-run on the output"): the M0 plan's sweep is
  match-based only. This is listed as an open issue for Task 22 / M1.
- Count: the plan's 3 + 8 added = 11 in the file; suite 302 passed, 1 skipped.

### Task 18 — fix round 1 (review G3 B1)

- **The kept-value allowance is now per output leaf, not global.** The rule above ("a kept
  value is allowed wherever it appears") failed open: with the `$.notes` e-mail span kept, an
  untransformed `$.email` field holding the same address passed the sweep (`passed=True,
  hits=[]`). An untransformed `$.dob` passed the same way whenever its date was kept in free
  text (gdpr-pseudonymise has `DATE: keep`). That contradicts spec §5.7.
- New rule in `verify_output`: a kept locator's value is allowed **only in the output leaf at
  that locator's path** (output paths equal input paths, because apply rewrites leaves in place
  and never renames keys). A hit at any other leaf, list element or object key still counts.
  Object keys are never excused, even the key directly above the kept leaf. `kept`, the
  signature and the `Verification` shape are unchanged.
- Granularity is the leaf, not the span: inside the leaf that holds the kept span, the value is
  allowed wherever it occurs. For a text payload (one leaf, `$`) this is the same as before.
  Tightening it to an occurrence count would risk false strict-mode refusals once synthesize
  rewrites text, so it is left as an open issue.
- Tests: `test_a_value_kept_on_purpose_is_not_a_leak_wherever_it_appears` is renamed
  `…_where_it_was_kept` (unchanged body, still passes). Added
  `test_a_kept_span_does_not_excuse_the_same_value_left_in_another_field` (the review's
  reproduction, plus the DOB case) and
  `test_a_kept_span_does_not_excuse_the_value_reappearing_elsewhere` (a copy in a list, and the
  value as an object key). Both fail against the previous `verify.py`.
- Count: 13 tests in the file; suite 304 passed, 1 skipped.

## Fix round 2 — review M0-G3-r2 (Tasks 12-18), blocking finding B2

- **`date_shift` now uses one offset per record, not one per entity type or path.** The plan's
  `_offset_days` (carried into Task 15) keyed the offset HMAC with `ctx.subkey("date_shift")`.
  That subkey's HKDF info is `date_shift|<ENTITY_TYPE>`, plus `|<path>` when the policy is
  field-scoped. So one record's `DATE_OF_BIRTH` and `DATE` shifted by different amounts, and
  under `consistency.field_scoped: true` so did two fields of the same type. Intervals, order
  and age bands broke, contradicting spec §5.5 ("all dates in the record shift equally"). The
  shipped presets only date-shift `DATE_OF_BIRTH`, so they never showed it; custom and
  field-scoped policies did.
- New derivation: `HMAC(record_subkey, anchor) mod (2W+1) − W`, never 0. `record_subkey` is
  HKDF info `date_shift|record`, salted by the kid like every subkey. It depends on neither the
  entity type nor the path. It goes through the new `TransformContext.record_subkey(method)`,
  which shares `subkey`'s guard: no key, a wrapped-only key or a malformed DEK raises
  `MethodError`. `subkey` itself is unchanged (signature and output). No method derives a
  per-type `date_shift` subkey any more, so `date_shift|record` cannot collide.
- Apply and reverse both go through `_offset_days`, so reveal stays exact. This is a wire change
  for any date shifted before it, but no key material has been issued yet (Tasks 22/24 are not
  built), which is why the review asked for it now.
- **Limit, documented in `_offset_days`:** equal shifts also need an equal `window_days`. The
  offset is drawn modulo `2W+1`, so dates treated with different windows shift differently. A
  policy that wants intervals preserved must give every date-shifted type the same window.
  Warning about mixed windows in the planner is left as an open issue.
- Tests: six new cases. The five that test the fix fail against the previous `methods.py`; the
  window-bound one passes on both and pins the documented limit. Suite: 310 passed, 1 skipped.
  - `tests/test_methods.py`: `test_date_shift_offset_is_one_per_record_whatever_the_entity_type`
    (also: offsets still vary by anchor and by key),
    `test_date_shift_offset_does_not_depend_on_the_path_when_field_scoped`,
    `test_date_shift_keeps_order_and_intervals_across_entity_types` (dates 100 days apart stay
    100 days apart), `test_date_shift_offset_is_bounded_by_the_window`.
  - `tests/test_apply.py`: `test_all_dates_in_a_record_shift_by_the_same_days_and_reveal`,
    parametrised over `field_scoped` False/True: `DATE_OF_BIRTH` at `$.dob`, `DATE` at
    `$.opened`, `$.history[0].on` and a span in `$.notes` all shift by one offset, and
    `apply_plan` then `reveal` round-trips the record.

## Task 19 — Surrogates and personas

Public names as planned: `SurrogateEngine.surrogate(entity_type, original, locale, anchor)`,
`SurrogateEngine.persona(anchor, locale)`, `PersonaFactory.for_anchor(anchor, locale, *,
original_dob, gender_hint)`, `Persona`, `SynthesisSeed`, `generate_synthesis_seed`, `seed_bytes`,
`seed_from_caller`, `seed_int`, `normalise_locale`. Added: `PersonaFactory.birth_date`,
`personas.birth_date`, `personas.ascii_fold`, `personas.faker_for`.

**The plan's own test fails against the plan's code.** Run against the plan's `personas.py` /
`surrogates.py`, `test_surrogates_are_rule_valid_and_consistent` failed on 3 of 3 runs and
`test_value_consistency_and_never_identity` fails about one run in three:

- **DOB band (order dependence).** `SurrogateEngine.persona()` cached the persona per
  `(anchor, locale)` on first use. The test asks for `EMAIL` before `DATE_OF_BIRTH`, so the
  persona was cached *without* the original DOB and the surrogate DOB was a random 18-80-year-old
  birth date, outside the 1985-1989 band (`2004-01-19`, `1963-02-28`, ...). Now each persona
  attribute group (gender, first name, last name, birth date, address) is drawn from its own
  HMAC sub-seed (`persona|anchor|locale|group`), so the persona built with the original DOB has
  the same name and address as the cached one, and the DOB surrogate is computed per call from
  `(seed, anchor, original DOB)`. The result no longer depends on which field was asked first.
- **Two e-mails in one record got the same surrogate.** The plan built every e-mail from the
  persona's local part plus one of three RFC 2606 domains, so `x@y.co` and `z@y.co` in record
  `r1` collided with probability 1/3. E-mails now get a value-derived numeric suffix
  (`meera.vyas4821@example.org`, still persona-coherent), and the engine keeps an
  `issued` map so that, within one engine (one request), two different identifiers never share a
  surrogate: a collision is re-drawn with a retry salt. Persona attributes (name, birth date,
  address, city, postcode, age), dates, card expiry and amounts are exempt (they legitimately
  repeat). **Superseded by the G4 fix round 1 below:** the `issued` map made surrogates depend
  on request order and was removed; reshaped identifiers use a keyed derangement instead.

**Never issued (spec §5.6) where the plan generated issued-space numbers:**

- **Aadhaar** now starts with `1` (Verhoeff-valid, Aadhaar-shaped; UIDAI never issues 0/1). The
  spec says "Verhoeff-valid Aadhaar-shaped numbers outside issued space"; the plan's
  `[2-9]` prefix draws from the issued space (about 1.3 bn of ~8 bn possible numbers are real
  people's). **Test change:** the plan asserted `validate("AADHAAR", aad)` (stdnum, which rejects
  a leading 0/1). The test now asserts 12 digits, leading `1`, `verhoeff.is_valid`, and
  `validate(...) is False` — the same trade the plan already made for SSNs (`9xx`,
  `validate("SSN") is False`). No validator was changed.
- **ITIN** groups are now `89` or `93`; the plan drew `70-88`, which is where the IRS issues
  ITINs. **SIN** now starts with `0` (never assigned); the plan drew any Luhn-valid number.
- **Phones:** NANP numbers use `NXX-555-01xx` (fictional in every area code; random valid area
  code, so a batch is not limited to 100 numbers); +44 numbers (GB and the Crown Dependencies)
  use Ofcom's drama range `07700 900xxx`. libphonenumber deliberately treats that range as
  invalid; the spec names it, so it is used as is. Other countries (IN, MX, ...) have no
  fictional range and get a valid number near the country's example number. The country now
  comes from the *original* number when it parses (a `+91` number under an `en_US` policy stays
  Indian); the plan always used the persona's country.

**Values the plan returned unchanged** (then the never-identity fallback appended `1` or bumped a
digit, e.g. `Metformin1`, `E11.0`, `HDFCINBBXXX1` — near-verbatim originals):
`DIAGNOSIS_CODE` and `MEDICATION` (returned the original by design), `SWIFT_BIC` (no digits to
replace) and unparseable `DATE` (returned the original). Now:
diagnosis codes are reshaped letters *and* digits (the ICD chapter letter alone reveals the
condition class), medications come from a list of common generics, BICs are reshaped keeping
the country code, unreadable dates become a plausible ISO date.

**Other changes:**

- *Never revealing.* A surrogate must not equal its original **or contain it** under the
  verifier's normalisation (`verify.normalise`, length ≥ 4), so a surrogate can never trip the
  leakage sweep. GENDER and NATIONALITY are exempt: forcing "different" on a two-valued attribute
  would reveal the original by inversion. The value seed's canonical form is also
  `verify.normalise`, so `2341 2341 2346` and `234123412346` get the same digits.
- *IBAN* keeps the original's country and BBAN shape and is mod-97 valid (stdnum
  `calc_check_digits`, re-drawn until `stdnum.iban.is_valid` for countries with national check
  digits). The plan used `Faker(...).iban()`, which produced a `GB` IBAN for every locale.
- *Shapes follow the original:* CVV length (3/4), card-expiry year width and separator, a
  leading `@` on usernames, UUID-shaped device ids (the plan emitted a 36-character hex run),
  MAC separator and case, IPv6 → `2001:db8::/32` (RFC 3849), single-letter / lower-case gender,
  ISO datetimes keep the time suffix, `AGE` stays in its five-year band, upper-case names stay
  upper-case. `AADHAAR` without digits returned 11 digits (no check digit) — fixed.
- *Determinism across days:* the persona's free birth date is drawn from 1950-2004 with the
  seed; the plan used `Faker.date_of_birth`, which depends on today's date, so a caller-held
  seed did not reproduce the same persona the next day.
- *Names* are re-drawn until they are one token of letters (Faker `en_IN` has `D’Alia`, `en_GB`
  `O'Brien`, `es_MX` `Ana María`), so "single-token original → first name" and the e-mail local
  part work in every locale. E-mail local parts are accent-folded ASCII (`acuna`, not `acua`).
- *Faker* instances are cached per thread and locale and re-seeded per draw (creating one costs
  ~1.2 ms; a 10k-record request would otherwise spend seconds constructing them).
- *Seed hygiene:* `SynthesisSeed` sets `hide_input_in_errors=True` and `seed` has `repr=False`;
  `seed_bytes` errors name the rule only.
- Empty / whitespace originals are returned as they are.

Tests: the plan's 4 (one assertion changed, above) + 11 added:
`test_the_birth_date_stays_in_its_band_whatever_was_asked_first`,
`test_persona_fields_do_not_depend_on_the_original_dob`,
`test_same_seed_same_surrogates_across_engines_and_formats`,
`test_distinct_identifiers_never_share_a_surrogate_within_an_engine`,
`test_never_issued_ranges`, `test_phone_keeps_the_original_country_and_format`,
`test_iban_keeps_country_and_shape_and_is_valid`,
`test_health_and_other_values_are_replaced_not_returned`, `test_shapes_follow_the_original`,
`test_names_are_single_tokens_in_every_locale`, `test_the_seed_never_shows_in_repr_or_errors`.
The test seed is random per run (as in the plan); the file was also run in-process against 500
fresh seeds with no failure. Count: 15 in the file; suite 325 passed, 1 skipped.

Open: two *different* people named in one record collapse onto the record's one persona (the
plan's persona-per-anchor design); PAN_IN, ROUTING_ABA, IN/MX phones have no unissued range and
may coincide with real values; NINO/NHS/CURP/RFC/passport fall back to digit/letter reshaping
without check digits.

## Task 20 — LLM gateway client

Public names and signatures as planned: `LlmGateway(settings, *, transport, clock)`,
`.provider`, `.model`, `.available()`, `.complete(messages, *, temperature, max_tokens,
purpose)`, `LlmResult`, `LlmUnavailable`, `LlmCallError`, `get_gateway`. Changes:

- **No chained exceptions.** Every `LlmCallError` is raised `from None`. The plan chained the
  `httpx` exception (`from exc`), whose message quotes the full request URL — and `config.py`
  already refuses to echo these URLs because they may carry userinfo credentials. The Vertex
  path chained the SDK exception, which can quote the prompt. Messages carry provider, host,
  HTTP status (`HTTPStatusError 503`) and exception type only.
- **Malformed responses are `LlmCallError`s.** `response.json()` raises `ValueError` on a
  non-JSON body (a proxy login page, an HTML error), and a list body made `.get` raise
  `AttributeError`; both escaped the plan's `except httpx.HTTPError`, and Task 21 only catches
  `LlmCallError` / `LlmUnavailable`, so a bad gateway response would have become a 500. Same for
  the COIN mint. A `null` content became the text `"None"` via `str(...)`; it is now an error.
- **Usage counts are tolerant:** `null` / non-numeric `prompt_tokens` gave `TypeError` /
  `ValueError` from `int(...)`; they now count as 0.
- `_b64` raises `from None` (the plan chained the `binascii.Error`).
- One value-free `llm.call` log line per call (`provider`, `purpose`, `model`, `outcome`,
  `error` type, `duration_ms` — all on the observability allow-list).
- Docstrings; the mint timeout is a named constant.

Kept from the plan, flagged for the M5 security review: the COIN token mint runs with
`verify=False`. This is the org-mandated pattern (DPC `dpc/arrange/client.py` and
`retrieval/get_llm.py` do the same, citing the Stellar onboarding handoff: the token endpoint is
behind the VDI's TLS-intercepting proxy). It sends the COIN client secret over an unverified TLS
channel; the posture guard should decide whether staging/prod may run that way.

Tests added: `test_errors_never_chain_an_exception_that_could_quote_a_url_or_secret` (also
checks the error metric), `test_malformed_completions_are_call_errors` (4 cases),
`test_malformed_mints_are_call_errors` (3), `test_bad_base64_credentials_fail_without_echoing_them`,
`test_usage_is_optional_and_tolerant`, `test_concurrent_calls_mint_one_token`,
`test_api_calls_send_the_bearer_and_timeout_settings`, `test_availability_per_provider`.
Against the plan's `gateway.py`, 7 of the 13 added cases fail; the plan's own 4 pass on both.
Count: 17 in the file; suite 342 passed, 1 skipped.

## Task 21 — LLM text synthesis over placeholders

Public names as planned: `PLACEHOLDER_RE`, `REWRITE_SYSTEM`, `LeakToModelError`,
`LlmRewriteRejected`, `PlaceholderText`, `to_placeholders`, `assert_placeholder_safe`,
`fill_placeholders`, `rewrite_placeholders`, `synthesize_text`. Additive, keyword-only and
defaulted: `synthesize_text(..., plan=None, screen=None)`, `fill_placeholders(..., plan=None)`;
`PlaceholderText.counts`; `info` gains `methods_used` and `notes`. Changes:

- **A text that is one value was returned verbatim.** For a text that is exactly one value
  (`"ann.k@example-mail.in"`), the ensemble emits a *whole-value* finding at `$` (no span).
  `to_placeholders` kept span findings only, so nothing was replaced, the mapping was empty and
  the original came back as the "synthetic" output (the strict sweep would then refuse the
  request). Whole-value root findings now cover the stripped text.
- **The leak guard no longer fails the request; it skips the model.** The plan ran
  `assert_placeholder_safe` unconditionally (also when no LLM call was going to happen) and let
  `LeakToModelError` escape, which Task 22 turned into an `EngineError`. When a detected value
  still appears in the template (e.g. a second occurrence the detectors missed), the model is now
  *not called* (`fallback: leak_guard`) and the deterministic fill runs; the leakage sweep in the
  service then rules on the output exactly as for any other request. The guard checks every
  finding's original (`collect_originals`), not only the placeholdered ones.
- **Spec §5.6 step 5 ("verify ... any hit fails closed to the deterministic path").** The plan
  accepted any rewrite that kept the placeholder set. Now a rewrite is also discarded when
  (a) the filled text contains an original that the deterministic fill had removed
  (`fallback: verification`), or (b) a caller-supplied `screen` (the service passes its
  detector) finds more values in the rewrite than in the template it was given
  (`fallback: llm_introduced_entities` — the model invented a name or number).
- **The policy decides each fill.** The plan filled every placeholder with a surrogate, so in
  text the synthesize plan was ignored: `synthetic-realistic` keeps `DATE` and `URL`, but text
  dates were shifted; `synthetic-strict` redacts `URL`, but text URLs got a fake one. With
  `plan=`, each label is filled by `run_method(entry.method, ...)` in synthesize mode (`keep`
  puts the original back after the model ran, `redact` writes the label, `surrogate` asks the
  engine). Kept values are still placeholdered before the call, so the model sees none of them.
  `methods_used` / `notes` report what actually ran, per finding.
- **Text that already looks like a placeholder.** `{EMAIL_1}` written in the customer's text
  collided with the generated label, and the plan's fill replaced any unknown label with `""`,
  deleting customer text. Generated labels now skip any placeholder-shaped string already in the
  text, and unknown labels are left untouched.
- `max_tokens` is capped at 8192 (the plan used `len(templated)`, up to `max_text_chars`).
- Temperature 0.7 and the system prompt are as planned (`REWRITE_TEMPERATURE` constant).
- `LLM_RAW_PII` stays a tripwire that nothing increments: no code path sends a detected value.

Tests added: `test_a_value_that_is_the_whole_text_never_reaches_the_model`,
`test_the_prompt_is_placeholders_only_with_the_spec_settings`,
`test_text_that_already_looks_like_a_placeholder_is_kept_and_never_reused`,
`test_the_leak_guard_skips_the_model_instead_of_sending`,
`test_a_rewrite_that_brings_an_original_back_fails_closed`,
`test_a_rewrite_that_invents_new_values_fails_closed`,
`test_gateway_failures_and_unavailability_fall_back`, `test_the_plan_decides_each_placeholder_fill`.
Against the plan's `llm_text.py`, 7 of the 8 fail: 4 on the behaviour above (whole-text value,
placeholder-shaped text, leak guard, a rewrite that brings an original back) and 3 because they
use the new `plan` / `screen` parameters or `info` fields; the plan's own 3 pass on both.
Count: 11 in the file; suite 353 passed, 1 skipped.

## Task 22 — The engine façade

Public names as planned: `PrivacyEngine(settings, *, gateway, cipher_factory)` with `.policy`,
`.detect`, `.plan`, `.obfuscate`, `.synthesize` (async), `.reveal`, and the attributes Task 24
reads (`settings`, `gateway`, `ner`, `fpe_enabled`); `Payload`, `ConsistencyOptions`,
`RequestOptions`, `DetectResponse`, `PlanResponse`, `TransformResponse`, `RevealResponse`,
`EngineError`, `VerificationFailed`. Step 3 (rename `_anchor_for` → `anchor_for`) was already
done in Task 17, so `apply.py` is unchanged. Changes:

- **Request models no longer echo customer data or keys in validation errors.** `Payload`,
  `ConsistencyOptions` and `RequestOptions` set `hide_input_in_errors=True`. With the plan's
  models, `Payload(data={...}, text="...")` raised a `ValidationError` whose message quoted the
  whole input (`input_value={'ssn': '123-45-6789'}, ..., 'text': 'Ananya Krishnan'`), and Task
  24's `payload_from` puts `str(exc)` into the HTTP 422 `detail`. `RequestOptions` carries
  `key_material` (the DEK) and `synthesis_seed`, which the same path would have echoed.
- **Enumerations are validated:** `consistency.scope` is `Literal["request", "key", "entity"]`
  and `token_style` is `Literal["envelope", "natural"]` (the plan took any string; an unknown
  token style silently meant `natural`, an unknown scope was copied into the key material).
  `wrap_key.type` must be `rsa-oaep-256` (defaulted when absent) and `public_key_pem` is
  required; both are checked before any work is done. The plan ignored `type`.
- **Every caller-caused error is an `EngineError`, raised `from None`:** a bad `mode` in
  `plan()` (the planner's `ValueError` escaped as a 500 in the plan), invalid custom
  recognizers, and a synthesize-mode method that needs a key (`MethodError` from the text path,
  which the plan did not catch). `from None` keeps a chained pydantic error (which can carry
  input) out of tracebacks. `reveal` passes a `MethodError`'s message through (value-free by
  contract: "reveal hints were produced under a different key (kid mismatch)"); other
  exceptions still report their type only.
- **Locale precedence works as the plan intended.** The plan's
  `options.locale or policy.locale.default or settings.default_locale` never reached the
  service default, because `LocaleConfig.default` always has a value (`en_US`). The policy's
  locale now wins only when the policy sets one (`model_fields_set`); every preset does, so
  presets behave as before, and inline policies without a locale use `DPS_DEFAULT_LOCALE`.
- **Text synthesis follows the plan and reports it** (Task 21): the service passes `plan=` and
  a `screen` built from the request's own detector, uses the methods that actually ran for
  `methods_used`, records a model fallback as `fallbacks["llm:<reason>"]` (also for
  `leak_guard`, when the model was never called), and adds `used` / `fallback` to
  `report.llm`. The plan's `except LeakToModelError` is gone: `synthesize_text` no longer raises
  it (the guard skips the model instead).
- The synthesis seed is validated together with `seed_bytes` inside the `EngineError` mapping.

Tests added: `test_text_obfuscation_reveals_with_and_without_hints`,
`test_bad_keys_hints_and_wraps_are_engine_errors_that_echo_nothing` (10 cases),
`test_request_models_never_echo_their_input`, `test_a_caller_held_seed_reproduces_synthesis`,
`test_text_synthesis_follows_the_plan`,
`test_llm_rewrite_needs_both_switches_and_sees_placeholders_only`,
`test_an_inventive_rewrite_is_discarded`, `test_locale_precedence`,
`test_warn_policies_return_output_and_report_the_failure`,
`test_a_synthesize_method_that_needs_a_key_is_an_engine_error`, `test_reports_carry_no_values`.
Against the plan's `service.py` (on top of Tasks 19-21 as built), 7 of the 11 fail; the plan's own
5 pass on both. Count: 16 in the file; suite 369 passed, 1 skipped.

Open (carried from Task 18): spec §5.7's "detectors re-run on the output" is still not part of
the obfuscate/synthesize sweep — FPE and surrogate output is *meant* to look like the real thing,
so a re-run cannot be a pass/fail signal as is; only the LLM rewrite is screened this way.
`locale.by_country_field` (per-record locale) is not implemented. JSON free-text fields are
synthesized span by span; the LLM rewrite applies to text payloads only.

### Task 19 — follow-up (found in the end-to-end run after Task 22)

- Phones in countries without a fictional range (IN, MX, ...) were drawn as the country's example
  number with the last 4 digits random, so every Indian surrogate read `+91 81234 5xxxx` (10^4
  numbers; a large request would exhaust them and the distinctness map would start accepting
  collisions). Up to 6 trailing digits are now random (validity-checked, falling back to 4).
  `test_phone_keeps_the_original_country_and_format` pins it.

### Task 19 — fix round 1 (G4 review r1: B1 order dependence, B2 non-ASCII originals)

**B1 — surrogates depended on request order.** The `issued` map ("distinct stays distinct
within one engine") gave the second of two colliding originals a retry value, so which original
won depended on the order of the request and on what else it held: under one caller-held seed
`CUST-3136000` came back as three different fakes, and 29 of 200 CVVs changed when the same
values arrived reversed. That breaks spec §5.6 ("same original → same fake, across fields,
tables and systems") and P3. Fix (the review's option b, with a for the rest):

- The `issued` map, `_UNTRACKED` and the collision fallback are gone. `surrogate()` is a pure
  function of `(seed, entity type, canonical original)` (plus the anchor for persona fields);
  the never-equal / never-contain retries were already salted with the canonical original only.
- New `dps/engine/synth/reshape.py`: identifiers whose surrogate keeps the original's shape —
  unmapped entity types, `CVV` (3/4 digits), `SORT_CODE`, `IFSC` (bank letters and the reserved
  fifth `0` kept), `AMOUNT` (now the generic path), `DIAGNOSIS_CODE`, `SWIFT_BIC` (country kept)
  and the non-IBAN-shaped `IBAN` fallback — map their digits (and, for letter values, every
  letter) through a **seed-keyed derangement** of the shape's value space: a permutation with no
  fixed point, `x → P⁻¹(P(x)+1)`, where `P` is a 10-round FF1-style Feistel (keyed BLAKE2b
  rounds, modular addition over the mixed-radix domain; cycle-walking for a single position).
  The key is `HMAC(seed, "dps.synth.reshape.v1" ‖ entity type ‖ mode ‖ canonical shape)`, so
  `CUST-884120` and `cust 884120` get the same digits. Result: collision-free within a shape,
  order-free, never the original, and stable across requests — the whole 1,000-value CVV space
  maps onto itself.
- Generators with a fixed output space (SSN 9xx, ITIN, SIN, Aadhaar, test-IIN cards, fictional
  phone ranges, e-mails, IBAN, ABA, IP/MAC, medications, ...) now *can* map two originals to one
  fake, with probability ~n²/2N (documented in the module docstring; tight spaces: Ofcom's
  1,000 UK numbers per format, ITIN ~2·10⁶, SIN 10⁷, 20 medications). A bijection there is
  impossible: the input space is larger than the never-issued output space.
- **Test change:** `test_distinct_identifiers_never_share_a_surrogate_within_an_engine` asserted
  the order-dependent property (6 one-digit account numbers → 6 distinct fakes via retries) and
  e-mail distinctness within a record. Replaced by
  `test_surrogates_do_not_depend_on_request_order` (forward vs reversed vs alone, six entity
  types incl. GB phones and medications; fails on the old code) and
  `test_reshaped_identifiers_are_a_keyed_derangement` (full CVV / one-digit / two-letter spaces
  are fixed-point-free permutations; CUST- ids and sort codes distinct). Two e-mails in one
  record now collide with probability 1/29,997 (4-digit suffix × 3 domains), so
  `test_value_consistency_and_never_identity`'s `x@y.co` ≠ `z@y.co` assertion can fail about
  once in 30,000 runs (random seed per run).

**B2 — non-ASCII originals came back verbatim.** The generic letter path replaced ASCII letters
only, so `राम`, `ПАСПОРТ` reproduced themselves on every attempt and the fallback returned
`राम1` / `ПАСПОРТ1` (strict presets: `VerificationFailed`; `leakage_check: warn`: the original
in the output); `Müller` kept its `ü`. Fix:

- Every letter is replaced, in its own script and case: the input is NFKD-decomposed, base
  letters are mapped within their script's alphabet (ASCII Latin, Greek, Cyrillic, Armenian,
  Georgian, Hebrew, Arabic incl. Persian/Urdu letters, Devanagari, Bengali, Gurmukhi, Gujarati,
  Oriya, Tamil, Telugu, Kannada, Malayalam, Sinhala, Thai, Hiragana, Katakana, CJK, Hangul jamo),
  combining marks (accents, vowel signs) are kept and the result is NFC-recomposed
  (`Müller` → `Xäqwer`, Hangul jamo recompose into syllables). The alphabets are filtered with
  the frozen `unicodedata.ucd_3_2_0`, so they do not shift with Python's Unicode version.
  Letters outside them (`ß`, `ł`, other scripts) are drawn per value from the same script's
  alphabet, or ASCII. `_reshape_hex` (device / cookie ids) now replaces non-ASCII letters too.
- The exhausted fallback is a seeded hex token of the original's length that does not reveal
  it (`_last_resort`); `original + "1"` (`_perturb`) is gone.
- Tests: `test_non_ascii_letters_are_replaced_in_their_own_script` (10 originals: Devanagari,
  Cyrillic, accented Latin, Greek, Arabic, CJK, Hangul, Tamil, Polish, a single `Ё`),
  `test_accented_and_unindexed_latin_letters_are_replaced_not_kept`,
  `test_the_last_resort_never_contains_the_original`,
  `test_the_derangement_is_a_permutation_without_fixed_points` (8 radix shapes).

Service level (`tests/test_service.py`): `test_a_caller_held_seed_maps_each_value_the_same_in_any_request`
(300 declared 3-digit refs: same fake reversed and alone, all distinct) and
`test_strict_synthesis_replaces_values_in_any_script` (`synthetic-strict` on `राम`, `ПАСПОРТ`,
`Müller` passes verification and returns none of them). Both fail against the previous
`surrogates.py`. Cost: ~20 µs per reshaped value (was ~10 µs; CVVs ~17 µs, was ~28 µs).

Still open: N1 (a surrogate can equal *another* record's original in a dense space, so a large
strict request can fail closed; a derangement does not change that rate, and an avoid-set would
make the mapping request-dependent again) and N2 (persona-derived fields depend on the anchor).

### Task 19 — fix round 2 (G4 review r2: B1 long-value mixing, B2 run time, B3 letters kept next to digits)

This round supersedes, in part, fix round 1 above: the single-domain derangement, the
`dps.synth.reshape.v1` key label, and "digits only when the value has digits".

**B1: long values were barely mixed.** `Derangement` capped its BLAKE2b round output at 64
bytes. When a Feistel half was wider than 512 bits, the round value moved only the low-order
part of the half, so the leading characters of each half survived every round. This started
above ~218 Latin letters, ~70 CJK characters or ~600 digits. A 400-letter value kept 186
positions, including a verbatim 91-letter prefix. An 80-word declared field came back with 42
words unchanged, and verification passed.

Fix (in `reshape.py`): the reshaper deranges the replaced positions in consecutive **blocks**
of at most ~200 bits.

- **Block size.** The width of a position is counted in whole bits (`ceil(log2 radix)`), as
  integers, so the block boundaries are the same on every platform. The blocks are balanced:
  about 38 letters, 48 digits or 12 ideographs each.
- **Keys.** Each block has its own key, `HMAC(shape key, block number)`. The shape key is the
  round 1 key with the label `dps.synth.reshape.v2`. The label changed because every reshaped
  output changes with this round. Seeds stay valid; only the mapping changed, before any
  release.
- **Full coverage.** Every round output of a block is 64 bits wider than its half, so every
  position is re-randomised.
- **Guarantees kept.** The product of the block permutations is still a bijection of the shape,
  and every block changes (no fixed point). So "never the original" and "distinct stays
  distinct" still hold, and are stronger: no block of a long value survives.
- **Guard.** `Derangement` now refuses a half wider than 448 bits (`ValueError`) instead of
  mixing it silently, and its split search uses prefix products.
- **Cache.** The reshaper caches the block ciphers per shape, for up to 4,096 shapes of up to 8
  blocks each, so free text does not grow the cache without bound.

**B2: run time was roughly cubic in value length** (8,000 letters took 20 s;
`synthesize({"customer_id": <6,000 letters>}, "synthetic-realistic")` took 8.8 s on the event
loop). The blocks above make the cost linear. On this machine, 100,000 characters now take
0.09 s for letters, 0.07 s for digits and 0.15 s for CJK. Through `PrivacyEngine.synthesize`
under `synthetic-realistic`, a 20,000-letter `customer_id` takes 0.03 s and a 100,000-letter one
0.13 s.

**B3: values with digits kept every letter** (inherited from the plan's generic path). Under
`synthetic-strict`, an MRZ kept the holder's name. A crypto address kept 26 of 34 characters.
A declared free-text field kept every name. Verification passed in all three cases.

- **MRZ** has a dedicated generator (`formats.py` with `SurrogateEngine._mrz`). It recognises
  the ICAO 9303 layouts TD1 (3×30), TD2 (2×36), TD3 (2×44), MRV-A and MRV-B, with lines split
  by any whitespace or not split at all. It rebuilds the MRZ field by field:
  - **Holder:** the record's persona as `SURNAME<<GIVEN`. If a persona shares a name part with
    the original holder (Faker `en_IN` has "Krishnan"), the generator tries the next persona.
  - **Document number and optional data:** fresh, in the original's letter/digit shape.
  - **Birth date:** the persona's birth date in the original's five-year band, the same date
    as the record's `DATE_OF_BIRTH` surrogate.
  - **Expiry date:** shifted by 1 to 730 days.
  - **Sex:** the persona's.
  - **Kept as they are:** the document code, the issuing state and the nationality.
  - **Check digits:** every check digit the original carries is recomputed.

  An MRZ that matches no ICAO layout has every letter and digit replaced, keeping only the `<`
  fillers and the first five characters (document code and issuing state).
- **CRYPTO_ADDRESS** has a dedicated generator. Bitcoin base58check addresses (P2PKH, P2SH,
  testnet) and bech32/bech32m addresses (SegWit v0, Taproot) become fresh **valid** addresses
  of the same kind, with the same prefix and length. Ethereum addresses become fresh lower-case
  hex, which is valid but has no EIP-55 checksum because hashlib has no Keccak. Any other format
  has every letter and digit replaced.
- **Generic path** (identifiers of unmapped types): letters are now replaced as well as digits.
  The only letters kept are short codes, as decided by `reshape.code_letters`:
  - **Kept:** in a value with at least as many digits as letters, a run of at most 4 ASCII
    letters in one case (`CUST-884120`, `MH14AB1234`, `K1234567`, `cust-1`).
  - **Replaced:** a mixed-case word (`Ravi 12345678`), a longer run (`KRISHNAN-1234`), a letter
    of another script (`山田 12345678`), and every letter of a letter-dominated value (free
    text, `REF-ANANYA-77`).
  - **Consistency:** `CUST-884120` and `cust-884120` still share their digits.
- **Related changes:**
  - `IFSC` now keeps the bank code and the reserved `0` and replaces branch letters as well as
    digits. This also fixes review note N9: an all-letter branch code no longer falls through
    to the hex token.
  - `AMOUNT` keeps its currency code or symbol (`INR`, `Rs.`), as before.
  - `reshape.has_digits` is removed: nothing uses it any more.

**Tests.** All 10 new tests fail on the previous code. The two timing tests fail by running
past a 60 s and a 90 s timeout.

In `tests/test_surrogates.py`:

- `test_long_values_are_mixed_in_every_position`: 1,000 letters, 1,000 digits and 300 CJK
  characters share no more than the chance rate of positions and no verbatim run.
- `test_every_block_of_a_long_value_changes_and_distinct_values_stay_distinct`
- `test_a_derangement_refuses_a_domain_wider_than_its_round_output`
- `test_reshaping_time_is_linear_in_the_value_length`: 100,000 characters in under 1 s, for
  letters, digits and a code mix.
- `test_mrz_surrogates_carry_the_persona_and_valid_check_digits`
- `test_td1_td2_and_unknown_mrz_layouts`
- `test_crypto_addresses_are_replaced_and_stay_valid`: checked with reference base58check and
  BIP-173/350 checksum code written in the test.
- `test_digit_bearing_values_keep_only_short_codes`

In `tests/test_service.py`:

- `test_strict_synthesis_replaces_names_in_mrz_crypto_and_digit_bearing_fields`: under
  `synthetic-strict`, the MRZ contains neither name part, the wallet keeps under 1/3 of its
  characters, the note keeps none of its names, and verification passes.
- `test_long_values_are_replaced_quickly_and_completely`: a 20,000-letter `customer_id`
  completes in under 3 s and keeps no more than the chance rate of positions; an 80-word
  declared field keeps at most 2 words.

Still open:

- An MRZ holder can differ from the record's `PERSON_NAME` surrogate in one case: when the
  record's persona shares a name part with the original holder. The MRZ then moves to the next
  persona, and `PERSON_NAME` does not.
- A short upper-case name inside a digit-dominated identifier (`RAVI 12345678`) still reads as
  a code and stays.

## Task 23 — Auth, rate limiting and audit

Public names as planned: `AuthError`, `Principal`, `KeyRecord`, `KeyStore`, `StaticKeyStore`
(`from_settings`, `lookup`, `__len__`), `hash_key`, `new_api_key`, `authenticate`,
`resolve_tenant`, `current_principal`, `require_scope`, `LOCAL_PRINCIPAL`, `API_KEY_HEADER`,
`TENANT_HEADER`, `SCOPES`; `RateLimiter(rate, burst, *, enabled, clock)`, `enforce_rate_limit`;
`AuditEvent`, `AuditSink`, `LogAuditSink`, `MemoryAuditSink`. The plan's 5 tests are unchanged
(reformatted only). Changes:

- `dps/auth.py` — **no implicit wildcard grants.** The plan defaulted a record's `tenant_ids`
  and `scopes` to `["*"]` and ignored unknown fields, so a record with a typo
  (`"scope": ["detect"]`) or without grants was silently granted every tenant and every scope,
  including `reveal` and `admin`. `KeyRecord` now requires both (non-empty) and forbids extra
  fields; the bootstrap key states its wildcard explicitly. The documented record shape
  (`{name, key|key_sha256, tenant_ids, scopes}` in `config.py` and the plan's HANDOFF §3)
  already lists both.
- `dps/auth.py` — **errors never echo a key.** `KeyRecord` sets `hide_input_in_errors=True`
  (a pydantic error on a record quoted `input_value={'key': 'dps_…'}`); `from_settings` maps a
  JSON error to `DPS_API_KEYS is not valid JSON (<msg> at line L column C)` and a record error
  to `DPS_API_KEYS record <i>: <field>: <rule>` (built from `errors(include_input=False)`),
  both raised `from None`; a non-list is refused.
- `dps/auth.py` — `key_sha256` must be 64 hex characters and is lower-cased (an upper-case
  digest never matched, silently); unknown scopes are refused naming the scope (vocabulary, not
  a secret); tenant ids in records must be identifiers; two records with the same key are
  refused naming both records (the plan let the second silently replace the first).
- `dps/auth.py` — **key ids** are `static-<first 12 hex of sha256(key)>` instead of
  `static-<index>-<8 hex>`, so a key keeps its id (and its audit trail and rate-limit bucket)
  when the record list is reordered or edited.
- `dps/auth.py` — **tenant header shape.** `resolve_tenant` answers 400 when `X-DPS-Tenant` is
  not an identifier (`[A-Za-z0-9][A-Za-z0-9_.-]{0,63}`), before the grant check. A wildcard key
  previously accepted any string as its tenant, and that string went verbatim into the audit log
  (a name, an account number or a newline could be smuggled into an id field). The detail does
  not quote the header. An empty header counts as absent.
- `dps/auth.py` — rejections are logged value-free (`auth_rejected` with status code, reason
  `key|scope|tenant` and route; never the key); `require_scope` is typed
  (`Callable[[Request], Principal]`) instead of `noqa: ANN201`, and refuses an unknown scope at
  import time; `HTTPException`s are raised `from None`.
- `dps/ratelimit.py` — an enabled limiter refuses `rate <= 0` or `burst < 1` (a zero rate
  divided by zero / locked every key out forever); `Retry-After` is `ceil(1/rate)` (at least 1)
  instead of a constant 1; buckets that have refilled are pruned once 10,000 keys are tracked
  (the per-IP fallback could otherwise grow without bound); a backwards clock never adds
  tokens; a rejection is logged (`rate_limited`, key id and route).
- `dps/audit.py` — `AuditEvent` forbids extra fields (nothing value-bearing can be attached by
  accident); new `NullAuditSink` so `create_app` can honour `DPS_AUDIT_ENABLED=false` (local
  only — the posture guard refuses it in staging/prod).

Tests added (8): `test_key_records_never_echo_a_key_in_errors` (7 bad records),
`test_hashed_keys_are_normalised_and_unknown_scopes_listed`,
`test_duplicate_keys_are_refused_naming_records_not_keys`,
`test_api_keys_setting_errors_are_value_free`, `test_key_ids_are_stable_and_do_not_reveal_the_key`,
`test_tenant_ids_from_headers_are_checked_for_shape`,
`test_rate_limiter_isolates_keys_and_never_exceeds_burst`,
`test_log_audit_sink_writes_every_field_and_no_value`. Against the plan's three modules, 6 of the
8 fail; the plan's 5 pass on both. Count: 13 in the file; suite 415 passed, 1 skipped.

## Task 24 — Layer 1 API, routers, app factory

Public names as planned: `create_app(settings=None, *, engine=None, audit_sink=None)`, `CSP`,
`FRONTEND_DIST`; `dps/layers/api/schemas.py` (`EngineRequest`, `PlanRequest`, `RevealRequest`,
`ValidatePolicyRequest`, `DetectOut`, `PlanOut`, `TransformOut`, `RevealOut`, `PolicyOut`);
`dps/layers/api/common.py` (`trace_id`, `payload_from`, `run_engine`, `TRACE_HEADER`); routers
`ops`, `detect`, `transform`, `policies` with the planned paths, scopes and response models.
Endpoints: `GET /health`, `/readyz`, `/metrics`; `POST /api/v1/{detect,obfuscate,synthesize,
reveal}`; `GET /api/v1/policies/presets`; `POST /api/v1/policies/{validate,plan}`;
`/api/v1/openapi.json`, `/docs`; SPA at `/`. Changes:

- `tests/test_api.py` — the fixture enters `with TestClient(app) as c:` and yields (the plan's
  own Step 6 note: Starlette runs the lifespan, and so sets readiness, only in a context
  manager). The plan's 5 tests are otherwise unchanged (reformatted).
- **422 bodies never echo the request.** FastAPI's default handler returns every error's
  `input` (and `ctx`): a reveal without `key_material` answered with the whole document, and a
  bad `options.consistency` echoed the caller's DEK. `create_app` installs a handler that
  returns `{type, loc, msg}` per error only; request schemas also set `hide_input_in_errors`.
- **Unexpected exceptions never reach the server log with their message.** Starlette's error
  middleware answers 500 and re-raises, and uvicorn then logs the traceback — e.g. a
  `ResponseValidationError`, whose text quotes the response (on `/reveal`: the revealed
  originals). New innermost middleware `_ValueFreeErrors` answers `{"detail": "internal
  error"}` and logs `unhandled_error` with the class name and route template only.
- **Body limit also applies to streamed bodies.** The plan checked `Content-Length` only, so a
  chunked request of any size reached the JSON parser. `_BodyLimit` is now pure ASGI: 413 up
  front by header, or while receiving (it raises `HTTPException(413)`, which FastAPI re-raises
  from body parsing). `_SecurityHeaders` is pure ASGI too (headers also land on 401/404/413/500).
- `common.run_engine(request, *, mode, policy, call)` — the plan's `policy_name` is `policy`
  and takes a **value-free label** from new `policy_label()`: a preset name, `unknown`,
  `<preset>+overrides` or `inline` (a successful inline policy's own name is used when it is an
  identifier). The plan audited `str(body.policy)`: an inline policy's whole dict (deny lists,
  dictionary values) or any string the caller sent as a preset name went into the audit log.
- `run_engine` takes `call: Callable[[], Awaitable[T]]` (PEP 695 generic; the plan's
  `Awaitable[T] | T` union did not type-check cleanly). Each router's `call` validates the
  payload and runs the engine, so 413/422 **rejections are counted and audited**
  (`outcome="rejected"`); in the plan `payload_from` ran before `run_engine` and the
  `except HTTPException` branch was dead. Mapped exceptions are raised `from None`; the 500
  path logs the class name; `locals().get("result")` became an explicit variable; building or
  writing the audit event can never fail the request (`audit_write_failed`, type only).
- **Sync engine calls run in Starlette's threadpool** (`detect`, `obfuscate`, `reveal`,
  `plan`). The plan ran CPU-bound engine work on the event loop, so one large request stalled
  `/health` and `/readyz` (liveness restarts under load). The engine is thread-safe for this
  (per-request detectors and surrogate engines, per-thread Faker, NER lock); structlog
  contextvars are copied into the worker (anyio 4). `synthesize` stays on the loop (async).
- `payload_from` — a fixed 422 message instead of `str(ValidationError)`; values are counted
  iteratively with an early exit at `max_items + 1` (the plan built the whole `flatten` list);
  documents nesting deeper than `MAX_DEPTH = 100` are a 422. Measured: the engine's `obfuscate`
  and `synthesize` raise `RecursionError` between 400 and 600 levels (a 500 in the plan).
- `X-Request-ID` is used as the trace id only when it is an identifier
  (`[A-Za-z0-9][A-Za-z0-9._-]{0,63}`); otherwise a fresh id (it is logged and audited).
- The `plan:` audit record counts findings from the result (it has no report).
- `presets` and `validate` also take the rate-limit dependency.
- `create_app`: readiness reports **the engine's gateway** (the plan built a second gateway from
  settings, so an injected engine's gateway was misreported); `api-core` turns false on
  shutdown; the key store is parsed before the app is built (bad `DPS_API_KEYS` fails the boot,
  value-free); `auth_no_keys` warning when auth is on and no key is configured;
  `NullAuditSink` when `DPS_AUDIT_ENABLED=false` and no sink is injected; ReDoc disabled; the
  SPA catch-all uses `Path.is_relative_to` on the resolved dist and is typed
  (`FileResponse | JSONResponse`) instead of `noqa: ANN202`.

Tests added (12): `test_request_validation_errors_never_echo_the_body`,
`test_trace_ids_are_taken_from_the_header_only_when_safe`,
`test_rejections_and_engine_errors_are_audited_value_free`, `test_tenant_header_rules_over_http`,
`test_rate_limit_answers_429_with_retry_after`, `test_body_limit_by_header_and_by_stream`,
`test_deep_documents_are_refused_not_crashed`,
`test_unexpected_errors_are_500_and_never_reach_the_log_with_values`,
`test_spa_mount_serves_index_with_csp_and_refuses_traversal`,
`test_hsts_outside_local_audit_switch_and_openapi`,
`test_readiness_reflects_the_injected_engine_and_posture_refuses_unsafe_config`,
`test_a_failing_audit_sink_never_fails_the_request`. Against the plan's Task 24 code (with
Task 23 as built) 8 of the first 11 fail; the tenant, rate-limit and SPA tests pass on both
(regression guards). Count: 17 in the file; suite 432 passed, 1 skipped.

Live check (uvicorn `--factory`, `DPS_ENV=dev`, bootstrap key, stub LLM): every endpoint
answered as expected (JSON and text obfuscate → reveal round trips, synthesize, plan, validate,
401, 404, 413 at 9 MB, 422 without input, 53 × 429 after the burst), HSTS present; 105 audit
lines and the whole server log contain no sample value and no key. With `DPS_FPE_ENABLED=true`
the FF1 presets round-trip over HTTP.

Open: CORS stays `allow_origins=["*"]` as planned (keys are headers, not ambient cookies;
restrict with a setting in M5). Uvicorn's access log and httpx's `HTTP Request:` lines are
stdlib logging and bypass the structlog allow-list (no values today: the API takes none in
paths or queries) — Task 27 should run uvicorn with `--no-access-log` or route stdlib records
through the allow-list. Auth failures are logged (`auth_rejected`) but not audit events (no
tenant yet; M2's audit table). Spec §6's `Idempotency-Key` and NDJSON streaming are not in M0.
