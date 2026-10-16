# FINAL fix r1 — the v1.0.0 candidate's blocking findings, fixed test-first

| | |
|---|---|
| Date | 2026-10-16, 10:25–11:55 IST |
| Tree | main checkout `/Users/neelu/document_intelligence/data-privacy-services`, branch `main` |
| Base | `45be8ac` (`chore(release): v1.0.0`) |
| Commits | `c7ad14f` fix(release): finalize opens the sealed policy, Mongo targets are no DDL, FKs join their parent, stopword surnames, sampled keys, T3 egress · `d033dd3` fix(release): the showcase deck is rebuilt from the v1.0.0 rehearsal; HANDOFF states T3's egress behaviour |
| Author | NilenduGanguli <nilendu.ganguli@gmail.com>, no trailer, explicit pathspecs. Not pushed (repo CLAUDE.md: no push without the owner's explicit request; `tools/dev/autopush.sh` not run). |
| Stack | main compose project, default ports. App, worker, browser-worker and tokenizer-mock recreated on images built from `c7ad14f` (sha256 of every changed module identical on host, app, worker and browser-worker). Stopped at the end (`docker compose stop`). |
| Untouched | `docs/screenshots/v1.0.0/` (untracked, another verifier's). |
| Evidence | `~/.cache/dps-build/reports/FINAL-fix-r1/`: build, gate and demo logs, probe copies and their JSON outputs. Counts, ids, labels and codes only. |

## 1. Findings and fixes

Every fix has a test that fails on `45be8ac` (checked by swapping the module's `45be8ac` version in
and back, or by the defect's own red run) and passes on `c7ad14f`.

| Finding | Fix (file) | Red-first tests | Live proof on the rebuilt stack |
|---|---|---|---|
| **B1** gates / l2-engines / synthetic B1 / deploy-demo B1: an inline or `@name` policy run dies in finalize | `jobs_run._finalize` opens the spec (`_finalize_spec` → `_spec`: F-18 body from `spec_enc`) for `_post_sample` and the pack's `policy.version`; evidence and audit keep the redacted `job.spec`; a seal that cannot be opened → post-write review `{"error": "secret_unavailable"}` (review required, fails closed) and the version read from the label; `_best_effort` turns `PrepareFailure` into `{"error": code}` | `tests/test_finalize_sealed_policy.py` (4; 3 red on `45be8ac`); `tests/connectors/test_run_sealed_policy.py` (PG inline + `@name` obfuscate, copula + inline `utility_label`, `@name` synthesize: red run showed `finalize_failed error=PrepareFailure`); Oracle and Mongo inline runs in `test_oracle_pipeline.py` / `test_mongo_pipeline.py` | Demo S6: 3 runs under `@retail-kyc` (PG, Mongo, Oracle, 20,000 rows each) succeeded; each pack `policy {name: retail-kyc, version: 2}`, verification passed. S8 copula: utility `run: true` (ratio 0.98), MIA `run: true` |
| **B2** l2-engines: MongoDB existing collection checked as DDL | `targets._check_existing` / `_target_plans` take `sampled=True` for a MongoDB target: unseen leaves are written, seen leaves keep the kind check, a `table` load is keyed on `_id` | `test_mongo_pipeline.py`: truncate_insert and upsert into an existing empty collection, named append into an empty and a sparse collection (all 4 red: "the target has no column $._id" / "… $.accounts[*].balance"), plus a kind refusal that must stay `target_invalid` | Covered by the tests on the stack's MongoDB (the probe objects of `p1_setup` were dropped by their verifier) |
| **Synthetic B2**: unclassified FK did not join its classified parent | Prepare reads each referenced table's accepted snapshot on the run's source connection (`snapshots.accepted_snapshot`, `jobs_run._parent_labels`); the FK is keyed like the parent key (`targets.parent_key_label`: entity type, or `KEY|…`); without a parent snapshot an unclassified FK takes the join identifier its referenced column's name says (`_reference_label`) | `test_an_unclassified_foreign_key_joins_its_classified_parent_under_one_seed` (red: label `KEY|…` vs `EMPLOYEE_ID`) | `bank.employees` then `bank.customers` (copula) under one seed via the API: **20,000 / 20,000** synthetic customers join a synthetic employee (0 / 20,000 before); both sides labelled `EMPLOYEE_ID`; 0 FK values and 0 employee ids equal a real employee id; scratch tables dropped (`probes-synthetic/fk-join-live.json`) |
| **Synthetic B3**: stopword surnames survive | `names.SURNAME_STOPWORDS` (`date le best branch holder sales`, the persona-pool surnames among the STOPWORDS) join a run right after a title or a name word, unless a capitalised non-name word follows (`Branch Office`); after a name word only a run a cue *before* it opened keeps them | `tests/test_names.py` (+50 cases, all red: spans, title position, headings, L1 synthesize ×2 presets and obfuscate ×2 presets with the 5 surnames: 0 kept); `tests/connectors/test_stopword_surnames.py` (L2 synthesize and obfuscate memo column: 0 aligned survivors; 120/120 on `45be8ac`) | `stopword_surnames.py` re-run: surname kept **0/10** for each of the 5 surnames under synthetic-realistic and synthetic-strict (10/10 before), verified 10/10; declared `last_name` case 0 kept |
| **l1-docs**: sampled e-mail as a dotted key came back while the sweep passed | `profile_from_sample` aliases (`field_N`, everywhere) a key that holds one of the sample's swept values (`_alias_value_keys`); `verify_against_sample` also sweeps each leaf's joined key path (`_key_paths`) | `tests/test_api_schema.py`: dotted, undotted-planted and name keys × JSON/CSV × generator-spec (6 red), and a three-level key path forced into the rows (red: 200) | `diag_p2b.py` / `diag_p2c.py` re-run: dotted, undotted and phone keys → 200 passed, CSV header and body hold the value **False**; generator-spec spec holds it **False** (all True / 422 before) |
| **privacy-ops B1**: T3 blocked under strict egress | `egress.residual_values` skips a system message equal to one of `fixed_system_prompts()` (labelling, rewrite, spec); any other system text is scanned in full; a protected value that is a value shape (`999`) is not looked for; L2 hints round rates to 2 decimals | `tests/test_llm_egress_t3_collisions.py` (L1 PASSPORT/AADHAAR/PRIVATE/ACCOUNT, L3 selected Passport/Private, registry, shapes: 8 red); `test_t3_reaches_the_provider_on_every_seeded_bank_table` (10 tables: the same 5 red on `45be8ac` — contacts, customers, kyc_documents, loans, transactions) | `probe_t3_l2_all.py` re-run: **10/10** tables reach the provider, blocked `[]` (5/10 before); `probe_t3_collision.py`: every L1 and L3 case 1 provider call, `llm_raw_values` 0, `llm_blocked` 0 |
| **deploy-demo B2**: stale deck | `tools/build_deck.py --strict` from the passing rehearsal | `tests/test_deck.py` 43 passed | Slide 1: `PRIVACY CONSOLE · v1.0.0`, "ran end to end in 3:01", footer `rehearsal 2026-10-16 05:34 UTC · commit c7ad14f`. Keynote PDF review: owner |

## 2. Gates (on `c7ad14f`, stack rebuilt from it)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools`, `ruff check dps tests tools`, `mypy dps` | 515 files formatted, all checks passed, mypy clean (155 files) |
| Unit (`-m "not (db or pg_source or s3 or oracle or mongo or browser or slow or deploy or network or ml or integration)"`) | **3778 passed**, 13 skipped (2:00) |
| M2 (`DPS_TEST_REQUIRE_SERVICES=1 -m "(db or pg_source or s3) and not (oracle or mongo or browser or slow)"`, incl. `tests/api`) | **430 passed**, 2 skipped (2:56) |
| M3 (`-m "(oracle or mongo) and not (browser or slow)"`) | **186 passed**, 2 skipped (2:20) |
| M4 (`-m browser`), slow resume-after-kill | §2a |
| `python tools/demo_seed.py --reseed && python tools/e2e_demo.py --console` | **S1–S12 PASS, 3:01**; 18 screenshots in `reports/demo-screens/`; S6 3×20,000 rows, S7 3/3 pairs, S8 fidelity 0.968 / exact rows 0 / NNDR 1.4373, S9 D4 non_get +0 form_get +0, S11 posture 5/11 (local) |
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m browser tests/console/test_console_pages.py` | **9 passed** |
| `tests/test_deck.py` (python-pptx 1.0.2 in a private target) | **43 passed** |
| Log scan of the recreated app / worker / browser-worker logs (every line since the post-fix start) for 89,792 demo source values | **0 hits** in each (`probes-logscan/log-scan.json`) |
| Not run | `-m deploy` (kustomize renders; nothing under `deploy/` changed) |

### 2a. Browser and slow gates

| Gate | Result |
|---|---|
| `DPS_TEST_REQUIRE_SERVICES=1 pytest -q -m browser` (no tesseract on the Mac) | **228 passed**, 102 skipped, 0 failed (12:46). The skips are the tesseract-gated capture tests and the deck module (python-pptx not in `.venv`); the same 330 tests the gates verifier ran as 320 passed / 10 skipped with a tesseract shim. |
| The tesseract-gated part again, with `DPS_TEST_TESSERACT_CMD` = a shim running tesseract from the rebuilt app image (`--network none`, `bin/tesseract`), `DPS_TEST_REQUIRE_TESSERACT=1` and the image-runtime variables | `tests/scanner/test_capture_ocr.py` **33 passed**; `test_capture_geometry.py`, `test_capture_select_drawn.py`, `test_crawler.py`, `test_capture_select_shapes.py`, `tests/test_browser_image_runtime.py` (`DPS_TEST_BROWSER_IMAGE`/`DPS_TEST_IMAGE` = the rebuilt images) **105 passed, 0 skipped** (10:08). With the deck module's 43 (above), no browser-marked test is skipped or failing. |
| `-m slow tests/connectors/test_resume_after_kill.py` | **6 passed** (1:40) |

## 3. Contract, deviations, docs

- Contract §(D-A key rule) amended: a declared foreign key takes the label of the referenced key's own run (`parent_key_label`, `pseudonym_columns(..., parents=)`). §(egress, M5 Task 16B) amended: fixed system prompts are not scanned, other system text in full, value-shape protected values not looked for, L2 hints at two decimals. No contract name changed; new names: `snapshots.accepted_snapshot`, `targets.parent_key_label`, `targets.declared_references`, `egress.fixed_system_prompts`.
- `docs/superpowers/build/M5-DEVIATIONS.md`: new section "FINAL r1 fix round" (each fix, every test whose expectation changed and why).
- `CHANGELOG.md`: a "Final verification fixes" bullet; two new known limitations (an FK parent discovered through another connection can still key the sides apart; T3 still skips a call when a sampled value equals a word of the call data itself — field/table name, a number in a declared type).
- `HANDOFF.md` T3 paragraph: what the egress verifier scans now and what still fails closed.

## 4. Tests whose expectation changed (not weakened)

- `test_copula_job_on_a_table_with_a_declared_foreign_key`: FK label `KEY|<schema>.employees.employee_id` → `EMPLOYEE_ID` (the label a discovered parent gets; the join property it guards is now also checked against the parent's own run by the new test).
- `test_leak_guard_blocks_the_call`: its sample's key "status" (equal to a sampled name "Status") is now aliased, so the prompt no longer holds it; the test asserts the alias and drives the leak guard with a prompt that holds the value (still `fallback: leak_guard`, 0 calls).
- `test_instructions_that_look_like_values_do_not_block`: uses the real labelling prompt; an ad-hoc system message is now scanned in full and pinned as blocked (stricter than before).
- `test_a_prompt_the_egress_verifier_stops_is_never_sent_and_is_counted`: its trigger was the fixed-prompt false positive this round removes; it now empties the fixed-prompt registry to keep testing the fail-closed path (nothing sent, `blocked` 1).

## 5. Coverage notes and residuals

- B1 tests: PG inline + `@name` × obfuscate + synthesize; Oracle and Mongo inline obfuscate. Oracle/Mongo `@name` is proved live by demo S6 (all three engines); Oracle/Mongo synthesize under an inline policy has no dedicated test (finalize's path is engine-independent).
- Schema synthesis behaviour change: a sample whose key is a sampled name or phone now answers 200 with that key aliased `field_N` (was 422 `leakage`); the value never comes back either way.
- Residuals recorded in CHANGELOG (above). None found by this round's runs.
- Owner: Keynote PDF review of `docs/demo/dps-showcase.pptx`; push (`tools/dev/autopush.sh`) when wanted.
