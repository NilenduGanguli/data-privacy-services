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
