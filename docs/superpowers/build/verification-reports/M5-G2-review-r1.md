# M5 G2 review, round 1: Tasks 6, 6B, 7, 7B, 8, plus SEC-4 (D-B) and D-A

Reviewer: independent. Worktree `/Users/neelu/document_intelligence/dps-wt-m5`, branch `m5`, HEAD `e231483`.
Date: 2026-10-13. The working tree was clean before and after the review. I changed no repository file.
Probes are in `/Users/neelu/.cache/dps-build/reports/probe-m5-G2/`.

**Verdict: FAIL.** There are 2 blocking findings, both about D-A. They are shown by source-vs-target probes on
this lane's containers. Every gate is green, and SEC-4 / D-B is sound.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` | 272 files already formatted (exit 0). I used `--check` so that no file would be modified. |
| `./lane.sh ruff check dps tests tools` | All checks passed |
| `./lane.sh mypy dps` | Success: no issues found in 111 source files |
| `./lane.sh pytest -q` with no containers | 1567 passed, 179 skipped |
| `./lane.sh pytest -q -rs` with the lane's `dps-db`, `source-pg` and `minio` up (`docker compose --profile sources up -d --no-deps dps-db source-pg minio`) | **1734 passed, 12 skipped** in 183 s |
| Frontend | Not run, because no frontend file changed on `m5` (`git diff 88eae2e..HEAD -- frontend` is empty) |

Of the 12 skips, the only ones that touch G2 are 2 `ml` tests: `test_mostly.py:194` and `test_mostly_linked.py:246`. They skip
because `mostlyai` cannot be installed in this lane (the `synth-ml` extra needs torch). The other 10 are unrelated:
- console tests need the app container, and there is no `m5` image;
- OCR tests need the docs mock or the tesseract binary;
- the presidio and spaCy tests need the `ner` extra.

All of the G2 integration suites ran and passed:
- `tests/connectors/test_synthesize_copula.py`
- `test_synthesize_identifiers.py`
- `test_linked_files.py`
- `test_targets.py`
- `test_resume_after_kill.py`
- `test_run_failures.py`
- `test_run_modes.py`

The SEC-4 and L2-reveal suites run against the real mock script in a thread, with a counting server:
`tests/test_external_binding.py` and `tests/test_reveal_rows_external.py`.

I ran `docker compose --profile sources down` afterwards. Only this lane's project was touched.

## 2. Blocking findings

### B1. D-A: classified personal attributes are copied verbatim, row-aligned, into "synthetic" tables (leak)

**Code.**
- `dps/layers/connectors/targets.py` `_identifiers` (around line 147) pseudonymises only kept columns that are
  a key, a link entity, or MEDIUM+ sensitivity.
- `dps/layers/connectors/discovery.py` (around line 148, `keep_non_sensitive`) sets the synthesize method of
  every LOW entity to `keep`. The LOW entities are GENDER, NATIONALITY, AGE, POSTAL_CODE, CITY, DATE, AMOUNT,
  LOCATION, ORG_NAME, SWIFT_BIC, IFSC, SORT_CODE, ROUTING_ABA, URL and CASE_ID. Both presets set it, so a kept
  column keeps its value.
- `dps/layers/connectors/tabular.py` `tabular_columns` (around line 144) skips every column that has an entity
  type, so the copula **never** models these columns.
- `jobs_run.synthetic_kept_columns` (around line 1454) subtracts every classified column, so the kept-column
  review never looks at them either.

**What D-A requires.** Programme plan §3 says a synthesize job "must not copy a sensitive key … **or any column
discovery classified as personal**". It also says "Non-modelled columns are kept only when discovery classified
them non-personal and the post-write sample finds no detector hit".

**The deviation log.** The D-A entry records the narrowing as an owner decision. It also says LOW attributes stay
because "the copula models them when they qualify". That statement is false: the copula never models a
classified column.

**Probe.** `probe-m5-G2/test_probe_da_kept.py`, output in `da_kept_counts.json`:
- Source: an `addresses`-shaped table (the demo `bank.addresses` columns, plus `gender` and `age`) with 400 rows.
- Run: a copula synthesize into a table target. The job **succeeded**.
- Classification: `city`→CITY, `postal_code`→POSTAL_CODE, `gender`→GENDER and `age`→AGE, all with synthesize
  `keep`.
- Alignment: I matched each target row to its source row by recomputing the key pseudonym of every source key
  with the seed the submit call returned. All **400/400** rows aligned.

Counts, source vs target:

| Column | Target rows equal to their own source row | Distinct values in the source |
|---|---|---|
| `city` | **400/400** | 220 |
| `postal_code` | **400/400** | 400, i.e. unique per row |
| `gender` | **400/400** | — |
| `age` | **400/400** | — |
| (city, postal, gender, age) | **400/400** rows equal on all four | — |

- The evidence pack still says `verification.passed: true`, `review_required: false` and `exact_row_matches: 0`.
- `qi_uniqueness` shows source 1.0, synthetic 1.0 and `unique_overlap` 326. The pack measures the leak but nothing
  gates on it.

**Second probe.** `probe-m5-G2/test_probe_da_loans.py`, output in `da_loans_counts.json`. `originated_on`
(DATE, LOW) is copied 400/400 in the same way.

**Why it blocks.** The finding contradicts D-A's text and the spec §1 rationale ("no path back to the original").
The other side of each synthetic row is a real person's exact postal code (unique per row here), city, gender and
age. That is a quasi-identifier set that re-identifies people, labelled "synthetic". The integration tests pass
only because they never compare LOW kept columns: `test_synthesize_identifiers.py` checks keys, national id and
e-mail only.

**Fix (test-first).** Pick one of these:
- (a) Columns with an entity type and synthesize `keep` that are not identifiers go to the tabular model.
  `tabular_columns` should stop excluding `entity_type is not None` when the method is `keep`, and should
  generalise or redraw them.
- (b) Otherwise, surrogate them, or null them.
- (c) Otherwise, refuse or flag them `review_required`.

Then add a source-vs-target assertion to the D-A integration test: for every LOW-classified written column, the
row-aligned equality count must be at chance level, not 100 %. Remove the false "copula models them" sentence
from `M5-DEVIATIONS.md`. If the owner really wants LOW attributes kept, that override has to be recorded in the
programme plan, not only in a deviation note.

### B2. D-A: unclassified keys are copied verbatim next to the pseudonymised identifiers, so the pseudonym can be re-linked

**Code.**
- `_identifiers` only pseudonymises keys that have an entity type.
- `synthetic_kept_columns` subtracts `plan.key`, so an unclassified key is neither pseudonymised nor reviewed.

**Probe.** `test_probe_da_loans.py` uses the demo `bank.loans` shape.
- `loan_id` (a key, unclassified) is written with its **real value in 400/400 rows**.
- In the same row, `customer_id` is replaced by its pseudonym, and `originated_on` is copied 400/400.

**Why it blocks.** Anyone with the real `loans` table, or with any L2 *obfuscate* copy of it, can rebuild the full
map from pseudonym to real `customer_id`. An L2 obfuscate copy is enough because obfuscate keeps integer keys
(`numeric_key_kept`). That map de-pseudonymises every table of the job group, including `customers_synth`. This
contradicts D-A's headline ("synthetic tables never carry linkable real identifiers") and its rationale.

This is an interpretation: D-A's explicit list names *sensitive* keys. The consequence, though, is a working
linkage path to `CUSTOMER_ID`, which D-A covers explicitly.

**Fix.** Pick one:
- Pseudonymise every key written by a synthesize `table`/`files`/`append` run, plus every foreign key that
  references a pseudonymised key. For unclassified keys, use a stable label such as the column name or `KEY`, so
  that joins inside the group still hold.
- Or make `append` + `generate` the only way to write an unclassified key, and refuse the run otherwise.

Add a source-vs-target test showing 0 real key survivors in a table whose key is unclassified.

## 3. SEC-4 / D-B: PASS

The code reviewed is `553ce70` and `cb9aafc`.

**The tag.** It is `base32(HMAC(HMAC(B, subkey(DEK,"external_tokenize|E|P")), token))[:26]`, where `B` is the HKDF
of the job KEK with label `dps.v1|xtb`.
- **Deviation from D-B: accepted.** D-B's DEK-only tag can be forged by any caller, because callers hold their own
  DEK. The server-held `B` closes that hole.
- The tag binds the entity type and the profile, so a profile or entity swap is refused.
- `_detokenize_prepass` checks **every** external hint before the first call. It uses `hmac.compare_digest`
  against each key in the KEK ring and fails closed when the ring is empty. It runs after the kid/kcv checks.
- The detokenize cache only ever holds bound tokens, so a hint that is skipped (its path does not fit) can never be
  detokenized.

**Call sites.** `grep` finds only these:
- `transform.reveal`, the NDJSON body stream and the JSON-array stream, all through `engine.reveal`, all with
  `on_detokenize`;
- `tools/reveal_rows.py`, which uses the **key holder's own** tokenizer client. The engine there is built with
  `tokenizer=None`.

No other path detokenizes.

**Audit.** There is one value-free event per batch (tenant, API key id, entity, count, trace id), written before
the call. A strict sink refusal returns 503 and makes 0 calls.

**Tests.** The counting-server tests cover:
- forged, missing, replayed and DEK-only tags, a profile swap, one forged hint among legit ones, and a tag from
  another KEK: **0** `/v1/detokenize` requests each time;
- rotation through `DPS_JOB_KEK_PREVIOUS`.

## 4. Other checks (no defect found)

- **Values in logs.** I re-ran the D-A probe with `DPS_LOG_LEVEL=DEBUG` and live log capture (`da_run.log`, 115
  lines) and scanned it for 1326 distinct source values (keys, customer ids, cities, postal codes). There were
  **0** hits. The evidence dumps in the G2 tests are also asserted value-free.
- **Synthetic rows equal to real rows.** `exact_row_matches` is 0 in both probes. The reject set is the projected
  non-key columns, with pseudonymised columns removed. That removal is correct.
- **Copula draws.** They are keyed by the row anchor only. They are not conditioned on the row's real values. In
  the loans probe, `principal`, `rate` and `internal_score` have 0 row-aligned equalities.
- **Linked (MOSTLY) generation.** It writes new keys, re-numbers the FK, and nulls every column that is neither
  modelled nor sensitive. It is not affected by B1/B2. `test_linked_files.py` passes.
- **Pseudonyms.** The keyed Feistel cycle map is injective and has no fixed point. The in-place refusal
  (`synthesize_in_place_sensitive_key`, 422) happens before a job row exists, and the integration test shows it.
  Offset/prefix append of a sensitive key is refused.
- **Contract §5.3 / §6.2 / §8.3.** They name `synthesize_in_place_sensitive_key`,
  `synthesize_pseudonym_unsupported`, `external_token_not_bound`, `tokenization_unavailable`, `key_pseudonym` and
  `review_required`.
- **Metrics can fail.** The NNDR and MIA gates raise `privacy_gate` when enforced, and the tests show it.

## 5. Deviation log judgement

### Task 6
Accepted:
- `validate_run` in `targets.py`;
- keyed order for reproducibility;
- projection and INT_DIGITS;
- engine-declined columns keep their source values. These are unclassified ones. **Note:** they are covered by
  the kept review only from D-A on.
- the leakage failure for an all-real table;
- the integration test path in `tests/connectors/`.

### Task 6B
Accepted.

### Task 7
Accepted. The SDK call is unverified because the extra is not installable here. **It must run `pytest -m ml`
before the engine is enabled anywhere.**

### Task 7B
Accepted:
- `LinkedTable` interface;
- sensitive FK allowed (it is re-numbered, and the test shows it);
- exact-row rejection for linked output;
- file layout.

Two limitations are noted:
- rejection knows only the sampled rows;
- `achieved_epsilon` is always null.

### Task 8
Accepted:
- `asyncio.to_thread` not needed;
- the CA bundle becomes an SSLContext;
- `tokenization_unavailable` retry;
- compose service on the shared env anchor.

**Note 1, port collision.** The lane's `.env` does not set `DPS_TOKENIZER_MOCK_PORT`, so `docker compose config`
resolves the mock to host port **8420**. That is the port the programme plan §4 gives to m4. The m5 port per §4
is 8425. This is a lane `.env` fix, not a repository change.

**Note 2, the mock never ran in a container.** There is no `m5` image. This is deferred to T24.

### SEC-4 fix
Accepted, including the stronger formula and the API key id as "key id".

### L2 reveal fix
Accepted.

### D-A fix
Accepted in part:
- **Accepted:** the pseudonym design, the in-place and offset refusals, the kept review for unclassified columns,
  and the `smallint` refusal.
- **Rejected:** the narrowing to MEDIUM+, and the "copula models them" claim (B1).
- **Not addressed:** unclassified keys (B2).

## 6. Non-blocking issues and nits

1. **The evidence calls the pseudonym irreversible, but the seed holder can invert it.**
   - **Where:** `evidence.py`, the `key_pseudonym` entry, which says `"reversible": False`.
   - **Problem:** the Feistel map is invertible by anyone who holds the synthesis seed, and `POST /jobs/run`
     returns the seed once to the submitter. The pack should say so in words. For example: "reversible only with
     the job's synthesis seed; treat the seed like a key". That matters for the privacy officer.
2. **Pseudonyms can collide with other real ids.** In dense text or integer id spaces, a pseudonym lands on
   *another* real customer's id, for example `C` plus 8 digits with 10⁷ customers gives about 10 %. It does not
   link back to the right person. However, the programme's "per-column source≠target 0 survivors" check will flag
   it at bank scale. Consider a disjoint tier, or reject-and-walk against the source key set.
3. **An integer key too wide for its column fails mid-run.** A value ≥ 10⁹ in an `integer` column gets a
   pseudonym with the same digit count, which can exceed int4. The partition then fails with
   `incompatible_method` mid-run instead of at submit.
4. **Missing adapter or KEK surfaces as `internal_error` retries.** An L2 policy with `external_tokenize` on a
   deployment that has no adapter (or no KEK on the worker) fails at the preflight with `internal_error` retries.
   It should be a coded refusal at submit. This is already listed as open in the deviations.
5. **`qi_uniqueness.unique_overlap` is not gated.** It is measured but never fails the job or sets
   `review_required`. Combined with B1, this is how a 100 % copy passed.
6. **Reproducibility is partial.** A table larger than fit + holdout rows is reproducible only when the whole
   selection is sampled. This is noted for HANDOFF.
7. **No resume-after-kill test for a copula synthesize run.** The plan does not require one, but it would be cheap
   to add to `test_resume_after_kill`.
8. **`.env.example` still lacks the M5 variables.** The lane's permission settings blocked the edit. This is
   deferred.

## 7. Summary for the orchestrator

Fix B1 and B2 test-first, each with a source-vs-target count assertion on the LOW-classified columns and on an
unclassified key. Then re-review. SEC-4 needs no change.
