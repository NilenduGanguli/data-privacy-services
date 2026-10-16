# S8-spec review, round 1 (fspec): schema synthesis from a sample at realistic sizes

**Verdict: FAIL, with 2 blocking findings.** Most of the work is sound. The 20 x 100 acceptance holds robustly, and planted whole-value leaks fail closed. But the new digit rule loses some real copies that M0 caught (B1), which the task explicitly forbids ("WITHOUT weakening real leak detection"). Separately, the exact-bounds rule rounds wide decimal columns *up* (B2). Both fixes are small. A prototype of the B1 fix is validated below.

Reviewed: branch `fspec`, HEAD `a46d1c5` (docs) on top of `9e38165` (code) and `e1f3a3a` (base). The worktree is clean, and I modified no repository file. The author/committer of all 3 commits is `NilenduGanguli <nilendu.ganguli@gmail.com>`, with no trailers. The lane image `data-privacy-services:fspec` holds byte-identical copies of the 4 changed source files (sha256 checked in the container).
Probes, logs and outputs are in `/Users/neelu/.cache/dps-build/reports/S8-spec-review-r1/`. All probes are in-process (TestClient over `create_app`, the real route) unless stated otherwise. Values were never printed: the samples are invented and the outputs are counts.

## 1. Gates (run by me, lane `dps-wt-fspec`, `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 435 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success, 150 files |
| `pytest -q` whole suite (lane venv; lane `dps-db` + `azure-ocr-mock` up) | **3282 passed, 354 skipped, 5 failed** (942 s), `pytest-full.log` |
| The 5 failures on an exported `e1f3a3a` tree, same venv/env | **the same 5 fail** (`baseline-5-failures.txt`): `scanner/test_capture.py::test_obfuscated_capture_tokens_the_dom_and_reveals_with_the_key`, `scanner/test_capture_residual.py::test_masked_capture_boxes_every_copy_of_a_detected_value`, `scanner/test_crawler.py::test_full_demo_crawl_in_memory`, `::test_dialogs_dismissed_and_popups_closed`, `test_playground_samples.py::test_text_sample_with_ner_on_still_never_returns_the_name`. These are pre-existing and unrelated (L3 scanner / L1 NER coverage). |
| M1 gate `DPS_TEST_REQUIRE_SERVICES=1 -m "docs or ocr"` | 139 passed, 2 skipped (tesseract) |
| schema files (`test_schema_profile`, `test_api_schema`, `test_schema_gen`) | 117 passed (including the real-Presidio live-sample case) |
| **20 x 100 through the route** (`p1_acceptance.py`) | the live sample (`LIVE`) and an independent variant (`MINE`, same 7 columns, other names/cities/dates): **20/20 200** each, patterns and Presidio NER. Extended: **500/500** (patterns) and **200/200** (NER) per sample, so **0 refusals in 1 480 calls** (1 680 with the token-class run). On `e1f3a3a`: LIVE 20/20 refused (both), MINE 17/20 (patterns) and 19/20 (NER) refused. |
| independent client-side check on those 1 480 outputs | 0 generated values equal to a sampled value of the same swept column (cust_id, full_name, email, opened). Raw-token sampled IDs appear only as the integer part of a decimal balance (`<ID>.99`, `p1b-token-class.txt`; "inside another number", by design). Sampled cities do appear (exempt). |
| **planted leaks through the route** (`p2_planted.py`, the value written into row 7 after the executor) | 19 real-copy forms give **422 `leakage`, value not echoed**: e-mail exact/re-cased/in another field, name exact/re-cased/in a sentence/hyphenated, ID int/str/float/`CUST-`/in an e-mail/`1,003`/full-width/in a sentence, ID as a key, e-mail as a key, date exact, date in an ISO `T` datetime. **4 real-copy forms give 200: see B1.** Accidents (ID inside a date, inside `21001.5`, as the integer part of `1001.25`) give 200 as intended, and so does an exempt city. |
| doomed run (a sample whose IDs fill the range, `p10_doomed.py`) | 422 `leakage` in 0.04 s (100 rows) / 0.27 s (10 000 rows), nothing echoed |
| declared bounds (`p4_bounds.py`, DDL through the route, 2 000 rows) | `NUMBER(3)` max 999, `NUMBER(5,2)` max 999.99, `NUMBER(20)` 10^20-1, `NUMBER(38)` 10^38-1, `NUMBER(18,2)` 9999999999999998.0, `NUMERIC(12,2)` 9999999999.99, `NUMBER(1,1)` 0.9, all correct. **But `NUMBER(30,10)` 1e+20, `NUMBER(38,10)` 1e+28, `DECIMAL(65,30)` 1e+35 exceed the column: B2.** JSON Schema edge cases (fractional integer bounds, empty integer interval, min>max, 1e99, 10^30) give no 500s. The empty interval falls back to text, as documented. |

Other checks that passed:
- **Without a sample the executor is byte-identical to `e1f3a3a`** (`p6_identity.py`: 14 generators incl. unique retries, nulls and a rule; 3 seeds x 150 rows; equal sha256 and retry counts).
- **Determinism with steering** (`p8_determinism.py`): the same sample+seed gives the same rows when `sample_redraws` > 0. An `Idempotency-Key` replay is identical (`Idempotent-Replay: true`). A spec-only replay differs in exactly `sample_redraws` rows (documented).
- **Realistic sizes** (`p5_realistic.py`, Faker en_IN, 8-10 columns):
  - 200 to 1 000 rows: 5/5 OK.
  - 1 000 to 1 000: 5/5 OK.
  - 1 000 to 10 000: 3/3 OK in 3.8-3.9 s.
  - With mobile and PAN columns, 1 000 to 10 000: 3/3 OK in 4.4-4.6 s.
  - Same-column equality 0 throughout.
- **CITY / CARD_EXPIRY value spaces** measured: 316 cities and **48** expiries over 20 000 draws. The exemption rationale's numbers are accurate. The taxonomy has no `STATE`/`COUNTRY` type (checked `dps/engine/taxonomy.py`).
- **Item 3 is recorded:** `M1-DEVIATIONS.md` has a new last section (in its format) with the e1f3a3a rule change (flagged keys aliased in the prompt only and mapped back, replacing the M1 Task 12 rule) and every S8 rule. `api-m1.md` is updated.
- **Tests:** only the two documented M1 test changes, and no other assertion was removed or weakened. The forced-leak tests go through the route and check that nothing is echoed.

## 2. P1 (prompts) — `p3_prompt_p1.py`

The LLM is faked at `LlmGateway._stellar`, so the real `complete()` and the strict egress verifier run. Tested on `/schema/generator-spec` and `/schema/synthesize`, with NER on and off:
- **Live sample: 0 of 27 sample values in the messages handed to the provider.** `source: llm`, no fallback, no egress block.
- **Flagged and non-identifier keys are aliased `field_N` in the prompt.** Checked: e-mail key, `Arjun Mehta`, phone-number key, `cust_1001`, PAN-like, Aadhaar-like, and a nested e-mail parent. The reply maps back, and spec/rows keep the caller's names (the e1f3a3a rule, recorded).
- **Pre-existing, not a regression (N2):** an identifier-like data key that the detector does not flag reaches the model as is. Examples: a person's name in snake_case `arjun_mehta` (NER on and off), and a city key `Mumbai` with NER off. **Identical on `0fd76d0`** (before e1f3a3a; `p3-p1-base-0fd76d0.txt`), so neither e1f3a3a nor 9e38165 changed what reaches the prompt.

## 3. Blocking findings

### B1. The new digit rule misses real copies M0 caught: digits next to other digits across whitespace, a country code or a separator

- **File:** `dps/engine/synth/schema_profile.py`, `_SampleValues.found` / `__call__` (shared by the guard and the sweep).
- **What changed:** S8 removes separators *before* checking digit-run boundaries. An all-digit original then counts only when it equals a whole run of the normalised text, so whitespace and punctuation no longer act as boundaries.
- **What the task asked for:** "numeric originals match as whole values/tokens (digit-run boundaries) ... WITHOUT weakening real leak detection".

Through the route (`p2-planted-patterns.txt`), each of these is a verbatim copy that `verify_output` (M0, the pre-S8 sweep) refuses, and each now answers **200 with the value in the body**:
- `"1001, 1002"`: two sampled customer IDs.
- `"1001 1002"`.
- `"2016-09-02 10:15:00"`: a sampled date in a space-separated datetime. The same value with `T` is caught, which is inconsistent.
- `"1003-07"`.

At sweep level, with a PHONE column (`p12-phone-forms.txt`), these are also not refused (M0 refuses all of them):
- **a sampled mobile `9810010012` written `+91 9810010012` or `+91-98100-10012`.** This is the format DPS's own PHONE generator uses (`+ phone`).
- `"1001.00"` as a string.
- `"1003/1004"`.

Why it matters: phones, Aadhaar numbers, cards and account numbers are digit-only originals. A real customer's mobile with a country code is the natural rendering of a copy, and the sweep is the fail-closed backstop that P1 relies on.

**Fix (validated in-process, `p7_hybrid_rule.py`, rule `combo`; no repository change).** Keep the S8 whole-run rule and add two matches:
1. **A digit original of 9+ digits** (longer than any date's 8) keeps M0's substring rule. No false-positive class exists at that length. This restores phones with country codes, Aadhaar, cards and account numbers.
2. **A shorter digit original** also matches when it spans whole **whitespace-delimited** tokens. That is, it starts and ends at a break: string start/end, a letter, or whitespace in the raw text between the neighbouring characters. `-`, `/`, `.`, `:` and a comma without a following space keep joining digits, so dates and amounts stay protected.

Prototype results (`p7-combo.txt`, `p5-realistic-combo.txt`):
- LIVE 200/200 and MINE 200/200 at 100 rows.
- 22 of the 23 planted real-copy forms give 422. The exception is `1003-07`, joined like a date and so ambiguous, consistent with the documented `1001-2020`. At sweep level `1003/1004` stays unmatched for the same reason.
- The implementer's `ACCIDENTAL` fixtures: 0 flagged. `LEAKS`: 0 missed.
- `+91 9810010012` / `+91-98100-10012` / `1001, 1002` / space datetime: refused.
- Realistic 1 000-row samples (with phone/PAN) to 1 000 rows: all 200.

The prototype is naive (`str.find` per needle) and slow at 10 000 rows. An implementation should split each normalised digit run at whitespace breaks and look up every contiguous span of segments in the existing `_digits` dict (k ≤ ~5 segments per run).

Optional extra: read numeric strings (`1001.00`, `1,001.00`) as their integral value, as `_canon` does for floats.

Tests to add: each form above as a planted leak through the route (422, no echo), the implementer's ACCIDENTAL list still passing, the 20 x 100 acceptance, and guard == sweep on the new forms.

### B2. Exact DDL bounds round *up* for wide decimal columns (low severity, one-line fix)

- **File:** `dps/engine/synth/schema_profile.py`, `_column_top`.
- **Cause:** `Decimal(10) ** digits - Decimal(1).scaleb(-scale)` runs in the default 28-digit decimal context. When integer digits + scale > 28, the subtraction rounds the bound **up**, so `exact` becomes `10**digits`. The `while Decimal(repr(top)) > exact` loop then never runs.
- **Result through the route, default context** (`p4-bounds.txt`):
  - `NUMBER(30,10)` gives `max 1e+20`.
  - `NUMBER(38,10)` gives `1e+28`.
  - `DECIMAL(65,30)` gives `1e+35`.
  - `DECIMAL(38,18)`, common in warehouse/banking DDL, gives `1e+20`.
  - Each is **above the column's maximum**, which contradicts item 2 ("kept exactly ... not rounded") and the deviation log ("rounded down, never up"; "a decimal column's the largest float whose decimal form is not above the bound").
- **Impact:** generated values stayed inside the column in 2 000 draws (a uniform draw almost never reaches its max). But the returned spec, a reusable artefact, declares a bound the column cannot hold. With a prec-200 context propagated into the worker thread, the same code gives a correct below-max value (9.999999999999997e+27 for `NUMBER(38,10)`), which confirms the cause. A direct `profile_from_ddl` check gives `DECIMAL(38,18)` max 1e+20 and `NUMBER(38,10)` max 1e+28, both above the column.
- **Fix:** compute `exact` under `decimal.localcontext()` with `prec >= digits + scale + 2`. Add `NUMBER(38,10)` and `DECIMAL(38,18)` to `test_declared_bounds_are_kept_exactly`, asserting `Decimal(repr(top)) <= exact` with exact computed at high precision.

## 4. Non-blocking findings and owner decisions

- **N1. Steering reveals the sample by absence much sooner than the record says (owner decision).** `p9-absence.txt` uses one-year opening dates (Faker en_IN, plausibly "accounts opened in 2023"). The prediction tested is "the absent days are the sampled days":

  | Sample rows | Output rows | Precision | Recall |
  |---|---|---|---|
  | 50 | 1 000 | 0.80 | 1.00 |
  | 200 | 1 000 | 0.99 | 1.00 |
  | 200 | 5 000 | 1.00 | 1.00 |

  - **A 1 000-row sample (or 500 to 5 000) is refused 3/3:** 422, because the sample exhausts the day space as cities do (`p5-realistic.txt` narrow).
  - **The deviation log's "Aggregate effect" understates this.** It gives the example "opening dates over nine years against 10 000 rows". By the same rationale that exempted CITY, narrow date spaces leak membership by absence or are refused.
  - The owner should decide between steering, exempting, or refusing per field when the sample covers a large share of the field's value space. At minimum, the record should carry these numbers.
- **N2. P1, pre-existing:** identifier-like data keys that the detector does not flag reach the model (`arjun_mehta`; `Mumbai` without NER). This is unchanged since `0fd76d0` (section 2).
  - Consider aliasing every sample key by default, or every key that is not a known schema word.
- **N3.** "Key/ID-like integer fields avoid exact sample values" is implemented only for fields DPS sweeps: detected identifier types (`cust_id` = CUSTOMER_ID, `account_no`) and flagged keys. Generic integer keys such as `id` and `ref` (`p11-profile-*.txt`) are neither swept nor steered, so a generated `id` may equal a sampled one. This is consistent with the sweep's scope, but narrower than the task's wording. It needs either a note in the record or steering of id-like integer keys.
- **N4.** Originals shorter than `SWEEP_MIN_LEN` (3-digit IDs) are neither swept nor avoided. This is M0's rule, and a 3-digit space is low-cardinality anyway.
- **N5.** Replaying the returned spec+seed *without* the sample regenerates exactly the coincident values that steering removed. Whoever holds output + spec + seed can therefore learn which values coincided with the sample (a membership signal). The seed is caller-custodied secret material (D2), so this is low risk. Worth one sentence in `api-m1.md` next to the replay caveat.
- **N6.** The `batch.CHANCE_EQUAL_ENTITIES` comment still says it mirrors `SWEEP_EXEMPT`, which is now a superset (the implementer noted this; it is L2 code).

## 5. Acceptance summary

| Item | Status |
|---|---|
| 1. 20 x 100 gives zero 422s | **met** (1 480/1 480 with two samples, NER on/off) |
| 1. Planted email/name/ID leak gives 422 | **met** for whole values. **Not met for the B1 forms** (two IDs in one value, a space datetime, a phone with a country code). |
| 1. Numeric originals as whole values/tokens, never inside numbers/dates | inside-number/date: met. **Tokens: not met (B1).** |
| 1. CITY: steer, or exempt with rationale | exempt, with measured rationale; CARD_EXPIRY likewise. No STATE/COUNTRY type exists. |
| 1. ID-like integer fields avoid sample values | met for swept identifier fields (N3) |
| 2. DDL bounds exact: `NUMBER(3)`, `NUMBER(5,2)` | met. **Wide decimals round up (B2).** |
| 3. Record the e1f3a3a and S8 rules | met (an N1 correction is advised) |

The lane stack was stopped with `docker compose --profile docs stop`.
