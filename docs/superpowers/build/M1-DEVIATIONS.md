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

## Task 2 — Coded errors and value-free policy labels in the API layer

- `dps/layers/api/common.py` `policy_label` (name collision with M0 as built): M0 already had a
  `policy_label(ref, default)` that every M0 router calls (`detect.py`, `transform.py`,
  `policies.py`), so the routers are **unchanged** and the function is replaced in place with the
  plan's signature and label format. Two rules stay stricter than the plan's snippet, because the
  plan's regex passthrough would audit a caller-typed lowercase string (a policy named `ananya`, or
  `{"preset": "ananya"}`) verbatim, weakening M0's "a name that is no preset is `unknown`" rule:
  - a string policy is audited verbatim only when it is a shipped preset (`preset_names()`); any
    other string with a preset name's shape (`^[a-z0-9][a-z0-9-]{0,63}$`) is `unknown` (M0's label),
    and anything else is `invalid` (the plan's label);
  - an inline policy's label base is its `preset` only when that is a shipped preset, else
    `custom`; the suffix is the plan's `+inline:<sha256(canonical JSON)[:12]>`.
- `dps/layers/api/common.py` `_audit_event`: removed M0's substitution of `report.policy` for the
  `inline` label (and the `_POLICY_LABEL` regex it used). Why: it audited an inline policy's
  caller-chosen `name` whenever that looked like an identifier — part of the inline body, which the
  plan's rule says audit never records. Labels are now computed once, by `policy_label`.
- `dps/layers/api/common.py` `run_engine`: the coded exceptions are raised `from None`, as M0 as
  built does, instead of the plan's `from exc` (no chained engine exception in tracebacks; the
  response and audit are the plan's).
- `tests/test_api.py` (M0 tests changed with their module, contract §0.7):
  `test_rejections_and_engine_errors_are_audited_value_free` now expects `invalid` for the policy
  string `"Ananya Krishnan"`, adds `"ananya"` → `unknown`, uses the inline name `"ananya"` (a name
  M0's label would have audited verbatim) → `custom+inline:…`, and `{"preset": "pci-dss"}` →
  `pci-dss+inline:…`; the test's closing "no `ananya` in any audit event" check covers both.
  `test_run_engine_takes_policy_name_and_sync_or_async_calls` expects the additive
  `"code": "bad_request"`.
- `docs/technical/api-m0.md` (errors carry an additive `code`) and `HANDOFF.md` §5 (the audit
  policy label format) are updated in the same commit; not in the plan's file list.
- Count: `tests/test_api_errors.py` 4 passed; suite 601 → 605 passed, 2 skipped.

## Task 3 — Engine output segments (where each replacement landed)

- `dps/engine/synth/llm_text.py` `placeholder_segments` takes the fill's own replacements and
  per-label methods — `placeholder_segments(text, ph, output, replacements, methods)` — instead of
  the plan's `(text, ph, output, surrogates, locale, anchor)`, which recomputed every slot with
  `surrogates.surrogate(...)`. Why: M0 as built fills placeholders through `_resolve`, which applies
  the **plan's** method per finding (`keep` puts the original back, `redact` writes the mask, a
  surrogate otherwise), and the service always passes the plan. Recomputed surrogates would differ
  from the output at every kept or redacted slot, the rebuild check would fail, and every
  plan-driven synthesis (all document synthesis) would get no segments. `_Fill` gains
  `label_methods` (each label's method) so a segment names the method that actually ran. The
  rebuild safety check ("no segments rather than wrong ones") is kept as the plan wrote it.
- `dps/engine/synth/llm_text.py` `synthesize_text`: M0 as built has several early returns of the
  deterministic text (no rewrite, leak guard, rejected or failed rewrite, invented entities,
  verification), so `info["segments"]` is computed once for the deterministic output and set to
  `[]` only when the model's rewrite is used, instead of the plan's single `if not
  info["llm_used"]` before one final return.
- `tests/test_segments.py`: the plan's `test_ambiguous_templates_yield_no_segments` asserted `[]`
  for `"Template {EMAIL_9} then a.b@example-mail.in"`. Impossible as M0 is built: `_substitute`
  fills only this text's own labels and leaves any other placeholder-shaped string untouched (M0's
  `test_text_that_already_looks_like_a_placeholder_is_kept_and_never_reused`), and labels never
  reuse a placeholder-shaped string already in the text, so the deterministic output always
  rebuilds and its segments are exact (observed: one correct `EMAIL` segment). Replaced by
  `test_placeholder_shaped_text_keeps_exact_segments` (the segment is exact and `{EMAIL_9}`
  survives) and `test_placeholder_segments_refuse_an_output_they_cannot_rebuild` (the safety check,
  called directly with an output that differs → `[]`). Added `test_placeholder_segments_follow_the_plan`
  (a `synthetic-realistic` plan: two `keep` and one `surrogate` segment, each slicing the output)
  and `test_a_used_rewrite_has_no_segments`.
- `tests/test_llm_text.py` (M0 test changed with its module, contract §0.7):
  `test_gateway_failures_and_unavailability_fall_back` compared `info` with an exact dict; it now
  pops and checks `segments` first, then compares the rest unchanged.
- `docs/technical/api-m0.md`: documents `options.return_segments` and the additive `segments`
  response field (M0 routes return `"segments": null` unless asked); not in the plan's file list.
- Count: `tests/test_segments.py` 9 passed (the plan's 6, one replaced, three added); suite
  605 → 614 passed, 2 skipped.

## Task 4 — Document models, layout geometry, document metrics and log fields

- `dps/layers/documents/models.py`: `PageBox`, `PageSummary`, `RedactionReport` and `DocumentInfo`
  gained one-line class docstrings (project rule: Google-style docstrings on public names); the
  `PageFinding.out_span` comment sits on one line. Fields, types and defaults are the plan's.
- `dps/layers/documents/errors.py`: the module docstring carries the plan's list of M1 codes and
  statuses, so later tasks find them next to `DocumentError`.
- `dps/observability.py`: the third-party logger caps are a module constant
  (`_THIRD_PARTY_LEVELS`, same loggers and levels as the plan) applied at the end of
  `configure_logging`. Before widening `_SAFE_FIELDS`, an AST scan of every `logger.*(...)` call in
  `dps/` confirmed that no M0 call site passes any of the 18 new field names, so the extension lets
  no M0 value through.
- No `docs`/`ocr`-marked tests exist yet (Tasks 1–4 add none, and compose has no `azure-ocr-mock`
  until Task 19), so the M1 integration gate `pytest -q -m "docs or ocr"` selects nothing at this
  point (630 deselected).
- Count: `tests/documents` 12 passed, `tests/test_observability.py` +2; suite 614 → 628 passed,
  2 skipped.

## Task 5 — PDF normalisation, text layer and serialised rasterisation

- No code deviation: `dps/layers/documents/render.py`, the PDF part of
  `dps/layers/documents/extract.py`, `tests/documents/fixtures.py` and `tests/documents/test_pdf.py`
  are the plan's code (ruff format leaves them unchanged).
- Step 2's expected failure: the run fails with `ModuleNotFoundError: dps.layers.documents.extract`,
  not `…render` as the plan says, because `test_pdf.py` imports `extract` before `render`. Same cause
  (the modules do not exist yet).
- Count: `tests/documents/test_pdf.py` 12 passed (also with `-W error::DeprecationWarning`);
  suite 628 → 640 passed, 2 skipped; `-m "docs or ocr"` selects 12.
