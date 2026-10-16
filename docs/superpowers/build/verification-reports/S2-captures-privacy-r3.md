# S2 — branch `m4` after main was merged into it — captures-privacy lens, round 3

**Verdict: FAIL — one blocking finding (B1).**

Round 2's three blocking findings are **fixed**:
- B1, root scroll snapping;
- B2, RTL and `vertical-rl` documents wider than the viewport;
- B3, a closed select's `label` attribute.

I checked them with direct captures, the crawler and the deployed browser-worker image, and with 20 new geometry variants. Every one is masked or withheld (fail closed).

**B1** is fix round 2's own select fix, which is incomplete. In three common shapes, a closed `<select>` still draws a value that the capture neither reads nor masks:
- a selected option whose `value` is empty;
- a selected option past the first 300 options;
- a customizable select (`appearance: base-select`, which Chromium 140 ships).

In each of these, the value is readable in the stored `original` and `annotated` screens, in the masked capture and in **both obfuscated captures**. That includes a stored obfuscated `masked_capture` from the deployed image. This happens even when the select is labelled "Account number" and the inventory classifies it as `ACCOUNT_NUMBER`.

The other parts of the lens hold:
- **Live demo scans:** clean.
- **Inventory, exports, evidence, reports, logs and database rows:** value-free.
- **Key custody (D2):** holds.
- **D4 counters:** did not move.

Persona fill values, however, do leave the page through **Cookie and Referer headers** when page script puts them there. This is round 2's N1 (encoded URLs), now widened. It is non-blocking under round 2's precedent, but it needs an owner decision (N1).

| | |
|---|---|
| Date | 2026-10-15, 08:30–08:58 IST |
| Tree | `dps-wt-m4`, branch `m4` @ **`cbbac38`**. `git status` was clean at the start and is clean at the end. **No repository file was modified**: probes ran from `~/.cache`, and nothing new appeared in the worktree. |
| Stack | The gates verifier's running lane stack (profiles docs, sources, oracle, scanner, tokenizer). I did not restart or rebuild it and never ran `down`. At the end: 11 services healthy, `/readyz` ready, browser 1 online. |
| Code identity | All 177 tracked `dps/**` files and `pyproject.toml` have the same sha256 in the worktree and in `app`, `worker` and `browser-worker`: 0 differing (`S2-captures-privacy-r3/logs/00-code-identity.txt`). Playwright is 1.55.0 in the lane venv and in the image, with Chromium 1187 (140.0.7339.16). |
| Probes / outputs | All under `~/.cache/dps-build/reports/S2-captures-privacy-r3/`: `hostile/` (site, generator, probes, shots, summaries), `live/`, `control/`, `evidence/`, `logs/` |
| OCR | The app image's tesseract (`docker run --network none`). Each image is upscaled 2× and read twice (`--psm 11` and `--psm 3`). A count of **2 = one readable copy**, seen once per pass. The live scans also get a fuzzy pass (edit distance ≤ 1, or ≤ 2 for 12+ characters). |
| Values | Only counts and needle **names** were printed. Hostile pages use made-up values: e-mail `zebulon.quixotic@example.org`, PAN `AQXPZ4821K`, account `50100234567891`, card `4111 1111 1111 1111`. No customer value, key, seed, OTP or cookie was printed or logged. The one container-log copy I made (Mongo) was deleted after counting. So were the 6 OCR-cache texts that held demo-site values (the positive control). |

## 1. Round 2's blocking findings: fixed

**Direct captures**, in process, through the product's code (`hostile/probe_direct.py`):
- one page per case; four captures each (unmasked control, masked, synthesize, obfuscate);
- **149 cases, 584 PNGs OCR'd** (`hostile/direct_compact.txt`, `direct_summary.txt`).

**Crawler**: `ScanRunner` + `MemoryScanSink`, both modes plus an unmasked control (`hostile/probe_runner.py`, `runner_summary.txt`).

**Deployed image**: `data-privacy-services-browser:m4`, `--network none` (`hostile/container_probe.py`, `container_summary.txt`).

| Round 2 finding | Pages | Direct (masked / synth / obf) | Crawler, every stored screen | Deployed image |
|---|---|---|---|---|
| B1, root snapping | `r2_snap`, `_prox`, `_sections`, `_select`, `_cross`, `_text`, `_field_only` | **0 / 0 / 0** (controls e2 p4 … p1) | **0** in `original`, `annotated`, both `filled_*` and `masked_capture` | 0; viewport measured at y 201 |
| B2, RTL / `vertical-rl` overflow | `r2_rtl_wide`, `_table`, `_select`, `_wide_frame`, `_cross`, `r2_vertical_rl` | **0 / 0 / 0** | **0** | 0; viewport measured at x 1144 / 544 / 586 |
| B3, closed select `label` | `r2_select_label_only` | **0 / 0 / 0** (control p2) | **0** | 0 |
| Extra fix: a select the fill chooses | `r3_sel_fill_choice` | — | filled screens show a `PAN_IN` box (`evidence/fixround_fill_chosen_select_masked_filled_synthesize.png`) | 0 |

`evidence/r2B1_fixed_snap_stored_original.png` and `r2B2_fixed_rtl_wide_stored_original.png` show every box sitting exactly on its value.

**New geometry variants, all 0 in every capture** (controls read 2–4 copies):

| Group | Cases |
|---|---|
| Snapping | inline `!important` snapping (`r3_snap_inline`: the masks follow the page at y 201); `body` as the snapping scroller; horizontal root snapping (x 324); RTL wide page with `both` snapping (viewport x 1120, y 174) |
| Direction and writing mode | `<body dir=rtl>` only (it propagates to the viewport); `body` `vertical-rl`; `vertical-lr` |
| Transforms and zoom | `html` transform scale (its frame painted whole) and translate; `zoom: 0.8` on an RTL page |
| Fixed and top-layer | fixed header and fixed frame on snapped pages; modal `<dialog>`; `popover` |
| Frames | an RTL, snapping child document; a frame inside a scaled root (painted whole) |

Pages that keep moving fail closed:
- `r3_scroll_restore` and `_raf`, which pin the scroll position: every capture is withheld (`mask`).
- `r3_marquee` and `r3_ticker`: the original is withheld. The obfuscated capture rewrites the text and reads 0.

**Lens items re-run, all 0 in every capture** (direct, and the frame cases in the crawler too):
- **Iframes:** every round 1 and round 2 frame case, 49 in all, plus the fill-scrolls-a-frame crawler cases. `r2_drop_shadow` is not counted here: its CSS-drawn copy is N7.
- **`display:contents`:** `contents`, `r2_fr_contents`, and a new nested grid and button case.
- **Attribute-drawn strings:**
  - `alt`, placeholder, input button and listbox `label`;
  - `::marker`;
  - a new `input type=image` alt and a `textarea` placeholder;
  - labels and attributes in `r3_meta_attrs`.

## 2. Live scans of the demo site through the API (deployed browser-worker)

| Scan | Spec | Outcome |
|---|---|---|
| `s_01M4YRG8VD43YCB4W9X4S42DTV` / `s_01M4YRG8VQB10QEFR55ZFBYJ4J` | synthesize / obfuscate, `/apply`, 25 pages, depth 3, 3 personas, demo session ref | both succeeded: 21 pages, 63 fields (51 personal), 3 captured / 0 withheld, blocked POST 60 + GET 1, `non_get_allowed` 0 |
| `s_01M4YRTF7F6AK9CTW2QT4T03S1` | synthesize, caller-supplied seed (fill custody) | succeeded, same counts |
| `s_01M4YRX71Z4AN3SAH04751JG36` / `s_01M4YRX72PKXQARRZAK0NNZC58` | obfuscate / synthesize, `/profile`, 6 pages (key custody) | succeeded |

**Downloaded everything the API serves for the main pair.**
- That is: scan, pages, page details, events, inventory and export (JSON and CSV), evidence (JSON, HTML and PDF), and every page artefact.
- I also copied the raw store objects (`docker compose cp`).
- All **73 + 76 store objects are byte-identical** to a served download.

**OCR** (`live/analyze_live.{log,json}`): 67 + 67 unique PNGs (138 + 138 files).
- `original` (19), `filled_synthesize` (12) and `filled_obfuscate` (12): **0 exact and 0 fuzzy hits**, in both modes.
- `annotated` (21) and `masked_capture` (3): only `application_ref` on `/apply/review`, the known N9 (not a detected entity).

**Text artefacts: 24 + 18 files, 0 hits.** That includes the evidence PDF text: 2588 and 2623 characters.

**Positive control** (`control/ocr_control.txt`): unmasked full-page shots at 1280×900, same OCR and needles.
- `/profile` 15/17 needles, `/profile/contact` 7/17, `/apply/review` 8/17. The same as rounds 1 and 2.
- This time I took them inside the scanner's guarded context: the demo counters moved **+0**. In round 2 the unguarded control moved them by +4 POST.

## 3. Logs, database, reports, metrics, key custody

- **Values** (`live/values_logs_db.txt`): line by line and row by row, after all my runs.
  - All 15 container logs: **0** demo values and **0** session-cookie hits.
  - Tables, all **0**: `dps_audit_log` 4573 rows, `dps_scan_page` 806, `dps_scan_field` 1933, `dps_scan_event` 3424, `dps_scan_request` 2270, `dps_job` 556, `dps_evidence` 280, `dps_artefact` 2736.
  - `dps_llm_raw_pii_sent_total 0.0`.
- **Reports** (`live/reports/`): `/reports/summary` (14 and 30 days), `/reports/jobs` (14 and 30 days), `/reports/evidence`, `/reports/api-rollups` and `/evidence` all had **0** value and 0 cookie hits.
- **Hostile pages, stored rows** (`hostile/meta_runner.txt`). Ten pages, both modes. The made-up values sat in:
  - the title, the scanned URL's query, a form action, links, labels, placeholders and `aria-label`/`title`;
  - an `aria-describedby` error;
  - and options (past index 300, empty-valued, customizable).

  Every stored page, field, request, event, non-PNG artefact and artefact meta: **0**.
- **D2 key custody** (`live/secret_check.log`): the DEK and the seed of both scans had **0 hits** in 15 logs, 9 tables (`dps_job_secret` included) and every download and store object.
  - `kid` appears in the job and evidence rows and the downloads, and `kcv` in the reveal hints. Both are by design.
  - `run_scans.log`'s coarse total of 101 counts every response field of 16+ characters, so it includes these identifiers.

## 4. Persona fill values

- **D4:** the demo site's `/__received` stayed at **88/20/11/10** from start to end, across 5 live scans and the control. `by_path` did not change, the guard refused 60 POST and 1 GET per scan, and `non_get_allowed` was 0.
- **Storage** (`live/fill_check.log`, seed computed locally):
  - Persona values appear **only** where they belong by design: `dps_scan_field`, the export JSON/CSV with their store objects, and page details. The other 31 of 38 corpora have **0**.
  - `credential`: 0 anywhere. `otp`: 0 as a stored field value.
  - The fill check's coarse scan also reported 3 `postal_code` hits in the Mongo log. Line by line they are **0** as whole numbers: the scan joins digits across separators (the gates verifier's N7).
- **Network channels** (`hostile/runner_summary.txt`). The crawler ran in both modes. The probe server counted, in memory, the requests carrying one of the guard's own fill needles (counts only). → **N1**.

  | Page script | GET requests carrying a fill value | Non-GET |
  |---|---|---|
  | `r3_fill_cookie`: saves typed values in `document.cookie` (a draft cookie) | **11 of 13 in each run, in the `Cookie` header.** That includes the next page's navigation, so the value travels with every later request of the scan. | 0 |
  | `r3_fill_referer`: mirrors form state into its URL with `history.replaceState` (the common SPA pattern) | **8 of 13, in the `Referer` header** of same-origin passive requests. The crawler records the fill as `failed/navigated`, but by then the values have left. | 0 |
  | `r3_fill_b64`: base64 in an image URL (round 2's N1) | 8 of 13 | 0 |
  | `r2_fill_exfil` (round 2's page) | 6 base64. The 6 plain values are refused (`filled_value`). | 0 |
  | `r3_fill_fragment`: state in the URL fragment (control) | 0 | 0 |

## 5. Blocking finding

### B1 — A closed `<select>` still draws values the capture neither reads nor masks (three shapes)

Fix round 2 made `shown_text` and `capture._shown` return `OptionFacts.drawn` (the label attribute, else the text). The doc now says: "A closed select is masked by what it draws" (`api-m4.md:315`). That holds only for a classic select whose selected option is among the first 300 and has a non-empty `value`.

| Shape | Cause | Cases (made-up values) |
|---|---|---|
| **(a) The selected option's `value` is empty or blank.** Typically a server-rendered default such as `<option value="" selected>` holding the customer's primary account. | `classify.py:376`: `shown_text` keeps only `o.selected and o.value.strip()`, so it returns `""`. `sensitive_prefilled` then skips the field, even one classified `ACCOUNT_NUMBER` by its label. Nothing sweeps it either: the census skips `select` subtrees (`dom.py:420`). | `r3_sel_empty_value`, `_ws_value`, `_empty_value_label` (an unlabelled "Branch" select); `r3b_sel_empty_value_acct` (labelled "Account number") |
| **(b) The selected option sits past the first 300 options**: long branch, beneficiary or account lists. | `dom.py:225`: `Array.from(el.options).slice(0, 300)` drops it, and `truncated` is not set. `shown_text` returns `""`, so it is the same failure as (a), even for a select classified personal. | `r3_sel_over300` (index 350), `r3_sel_over300_email`, `r3b_sel_over300_acct` ("Account number", 400 options), `r3b_sel_over300_name` ("Beneficiary name", `autocomplete=name`). Control: the same page with the account selected at index **299** is masked (`r3b_sel_299_acct`: 0 / 0 / 0). |
| **(c) Customizable select** (`select, ::picker(select) { appearance: base-select }`, on by default in this Chromium). The closed select draws its `<button>`: either a `<selectedcontent>` clone of the selected option's **content**, or the author's own text. It does not draw the `label` attribute. | `models.py:89`: `drawn` returns the label attribute. `EXTRACT_JS` skips text under `select`/`option`/`button` (`SKIP_TEXT`, `dom.py:84`) and `CENSUS_JS` skips `select` subtrees, so the button and `<selectedcontent>` text is never read. | `r3_sel_base_label` (option `label="Branch B"`, content holds the PAN); `r3_sel_base_button` (`<button>Debit from …PAN</button>`); `r3b_sel_base_acct` ("Debit from", content holds the account) |

**Proof (OCR; every control reads its value).** Each row reads one readable copy (2), and only the select's value is read. Other values on the same pages are masked.

| Where | (a) | (b) | (c) |
|---|---|---|---|
| Direct, in process: masked / synthesize / obfuscate | 2 / 2 / 2 in all 4 cases | 2 / 2 / 2 in all 4 cases | 2 / 2 / 2 in all 3 cases |
| Crawler, stored `original` and `annotated`, both modes | 2 (annotated 1–2) | 2 (annotated 1–2) | 2 |
| Crawler, stored filled screens | 0: the fill picks another option | 0 | **2** in `filled_synthesize` and/or `filled_obfuscate` |
| Crawler, stored obfuscated **`masked_capture`** (`r3c_*`: the same selects on a page that also renders an e-mail, so a capture is made) | **acct 2, both modes**. The e-mail is tokenised. | **acct 2, both modes** | **acct 2, both modes** |
| Deployed image, `--network none` (direct and crawler, `r3c_*` included) | same | same | same |

Images:
- `evidence/B1a_empty_value_option_account_select_stored_masked_capture.png`
- `evidence/B1b_option_past_300_account_select_stored_masked_capture_deployed_image.png`, next to `B1b_control_same_select_option_299_masked.png`
- `evidence/B1c_customizable_select_selectedcontent_obfuscate.png` and `B1c_customizable_select_button_masked.png`

**Why it blocks.** It is the round 2 B3 class: what a select draws is not what the code reads. Round 2 rated that blocking at lower likelihood. Shapes (a) and (b) also defeat the label rule: a select the inventory itself reports as `ACCOUNT_NUMBER` is stored readable in every screen. All of this is M4 code about to be merged into main:
- the 300 cap dates from `80b8790`;
- the `value` filter from `27cf286`;
- `drawn` from `cbbac38`.

**Fix (do not weaken any check):**
1. **Read every selected option, whatever its index.** For example, extract `Array.from(el.selectedOptions)` in addition to the capped list. Otherwise, mark the snapshot `truncated` when a select has more options than the cap.
2. **Base `shown_text` on what is drawn, not on `value`.** The "real option" rule belongs to the fill (`personas_fill._usable`). Masking a drawn placeholder ("Choose…") costs nothing.
3. **Read what a customizable select draws.** When `getComputedStyle(select).appearance === 'base-select'`, or it has a `button`/`selectedcontent` child, read the rendered text of its button as the shown value, and let the census descend into it. Or, failing closed, box the whole select whenever it draws text.
4. **Update `api-m4.md:315`.**
5. **Tests, failing first.**
   - Pixel/OCR and crawler tests for (a), (b) with the index-299 control, and (c). Assert that the masked capture, both obfuscated captures and every stored screen read 0, against an unmasked control.
   - A classification test (`shown_text`) for each shape.
   - The pages in `hostile/site/` (`r3_sel_*`, `r3b_*`, `r3c_*`; made-up values; generator `hostile/make_r3_site.py`) can be copied as fixtures.

## 6. Non-blocking findings

- **N1 — Persona fill values leave the page in request headers** (round 2's N1, widened; §4).
  - The guard checks only URLs for fill values (`guard.py` `_readings`/`_carries_value`) and lets passive requests of filled pages through.
  - Two browser-added headers carry values that the page put somewhere:
    - **`Cookie`**: in plain text, and persistent. Every later request of the scan carries it.
    - **`Referer`**: in plain text, whenever the page reflects its state in its URL.
  - The encoded passive URLs (base64) of round 2 are still open.
  - These are synthetic persona values or their tokens, sent only to in-scope hosts, never a non-GET. So I keep round 2's non-blocking rating. But "persona fill values never leave the page" does not hold, and the guard docstring's premise ("only passive resources … still load") misses headers the browser adds.
  - Suggested fix: snapshot the context's cookies at `arm` and restore them when the filled page closes; abort any request whose `Cookie` or `Referer` (`request.all_headers()`; the proxy sees them over plain HTTP) carries a needle; continue passive requests of filled pages without a `Referer`; decode base64/hex in `_readings`. **Owner / D4 decision.**
- **N2 (round 2's N2, unchanged code) — the persona one-time code shows in clear in the stored `filled_synthesize` of `/login`** (`live/out/synthesize/art/p07_filled_synthesize_*.png`). `api-m4.md:245-247` says credential and OTP values are "never stored or exported". The value is synthetic. Either box OTP and credential fields in the filled screens, or reword the claim.
- **N3 — a select populated by script after the page was read** (`r3_sel_late`): readable in all captures. This is the select form of the documented known limit "an input value a script sets after the page was read". The doc lists inputs only. Either re-read field values at measure time, or extend the doc.
- **N4 — detection limits seen in the captures. These are not masking defects; they follow the precedent of round 2's N7.**
  - **A value split across inline elements is never detected**, because classification works per text node: a card number in four `<span>`s, or a PAN one letter per span (`r3_split_card`, readable in all captures). With a header or label hint, each piece is caught and the capture withholds or masks (`r3_split_card_label`: masked withheld; synthesize withheld `leakage`; obfuscate 0).
  - **A lowercase PAN drawn uppercase by `text-transform`** is missed by the case-sensitive PAN pattern (`r3_text_transform`).
  - A name in free text (`r2_meta`, round 2's N7) is unchanged.
  - Suggestion: run detection over the rendered text of inline runs (block `innerText`, mapped back to nodes), and case-fold before the PAN pattern.
- **N5 (carried, unchanged) — the documented known limits still read as documented:** closed shadow roots (`closed_shadow*`, `closed_dsd_only`), CSS generated content (`css_attr*`, `css_literal_only`), canvas text and `late_input`.
- **N6 (carried) — late text in the obfuscated capture.** `late_text` (synthesize and obfuscate e2 p2) and `react_rewrite` (the stored `masked_capture` shows the observer's new e-mail and PAN). This is still not in the doc's known limits.
- **N7 (carried) — CSS-drawn copies.** `r2_text_shadow` (masked e2) and `r2_drop_shadow` (masked e2 p2). The obfuscated captures read 0.
- **N8 (carried, N9) — `application_ref`** is readable on `/apply/review` in `annotated` and `masked_capture`, in both modes.
- **N9 — information.** A page that pins its scroll position (`r3_scroll_restore*`) is never captured: every capture is withheld. That is correct fail-closed behaviour, and an availability cost.

## 7. Probe inventory (all under `S2-captures-privacy-r3/`)

- **`hostile/`**
  - `make_r3_site.py` and `site/`: the 121 pages of rounds 1 and 2 (cross-origin ports templated) plus 52 new `r3*` pages;
  - `common.py`: the server and the OCR;
  - `probe_direct.py`, `probe_runner.py`, `container_probe.py`, `meta_runner.py`;
  - `ocr_shots.py`, `summ_direct.py`, `summ_runner.py`;
  - `direct_compact.txt`, `direct_summary.txt`, `runner_summary.txt`, `container_summary.txt`, `meta_runner.txt`;
  - `shots/`, `runner_shots/`, `container_shots*/` with their `*.ocr.json`.
- **`live/`**: `run_scans.py`/`.log`, `analyze_live.*`, `fill_check.*`, `secret_check.*`, `values_logs_db.*`, `reports_check.py` and `reports/`, `out/`, `out_fill/`, `out_secret/`.
- **`control/`**: the guarded positive control and counters.
- **`evidence/`**: B1 images and the round 2 fix proofs.
- **`logs/`**: code identity and the final counters.
