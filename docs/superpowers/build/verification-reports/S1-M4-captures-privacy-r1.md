# S1 — main after the M4 merge — captures-privacy lens, round 1

**Verdict: FAIL — three blocking findings (B1, B2, B3).**

> **Resume re-confirmation, 2026-10-15 22:03–22:12 IST (this run was relaunched after the report
> above was written; its verdict had not been recorded).** Nothing was redone that the earlier
> attempt completed; the verdict was re-checked on the same state before it is returned:
> - **Same tree and same deployed code.** `main` still @ `8ea220a`; no tracked change, no stash. The 177
>   tracked `dps/**` + `pyproject.toml` files hash the same as in the earlier run (`logs/tree.sha`) and
>   in `app`, `worker` and `browser-worker` (0 differing each). `browser-worker` is still image
>   `71eb60f19436`, started 14:16:36Z. All 11 services healthy, `/readyz` 200.
> - **Fresh direct probe + fresh OCR** (`recheck/`: own OCR cache, own `--network none` tesseract
>   container `s1m4cp-r1b-tess`, removed at the end; 46 pages, 180 PNGs, main's `.venv`,
>   `PYTHONDONTWRITEBYTECODE=1`, no `.pyc` written in the repo). Results are identical to §1, §2 and §6:
>   - S2 r3 B1a–c pages (11, `40d8b34`): masked / synthesize / obfuscate **0** on all 33 captures;
>     every control reads its value. **Fixed.**
>   - Lens items (14: `display:contents` ×3, `alt` ×2, `placeholder`, `marker`, `input_button`,
>     `r3_meta_attrs`, iframes ×3, select labels ×2): **0**, controls read.
>   - **B1** (8 label-empty pages): **2 / 2 / 2** on every page, `prefilled: []`.
>   - **B2** (5 placeholder-prefix pages): **2 / 2 / 2**; controls `r4_ph_ctrl_copy`,
>     `r4_ph_valued_copy`, `r4_ph_only_pick`: **0**.
>   - **B3** (3 listbox pages with nothing selected): names / account read 2 in every capture;
>     control `r4_lb_names_sel`: **0**.
>   - Probe servers: 156 requests, **0 non-GET**.
> - Code re-read: `dom.py:208` still pushes `o.label` (B1); `classify.py:376` prefix regex and
>   `:403-418` `draws_placeholder` unchanged (B2). `M4-DEVIATIONS.md:783-786` lists listbox options in
>   the census, which only the detectors classify; `api-m4.md` lists no listbox limit (B3 stands).
> - D4: demo counters still **16/4/2/2**. No repository file modified.

The S2 r3 open findings **B1a–c are fixed** on main (`40d8b34`): a closed select with an empty-valued
option, an option past the first 300, or `appearance: base-select` is masked. I checked them in
process, through the crawler and in the deployed browser-worker image. Across all 149 hostile pages
of rounds 1–3, main's captures are **identical to S2 r3** (status and OCR reads, case by case), except
for those 11 select pages, which now read 0. Nothing else regressed.

The lens fails on three new select shapes. Only made-up values were used to find them:

- **B1 (regression of `40d8b34`)**: a closed select whose selected option has an **empty `label`
  attribute** (`<option label="" selected>…`) draws the option's text. The new extraction reads only
  `o.label`, which is `""`, so the select is neither classified by what it draws nor boxed, even under
  "Account number".
  - The value is readable in the masked capture, both obfuscated captures, every stored crawler
    screen (including a stored obfuscated `masked_capture`) and in the deployed image.
  - The pre-fix code `cbbac38` masked the same pages: **0 → 2**.
- **B2**: `40d8b34`'s placeholder rule treats a real value as a placeholder ("Choose…") when its text
  merely *starts* with pick/please/select/choose/`--` and the option has no value, is disabled, or
  sits past the first 300. Such a value is no longer swept, so its other copies on the page stay
  readable in every capture.
  - This contradicts the rule's own docstring, its unit test intent and `api-m4.md`.
  - For a disabled option the masked capture regressed (`cbbac38` 0 → 2).
- **B3 (pre-existing since M4, not caused by the merge)**: a **listbox** (`size > 1` or `multiple`)
  with no option selected is never boxed, even when the inventory classifies it ACCOUNT_NUMBER or
  PERSON_NAME. Its options (names, account numbers) are readable in every capture and stored screen.

The rest of the lens holds:
- **Live demo scans** (both modes): clean.
- **Inventory, exports, evidence (JSON/HTML/PDF), reports, stored rows and all 15 container logs**:
  value-free.
- **D2 key custody**: holds.
- **D4**: the demo site's counters did not move (16/4/2/2 → 16/4/2/2 across 27 scans in my window).
- **N1** (persona fill values in Cookie/Referer headers) is unchanged from S2 r3. It is reported
  here, not blocking (owner decision pending).

| | |
|---|---|
| Date | 2026-10-15, 21:08–21:40 IST |
| Tree | `/Users/neelu/document_intelligence/data-privacy-services`, `main` @ **`8ea220a`**. **No repository file was modified by me.** Probes ran from `~/.cache`; no `.pyc` newer than my start in `dps/ tests/ tools/`; HEAD unchanged; no stash. At 21:31 a concurrent process (console tests of another verifier running pytest in the main checkout, known A-N2) rewrote the 54 tracked PNGs in `docs/superpowers/build/m3-shots/`. I left them as found (see N8). |
| Stack | The gates verifier's running stack (profiles docs, sources, oracle, scanner, tokenizer). I did not restart, rebuild or `down` it. At the end: 11 services healthy, `/readyz` ready, browser 1 online. |
| Code identity | All 177 tracked `dps/**` files and `pyproject.toml` have the same sha256 in the tree and in `app`, `worker` and `browser-worker` (0 differing; `logs/*.sha`). `browser-worker` runs image `data-privacy-services-browser:local` (`71eb60f19436`, Chromium 140.0.7339.16). The host probes use main's `.venv` (playwright 1.55.0, Chromium 1187). |
| Probes / outputs | `~/.cache/dps-build/reports/S1-M4-captures-privacy-r1/`: `hostile/` (site, generators, probes, shots, summaries), `hostile_cbbac38/` + `cbbac38/` (the pre-fix code via `git archive`, for comparison), `live/`, `control/`, `evidence/`, `logs/` |
| OCR | The app image's tesseract 5.5.0 in my own `--network none` container (removed at the end). Each image is upscaled 2× and read with `--psm 11` and `--psm 3`. **2 = one readable copy** (read once per pass). The live scans also get a fuzzy pass. |
| Values | Only counts and needle **names** are printed. Hostile pages use made-up values: e-mail/name `zebulon.quixotic@example.org` / "Zebulon Quixotic", PAN `AQXPZ4821K`, account `50100234567891`, and the names "Pickering Zebulon", "Pleasence Quorra" and "Ottoline Brackwater". No customer value, key, seed, OTP or cookie was printed. The OCR-cache texts that held demo-site values (3 from the positive control, 3 from `application_ref` reads) were deleted after counting. |

## 1. S2 r3 open findings B1a–c (`40d8b34`): fixed

| Where | Cases | Result |
|---|---|---|
| Direct, in process (main's code): masked / synthesize / obfuscate + an unmasked control | The 11 B1 pages of S2 r3: `r3_sel_empty_value`, `_ws_value`, `_empty_value_label`, `r3_sel_over300`, `_over300_email`, `r3_sel_base_label`, `_base_button`, `r3b_sel_empty_value_acct`, `_over300_acct`, `_over300_name`, `_base_acct` | **33 captures: 2 → 0** (S2 r3 vs now; every control still reads its value). Also 0: `r3b_sel_299_acct`, `r3b_sel_base_acct_named`, `r3_sel_base_text` and the r2 select pages. |
| Crawler (`ScanRunner` + `MemoryScanSink`), both modes | 14 select pages: 11 `r3*_sel*` (including the three `r3c_*`) and 3 r2 select pages | Every stored product screen (`original`, `annotated`, both `filled_*`, `masked_capture`) reads 0 |
| Deployed image, `--network none`, uid 10001 | Direct: `r3b_sel_empty_value_acct`, `_over300_acct`, `_base_acct`, `r3_sel_base_label`, `r3_sel_empty_value`. Crawler: `r3c_sel_empty_value_acct`, `r3c_sel_base_acct` | 0 everywhere; controls read |

This agrees with the gates verifier's §4.2.

## 2. No regression on the rounds 1–3 corpus (lens items included)

**All 149 hostile pages of rounds 1–3**, through main's code (`hostile/direct_summary.txt`):

| Item | Result |
|---|---|
| Comparison with S2 r3 | Status **and** set of OCR-read needles compared for control, masked, synthesize and obfuscate, page by page. Identical for 138 pages. The only differences are the 11 B1 pages (2 → 0). |
| Reads in non-control captures | Only the documented or carried limits, unchanged: canvas, closed shadow ×3, CSS generated content ×3, `late_input`, `late_text`, `react_rewrite`, text/drop shadow, `r2_meta` (a name in free text), `r3_split_card`, `r3_text_transform`, `r3_sel_late` (N4) |
| Lens items | All 0, with their positive controls reading: `display:contents` (`contents`, `r2_fr_contents`, `r3_contents_deep`); attribute-drawn strings (`alt`, `r3_input_image_alt`, `placeholder`, `input_button`, `marker`, `r3_meta_attrs`, listbox `label`); the 49 iframe cases; select labels |
| Crawler (r3's runner set, 36 pages × 3 runs) | Every product screen 0, except `react_rewrite`'s `masked_capture` (carried N6) |
| Deployed image | 33 direct pages and 6 crawler pages, 190 PNGs. All 33 controls read their value. Of the 157 non-control PNGs, 95 read 0; **all 62 reads are in this report's finding pages**. The lens items above (`contents`, `r3_contents_deep`, `r2_fr_contents`, `alt`, `r3_input_image_alt`, `placeholder`, `input_button`, `marker`, `iframe_plain`, `iframe_scrolled`, `r2_iframe_combo`, `r3_meta_attrs`, `select_label`, `r2_select_label_only`) read 0. |
| D4 at the probe servers | 0 non-GET requests: 852 direct requests, 987 crawler requests (951 + 36), the container run, the meta run and the `cbbac38` comparison runs |

**New pages** (`hostile/make_r4_site.py`, 49 pages: r4/r4c/r4d/r4e). These read 0 where expected:
- customizable selects with an overflowing or absolutely positioned button (`r4_cs_overflow_multi`
  is withheld: fail closed), a forced-open picker, an `<img alt>`, in a shadow root, in frames;
- a select in an open shadow root and inside an `<optgroup>`;
- `multiple size=1` past 300;
- disabled and hidden options;
- quirks mode (Chromium draws the `label` attribute there too);
- a fill from an empty-valued option.

Display:contents on a select computes to `none` (nothing drawn). A whitespace-only `label` draws
nothing.

## 3. Live scans of the demo site through the API (deployed browser-worker)

| Scan | Spec | Outcome |
|---|---|---|
| `s_01M5040K5H58E4W08M5CWA1Y0E` / `s_01M5040K60DHZ1BN5NBR056765` | synthesize / obfuscate, `/apply`, 25 pages, depth 3, 3 personas, demo session | both succeeded: 21 pages, 63 fields (51 personal), 3 captured / 0 withheld, blocked POST 62/61 + GET 1, `non_get_allowed` 0, evidence `submissions` 0 |
| `s_01M50469H913ZHAVFPVF5ZRGCV` / `s_01M50469HNXHV7P1RE74CFEH9W` | obfuscate / synthesize, `/profile` (key custody) | succeeded |
| `s_01M5047YM81T07123NPZ8D0V62`, `s_01M504QN8YTET2NBM7NMG31E2V` | synthesize with a caller-supplied seed (fill custody) | succeeded, same counts |

- **Downloads and store objects.** I downloaded everything the API serves for the main pair: scan,
  pages, page details, events, inventory and export (JSON and CSV), evidence (JSON, HTML and PDF)
  and every artefact. I also copied the raw store objects. All **73 + 76 store objects are
  byte-identical to a served download**.
- **OCR** (`live/analyze_live.*`): 67 + 67 unique PNGs (138 + 138 files).
  - `original` (19), `filled_synthesize` (12) and `filled_obfuscate` (12): **0 exact and 0 fuzzy**
    hits, in both modes.
  - `annotated` (21) and `masked_capture` (3): only `application_ref` on `/apply/review`, the known
    N9 (not a detected entity).
- **Text artefacts: 24 + 18 files, 0 hits**, including the evidence PDF text (2,590 / 2,625
  characters).
- **Positive control** (`control/ocr_control.txt`): unmasked shots, taken in a guarded context.
  - `/profile` 15/17 needles, `/profile/contact` 7/17, `/apply/review` 8/17. Same as S2 r3.
  - Demo counters during the control: +0.

## 4. Logs, database, reports, metrics, key custody

- **Logs** (`live/values_logs_db.txt`). All 15 containers were read from the end
  (`docker logs -t --tail 400000`): main's Mongo log cannot be read forward (gates N2). Coverage up to
  16:04Z: Mongo from 2026-10-14T13:54Z (400,000 lines); app, worker and browser-worker from their
  14:16Z start. **0 demo values and 0 session-cookie hits, line by line.**
- **Tables**, row by row:
  - **0** in `dps_audit_log` 5119, `dps_scan_page` 436, `dps_scan_event` 1861, `dps_scan_request` 1214,
    `dps_job` 1010, `dps_evidence` 538 and `dps_artefact` 2323.
  - `dps_scan_field` 1048 rows: 3 hits of one name token. These are **synthetic persona fill values**
    (`organization`, `address_line1` in `fill_synthesize`/`fill_obfuscate`). They contain a common
    surname that is a whole word in the persona generator's own lists (`personas.py`,
    `surrogates.py`) and also in the demo customer's name. A coincidence, by design, not page data.
- **Reports** (`live/reports_check.log`): `/reports/summary` (14 and 30 days), `/reports/jobs` (14 and
  30 days), `/reports/evidence`, `/reports/api-rollups` and `/evidence`: **0** value and 0 cookie hits.
- **Stored rows on hostile pages** (`hostile/meta_runner.txt`, 18 pages × 2 modes). The made-up
  values were placed in titles, the URL query, form actions, labels, attributes, options past 300,
  empty-valued and empty-label options, placeholder-like names, listboxes, a hidden customizable
  select and a forced picker. Pages, fields, requests, events, non-PNG artefacts and artefact meta:
  **0**.
- **D2** (`live/secret_check.*`): the DEK and the seed of both scans had **0 hits** in 15 logs,
  8 tables and every download and store object.
  - `kid` is in the job and evidence rows and files, and `kcv` in the reveal hints, by design.
  - Every finished job holds no sealed key: `dps_job.key_enc` is NULL for my 6 scans and for all
    1010 jobs.
  - `dps_llm_raw_pii_sent_total 0.0`.

## 5. Persona fill values

- **D4**: `/__received` was 16/4/2/2 at the start and at the end (by_path unchanged). Every live
  script that reads the counters measured +0 itself (the scans, both fill checks and the control).
  All 6 of my scans: `non_get_allowed` 0. Blocked: POST 60–62 + GET 1 on `/apply`, POST 10 + GET 1
  on `/profile`.
- **Storage** (`live/fill_check2.log`, seed computed locally):
  - Persona values appear only by design: `dps_scan_field`, the exports, page details and the export
    store objects. The other 31 of 38 corpora hold 0.
  - **Strict line-by-line check** (a digit form must be a whole number): **15/15 logs 0**. Mongo's
    coarse postal-code hits were digits joined across separators, as in S2 r3.
  - `credential`: 0 anywhere. `otp` as a stored field value: 0.
  - OCR of the 69 stored screens: the persona OTP is drawn in clear in both filled screens of
    `/login` (page 7). See N2.
- **Network channels** (crawler, `hostile/runner_8951.json`): **N1, unchanged from S2 r3.**

  | Page script | GET requests carrying a fill value | Non-GET |
  |---|---|---|
  | `r3_fill_cookie` | **11 of 13 per run, in `Cookie`** (10/12 in the control run) | 0 |
  | `r3_fill_referer` | **8 of 13, in `Referer`** (the fill is recorded `failed/navigated`) | 0 |
  | `r3_fill_b64` | 8 of 13, base64 in the URL | 0 |
  | `r2_fill_exfil` | 6 base64. The 6 plain values are refused (`filled_value`). | 0 |
  | `r3_fill_fragment` (control) | 0 | 0 |

## 6. Blocking findings

### B1 — A select whose selected option has an empty `label` attribute is never masked (regression of `40d8b34`)

**What happens.** `<option value="a1" selected label="">50100234567891</option>` draws its text. The
HTML "label of an option" is its label attribute only when that is not empty. Chromium draws the text,
and a whitespace-only label draws nothing.

**Cause.** The new `selectDrawn` reads `raw.push(o.label)` (`dps/layers/scanner/dom.py:208`). The
`label` IDL attribute returns the attribute's value whenever it is present, so it returns `""`. That
gives `FieldFacts.drawn == []`, so:
- `shown_texts` returns `[]` (`classify.py:388-394`);
- `sensitive_prefilled` skips the field (`classify.py:637`), whatever its classification;
- nothing is boxed, swept or classified by what it draws.

**Why this is a regression.** Before the fix, `shown_text` used `OptionFacts.drawn`, which is
`self.label or self.text` (`models.py:89`), and masked these selects.

**Proof** (OCR; every control reads its value):

| Where | Pages | Result |
|---|---|---|
| Direct, HEAD: masked / synthesize / obfuscate | `r4_sel_label_empty_acct` ("Account number"), `_pan` (unclassified "Branch": the PAN is not even detected), `_name` (autocomplete=name); `r4c_*` (with a detected e-mail); `r4d_cs_label_empty_nobutton` (a customizable select without a button is not boxed either); `r4d_sel_label_empty_iframe`; `r4d_sel_label_empty_all_options` | **2 / 2 / 2** on all 8 pages (24 captures). The inventory has `prefilled: []`. |
| Direct, pre-fix `cbbac38` (same probe, `PYTHONPATH` to a `git archive` of `cbbac38`) | `_acct`, `_pan`, `_name`, `r4c_…_acct` | **0 / 0 / 0**, `prefilled` ACCOUNT_NUMBER / PAN_IN / PERSON_NAME |
| Crawler, both modes | the label-empty pages | `original` and `annotated` read the value on every page. The stored obfuscated **`masked_capture`** reads it on `r4c_*` (acct 2 / otto 2). The filled screens read it whenever the fill leaves or picks a label-less option (`r4d_…_all_options` obfuscate: acct 2 in both `filled_*`). So the rule "a personal select stays masked whoever chose its option" fails too. |
| Deployed image | direct: 4 pages; crawler: `r4c_…_acct`, `r4_…_acct` | same: 2 in every capture, and in the stored `masked_capture` |

Evidence images:
- `evidence/B1_label_empty_account_select_masked_HEAD.png` next to
  `B1_label_empty_account_select_masked_cbbac38_prefix.png`;
- `B1_label_empty_account_select_obfuscate_HEAD.png`;
- `B1_label_empty_account_select_with_email_synthesize_HEAD.png`.

**Fix (test-first; do not weaken any check):**
- In `selectDrawn`, push `o.label || o.text`, the option's label as HTML defines it, as `OptionFacts.drawn`
  already does.
- Failing-first OCR and crawler tests with `label=""` on:
  - an account under "Account number";
  - a PAN under an unclassified label;
  - a name with `autocomplete=name`;
  - a customizable select without a button;
  - a same-origin frame;
  - a fill that picks a label-less option.
- A `shown_texts` unit case, plus a whitespace-label control.
- Fix the wording of `api-m4.md:315-321` ("its `label` attribute when it has one" → "when it is not
  empty").

### B2 — The placeholder rule stops sweeping real values whose text merely starts like a placeholder

**What happens.** `draws_placeholder` (`classify.py:403-418`) returns true when no option *in the first
300* is selected with a non-blank value and not disabled, and every drawn text matches
`PLACEHOLDER_OPTION = ^\W*(choose|select|pick|please|--)` (`classify.py:376`, a prefix test).

**Effect.** For such selects:
- `shown_values` drops the drawn text from the sweep (`capture.py:421`), in the masked capture, the
  obfuscated capture and the filled screens' `sweep`;
- the crawler takes no obfuscated capture for them alone (`crawler.py:641`).

**What it catches.** Real values like "Pickering Zebulon" or "Pleasence Quorra", and "-- Ottoline
Brackwater --" (a prefix match), in an option that:
- has no value;
- is disabled with a value;
- **sits past the first 300 options with a value** (`facts.options` stops at 300).

The docstring says "A value behind an empty value (B1a), an option selected past the listed ones
(B1b) … are never placeholders". The unit test asserts the past-the-list case
(`test_capture_select_drawn.py:367`) only with a text that does not start like a placeholder.
`api-m4.md` says "every copy of a value a select draws is boxed elsewhere on the page".

**Proof.** The select itself is boxed. The other copy (a plain-text sentence; a name in free text is
not detected) is readable:

| Page | Direct masked / synth / obf | Crawler | Deployed image | `cbbac38` |
|---|---|---|---|---|
| `r4_ph_pick_copy` (empty value) | 2 / 2 / 2 | 2 in `original`, `annotated` and both `filled_*`; `r4c_ph_pick_copy` also in `masked_capture` | 2 / 2 / 2 | 4 (select and copy both readable: B1a) |
| `r4_ph_please_copy`, `r4_ph_dash_copy` | 2 / 2 / 2 | — | — | 4 (`dash`; `please` not run there) |
| `r4_ph_disabled_copy` (disabled, with a value) | 2 / 2 / 2 | — | 2 / 2 / 2 | **masked 0** (regression in the masked capture), obfuscated 2 |
| `r4_ph_over300_copy` (index 350, with a value) | 2 / 2 / 2 | 2 in every stored screen | 2 / 2 / 2 | 4 (B1b) |
| Controls: `r4_ph_ctrl_copy` (a name that does not start like a placeholder), `r4_ph_valued_copy` (the same name with a value) | **0 / 0 / 0** | 0 | 0 | — |

Evidence images:
- `evidence/B2_placeholder_rule_name_copy_unswept_masked_HEAD.png` next to
  `B2_control_same_page_plain_name_swept_masked_HEAD.png`;
- `B2_disabled_option_copy_unswept_masked_HEAD.png` next to
  `B2_disabled_option_copy_swept_masked_cbbac38.png`.

**Scope.** This is narrower than B1: only copies of the value that the detectors miss are exposed.
It is still a fix-round rule that switches off the code's own "every copy is boxed" safety net for
real values, and regresses the disabled-option case.

**Fix:** sweep whatever a select draws, and use the placeholder rule only for the capture trigger.
Otherwise make `draws_placeholder` exact:
1. A selected option past the listed options is a value (compare `facts.drawn` with the listed
   selected options).
2. A disabled option with a non-blank value is a value.
3. A placeholder is a whole short placeholder phrase without digits or "@" (e.g. "Choose…",
   "-- Select --", "Please select"), not any text that begins with those letters.

Add the r4_ph pages as OCR and crawler tests, and `draws_placeholder` unit cases ("Pickering …" with
an empty value, disabled with a value, past the list).

### B3 — A listbox classified personal is not masked unless one of its options is selected (pre-existing)

**What happens.** A select with `size > 1` or `multiple` draws every option in its box. `shown_texts`
only returns the *selected* options' labels. So with nothing selected, `sensitive_prefilled` skips
it, even when `classify_fields` names the field ACCOUNT_NUMBER (label) or PERSON_NAME (label,
`autocomplete=name`). The census then classifies each option with the detectors only, which miss
names and bare account numbers.

**Proof** (both pre-fix and HEAD: present since M4, not caused by the merge):

| Page | Direct masked / synth / obf | Crawler (both modes) | Deployed image |
|---|---|---|---|
| `r4_lb_names_nosel` ("Beneficiary name", `autocomplete=name`, `size=4`) | all three names 2 / 2 / 2 | `original` and `annotated` read them; `r4c_lb_names_nosel`'s stored **`masked_capture`** too | same |
| `r4_lb_multi_names_nosel` (`multiple`) | 2 / 2 / 2 | — | — |
| `r4_lb_acct_nosel` ("Account number", `size=3`) | acct 2 / 2 / 2 | — | same |
| Control `r4_lb_names_sel` (one option selected) | **0 / 0 / 0** (the whole listbox is boxed) | 0 | 0 |

Evidence images: `evidence/B3_listbox_accounts_no_selection_masked_HEAD.png`,
`B3_listbox_names_no_selection_obfuscate_HEAD.png` and the control
`B3_control_listbox_names_one_selected_masked_HEAD.png`.

**Why it blocks.** It is the r2 B3 / r3 B1 class (what a select draws is not what is masked). As in
S2 r3 B1(a)/(b), it defeats the label rule: the inventory itself reports the field personal while
every stored screen shows its values.

**Fix:**
- Record listbox-ness in `EXTRACT_JS` (`el.multiple || el.size > 1`).
- A visible listbox that is classified personal, or whose option labels have a detector hit, is
  boxed whole (fail closed) whatever is selected.
- Its option labels are swept (all of them, not only the first 300; or withhold past the cap).
- Tests: the r4_lb pages (OCR and crawler), with the selected-option control.

## 7. Non-blocking

- **N1 — Persona fill values leave filled pages in `Cookie` and `Referer` headers, and base64 in
  URLs** (§5). Unchanged from S2 r3 N1. Synthetic values, in-scope hosts only, never a non-GET.
  **Owner decision pending**; reported, not blocking.
- **N2 (carried S2 r3 N2) — The persona OTP is drawn in clear in both stored filled screens of
  `/login`** (page 7, fill-custody scan). `api-m4.md:245-247` says credential and OTP values are
  "never stored or exported". Either box OTP and credential fields on filled screens, or reword the
  claim.
- **N3 — A customizable select hidden with `visibility:hidden` whose `<button>` is
  `visibility:visible`** (`r4_cs_vis_button`, `r4c_*`) draws its button. The extraction counts the
  select as invisible, so it is not boxed: the account reads 2 in every capture, including the
  stored `masked_capture`, and in the deployed image. This is contrived CSS and pre-existing
  (`cbbac38` is the same). Suggestion: count a select as visible when a rendered descendant is
  visible, and box that descendant.
- **N4 (carried S2 r3 N3/N6) — A select populated by script after the page was read**
  (`r3_sel_late`): readable in every capture. `api-m4.md` lists only inputs as a known limit.
- **N5 (carried) — Known limits and detection limits, unchanged:**
  - closed shadow roots, CSS generated content, canvas, `late_input`, `late_text`/`react_rewrite`;
  - text/drop shadows;
  - split card, `text-transform` (also `r4_sel_texttransform_lower_pan`: a lowercase PAN drawn
    uppercase in an unclassified select);
  - a name in free text (`r2_meta`).
- **N6 (carried N9) — `application_ref`** is readable in `annotated` and `masked_capture` of
  `/apply/review`, in both modes.
- **N7 (info) — Capture maps are stored with a `.png` key extension.**
  `ARTEFACT_EXT["capture"] = "png"` (`dps/storage.py:48`), so `capture_json` objects
  (application/json) are stored as `art_….png`. They are served correctly. Cosmetic.
- **N8 (info) — The main checkout's 54 `m3-shots/` PNGs were modified at 21:31 by another process.**
  A concurrent verifier's console tests in the main checkout (A-N2). I ran no pytest and did not
  touch or restore them.
- **N9 (info) — S2 r3's `secret_check` named a table main does not have (`dps_job_secret`).** On main,
  the job secret is sealed in `dps_job.key_enc`, which is covered: it is NULL for every finished job.

## 8. Probe inventory (all under `S1-M4-captures-privacy-r1/`)

- **`hostile/`**
  - `site/`: 174 files of rounds 1–3 (byte-identical copy of S2 r3), plus 49 new `r4*` pages from
    `make_r4_site.py`.
  - `common.py` (the server and OCR; adds the r4 name needles) and `probe_direct.py`.
  - `probe_runner.py`, `container_probe.py`, `meta_runner.py` and `quick_probe.py`.
  - Run scripts: `run_direct_full.sh`, `run_direct_r4.sh`, `run_runner.sh` and `run_container.sh`.
  - Results: `direct_*.json` and `direct_summary.txt` (200 pages); `runner_*.json` and
    `runner_summary.txt` (63 pages); `container_shots_results.json`; the `*.ocr.json` files;
    `shots/`, `runner_shots/`, `container_shots/`.
- **`hostile_cbbac38/`** (with `cbbac38/` = `git archive cbbac38 dps tests`): the same direct probe on
  the pre-fix code; `compare.ocr.json`.
- **`live/`**: `run_scans.*`, `analyze_live.*`, `secret_check.*`, `fill_check.*` and
  `fill_check2.log` (strict counts plus screen OCR), `values_logs_db.*`, `reports_check.*`,
  `logs_window.py` (tail-based log reader), `out/`, `out_secret/` and `out_fill/`.
- **`control/`**: the guarded positive control and the D4 counter snapshots.
- **`evidence/`**: B1–B3 images.
- **`logs/`**: code identity (`tree.sha`, `app.sha`, `worker.sha`, `browser-worker.sha`) and the
  window start.

**State left.** The stack is running as I found it, with 6 more succeeded demo scans. My OCR
container has been removed. Demo counters are at 16/4/2/2.
