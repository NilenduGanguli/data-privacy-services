# Release — the finish branches merged into `main`, v1.0.0 committed (2026-10-16)

**Verdict: DONE.** All seven finish branches are merged into `main` in the requested order. The
merged tree passes ruff and mypy. `pytest -q` found 9 failures: 8 were defects the merges
introduced, fixed test-first in three commits and re-run green; the 9th came from a stale container.
`chore(release): v1.0.0` is committed (`45be8ac`) and pushed by the owner's checked auto-push. The
`full` stack was rebuilt from scratch (`down -v`, then `up --build -d`) and is **left running**:
`/readyz` reports ready, version 1.0.0 and `browser: 1 online`. No tag was created. The full
Definition of Done is left to the FINAL panel.

| | |
|---|---|
| Working copy | `/Users/neelu/document_intelligence/data-privacy-services`, branch `main` (was `1ce701d`, now `45be8ac` = `origin/main`) |
| Author | every commit by NilenduGanguli &lt;nilendu.ganguli@gmail.com&gt; (identity read from `~/.gitconfig`, no local override); no trailer of any kind; fix and release commits use explicit pathspecs |
| Values | only counts, ids and file names are printed or recorded here. No customer value, key or secret appears in this report or its logs. |
| Logs | `~/.cache/dps-build/reports/release/`: `pytest-full.{log,xml,time}`, `pytest-scanner-ocr.{log,time}`, `compose-build-pre.log`, `compose-down.log`, `compose-up.{log,time}` |

## 1. Resume check and open findings

- No partial state was found: the tree was clean, no merge was in progress, and no `release.md`
  existed. Since the 15 Oct state, `main` had gained the S1-M4 fix round (`f35bb21`, `a0027b2`,
  `4f0aac4`, `39405d5`, `1ce701d`; report `S1-M4-fix-r1.md`).
- Blocking findings in the given results and where each one was fixed:

| Stream | Finding | Status |
|---|---|---|
| s1m4 (main) | captures-privacy B1–B3, adaptations A1–A2, regression (cancel counts), console B1–B3 | fixed on `main` before this run, test-first (`S1-M4-fix-r1.md`: 42 select tests red→green, 4 cancel tests, live re-checks). This run re-ran the selects' OCR and crawler suites on the merged tree: **147 passed** (§4). |
| s3.b (fsec) | R2-B1: the L3 label was sent above `min_score` 0.85 | fixed on `fsec` (`6147f85`, recall-first redaction; 7 tests red→green, `S3b-fix-r2.md`). Merged. |
| s3.c (fsecc) | — (PASS) | merged |
| s4b (fimg) | — (PASS; S11 needed fsec) | merged; S11 (`Cache-Control: no-store`) now on `main` |
| s6b (fdemo) | — (PASS) | merged |
| s8 fspec | B1 (comma lists), B2 (full-width whole number) | fixed on `fspec` (`a0e2e59`) and merged into `fllm`; S8-integrate re-ran the probes on the merged tree |
| s8 fpersona / fidcheck | — (PASS) | merged via `fllm` |

## 2. Merges (`git merge --no-ff <branch> -m "merge: <branch> into main"`)

| Order | Branch (tip) | Merge | Conflicts and how both sides were kept |
|---|---|---|---|
| 1 | `fsec` (`6147f85`) | `edb4480` | `routers/jobs.py`, `connectors/targets.py`: one **rule joined** (below); `scanner/crawler.py`: main's placeholder rule for the obfuscated capture plus fsec's `_capture(t, record, snap, classes)`, with both imports; contract §5 `FilesSpec` comments: both; HANDOFF: both sections (fsec's became §18) |
| 2 | `fsecc` (`5e529bf`) | `976a904` | M5-DEVIATIONS: both entries |
| 3 | `fimg` (`3f8af02`) | `cdd2145` | HANDOFF (fimg's sections renumbered), M5-DEVIATIONS: both |
| 4 | `fos` (`866da65`) | `d81855e` | HANDOFF secrets paragraph: main's M4 sentences plus fos's OpenShift Secrets; fos's section; M5-DEVIATIONS: fos's "Closed by Task 25" line plus main's sections |
| 5 | `fdemo` (`b57ec86`) | `2dabe95` | HANDOFF: both |
| 6 | `fdocs` (`f4970da`) | `3a3c28d` | `.gitignore` (one anchored `/reports/` with both comments); README (fdocs's final frame plus main's M4 develop and test facts; every "†" placeholder resolved); M5-DEVIATIONS: both; **HANDOFF: final shape** (§3) |
| 7 | `fllm` (`bdbfa16`; carries `fpersona`, `fspec`, `fidcheck`) | `0541dc9` | `surrogates.py`: both imports; `methods.py`: main's S2 `repr=False` on `binding` plus fpersona's `name_hint`; `jobs_run.py`: `_best_effort` catches `PatternTooSlow` **and** `SelectionError`; HANDOFF: fidcheck's identifier-check paragraph moved into the final frame (§11 Synthesis); M5-DEVIATIONS: both |

Every branch is an ancestor of `main` (`fsec`, `fsecc`, `fimg`, `fos`, `fdemo`, `fdocs`, `fllm`,
`fpersona`, `fspec`, `fidcheck`).

**The joined production-write rule** (a decision the owner should see; it is stricter than either
input):
- `main` (S1-M3 post-merge r1 security B1) required `admin` for every write mode into a
  `production`-tagged connection.
- `fsec` (F-01) required `admin` for `in_place` and `append` through any connection that is tagged
  or holds a production credential.
- After the merge, `validate_run` and `check_retry_scope` apply `holds_production_credentials` (tag
  **or** credential) to **every** write mode of a run. An untagged holder of the production login is
  therefore production for `table` and `files` too.
- Nothing either branch enforced was weakened. Contract §5.1 and §5.3 and security review §3.1
  record the amendment (2026-10-16).

## 3. Defects the merges introduced, fixed test-first (found by `pytest -q` on the merged tree)

| Commit | Defect | Red test(s) | Fix |
|---|---|---|---|
| `90f1312` | The joined rule refused API fixtures that modelled a UAT target holding the **production connection's own login**. F-01 also now refuses a `jobs` key naming a production credential after the tag. | 7 × `tests/api/test_api_jobs.py`, `test_inline_policy_sealed`, 6 × `tests/api/test_production_write_gate.py` | Fixtures only, no rule relaxed. The fixtures' production connection holds its own login (`dps/src/test-pg-admin`). The S1-M3 gate's UAT target uses a non-production ref, and its retry case names the credential before the tag. `tests/api`: **55 passed**. |
| `f8850d4` | fsec's F-11 posture rule (`DPS_TOKENIZER_PROFILES` required with the tokenizer on, staging/prod) failed the committed prod render of fos's manifests | 6 × `tests/deploy/test_openshift.py` (posture guard ×2, schema-wait settings, release gate, placeholders, release example); new `test_tokenization_is_refused_until_the_release_lists_each_tenants_profiles` (red first: `KeyError`) | Base ConfigMap: `DPS_TOKENIZER_PROFILES={}`, which fails closed (no tenant may tokenize) and passes the guard. The release example lists `ENTITY:profile` entries; dev leaves the setting empty for the mock. README parameter table updated. `tests/deploy`: **62 passed**. |
| `dea7b84` | After the M4 post-merge fixes, `shown_texts` includes a select's drawn placeholder. fsec's `page_protected` therefore protected "Choose…", and the strict egress verifier found "choose" in the T3 prompt's fixed instructions. Result: **every T3 call on a page with an untouched select was blocked** (fail closed, no leak). | `test_a_placeholder_option_is_page_text_and_never_blocks_the_call` (fsec's own guard test), `test_a_crawl_labels_leftover_fields_value_free`; new `test_a_drawn_placeholder_is_no_protected_value_but_a_drawn_value_is` (red first). Before the fix, only the first option of a listbox was protected. | `page_protected` now protects every string a field shows, except the placeholder phrase of an empty-valued option the field lists. A real value behind an empty value stays protected, and so does every listbox option. T3 tests: **36 passed**. |

The 9th failure, `tests/scanner/test_demo_site.py::test_received_counts_the_pages_and_files_it_served`,
ran against the old stack's demo-site container. That container was started before fimg's
`server.mjs` change (the file is bind-mounted, but the node process had loaded the old version). On
the rebuilt stack it passes: **1 passed**.

## 4. Gates run in this step (not the full DoD)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` / `ruff check` | 511 files formatted / all checks passed |
| `mypy dps` | no issues in 155 files |
| `pytest -q`, whole suite, against the running old `full` stack (DB and sources in-process) | **4 577 passed, 127 skipped, 9 failed** in 23:23. The 9 are covered in §3. Skips: 101 need tesseract (run separately below), 9 need `var/demo/demo-keys.json` (`demo_seed.py`), 6 need image tags (`DPS_TEST_IMAGE`/`DPS_TEST_BROWSER_IMAGE`), 2 MOSTLY/pandas, 2 spaCy model, 2 host tesseract, 2 by design (Mongo composite key, key-less PG), 1 collection, 2 others. |
| Re-runs after the fixes | `tests/api` 55 passed; `tests/deploy` 62 passed; `tests/test_llm_label_l2_l3.py` + `tests/scanner/test_crawler_llm_label.py` 36 passed; `tests/test_handoff.py` + `tests/test_package.py` 11 passed |
| Tesseract-dependent scanner suites with `DPS_TEST_TESSERACT_CMD` set (`docker run --rm -i --network none --entrypoint tesseract data-privacy-services:local`), post-fix code | `test_capture_select_shapes.py`, `test_capture_select_drawn.py`, `test_capture_ocr.py`, `test_capture_geometry.py`, `test_crawler.py`: **147 passed, 0 skipped** in 11:20 |
| Console (`frontend/`) | `npm run typecheck` clean; `npm test` **46 node + 418 Vitest** passed; `npm run build` OK |
| `python tools/settings_table.py --check` | current (186 settings) |

## 5. Task 28 steps 1–2 (version, HANDOFF, CHANGELOG) and the release commit

- **Version 1.0.0** in `pyproject.toml`, `dps/__init__.py`, `frontend/package.json` and
  `package-lock.json` (`npm version 1.0.0 --no-git-tag-version`). `/readyz` reports
  `api-core.version 1.0.0`.
- **HANDOFF, final shape.**
  - Frame: fdocs's §1–§13. Every "(at the M4 merge)" and "(Task 24/25/26)" placeholder is filled
    from the merged streams. Filled facts include:
    - the browser image, the arbitrary-UID rules and the container runtime;
    - the `scanner` profile services and the M4/F-11/F-06 posture;
    - the scanner sessions (SEC-5), the production credentials and the joined D3 rule;
    - the as-built OpenShift section (schema waits, digests, maintenance CronJob,
      `openshift_check`, profiles);
    - the KEK window (F-21), the S5 host rules, the browser-worker probes and metrics, and
      `DpsAuditMaintenanceStale`;
    - the pattern bounding (F-02, rounds 1–3), per-call tiers (F-10), the own-name spans and the
      identifier check on every row.
  - §14: main's merge records (M3 integrated, M4 lane/pre-merge/integration, `m4`→fsec, fimg, fos,
    fdemo), renumbered, plus §14.9 "The release merge".
  - Settings table: regenerated by `tools/settings_table.py`, after adding `FIELD_DOCS` for the 21
    settings the other streams added (the 18 M4 scanner settings, `DPS_PATTERN_CPU_SECONDS`,
    `DPS_PATTERN_PROCESSES`, `DPS_TOKENIZER_PROFILES`).
  - "Last verified" holds this step's gate summaries. The DoD lines belong to the FINAL panel.
  - `tests/test_handoff.py`: 10 passed. These tests check every setting, compose service, OpenShift
    role, mounted secret, and every workload that mounts each secret.
- **CHANGELOG** dated `## v1.0.0 — 2026-10-16`:
  - The M4 paragraph is confirmed with its merge and post-merge fixes.
  - A "Finish stage" block covers the security review, Task 24, Task 25, Task 26 and Task 27, and
    the **live-Vertex fixes** (T3 egress verifier, one persona per person, schema/sample synthesis
    leakage false positives, the whole-table identifier check).
  - Known limitations added: Oracle `ENABLE NOVALIDATE` keys, the browser image's missing init
    process, the deny-list build cost of up to ~2.3×, the LLM rewrite fallback, schema re-verify,
    name-part over-detection.
- Also updated:
  - README: status "v1.0.0 — M0–M5 shipped", links, HANDOFF section references.
  - RESUME: "Last updated" and TL;DR.
  - M5-DEVIATIONS: the "Task 28 — the release merge and v1.0.0" entry.
- Commits after the merges: `90f1312`, `f8850d4`, `dea7b84` (fixes), then `45be8ac`
  `chore(release): v1.0.0`. The release commit's pathspec: `pyproject.toml dps/__init__.py
  frontend/package.json frontend/package-lock.json HANDOFF.md CHANGELOG.md
  docs/superpowers/build/M5-DEVIATIONS.md docs/superpowers/RESUME.md`. **No tag.**

## 6. Clean stack (left running)

- `docker compose --profile full down -v` removed the project's containers and volumes (5 s).
  `docker compose --profile full up --build -d` exited 0 (2:57; the images had been pre-built from
  this tree and only the last layers rebuilt).
- `/readyz` was `"ready": true` on the first poll. Components:
  - `api-core` up, version 1.0.0; `db` up with migrations verified; `jobs` ready;
  - `keys` db+static; `tokenizer` up (http); `ocr` up (azure-read-v3.2); `fpe` ff1;
  - `llm-gateway` configured (stub, coin_tls verified); `browser` 1 online (after the first 15 s
    heartbeat);
  - `ner` patterns-only (expected: no NER in the default image).
- `docker compose ps -a`: 11 services running and healthy (app, worker, browser-worker, dps-db,
  source-pg, minio, mongo, oracle, azure-ocr-mock, tokenizer-mock, demo-site). The four one-shots
  exited 0 (migrate, minio-init, demo-secrets, seed-sources).
- Code identity: the 155 tracked `dps/**/*.py` files and `pyproject.toml` hash the same in the tree
  and in `app`, `worker` and `browser-worker` (0 of 156 differ).
- Demo site `/__received` after the one demo-site test: non_get 0, form_get 0, logout_hits 0,
  admin_hits 0, get 3. That test is the only thing run against the fresh stack. `demo_seed.py` and
  the smoke have **not** been run on it.

## 7. Push

`tools/dev/autopush.sh` (rc 0) pushed `main` `45be8ac`. The owner's LaunchAgent had already pushed
the merges and fixes (`2dabe95`, `0541dc9`, `dea7b84`). The script checked author, trailers and
gitleaks and logs to `~/.cache/dps-build/autopush.log`. `origin/main` = `main` = `45be8ac`.

## 8. For the FINAL panel (gates verifier) and the owner

- The stack is fresh. Run `python tools/smoke_test.py` (it registers the demo connections the console
  tests need) and `python tools/demo_seed.py` (9 tests skip without `var/demo/demo-keys.json`) before
  the gate runs.
- Smoke with the scanner should now pass S11 (fsec is merged). Expected **60/60** with
  `DPS_SMOKE_SCANNER=on DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1 … --m3`, after
  `tools/seed_sources.py --target all --rows 20000 --seed 7`.
- The capture OCR tests need `DPS_TEST_TESSERACT_CMD`. A wrapper is at
  `~/.cache/dps-build/s1-m4-gates-r1/bin/tesseract`, with a copy in this session's scratchpad.
- Owner decisions:
  - The joined production-write rule (§2): any write through a credential holder needs admin.
  - S3c: deny-list trie build CPU of 1.05–2.32× round-1, against the "≤ round-1" acceptance (charged
    and bounded).
  - The LLM free-text rewrite fell back to the deterministic text in both live runs (product
    question).
  - The deck still needs its Keynote PDF review.
- Non-blocking items carried (CHANGELOG "Known limitations" or the reports):
  - S3c docs nits (M5-DEVIATIONS points to a security review §5 that lacks N3–N6;
    `PrivacyEngine.synthesize`'s docstring still says no CPU step runs on the loop).
  - S4b N1–N6.
  - S6b notes (slide 5 text vs. screenshot; stale HANDOFF demo-timing note, now in §14.8).
  - S8 persona N1/N2.
  - fidcheck N1–N5 (N1 is a real pre-existing Oracle `ENABLE NOVALIDATE` defect).
  - fos follow-up: the app should keep re-verifying the schema or exit (contract §2.1).
- Tag after the DoD passes: `git tag -a v1.0.0` on `45be8ac` (or its successor). Locally,
  `docker tag data-privacy-services:local data-privacy-services:1.0.0`, and the same for the
  browser image.
