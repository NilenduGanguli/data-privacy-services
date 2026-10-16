# S8-spec review, round 1 re-check (fspec at 41f30d6): schema synthesis from a sample at realistic sizes

**Verdict: FAIL, with 2 blocking findings.** Both are small, and I validated a prototype fix for each.

The fix round closed the previous review's B1 and B2:
- The 20 x 100 acceptance holds: **0 refusals in 880 calls**.
- 22 of 23 planted real-copy forms give 422, with no echo.
- Wide decimal bounds now stay inside the column.
- ID-like keys avoid their sampled values.
- P1 holds for sample values.

Two defects remain:
- **B1.** The digit rule still lets token copies through when they are joined by separators that never occur in a date, an amount or a range (`_`, `;`, `|`, quotes, brackets, `#`, `+`). It answers 200 with sampled customer IDs as tokens on a realistic sample. M0 refused these, and the task's token rule counts them.
- **B2.** The new `_Steering.give_up` makes a realistic sample that `a46d1c5` synthesized fail with 422 every time. This is a regression of the S8 goal itself.

Earlier review of `9e38165`/`a46d1c5`: kept as `S8-spec-review-r1-at-a46d1c5.md`. Probes, logs and outputs for this re-check: `/Users/neelu/.cache/dps-build/reports/S8-spec-review-r1/at-41f30d6/`.

All probes are in-process (TestClient over `create_app`, the real route) unless stated. Samples are invented and outputs are counts; no values are printed. I modified no repository file. The worktree is clean at `41f30d6`, and the lane stack is stopped (`docker compose --profile docs stop`).

Commits reviewed: `ec74985` (code, tests, `api-m1.md`, a comment in `batch.py`) and `41f30d6` (M1-DEVIATIONS). The author and committer are `NilenduGanguli <nilendu.ganguli@gmail.com>`, with no trailers.

## 1. Gates (run by me on lane `dps-wt-fspec`, `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 435 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success, 150 files |
| `pytest -q` whole suite (lane venv, lane `dps-db` + `azure-ocr-mock`) | **3 290 passed, 354 skipped, 5 failed** (841 s, `pytest-full.log`). The 5 are exactly the pre-existing failures that fail identically on an exported `e1f3a3a` tree (`baseline-5-failures.txt`): scanner capture x2, crawler x2, playground NER coverage. |
| schema files (`test_schema_profile`, `test_api_schema`, `test_schema_gen`) | 125 passed |
| **20 x 100 through the route** (`p1_acceptance.py`) | `LIVE` (the task's sample) and `MINE` (an independent variant) both 200 in every call: 20/20 with patterns, 20/20 with NER, 300/300 with patterns, 100/100 with NER. **0 refusals in 880 calls.** Client-side check (own normaliser): 0 generated values equal a sampled value of the same swept column. Sampled-ID raw tokens appear only as the integer part of a `MINE` balance (`<ID>.99`, `<ID>.9`; `p1b-token-class.txt`), by design. |
| **planted leaks through the route** (`p2_planted.py`, written into the rows after the executor) | **22/23 real-copy forms give 422 `leakage` with no echo**, under patterns and NER. These include the r1 forms `1001, 1002`, `1001 1002` and the space datetime. `1003-07` gives 200, a documented trade-off. The 4 accidents give 200: an ID inside a date, inside `21001.5`, as the integer part of `1001.25`, and an exempt city. |
| phone forms (`p12`) | `+91 9810010012`, `+91-98100-10012`, `1001.00`, `1001, 1002` and `1001 1002` are now refused, as in M0. `1003/1004` and `1003-07` are not, which is documented. |
| DDL bounds (`p4`) | All 10 columns are within the column. `NUMBER(30,10)` 9.999999999999998e19, `NUMBER(38,10)` 9.999999999999997e27, `DECIMAL(65,30)` 9.999999999999998e34. JSON Schema edge cases return no 500. |
| doomed sample (`p10`) | 422 `leakage` in 0.04 s (100 rows) and 0.29 s (10 000 rows), with nothing echoed |
| realistic sizes (`p5`) | 1 000-row Faker sample to 10 000 rows: 3/3 return 200 in 3.3 s, with same-column equality 0 |
| no sample (`p6`) | Executor output is byte-identical to `e1f3a3a` (hash files equal) |
| determinism (`p8`) | Same seed gives the same rows with redraws > 0. An `Idempotency-Key` replay is identical. A spec-only replay differs in exactly `sample_redraws` rows, as documented. |
| guard == sweep (`p19`) | Differential fuzz of 30 000 random strings: the indexed guard and sweep both equal an independent naive reading of the documented rule (0 differences, 8 474 flagged) |

### P1 (`p18_prompt_ids.py`, `p3_prompt_p1.py`)

Setup: the LLM is faked at `LlmGateway._stellar`, with the real `complete()` and the strict egress verifier running.

Results for the `LIVE` sample and a sample of ID-like keys (`id`, `order_ref`, `invoiceNo`, `account_number`, `phone_number`, `txn_key`), with NER on and off:
- **0 sample values in the provider messages** (0/27 and 0/48).
- 0 sample values in the response `profile` or metadata.
- `FieldStats.id_values` is referenced only inside `schema_profile.py`: never logged, never rendered into a prompt, never returned.

Unchanged and pre-existing (N2 below): the data-like keys `arjun_mehta`, and `Mumbai` with NER off.

## 2. Blocking findings

### B1. Separators that never occur in a date, an amount or a range still join digits, so token copies pass the sweep and the guard

- **Where:** `dps/engine/synth/schema_profile.py:1283`, `_words` (it splits on whitespace only). The guard and the sweep share this through `_SampleValues._tokens`.
- **Mechanism:** `normalise` drops every non-alphanumeric character, and `_words` only cuts at whitespace. So any other character between two digit groups joins them into one run. A short all-digit original then has to equal a whole whitespace-delimited span of that run.
- **What the task asked for:** "numeric originals match as whole values/tokens (digit-run boundaries), never inside other numbers or dates ... WITHOUT weakening real leak detection".
- **What the docs say:** the docstring of `_SampleValues`, `api-m1.md` and M1-DEVIATIONS all say that "`-`, `/`, `.`, `:` and a comma with no space after it still join digits (dates, amounts, ranges)". In the code, *every* non-whitespace separator joins.
- **The inconsistency:** `TXN_1003` is found (a letter is a break), but `TXN_4821_1003` is not.

**Sweep level** (`p13-separators.txt`, cust_id 1001-1004 plus opening dates plus a PHONE column). Each of these is refused by M0 `verify_output`. Each is **not** refused by the sweep, and **not** flagged by the guard:
- `1001;1002`
- `1001|1002`
- `["1001","1002"]`
- `TXN_4821_1003`
- `1001_1002`
- `(1001)(1002)`
- `#1001#1002`
- `1001+1002`
- two sampled dates, `2016-09-02;2019-04-11`
- a sampled date before `_10:15:00`
- `[2016-09-02]10:15`

**Through the real route, on a realistic sample** (`p14_code_columns.py`, `p14-code-columns-1000.txt`). The sample is Faker en_IN with 1 000 rows: 4-digit `cust_id`, name, e-mail, opening date, and two common bank-data shapes. Both shapes become `pattern` generators from their value shape:
- `txn_ref` `TXN_dddd_dddd`
- `related`, a JSON list string of two customer IDs, `["dddd","dddd"]`

Synthesizing 1 000 rows gives **200 in 3/3 runs, with `verification.passed`**. Even so, **176-202 generated `related` values and 178-208 `txn_ref` values per run hold a sampled customer ID as a delimited token.** The random 4-digit groups coincide with the 1 000 sampled IDs about 11% of the time, and nothing steers them. This is the class the task called wrong ("the wider ID range can also reproduce an exact sample ID"; "a generated value equal to a sampled ID in any field still fails"), and S8 already steers it for `CUST-1003` and `x1003@...`.

**Fix (prototype validated, `p15_proto_rule.py`; not a repository change).** In `_words`, cut words at whitespace and also at every character that is neither a letter, a digit nor one of the joiners `-` `/` `.` `:` `,`. In practice: split on `[^\w\-/.:,]|_` after NFKC, or equivalently on any character `normalise` drops other than the joiners.
- Adding cuts only adds candidate spans, so this matches a superset of the current rule. It is also still not M0: dates, amounts and ranges stay joined.

Prototype results:
- All forms above are refused (`p15-proto-p13.txt`).
- The accidents still pass: `2010-01-04`, `21001.5`, `1001.25`, `1001-2020`, `1003-07`.
- `p14` gives 200 in 3/3 with **0** sampled-ID tokens. The redraws (~900-1 000 per 1 000 rows) absorb it.
- `LIVE`/`MINE` give 20/20 + 20/20 200 (`p15-proto-p1.txt`).
- Planted results are unchanged: 22/23 refused, accidents pass (`p15-proto-p2.txt`).
- 1 000-row samples to 10 000 rows give 3/3 200 in 3.5-3.6 s. With mobile + PAN: 4.6-4.8 s (current code: 3.3 s).

Tests to add:
- In `LEAKS`: `1001;1002`, `1001|1002`, `["1001","1002"]`, `TXN_4821_1003`, `2016-09-02;2019-04-11`, `2016-09-02_10:15:00`.
- Update `_naive_found` to the new break set. `ACCIDENTAL` stays unchanged.
- A route test: a sample with a `TXN_9999_9999` code column gives 200, and no sampled ID appears as a token.
- Correct the three documentation sentences so they match the break set.

### B2. `_Steering.give_up` stops steering a field around the swept values when only its own ID test was exhausted, so samples that `a46d1c5` synthesized are now refused 3/3

- **Where:** `dps/engine/synth/schema_gen.py:651-658`, `_Steering.give_up`. Its `else` branch runs `self.how.pop(field.name, None)`.
- **Mechanism:** an ID-like key whose generated range is (almost) covered by its sampled values exhausts its own `avoid_in` test on the first rows. `give_up` then removes the field from `how` altogether. From then on it is no longer steered around the **sample guard** either. Any later value that equals another swept field's sampled value is kept, and the sweep refuses the run.
- **Repro** (`p16_giveup.py`, through the route): a 1 000-row sample with `id` 1000..1999 (dense: a "later page", or ids starting at 1000) and `cust_id` = 1 000 random 4-digit values (swept as CUSTOMER_ID).
  - **`a46d1c5`: 200 in 3/3** (`p16-giveup-a46d1c5.txt`; `id` steered around the sampled cust_ids, 0 equal).
  - **`41f30d6`: 422 `leakage` in 3/3** (`p16-giveup-41f30d6.txt`).
  - Any dense ID-like column next to a swept identifier of the same digit length hits this. With 5-digit ids and 5-digit customer IDs, about 11 overlapping values already make a refusal near-certain at 1 000 rows.
  - The fail-closed sweep still holds (no leak). But this is a new false refusal of exactly what S8 was asked to make work.
  - The unit test `test_an_id_like_key_avoids_its_sampled_values` misses it, because its doomed `id` range (5001..5004) does not overlap the swept `cust_id` values.
- **Fix (prototype validated, `p17_proto_giveup.py`):** in the own-only branch, drop only the field's own test (`self.own.pop(field.name, None)`). Remove the field from `how` only when there is no guard (`self.avoid is None`). Result: 200 in 3/3, 0 generated ids equal a sampled cust_id (`p17-proto-giveup.txt`). The documented behaviour stays: the field keeps its own sampled values when it cannot avoid them.
- **Test to add:** the doomed-`id` case with a swept field whose sampled values lie inside the `id` range must answer 200, with no id equal to a swept value.

## 3. Non-blocking findings and owner decisions

- **N-a (documented trade-off).** Some digit forms are still not refused:
  - comma lists without a space: `1001,1002`, `[1001,1002]`. The comma is ambiguous with thousands separators and the decimal comma.
  - `-` / `/` pairs: `1003-07`, `1003/1004`, `1001-1002`. These are ambiguous with dates and ranges.
  - Both are recorded in M1-DEVIATIONS. B1's fix keeps them as they are.
- **N-b.** Zero-width and other Unicode format characters also join digits (`1001​1002`). DPS generators do not produce them. If B1 is fixed by "every character `normalise` drops, except the joiners", this case is covered too.
- **N-c (P1, pre-existing since `0fd76d0`, recorded as N2).** Identifier-like data keys that the detector does not flag still reach the model: `arjun_mehta`, and `Mumbai` with NER off (`p3-p1-*.txt`, identical to `0fd76d0`). This lies outside this branch's change, but it is the one open P1 item for the owner before v1.0.0. Option: alias every sample key that is not a known schema word.
- **N-d.** When an ID-like key's range is the sample's, the key keeps its sampled values (documented). In `p17`, about 600 of 1 000 generated `id`s are sampled ids. Generic surrogate ids are not tied to a sample record, so this is acceptable as documented. Widening such a field's range to its order of magnitude, as for collapsed ranges, would let it avoid them.
- **N-e.** As before: N1 (absence over narrow date spaces), N4 (3-digit IDs) and N5 (spec+seed replay reveals the coincident values) are recorded in M1-DEVIATIONS and `api-m1.md`. The N6 comment is fixed.

## 4. Acceptance summary

| Item | Status |
|---|---|
| 1. 20 x 100 gives zero 422s | **met** (880/880, two samples, NER on and off) |
| 1. A planted email, name or ID leak gives 422 | **met** for 22/23 forms, with the documented `1003-07` exception |
| 1. Numeric originals match as whole values/tokens, never inside numbers or dates | inside numbers and dates: met. Tokens joined by `_ ; \| " [ ] ( ) # +` are **not met (B1)**. |
| 1. CITY: steer or exempt with rationale | exempt with measured rationale (unchanged from round 1). No STATE or COUNTRY type exists. |
| 1. ID-like integer fields avoid sample values | met. The give-up when impossible is documented (N-d), but it **regresses steering (B2)**. |
| 2. DDL bounds exact | met, including wide decimals |
| 3. Record the e1f3a3a and S8 rules | met. The joiner sentence must be corrected with B1. |
| Gates | ruff and mypy clean. Suite 3 290 passed / 5 pre-existing failures (identical to `e1f3a3a`). |
