# Live LLM check (Vertex / Gemini)

`vertex_live_test.py` runs the DPS API in-process (no database: static bootstrap key, Layer 1 only) on
`127.0.0.1:8433` with `DPS_LLM_PROVIDER=vertex`, exercises the three LLM features through HTTP and records every
prompt handed to the provider:

1. gateway smoke (`READY`),
2. T3 metadata labelling (`POST /api/v1/obfuscate` with a policy whose `detect.tiers` include `llm_label`),
3. LLM text rewrite (`POST /api/v1/synthesize`, `options.llm_rewrite=true`, `DPS_LLM_TEXT_REWRITE=true`),
4. generator spec from a sample (`POST /api/v1/schema/generator-spec`, `use_llm=true`) and `schema/synthesize`,

then checks P1 — no raw input value in any prompt — and that no raw value survives in the outputs. All "PII" in it
is invented.

## Run

```bash
# a venv with the extras the check needs (from the repository root)
uv venv --python 3.12 /tmp/dps-live && uv pip install --python /tmp/dps-live/bin/python -e ".[vertex,ner,fpe,docs,tabular]"
uv pip install --python /tmp/dps-live/bin/python "en_core_web_sm @ https://github.com/explosion/spacy-models/releases/download/en_core_web_sm-3.8.0/en_core_web_sm-3.8.0-py3-none-any.whl"
/tmp/dps-live/bin/python -I docs/superpowers/build/live-llm/vertex_live_test.py <service-account-key.json> <gcp-project>
```

The key file stays where its owner keeps it: never copy it into the repository, a container or a log; the script
only passes its path to `GOOGLE_APPLICATION_CREDENTIALS`. Prompts are written next to the script as
`prompts_sent.json` (placeholders and metadata only) — do not commit that file.

## What it found (2026-10-15, gemini-2.5-flash, location `global`)

- **T3 never reached the model under `DPS_LLM_EGRESS_CHECK=strict`**: the labelling system prompt lists the entity
  types and `USERNAME` has a SWIFT BIC's shape (valid ISO country `NA` in positions 5-6), so the egress verifier
  blocked every call. Fixed (`0fd76d0`, then `c7ad14f`: fixed system prompts are not scanned; other system text is).
  The unit tests had overridden `complete()`, above the verifier — fake the provider at `_stellar`/`_vertex`.
- **Without NER** (the default image is built without `WITH_NER=1`) names with no cue ("Priya Nair") are not
  detected, reach the rewrite prompt and survive in the output; with Presidio + `en_core_web_sm` they become
  placeholders. Owner decision pending: build the default image with NER.
- **Deterministic defects surfaced by the run** (not LLM-caused), all fixed on `fllm` and merged: distinct people in
  one record collapsed to one synthetic person; `first_name`/`last_name` columns disagreed; sample-based specs
  renamed columns to `field_N`, typed 4-digit IDs as DATE and collapsed integer ranges; sample synthesis at 100 rows
  failed `422 leakage` 17-19/20 runs (city repeats, ID substrings); the Oracle/MongoDB D-A identifier check read only
  the first 5 000 rows while reporting `whole_table`.
- Gemini at temperature 0.7 sometimes drops a placeholder in the rewrite: DPS rejects it and falls back to the
  deterministic fill (`fallback: placeholders_missing`), which is the designed safe path.
