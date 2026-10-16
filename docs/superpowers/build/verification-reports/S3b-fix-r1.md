# S3b fix round 1 — B1: the L3 labelling prompt's label is value-free whatever its source

**Status: B1 fixed, gates green, committed on `fsec` as `3d257a3`.**

Worktree `/Users/neelu/document_intelligence/dps-wt-fsec`, branch `fsec`, from `fee0041` (clean at
start; no partial state from an earlier attempt). Review: `~/.cache/dps-build/reports/S3b-review-r1.md`
(B1 blocking; N1–N7 non-blocking, not in scope). Artefacts: `~/.cache/dps-build/reports/S3b-fix-r1/`.

## 1. The defect (B1, P1)

`classify.leftover_field_meta` sent `redact_text(f.label)`: a detector-redacted label. A label
taken from an attribute (`aria-label`, `title`, the `placeholder` fallback) or a hidden
`aria-labelledby` target is in no protected list (`page_protected` covers digit/`@` tokens of
rendered text only), so a record value no detector names reached the provider; with
`label_source == "placeholder"` the raw placeholder was the label. The same number in a rendered
`<label for>` was caught by the leak guard, which then blocked the whole page's call.

## 2. The fix (`dps/layers/scanner/classify.py`)

- `prompt_label(facts, detector)`, used by `leftover_field_meta`:
  - **no label from the `placeholder` fallback** (its shape is already sent);
  - **no label unless the detector runs NER** — `ner_runs(detector)`: an NER adapter, `ner` in the
    policy's tiers, and the adapter `available` (read again after the label was analysed, so an
    NER whose spaCy model failed to load counts as off);
  - else the label **detector-redacted, then every whitespace token still holding a digit or an
    `@` replaced by its `value_shape`** (uncut): `Close loan 77120034519` → `Close loan
    99999999999`. Inside such a token only digit-free redaction tags (`<PERSON_NAME>`; no built-in
    entity type holds a digit) are kept, so a page's own `<A1234567>` is shaped too.
- Why NER is required for every label, not only attribute-sourced ones (the review allowed either,
  or a residual): per-record controls carry their record in a `<label>` wrap, a `<label for>` or an
  `aria-labelledby` table cell as often as in an `aria-label`, and only NER finds a bare name
  (T1's name scan needs a cue). Cost: in the default images (`DPS_NER_ENABLED=false`, no
  `WITH_NER`) the L3 prompt carries no label; fields are still labelled by name/id, type,
  placeholder shape and hints, in the same one call per page. A number in a rendered label no
  longer blocks the page's call (side benefit the review asked for).
- Docstrings: `classify.py` module docstring, `leftover_field_meta`, `llm_label.py` module
  docstring and `FieldMeta.label`.

## 3. Tests (failing first)

New (all 9 red on `fee0041`'s `classify.py`, green on the fix; red run captured by swapping the
previous `classify.py` in and restoring it byte-identical, `cmp` checked):

| Test | What it proves |
|---|---|
| `tests/test_llm_label_l2_l3.py::test_l3_labels_are_value_free_whatever_their_source[False/True]` | NER on (fake T2 that finds the made-up name): labels from `aria-label`, `title`, hidden `aria-labelledby` (and a rendered `<label for>`) reach the prompt as `Close loan 99999999999`, `Nominee <PERSON_NAME> agrees`; the placeholder field has no label, only its shape; none of the 5 numbers or the name parts in the messages the provider received; one call; `{calls 1, raw_values 0, blocked 0}`. On the old code: the attribute variant leaked the values, the rendered variant made 0 calls (leak guard) |
| `...::test_l3_labels_are_sent_only_when_ner_runs[off/failed_to_load]` | no label at all without NER or with an NER whose model failed to load; the call is still made; no value sent |
| `...::test_a_label_value_token_keeps_its_redaction_tags` | `Ack (<PERSON_NAME>),x.x@x ref 9x`; a page's `<A1234567>` → `<X9999999>` |
| `tests/scanner/test_crawler_llm_label.py::test_record_values_in_labels_never_reach_the_model[ner × /attrs, /loans]` | end to end: real Chromium, `ScanRunner`, a local page whose labels come from `aria-label`, `placeholder`, `title`, a hidden `aria-labelledby` target (`/attrs`) plus a rendered `<label for>` (`/loans`); `dom.py` resolves those label sources; the provider (faked at `_stellar`, real `complete()`, `strict`) receives none of the 6 made-up values; `outcome.llm == {calls 1, raw_values 0, blocked 0}`; labels shaped/redacted with NER, absent without; 0 writes reached the site (D4) |

Updated: `test_scanner_labels_a_leftover_field` runs with a (fake) NER so its label is still sent;
`test_a_prompt_that_would_carry_a_page_value_is_never_sent` now plants a name NER does not know in
a label, shown as another field's value (a number can no longer reach a label), and still proves
the leak guard (`leak_guard`, 0 calls). `tests/scanner/helpers.FakeNer` is the T2 stand-in (the
lanes have no spaCy model). Every LLM test fakes the provider at `_stellar`, never `complete()`.

## 4. Proofs on the fix

- Reviewer's probe `S3b-review-r1/probes/l3_label_leak_v2.py` (NER off), unchanged: page `/a`
  (aria-label/placeholder/title) `planted_in_messages_sent` 0 of 4 (was 4 of 4), 1 provider call,
  `{calls 1, raw_values 0, blocked 0}`, prompt keys `data_type, hints, name, ref, shapes`; page
  `/b` (rendered `<label for>`) now makes its call (was 0 calls, `leak_guard`), 0 values sent.
  Variant with a fake NER (`S3b-fix-r1/probes/l3_label_leak_v2_ner.py --ner`): 0 of 4 / 0 of 1
  sent, 1 call per page, `label` key present. 0 POSTs received in every run. Output:
  `S3b-fix-r1/l3-label-probe.out` (booleans and counts only, made-up values).

## 5. Gates (lane `fsec`: dps-db, source-pg, minio, mongo, demo-site, app, worker, browser-worker)

| Gate | Result | Log (`S3b-fix-r1/`) |
|---|---|---|
| `ruff format --check dps tests tools` | 467 files already formatted | `ruff-format.log` |
| `ruff check dps tests tools` | All checks passed | `ruff-check.log` |
| `mypy dps` | no issues, 155 files | `mypy.log` |
| `pytest -q` (full lane suite, on the fix) | **3749 passed, 164 skipped, 0 failed** (review baseline on `fee0041`: 3740 passed, 164 skipped; +9 = the new cases; same 2 pre-existing warnings) | `pytest-full.log` |
| T3 files after the last (docstring) edit: `tests/scanner/test_crawler_llm_label.py tests/scanner/test_scan_llm_metrics.py tests/test_llm_label_l2_l3.py tests/test_llm_egress.py tests/connectors/test_discover_job_llm_label.py` | 43 passed | `pytest-t3-final.log` |

Note: passing those files interleaved by directory (`tests/x.py tests/scanner/a.py tests/y.py
tests/scanner/b.py`) makes pytest report `fixture 'scan_engine' not found` for
`test_scan_llm_metrics.py` (a collection quirk of interleaved directory arguments; grouped, alone
and in the full suite they pass). D4: no browser, guard or proxy code changed; the new browser
test asserts 0 writes, and the full suite's D4 proofs pass. No live smoke: the stack has no LLM
provider, so the live scanner never reaches T3. Stack stopped at the end (`docker compose
--profile '*' stop`, 0 `dps-wt-fsec-*` containers running); other lanes' containers untouched.

## 6. Docs

- `docs/security/2026-10-11-m5-security-review.md` §3.26: the "Metadata only" bullet corrected and
  a new bullet "A field's label is value-free whatever its source" (the defect, the reviewer's
  measurement, the rule now, the proofs); §5's L3 residual rewritten (names NER misses; the
  `name`/`id` attribute as is when identifier-like).
- `docs/superpowers/build/M5-DEVIATIONS.md`: Task 8B's "label as the inventory stores it" points to
  the fix; new section "S3b review r1 fix — B1".
- `HANDOFF.md` (T3 labelling paragraph): the label needs NER on the browser-worker.

## 7. Not changed / for the owner

- Review N1–N7 untouched (non-blocking). Related to N2: a shaped label (`99999999999`) can collide
  with a page token made only of nines (`9999`), which the leak guard then refuses — fail closed,
  functional only, same class as N2.
- Residual (security review §5): a name NER misses in a label is sent (NER recall is the bound);
  a field's `name`/`id` attribute is sent as is when identifier-like (`_PLAIN_NAME`).
- Observation outside B1's scope: the inventory itself stores labels detector-redacted only
  (M4 design), so a record number in an `aria-label` is stored in `dps_scan_field.label`; the same
  `prompt_label`-style shaping could apply there (a storage question, not an LLM one).
- Pushing: not done (owner's auto-push).

## 8. Git

- `3d257a3 fix(fsec): the L3 labelling prompt's field label is value-free whatever its source
  (S3b review r1, B1)` — author and committer NilenduGanguli <nilendu.ganguli@gmail.com>, no
  trailer; explicit pathspec (8 files: `dps/layers/scanner/classify.py`,
  `dps/engine/detect/llm_label.py`, `tests/test_llm_label_l2_l3.py`,
  `tests/scanner/test_crawler_llm_label.py`, `tests/scanner/helpers.py`,
  `docs/security/2026-10-11-m5-security-review.md`, `docs/superpowers/build/M5-DEVIATIONS.md`,
  `HANDOFF.md`). Worktree clean. Not pushed (no autopush run: not a milestone commit).
