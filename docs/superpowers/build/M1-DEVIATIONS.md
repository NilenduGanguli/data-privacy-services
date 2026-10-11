# M1 build — deviations from the plan

Plan: `docs/superpowers/plans/2026-10-11-m1-documents-and-l1-complete.md`.
Contract: `docs/superpowers/plans/2026-10-11-m1-m5-contract.md`.
One section per task: file, what changed, why. Public names and signatures that later tasks rely on
are preserved unless a section says otherwise.

## Task 1 — Dependencies, M1 settings, OCR posture, test service discovery

- `.env.example`: **not changed in this commit.** The build session's permission rules deny every
  tool access to `.env*` paths (the file could be read only through `git show HEAD:.env.example`, and
  neither an edit nor an append was allowed), so the M1 block could not be written. Follow-up for
  whoever holds that permission — append, in the lane-safe form of contract §0 (python-dotenv 1.x and
  compose both expand `${VAR:-default}` in a `.env`; checked with python-dotenv 1.0.1):

  ```bash
  # Documents (M1). OCR goes through the Azure Read v3.2 contract: the compose mock locally,
  # the org gateway (azure-gateway-api, health path /_gw/health) in prod. The mock's host port is
  # DPS_OCR_PORT (contract port 5016), so a lane that moves it moves this endpoint too.
  DPS_OCR_PROVIDER=azure
  DPS_AZURE_VISION_ENDPOINT=http://127.0.0.1:${DPS_OCR_PORT:-5016}
  DPS_AZURE_VISION_KEY=mock-key
  DPS_OCR_HEALTH_PATH=/health
  DPS_MAX_DOCUMENT_MB=20
  DPS_MAX_DOCUMENT_PAGES=200
  DPS_PDF_RENDER_DPI=200
  DPS_REDACT_IMAGE_FORMAT=jpeg
  DPS_SCHEMA_MAX_ROWS=10000
  ```

  The plan's block shows the literal `5016` with a comment; the contract's lane-safe amendment says a
  literal host port in a `.env` example stands for its variable form, so the block above uses it.
- `dps/posture.py`: the https rule for `DPS_AZURE_VISION_ENDPOINT` is
  `urlparse(endpoint).scheme != "https"` instead of `not endpoint.startswith("https://")`. Why: the
  M0 Stellar/COIN rule in the same function compares the parsed scheme, which is case-insensitive
  (`HTTPS://…` passes there); one rule for every egress URL. An empty endpoint still fails it.
- `dps/config.py`: the plan's trailing comments are reflowed onto the line above each field so the
  file stays within 100 columns; `ocr_configured` is wrapped by `ruff format`. Fields, defaults,
  bounds, validators and properties are exactly the plan's.
- `tests/conftest.py`: `from tests.services import require_service` sits with the module's other
  imports instead of being appended above the fixture (ruff `E402`, module import not at top).
- `HANDOFF.md` (§4 configuration table, §6 posture guard, §7 secrets) and `README.md` (develop install
  `.[dev,fpe,docs]`, the `docs` and `uitest` extras) are updated in the same commit. Why: contract §0.6
  (the Wega rule) — a change to env or extras updates HANDOFF.md in the same commit; the plan's Task 1
  file list omits both.
- Count: the plan expects 12 new test cases; its own tests are 13 (config 8 — of which 6
  parametrised — posture 1, services 4). Suite 588 → 601 passed, 2 skipped (the `ner` extra).
