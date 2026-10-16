# S3b review r2 — Task 8B (T3 labelling in L2/L3) and Task 17 S5 (scanner SSRF guard) on `fsec`

**Verdict: FAIL — 1 blocking finding (R2-B1: the fixed L3 label still carries NER-found names to
the provider when the policy's `detect.min_score` is above the NER score, P1).** It is narrow and
cheap to fix (below). Everything else holds: r1's B1 is fixed for every label source and format
tried, the new tests are red on the pre-fix code, gates are green, S5 decisions are unchanged
(118-case corpus), D4 re-proved live on images rebuilt from `3d257a3`.

Reviewer: S3b review round 2, 2026-10-15 23:05–23:36 IST. Worktree
`/Users/neelu/document_intelligence/dps-wt-fsec`, branch `fsec` at `3d257a3` (clean; nothing in
the repository modified by this review). Reviewed: `3d257a3` (fix for r1 B1) in full, and the
whole S3b range `c450a6e..3d257a3` again for P1/SSRF/D4. Inputs: `S3b-impl.md`,
`S3b-review-r1.md`, `S3b-fix-r1.md`. Artefacts: `~/.cache/dps-build/reports/S3b-review-r2/`.

## 1. Gates (lane `fsec` stack: dps-db, source-pg, minio, mongo, demo-site, app, worker, browser-worker; app and browser-worker images rebuilt from `3d257a3` at 23:13, containers verified to run them)

| Gate | Result | Log (`S3b-review-r2/`) |
|---|---|---|
| `ruff format --check dps tests tools` | 467 files already formatted | `ruff-format.log` |
| `ruff check dps tests tools` | All checks passed | `ruff-check.log` |
| `mypy dps` | no issues, 155 files | `mypy.log` |
| `pytest -q` (full lane suite, Chromium, demo site, db, sources) | **3749 passed, 164 skipped, 0 failed** (17 min 09 s; skips: 100 oracle profile, 55 tesseract, 3 OCR mock, 2 mostlyai, 1 pandas, 2 spaCy model, 1 mongo by design) — the fix report's counts; r1 on `fee0041`: 3740 + 164 (+9 new cases) | `pytest-full.log`, `pytest-full.xml` |
| `pytest -q tests/security` (separate run, own `dps_test_<pid>` database) | **278 passed, 0 failed, 0 skipped** (28 s) | `pytest-security.log`, `pytest-security.xml` |
| browser gate (`-m browser`, 245 tests collected) and scanner integration (`tests/scanner`, 832 collected) | covered by the full run and cross-checked by node id against its junit XML (`probes/junit_subset.py`): **browser 186 passed, 59 skipped (53 tesseract, 6 oracle-profile console), 0 failed** of 245 (r1: 182 + 59; +4 new end-to-end cases); **`tests/scanner` 779 passed, 53 skipped (tesseract), 0 failed** of 832; `tests/security` 278/278 in the full run too. Not re-run as separate processes: a second concurrent run against the shared demo site would disturb the D4 counters, and a sequential one repeats the same tests | `collect-browser.txt`, `collect-scanner.txt`, `pytest-full.xml` |
| r1-B1 tests on the pre-fix code (`git archive fee0041` + `3d257a3`'s tests, `dps` imported from the export) | **9/9 red** (the 5 unit cases and the 4 Chromium end-to-end cases) — the tests catch the defect | (scratch run) |
| Live D4 smoke on the rebuilt stack: `DPS_SMOKE_SCANNER=1 DPS_SMOKE_M2=off tools/smoke_test.py` | **34/34 passed** (2 OCR skipped): 21 pages, 63 fields, 51 personal; `non_get +0, form_get +0, logout_hits +0, admin_hits +0; non_get_allowed 0`, blocked 60 POST / 1 GET; evidence submissions 0; 3/3 masked captures; persona export 0 demo values. Demo `/__received` 0 → 0 on every counter | `d4-smoke.log`, `d4-received-{before,after}.json` |
| Browser-worker posture in the running container | `--host-resolver-rules=MAP * ~NOTFOUND, EXCLUDE 127.0.0.1`; `prompt_label` present in the image | — |
| Container log scan (app, worker, browser-worker, demo-site; 40 min, covers the smoke scan) | 0 of 10 demo values, raw or normalised (90 lines) | `log-scan.out` |
| Git | `3d257a3` authored and committed by NilenduGanguli <nilendu.ganguli@gmail.com>, no trailer; explicit 8-file pathspec; worktree clean | — |

## 2. Acceptance

| Item | Status |
|---|---|
| 8B L2 (leftover columns, metadata only, `protected` = sampled values, cap 0.85, `min_score`, `llm_label` detector, anchors kept, opt-in, stub no-op, ≤ `DPS_LLM_LABEL_MAX_FIELDS`) | met (unchanged since r1; tests pass) |
| 8B L3 value-free prompt, one call per page, leftover fields only | met for every label source and format tried at the shipped thresholds (§3); **not met when the policy's `min_score` > 0.85 (R2-B1)** |
| `progress.metrics.llm` on discover and scan jobs | met (r1 N1 still applies) |
| LLM tests faked at the provider seam | met: the new tests' `FakeGateway`/`FakeNer` override `_stellar` / the NER adapter; `complete()` and its `strict` egress verifier run as in production |
| S5 (`check_scan_url` at `POST /scans`, browser-worker re-check, every-hop dial control, Chromium `--host-resolver-rules`; encodings, IPv4-mapped, metadata, DNS → link-local) | met (code unchanged since r1; corpus re-run identical, §6) |
| D4 re-check | met (live smoke +0; suite proofs) |
| Docs (security review §3.25–§3.27/§5, M5-DEVIATIONS, contract, HANDOFF §17.1) | accurate, except §3.26's unconditional "detector-redacted" claim (R2-B1) |

## 3. r1 B1 — verified fixed

`classify.prompt_label`: no label from the placeholder fallback; no label unless `ner_runs`
(adapter, `ner` tier, model loaded — re-read after the label was analysed); else
`redact_text` then every whitespace token holding a digit or `@` replaced by `value_shape`, only
digit-free `<TAG>`s kept. Checked independently:

- **r1's own probe (`l3_label_leak_v2.py`, NER off) and the fix's NER variant, re-run on
  `3d257a3`** (`l3-r1-probe-rerun.out`): page `/a` (aria-label / placeholder / title / name in an
  aria-label) **0 of 4** planted values in the provider's messages (r1: 4 of 4), 1 call; page `/b`
  (number in a rendered `<label for>`) now makes its call (r1: `leak_guard`, 0 calls), 0 of 1
  sent; with NER the `label` key is present and still 0 values; `{calls 1, raw_values 0,
  blocked 0}` and 0 POSTs on every page.
- **Adversarial page, real Chromium + `launch_args()` + `ScanRunner` + real `complete()`
  (`strict`), provider faked at `_stellar`** (`probes/l3_label_adversarial.py`,
  `l3-label-adversarial.out`): 18 fields, label sources `aria_label, aria_labelledby (hidden,
  and two ids one hidden), label_wrap, nearest_text, placeholder, title, none`; planted numbers in
  every source, a hidden-`aria-labelledby` e-mail, a spaced phone, a PAN, a date, an amount,
  full-width and Devanagari digits, numbers in `id`/`name`, a `select` title, a `textarea`
  placeholder. NER off: 1 call, 16 entries, **0 labels**, 0 planted values sent. NER on (fake
  T2 that knows one name): 1 call, 12 labels, **0 planted values sent**; `{calls 1, raw_values 0,
  blocked 0}`; 0 POSTs. Only the documented residuals reached the provider (a name NER does not
  know, a letters-only reference, a name inside an identifier-like `name` attribute — security
  review §5).
- **Tests are meaningful:** all 9 new cases fail on `fee0041`'s code (§1).
- Docs (§3.26, §5, M5-DEVIATIONS, HANDOFF) match the code, except the guarantee R2-B1 breaks.

## 4. Blocking

### R2-B1 — a policy `detect.min_score` above the NER score turns label redaction off while `ner_runs` stays true (P1)

`prompt_label` (`dps/layers/scanner/classify.py:689`) sends a label only when
`ner_runs(detector)` (`:674`) and then trusts `redact_text(facts.label, detector)` (`:705`) to
replace the names NER finds. But `redact_text` calls `detector.detect()`, which drops every
finding below `detector.config.min_score` = `policy.detect.min_score`
(`dps/engine/detect/ensemble.py:89`: `f.score >= self.config.min_score`). Presidio scores
spaCy PERSON hits at `NerModelConfiguration.default_score = 0.85` (presidio-analyzer 2.2.364 in
the lane venv; `FakeNer` mirrors it). `DetectConfig.min_score` accepts any value in [0, 1] with no
cross-check against `ner`, so a tenant policy such as
`{"preset": "gdpr-pseudonymise", "detect": {"tiers": ["structural", "patterns", "ner", "llm_label"], "min_score": 0.9}}`
is valid — and with it NER "runs" (so labels are sent) but none of its names are redacted. Nothing
downstream catches it: `page_protected`'s rendered findings use the same threshold, and the egress
verifier's pattern scan does not look for names.

Reproduced through the real `complete()` (`strict`), provider faked at `_stellar`
(`probes/l3_min_score_probe.py`, `l3-min-score.out`; label `aria-label="Nominee <made-up name>
agrees"`):

```
min_score 0.6  -> ner_runs true, 1 call, label sent, name parts in provider messages [false, false]
min_score 0.85 -> ner_runs true, 1 call, label sent, name parts in provider messages [false, false]
min_score 0.86 -> ner_runs true, 1 call, label sent, name parts in provider messages [true, true]
min_score 0.9  -> ner_runs true, 1 call, label sent, name parts in provider messages [true, true]
```

End to end (real Chromium with `launch_args()`, `ScanRunner`, real `complete()` under `strict`,
provider faked at `_stellar`; `probes/l3_min_score_table_e2e.py`, `l3-min-score-table-e2e.out`):
a "Nominees" table whose per-row checkboxes take their label from the neighbouring name cell
(`nearest_text`) — `min_score 0.6`: 1 call, 2 labels sent, **0 of 2 names** in the provider's
messages; `min_score 0.9`: 1 call, 2 labels sent, **2 of 2 names** in the provider's messages,
while `progress.metrics.llm` reports `{calls 1, raw_values 0, blocked 0}`; 0 POSTs either way.

This is not the documented "names NER misses" residual: NER finds the name and the policy
threshold discards it, for every label on every page — including `nearest_text` labels taken from
the neighbouring cell of a per-record row, the realistic case shown above. Security review §3.26 states
the label is "detector-redacted (`<PERSON_NAME>`)" whenever NER runs; under such a policy it is not.
Shipped presets use 0.5–0.6, so it needs a custom policy (plus a `WITH_NER` image and the
`llm_label` tier) — but the setting is valid and silent.

**Fix (one of):**
1. Recall-first redaction for the prompt (preferred: keeps labels useful): in `prompt_label`,
   redact with a detector whose `min_score` is capped at a fixed floor (e.g. the NER adapter's own
   `score_threshold`, 0.4) — `Detector(dataclasses.replace(detector.config,
   min_score=min(detector.config.min_score, FLOOR)), ner=detector.ner, custom=detector.custom)`;
   over-redaction only costs label detail. Or
2. Fail closed: `ner_runs` (or `prompt_label`) returns no label when `detector.config.min_score`
   exceeds the NER score.

Test through `complete()` with `min_score` 0.9 (and 0.86): the provider never receives the name
and the call is still made; state the threshold dependency (or its absence) in §3.26.

## 5. Non-blocking

- **N8 (new, residual wording):** `_PLAIN_NAME` lets a grouped number in a `name`/`id` through
  whole (`acct_771-200-345`: no run of four digits) — sent raw unless the page also renders it.
  §5 documents the rule; consider aliasing any name with ≥ 6 digits in total.
- **N9 (informational):** `_VALUE_TOKEN` uses `\d` (Unicode Nd) and `[@]`: digits outside Nd
  (superscripts, circled numbers) and a full-width `＠` are not value tokens. Not a realistic
  encoding of a record value; listed for completeness.
- **r1 N1–N7 still open** (owner's): N1 `blocked` counts egress blocks only; N2 numeric hints
  vs. protected digit tokens (and, after the fix, shaped labels vs. a page token of nines); N3
  interpreter-dependent `forbidden_ip_reason` (this round's corpus shows the reason text for
  `64:ff9b::a9fe:a9fe` flipping between runs — same decision); N4 Azure WireServer
  `168.63.129.16` allowed; N5 `ScanGuard(allow_loopback=True)` default; N6 egress verifier scans
  the fixed instructions (blocks L2 calls); N7 combining marks in shapes.
- The inventory still stores attribute labels detector-redacted only (M4 design; the fix
  report's observation) — a storage question outside P1.

## 6. S5 / D4 — re-checked

- No guard, proxy, netguard or browser code changed since r1 (`git diff fee0041 3d257a3 --stat`:
  `classify.py`, `llm_label.py` docstrings, tests, docs).
- r1's adversarial corpus re-run on `3d257a3` (`ssrf-corpus.out`, 118 cases: address rule, URL
  check × prod/local, every-hop proxy dials with a counting server, real resolver): **identical
  decisions to r1**; the only diff is the reason text of one refused address (N3's set-order).
- D4: live smoke +0 on every counter (§1); the full suite's D4 proofs and the new end-to-end test
  (`writes == []`) pass.

## 7. Artefacts (`~/.cache/dps-build/reports/S3b-review-r2/`)

`ruff-*.log`, `mypy.log`, `pytest-full.{log,xml}`, `pytest-security.{log,xml}`,
`collect-{all,browser,scanner,security}.txt`, `d4-smoke.log`, `d4-received-{before,after}.json`,
`log-scan.out`, `ssrf-corpus.out`, `l3-r1-probe-rerun.out`, `l3-label-adversarial.out`,
`l3-min-score.out`, `l3-min-score-table-e2e.out`, `probes/` (`l3_label_adversarial.py`,
`l3_min_score_probe.py`, `l3_min_score_table_e2e.py`, `log_scan.py`, `junit_subset.py`). Every probe uses
made-up values and prints booleans and counts only. The lane's compose stack was stopped at the end
(`docker compose --profile '*' stop`, 0 `dps-wt-fsec-*` containers running; images rebuilt from
`3d257a3` kept as `data-privacy-services{,-browser}:fsec`); no repository file was modified
(worktree clean, HEAD `3d257a3`).
