# Data Privacy Services (DPS)

Data obfuscation and synthetic data generation for the enterprise — one Privacy Engine, three entry
points (API, database connectors, UI scanner), two outcomes (reversible obfuscation, synthetic data),
measurable privacy, and a hard rule that **no LLM ever sees raw PII**.

**Status:** M0 shipped — Layer 1 API (`/api/v1/detect`, `/obfuscate`, `/synthesize`, `/reveal`, policy
tooling) and the Privacy Console (Overview + Playground), offline, in one container.

| Read | For |
|---|---|
| [`docs/specs/2026-10-11-data-privacy-services-design.md`](docs/specs/2026-10-11-data-privacy-services-design.md) | the design (§12 lists the open decisions) |
| [`HANDOFF.md`](HANDOFF.md) | build, run, configuration, probes, traps |
| [`docs/technical/api-m0.md`](docs/technical/api-m0.md) | the Layer 1 API reference |
| [`docs/design/mockups/index.html`](docs/design/mockups/index.html) | the console mockups (M0 builds Overview and Playground) |
| [`docs/superpowers/plans/`](docs/superpowers/plans/) | the milestone plans (M0–M5) and the M1–M5 contract |

## Run it

```bash
docker compose up --build -d         # console + API on http://127.0.0.1:8400 (key: dps_local_dev_key_change_me)
python tools/smoke_test.py           # 13 end-to-end checks (needs the dev install below for httpx)
docker compose down
```

Port 8400 taken? `DPS_APP_PORT=8401 docker compose up -d` and `DPS_APP_PORT=8401 python tools/smoke_test.py`.
Open the console, paste the key into the header, and try the Playground: Obfuscate → Reveal with key,
or Synthesize.

## Develop

```bash
uv venv --python 3.12 .venv && source .venv/bin/activate && uv pip install -e ".[dev,fpe]"
pytest -q && ruff check dps tests tools && mypy dps
DPS_AUTH_ENABLED=false DPS_LLM_PROVIDER=stub uvicorn dps.app:create_app --factory --port 8400
cd frontend && npm ci && npm run dev             # console on :5174, API calls proxied to :8400
```

`npm run build` writes `frontend/dist`, which the API process serves at `/` when it exists.
Optional extras: `ner` (Presidio + spaCy, higher recall on names and places), `fpe` (FF1
format-preserving encryption, used only with `DPS_FPE_ENABLED=true`), `vertex` (laptop path to
Gemini). Production goes through the Stellar gateway — see `HANDOFF.md`.

## How it works

detect (structural key names + patterns/checksums + optional NER + client declarations) → plan
(policy → one method per finding, with a reason) → transform (AES-SIV tokens, FF1, keyed date shift,
surrogates…) → verify (no original value survives in the output) → report (counts, methods,
fallbacks, verification — never a value). Keys and synthesis seeds are returned to the caller and
never stored. Synthetic text goes through the LLM as typed placeholders only.
