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
