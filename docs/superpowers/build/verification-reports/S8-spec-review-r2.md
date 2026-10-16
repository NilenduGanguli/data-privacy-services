# S8-spec review, round 2 (fspec at c5b5fc2): schema synthesis from a sample at realistic sizes

**Verdict: FAIL.** There are 2 blocking findings:
- **B1** is a real leak class.
- **B2** is minor: one line.

Both have a one-line fix that I validated as a prototype through the real route (no repository change).

The fix round itself is correct and complete:
- **r1 B1 closed.** Every non-joiner separator now breaks a digit run.
- **r1 B2 closed.** A field that gives up on its own values stays steered around the swept ones.
- The acceptance holds: 0 refusals in 680 calls of 20 x 100 and 300 x 100. All 38 planted copies are refused, with no echo.
- The gates are green, apart from the 5 pre-existing failures.

What remains is the comma:
- `_BREAK` keeps every comma as a joiner.
- A comma-joined list of IDs is therefore read as one number. Examples: a CSV list in a cell, `1001,1002`, or a compact JSON integer list, `[1001,1002]`.
- So a sampled customer ID in such a list is neither steered nor refused.
- On a realistic sample, 11-12 % of the generated values of such a column hold a sampled customer ID. The run still answers 200 with `verification.passed`.

The r1 rationale for the comma covers a comma before 2-3 digits: thousands or lakh grouping, or a decimal comma. It does not cover a comma before 4 or more digits, which is never a number.

## Method

- **Commit reviewed:** `c5b5fc2`, parent `41f30d6`.
  - Author and committer: `NilenduGanguli <nilendu.ganguli@gmail.com>`, with no trailers.
  - Paths: `schema_profile.py`, `schema_gen.py`, 2 test files, `api-m1.md`, `M1-DEVIATIONS.md`.
- **Probes:** in process, through the real route (TestClient over `create_app`).
- **LLM:** faked at the provider seam `LlmGateway._stellar`, so the real `complete()` and the strict egress verifier ran.
- **Data:** samples are fictitious, and the outputs are counts only.
- **Repository state:** I modified no repository file. The worktree is clean at `c5b5fc2`, and the lane stack is stopped (`docker compose --profile docs stop`).
- **Evidence:** `/Users/neelu/.cache/dps-build/reports/S8-spec-review-r2/`.
  - My probes are in `probes/` (`r2_accept.py`, `r2_planted.py`, `r2_wide.py`, `r2_proto.py`).
  - The r1 reviewer's probes, re-run here, are in `r1probes/`.

## 1. Gates (lane `dps-wt-fspec`, `./lane.sh`)

| Gate | Result |
|---|---|
| `ruff format --check dps tests tools` | 435 files already formatted |
| `ruff check dps tests tools` | All checks passed |
| `mypy dps` | Success, 150 files |
| `pytest -q`, whole suite (lane `dps-db` + `azure-ocr-mock`; `pytest-full.log`) | **3 294 passed, 354 skipped, 5 failed** (831 s). The 5 are exactly the pre-existing baseline that fails identically on `e1f3a3a` (`failures.txt` = r1 `baseline-5-failures.txt`): scanner capture x2, crawler x2, playground NER coverage. |
| M1 gate `DPS_TEST_REQUIRE_SERVICES=1 pytest -m "docs or ocr"` | 139 passed, 2 skipped |
| Schema files (`test_schema_profile`, `test_api_schema`, `test_schema_gen`) | 129 passed |
| The 5 new or extended tests against the `41f30d6` code (scratch export) | **5 failed, 87 passed**: they catch both r1 defects |

## 2. Acceptance

### 20 x 100 through `POST /api/v1/schema/synthesize` (`r2_accept.py`, my probe)

The sample is the task's own: 7 columns and 4 rows (cust_id 1001-1004, full_name, email, city, segment, balance, opened).

Modes:
- **stub:** the heuristic spec.
- **model:** a fake provider answers from the prompt the way a model would. It alternates `sequence{start:1000}` and `integer{1000..9999}` for integers, gives a date range from `year_range`, and a decimal for amounts.

| Mode | NER | Calls x rows | Status | Same-column equal | Sampled-ID tokens | Sample values in prompt |
|---|---|---|---|---|---|---|
| stub | off | 20 x 100 | **200: 20/20** | 0 | 0 | - |
| stub | off | 300 x 100 | 200: 300/300 | 0 | 0 | - |
| stub | on | 20 x 100 | **200: 20/20** | 0 | 0 | - |
| model | off | 20 x 100 | **200: 20/20** | 0 | 0 | 0 |
| model | off | 300 x 100 | 200: 300/300 | 0 | 0 | 0 |
| model | on | 20 x 100 | **200: 20/20** | 0 | 0 | 0 |

That is **0 refusals in 680 calls**.

How the client-side oracle works:
- It uses its own normaliser.
- "Same-column equal" covers cust_id, full_name, email and opened.
- A token is a sampled ID that is not next to a digit and not joined to one by `- / . : ,`.

With NER on, the key `cust_id` is flagged, so the model's `sequence` is used. The sequence skips the sampled IDs: 0 copies.

### Planted leaks through the route (`r2_planted.py`, my probe)

**Part A.** One form of a sampled value is written into row 3 after the executor, so no steering can reach it. **34/34 real-copy forms give 422 `leakage` with no echo**, under NER off and patterns. The forms:
- email exact, and email upper-cased in another column
- name with `_`, and a dotted name in text
- ID as an int, a float and a `1004.00` string
- the separator forms: `;`, `|`, `TXN_4821_1003`, `["1003","1004"]`, `(1003)`, `#`, `+`, `ID:1003`, `id=1003&x=1`, tab, newline, NBSP, apostrophe, middle dot, en dash, minus sign, a ZWJ inside the ID, and full-width digits
- in a nested object and a nested list
- as a key: ID and email
- dates: exact, as an int, a `;` pair, `_time` and `[date]time`

The documented joined forms answer 200 by design: `1003-07`, `1003/1004`, and full-width `／`. So do the 3 accidents: an ID inside a date, `21003.5` and `1003.25`.

Two forms pass that M0 refused:
- `1003,1004` and `[1003,1004]`: this is **B1**.
- `１００４．００`: this is **B2**.

**Part B.** The model proposes a literal holding a sampled value. **4/4 answer 422 `leakage`**: a `constant` e-mail, a `constant` `TXN_1003`, a `choice` holding a sampled name, and a `pattern` with the literal `C-1002;99`.

### r1 findings re-checked (r1 reviewer's probes on `c5b5fc2`)

| Probe | Result |
|---|---|
| p13, separator forms (sweep, guard, M0) | Every non-joiner form is refused, and the guard equals the sweep: `;`, `\|`, JSON string list, `_` code and pair, parentheses, `#`, `+`, `=&`, ZWSP, NBSP, narrow NBSP, newline, date`_`time, `[date]time`, `date;date`. Not refused: `json_list_int` and `comma_nospace` (**B1**), and the `-` and `/` forms and accidents (documented). |
| p14, code and list columns (1 000-row sample to 1 000 rows, x3) | **200 3/3, 0 values holding a sampled token** (redraws 934-1 036). At `41f30d6`: 176-208 per column. |
| p16, dense `id` next to swept `cust_id` (x3) | **200 3/3, 0 `id` equal to a sampled cust_id** (redraws 264-279). At `41f30d6`: 422 3/3. 598-628 of 1 000 `id`s equal a sampled `id` (N-3). |
| p10, doomed sample | 422 `leakage`, no echo: 0.05 s at 100 rows, 0.34 s at 10 000 rows |
| p5, realistic sizes (1 000-row Faker sample to 10 000 rows, x3) | base: 200 3/3 in 3.64-3.77 s. Phone + PAN: 200 3/3 in 4.73-4.79 s. Same-column equality 0. |
| p6, no sample | Executor hashes identical to `e1f3a3a` (`p6-identity-c5b5fc2.txt` = r1 `p6-identity-e1f3a3a.txt`) |

### Wide bank sample (`r2_wide.py`, my probe)

The sample has 16 columns: cust_id, name, e-mail, 12-digit account, IFSC, pincode, `+91` mobile, Aadhaar 4-4-4, card 4x4, dob, a space datetime, address, `BR_dddd`, `Ref#dddd; ok`, and two list columns.

| Sample to output | Result |
|---|---|
| 200 to 1 000 (x3) | 200 3/3, 0.65-0.73 s |
| 600 to 1 000 (x3) | 200 3/3, 0.83-0.9 s |
| 600 to 10 000 (x2) | 200 2/2, 7.9-8.1 s |

In every run, same-column equality is 0 for all 10 personal columns. The new break set causes no false refusal. The list columns are **B1**.

### P1 and logs

- **Sample values (`p18`, `p3`, `r2_accept` model mode):** 0 in the provider messages, the response `profile` or the metadata, for LIVE, IDS and KEYED, with NER off and on.
- **Data-like keys:** unchanged since `41f30d6` and `0fd76d0` (N-2).
- **`FieldStats.id_values`:** read only inside `schema_profile.py`.
- **Log scan** of the in-process app over 5 model calls plus the 48 planted calls: 0 of 24 sampled values (IDs, names, name parts, e-mails, dates).

### Code review of the fix

- **`_words`:** after NFKC, it cuts at `_BREAK` runs and normalises each piece, so the concatenation equals `normalise` of the whole value.
  - The added cuts can only add candidate spans, so the new rule matches everything the round-1 rule matched.
  - It is linear: each left mark tries at most 8 right marks.
  - `_BREAK`'s alternatives are disjoint, so there is no backtracking blow-up.
- **`_Steering.give_up` and the two budgets:**
  - The own test is dropped once.
  - The guard-flagged value is redrawn or skipped with a fresh `MAX_SAMPLE_REDRAWS`.
  - A second exhaustion sets `lost`.
  - Without a guard, the field leaves `how` as before.
  - `_skip` and the redraw loop agree.
  - Benign by analysis: `give_up` also drops the own test when the guard, not the own test, used up the budget. A field then exhausts the guard 20 times in a row only when the swept values cover most of its range. Its own values are then mostly swept values too, so they stay avoided.
- **Docs:** `api-m1.md`, the docstrings and `M1-DEVIATIONS` now match the break set and the give-up rule. The exceptions are the comma (B1) and full-width whole numbers (B2).

## 3. Blocking findings

### B1. A comma before four or more digits joins them, so comma-joined ID lists hold sampled IDs that are neither steered nor refused

- **Where:** `dps/engine/synth/schema_profile.py:157`, `_BREAK = re.compile(r"((?:[^\w\-/.:,]|_)+)")`. Every comma is a joiner. The guard and the sweep share this through `_words`.

**Why it is a defect, not the r1 trade-off (N-a):**
- The task: "numeric originals match as whole values/tokens (digit-run boundaries) ... while a generated value equal to a sampled ID in any field still fails ... WITHOUT weakening real leak detection".
- In `1001,1002` or `[1001,1002]`, the ID is a whole token: the comma ends the digit run, and the value is not inside a number or a date.
- M0, and `e1f3a3a` before S8, refused these.
- The recorded rationale (M1-DEVIATIONS 1919-1920: "a comma with no space is ambiguous with thousands separators") holds only for a comma before 2 or 3 digits: `1,001`, the lakh grouping `10,01,002`, or a decimal comma `1001,25`.
- A comma before a group of 4 or more digits is never a numeric grouping.
- DPS generators never write a decimal comma: amounts are floats and dates are ISO.
- These formats are common. JavaScript's `JSON.stringify` writes compact JSON lists, `json.dumps(..., separators=(",", ":"))` does too, and bank exports often put several IDs in one CSV cell.

**Measured through the route** (`r2-wide-*.txt`). The two columns hold other customers' IDs: `related_csv` as `dddd,dddd` and `related_json` as `[dddd,dddd]`. Both become `pattern`.

| Sample | Output rows | Runs answering 200 | Values per column holding a sampled cust_id as a list element |
|---|---|---|---|
| 600 rows | 1 000 | 3/3 | 110-123 (11-12 %) |
| 600 rows | 10 000 | 2/2 | 1 149-1 226 |
| 200 rows | 1 000 | 3/3 | 33-56 |

Every one of those runs has `verification.passed`. This is the same class as r1's B1 (176-208 per 1 000 values), only with a comma.

**Fix (prototype validated, `probes/r2_proto.py comma|both`).** Join on a comma only before a group of exactly two or three digits:

```python
_BREAK = re.compile(r"((?:[^\w\-/.:,]|_|,(?![0-9]{2,3}(?![0-9])))+)")
```

Prototype results:
- **Refused now** by both the sweep and the guard: `1001,1002` and `[1001,1002]`.
- **Unchanged:**
  - `1,001` is found as 1001, as it is now.
  - `10,01,002`, `1,00,01,002`, `1001,25`, `48,250.50` and `1001,100` stay joined.
  - All of `ACCIDENTAL` passes, and the other `JOINED` forms pass.
  - All of `LEAKS` is refused.
  - The 3 000-string `_words == normalise` property holds (0 violations; `r2-proto-lists.txt`).
- **Through the route:**
  - LIVE 20 x 100, stub with NER off: 20/20 200.
  - LIVE 20 x 100, model with NER on: 20/20 200.
  - Wide 600 to 1 000: 200 3/3 with **0** list values holding a sampled ID (redraws 1 067-1 102).
  - Wide 600 to 10 000: 200 in 9.3 s (8.0 s without the change).

**Tests:**
- Move `1001,1002` and `[1001,1002]` from `JOINED` to `LEAKS`.
- Add `10,01,002`, `1001,25` and `48,250.50` to `JOINED`.
- In `_naive_found`, a comma is a break unless exactly 2-3 digits and then a non-digit follow it.
- Add a route test: a sample with a compact JSON integer-list column and a CSV-list column of customer IDs. It must answer 200 with 0 sampled IDs as list elements. It fails on `c5b5fc2`.

**Docs:** fix the comma sentences in:
- the module docstring, `_SampleValues` and `verify_against_sample`
- `docs/technical/api-m1.md` around line 160
- `M1-DEVIATIONS.md` lines 1919-1920 and 1949

### B2 (minor). The whole-number check runs before NFKC, so a full-width `１００４．００` passes although `1004.00` is refused

- **Where:** `dps/engine/synth/schema_profile.py:1298-1300` (`_words`). The check `"." in text and _INTEGRAL_TEXT.fullmatch(text)` sees U+FF0E, not `.`.
- **Effect:** NFKC then gives `1004.00`, which normalises to the digit run `100400`, so the sampled ID is not found.
- **Planted through the route:** 200, while M0 refuses it (`r2-planted-ner0.txt`, `q_fullwidth_integral`).
- **Contradiction:** `api-m1.md` says "full-width forms count as their ASCII ones", and the `_words` docstring says the string is "read after NFKC".
- **Scope:** narrow. DPS generators never write full-width text, so it matters only for a spec literal or a planted value.
- **Fix (validated in the same prototype):** start `_words` with `text = unicodedata.normalize("NFKC", text)`.
  - In `test_the_sweep_reads_a_string_as_normalise_does`, skip strings whose NFKC form is a whole number. Otherwise a string such as `1，0.0` (full-width comma), which NFKC turns into a whole number, would trip the property.
  - Add `１００４．００` to `LEAKS`.

## 4. Non-blocking findings and owner decisions

- **N-1.** A whole number inside text, such as `Rs 1004.00`, is not refused (200; M0 refuses). The whole-number reading applies to a whole string only. This is the documented rule, and it is the same class as the `1001.25` accident.
- **N-2 (P1, pre-existing; r1 N-c, implementer N2).** Data-like identifier keys that the detector does not flag still reach the model.
  - `p3` KEYED: `arjun_mehta` with NER off and on, and `Mumbai` with NER off.
  - The result is identical at `41f30d6`, and the keys also reach the model at `0fd76d0` (that commit sent them unaliased).
  - `e1f3a3a` and this branch only add aliasing.
  - This is the one open P1 item to decide before v1.0.0. Options:
    - alias every sample key that is not a known schema word;
    - run the detector on the key with `_` read as a space.
- **N-3 (r1 N-d).** A dense ID-like key that cannot avoid its own values keeps them: 598-628 of 1 000 generated `id`s are sampled ids in p16.
  - This is documented.
  - `id` is not a sensitive key under the D-A taxonomy.
  - Widening such a field's range to its order of magnitude would let it avoid them.
- **N-4.** These remain documented trade-offs, ambiguous with dates and ranges: `-` and `/` pairs (`1003-07`, `1003/1004`, `1001-1002`), and `:` and `.` between 4-digit groups.

## 5. Acceptance summary

| Item | Status |
|---|---|
| 1. 20 x 100 with zero 422s | **met**: 0 refusals in 680 calls (stub and model, NER off and on) |
| 1. A planted email, name or ID gives 422 | **met** for 34/34 forms after the executor and 4/4 model literals, with no echo. Not refused: comma lists (**B1**) and full-width whole numbers (**B2**). |
| 1. Numeric originals match as whole values or tokens, never inside numbers or dates | Inside numbers and dates: met. Every separator other than a joiner is a break (r1 B1 fixed). Comma-joined tokens: **not met (B1)**. |
| 1. CITY | Exempt, with the measured rationale (unchanged). There is no STATE or COUNTRY type. |
| 1. ID-like integer fields avoid sample values | Met, with the documented give-up (N-3). The give-up no longer stops steering around swept values (r1 B2 fixed; p16 200 3/3). |
| 2. DDL bounds exact | Met (r1; unchanged by this commit) |
| 3. Record the e1f3a3a and S8 rules | Met. The comma sentences change with B1. |
| Gates | ruff and mypy clean. Suite: 3 294 passed, 5 pre-existing failures. M1 gate 139 passed. |
