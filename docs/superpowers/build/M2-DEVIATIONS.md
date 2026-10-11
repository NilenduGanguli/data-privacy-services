# M2 build — deviations from the plan

Plan: `docs/superpowers/plans/2026-10-11-m2-jobs-and-postgres.md`. Contract:
`docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (incl. the 2026-10-12 lane-safe amendment).
One section per task: file, what changed, why. Contract names are preserved unless a section says
otherwise.

## Task 1 — Dependencies, markers, M2 settings and posture

- `pyproject.toml`: the `db`, `pg_source` and `s3` marker descriptions name the port variables
  (`127.0.0.1:${DPS_DB_PORT:-5441}` …) instead of literal host ports. Why: contract §0 amendment
  2026-10-12 (lane-safe compose) supersedes literal host ports anywhere in a plan. The `dev` extra
  is written one entry per line (formatting only).
- `.env.example`: **not modified.** The session's permission rules deny every read and write of
  `.env*` files in this worktree (`cat`, `Read` and `tail` on `.env.example` were refused), so the
  plan's commented M2 block could not be appended safely. The same variables (with the KEK
  generation command and the compose role names) are documented in `HANDOFF.md` §4 "M2 settings"
  instead. Open issue: append the plan's block to `.env.example` from a session that may edit it.
- `HANDOFF.md`: §4 gains the M2 settings table, §6 the M2 posture checks and §7 the M2 secret names.
  Why: contract §0.6 (HANDOFF changes in the same commit as any config change); the plan leaves the
  full pass to Task 38.
- `.dockerignore`: `var` added beside `data`. Why: `var/` holds local artefacts, exports and secret
  directories (`DPS_SECRET_DIR=./var/secrets`); keeping it out of the build context means a local
  secret can never reach an image layer. Same reason as the plan's `.gitignore` entry.
- `dps/config.py`: the allowed values are module constants (`MIGRATIONS_MODES`, `ARTEFACT_STORES`)
  and the KEK error text is one constant; behaviour, names and messages are the plan's.
- `tests/test_config_m2.py`, `tests/test_posture_m2.py`: `test_kek_from_file` and
  `test_bad_m2_production_is_refused` clear `DPS_JOB_KEK` first (`monkeypatch.delenv`), so a KEK
  exported in the developer's shell cannot flip their result; lines are wrapped to 100 characters.
  Assertions unchanged.

## Task 2 — Ids and sealing

- `dps/secrets.py`: `seal` refuses a KEK that is not 32 bytes (`ValueError("the job KEK must be 32
  bytes")`, no detail). Why: `AESGCM` also accepts 16- and 24-byte keys, so a short key would have
  sealed silently with AES-128/192 while `open_sealed` (which already checks 32 bytes) could never
  open it. Test added: `test_seal_refuses_a_kek_that_is_not_32_bytes` (17 tests, plan: 16).
- `dps/secrets.py`: `open_json` uses PEP 695 type parameters (`def open_json[M: BaseModel](…)`) instead of a
  module-level `TypeVar` (ruff UP047 under `target-version = "py312"`); same signature for callers.
- `dps/secrets.py`: `SECRET_FILES` is documented and laid out one name per line; slices formatted by
  `ruff format`. Behaviour as planned.
