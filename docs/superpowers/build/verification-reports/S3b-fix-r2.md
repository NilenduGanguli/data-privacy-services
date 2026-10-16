# S3b fix r2 — R2-B1: the L3 prompt's label is redacted whatever the policy's `min_score` (`fsec`)

**Status: FIXED** — commit `6147f85` on `fsec` (worktree `/Users/neelu/document_intelligence/dps-wt-fsec`),
on top of `3d257a3`. Review: `~/.cache/dps-build/reports/S3b-review-r2.md` (1 blocking finding,
R2-B1, P1). Artefacts: `~/.cache/dps-build/reports/S3b-fix-r2/`.

## 1. The defect (as reviewed)

`classify.prompt_label` sent a label whenever `ner_runs(detector)` and redacted it with
`redact_text(label, detector)` — the policy's detector, whose `detect()` drops every finding
below `policy.detect.min_score` (`ensemble.py:89`). T2 scores a name 0.85 (Presidio's spaCy
`default_score`; `FakeNer` the same) and `DetectConfig.min_score` accepts 0–1, so a valid custom
policy above 0.85 (with `ner` + `llm_label` tiers on a `WITH_NER` image) sent every NER-found name
in every label to the provider while `progress.metrics.llm` said `{calls 1, raw_values 0, blocked 0}`.

## 2. Fix (`dps/layers/scanner/classify.py`)

- New `PROMPT_LABEL_MIN_SCORE = 0.0` and `_recall_first(detector)`: a copy of the scanner's
  detector (same `DetectorConfig` but `min_score`, same NER adapter, locale and custom
  recognizers) at score 0 — every span any tier reports. `prompt_label` redacts with it:
  `redact_text(facts.label, _recall_first(detector))`. The caller's detector is not modified
  (a new `Detector` is built only when its `min_score` is above 0; construction is a dict lookup).
- **Why 0 rather than the review's suggested 0.4** (the NER adapter's `score_threshold`):
  `resolve_overlaps` lets a typed hit of a higher sensitivity tier win over an overlapping NER hit
  (`_typed_beats_ner`), and typed hits can score below 0.4 (`NHS_NUMBER` and `SWIFT_BIC` patterns
  at 0.3 with context; custom recognizers from 0). Filtering such a winner at 0.4 would drop the
  name's redaction with it. Lowering the threshold only adds findings (`detect()` filters after
  overlap resolution; a whole-value finding replaces the whole label), so a prompt's label is never
  less redacted than before the change; over-redaction costs label detail only.
- Unchanged by design: the policy's `min_score` still governs what a job transforms, the scan's
  classifications, `page_protected` and the labels the inventory stores (storage, outside P1 — the
  review's note on stored attribute labels stands for the owner).
- Docstrings (module, `prompt_label`) state the recall-first rule.

## 3. Tests — failing first

Written before the fix and run on `3d257a3`'s `classify.py`: **7 failed, 3 passed** (every case
above 0.85 red; the 0.0 / 0.6 controls green). After the fix: all green.

- `tests/test_llm_label_l2_l3.py::test_l3_label_names_are_redacted_whatever_the_policy_min_score`
  `[0.86, 0.9, 1.0]` — the record-labels page through the real `LlmGateway.complete()` under
  `strict`, provider faked at `_stellar` (`FakeGateway`), T2 = `FakeNer` (0.85): no planted value
  and no name part reaches the provider, `nominee_ok` label sent as `Nominee <PERSON_NAME> agrees`,
  exactly one call, `{calls 1, raw_values 0, blocked 0}` (`_label_record_page` gained a
  `min_score` keyword).
- `tests/test_llm_label_l2_l3.py::test_prompt_label_is_redacted_recall_first`
  `[0.0, 0.6, 0.86, 0.9, 1.0]` — a `nearest_text` label that is only a name → `<PERSON_NAME>`; a
  `title` label → `Nominee <PERSON_NAME> agrees`; the caller's detector keeps its `min_score`.
- `tests/scanner/test_crawler_llm_label.py::test_names_in_labels_never_reach_the_model_whatever_the_policy_min_score`
  `[0.6, 0.9]` (browser, slow) — real Chromium + `ScanRunner`, the reviewer's nominees table
  (per-row checkboxes labelled by the neighbouring name cell, `nearest_text`) served by a local
  counting server: both labels sent as `<PERSON_NAME>`, 0 name parts in the provider's messages,
  `outcome.llm == {calls 1, raw_values 0, blocked 0}`, one call, `writes == []` (D4).

## 4. Gates (lane `fsec` stack: dps-db, source-pg, minio, mongo, demo-site, app, worker, browser-worker)

| Gate | Result | Log (`S3b-fix-r2/`) |
|---|---|---|
| `ruff format --check dps tests tools` | 467 files already formatted | `ruff-format.log` |
| `ruff check dps tests tools` | All checks passed | `ruff-check.log` |
| `mypy dps` | Success: no issues found in 155 source files | `mypy.log` |
| `pytest -q` (full lane suite) | **3759 passed, 164 skipped, 0 failed** (18 min 51 s; r2 on `3d257a3`: 3749 + 164 — the +10 are the new cases, all `passed` in the junit XML incl. both Chromium ones; same 164 skips, same 2 pre-existing warnings) | `pytest-full.log`, `pytest-full.xml` |
| `tests/test_llm_label_l2_l3.py` + `tests/scanner/test_crawler_llm_label.py` (incl. demo-site crawls) | 35 passed | — |

## 5. The review's probes, re-run on the fix

| Probe | Review r2 (on `3d257a3`) | Now | Output |
|---|---|---|---|
| `l3_min_score_probe.py` (real `complete()`, `strict`) | name parts sent at 0.86 `[true,true]`, 0.9 `[true,true]` | `[false,false]` at 0.6 / 0.85 / 0.86 / 0.9; 1 call each, label sent | `l3-min-score.out` |
| `l3_min_score_table_e2e.py` (Chromium, `nearest_text` table) | 0.9: 2 of 2 names sent | 0.6 and 0.9: 0 of 2 names sent, 2 labels sent, `{calls 1, raw_values 0, blocked 0}`, 0 POSTs | `l3-min-score-table-e2e.out` |
| `l3_label_adversarial.py` (r1 regression check, NER off / `--ner`) | 0 unexpected values; residuals as documented | identical: NER off 0 labels / 0 unexpected; NER on 12 labels / 0 unexpected, same documented residuals (`unknown_name`, `letters_ref`, `name_attr_name`); 0 POSTs | `l3-label-adversarial.out` |

Probes print booleans and counts only; all values are made up.

## 6. Docs

- `docs/security/2026-10-11-m5-security-review.md` §3.26: the label bullet states the recall-first
  rule (`PROMPT_LABEL_MIN_SCORE = 0`, the policy's `min_score` does not apply to a prompt's label);
  new paragraph "No threshold dependency" (the defect, why 0 not 0.4, proofs).
- `docs/superpowers/build/M5-DEVIATIONS.md`: new section "S3b review r2 fix — R2-B1".
- `HANDOFF.md` §17.1: the scanned label is redacted of every span any tier reports, at any score.

## 7. Not changed / open (owner's)

- Stored labels in the inventory (`inventory.py` → `redact_text(f.label, detector)`) and
  `page_protected` still follow the policy's `min_score` (storage / the egress verifier's protected
  list; outside P1 for this finding; the reviewer's storage note stands).
- r1 N1–N7 and r2 N8 (`_PLAIN_NAME` lets a grouped number in a `name`/`id` through) and N9
  (`\d`/`@` value-token coverage) stay open as non-blocking.
- No image rebuild / live smoke this round: no network, guard, proxy or browser code changed; D4
  for the new path is proven by the counting server in the new end-to-end test (`writes == []`).

## 8. Git

`6147f85` `fix(fsec): the L3 prompt's field label is redacted recall-first, whatever the policy's min_score (S3b review r2, R2-B1)` authored and committed by NilenduGanguli
<nilendu.ganguli@gmail.com>, no trailer; explicit 6-file pathspec (`dps/layers/scanner/classify.py`,
`tests/test_llm_label_l2_l3.py`, `tests/scanner/test_crawler_llm_label.py`,
`docs/security/2026-10-11-m5-security-review.md`, `docs/superpowers/build/M5-DEVIATIONS.md`,
`HANDOFF.md`). Not pushed (fsec is a lane branch; autopush is run from the main checkout after
milestone commits only). Lane stack stopped at the end (`docker compose --profile '*' stop`).
