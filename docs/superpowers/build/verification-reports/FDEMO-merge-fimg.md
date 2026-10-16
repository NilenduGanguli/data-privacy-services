# FDEMO — `fimg` merged into `fdemo`: Task 24 and the UI scanner under the demo tools

**Verdict: DONE.** `fimg` is merged into `fdemo` with both sides of every conflict kept, the stream's
work (the deck builder and the presenter script) is brought in line with what came in (test first),
and every gate is green on this lane's `--profile full` stack. The one exception is the known smoke
check `api responses are not cacheable` (M5 Task 17 S11, which lives on lane `fsec`), the same single
failure `fimg` reports; it is not an `fdemo` defect and no check was weakened. The worktree is clean,
the stack is stopped (`docker compose stop`, volumes kept).

| | |
|---|---|
| Worktree / branch | `/Users/neelu/document_intelligence/dps-wt-fdemo`, `fdemo`: `2223661` → **`2b31b7a`** |
| Merged | `fimg` @ **`3f8af02`** (Task 24 + S4b; M4 as merged on `main` `5959a75`, integrate fixes to `8ea220a`) |
| Commits (author `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer) | `a0f8e3e` merge (parents `2223661`, `3f8af02`; the given message) · `7141406` `fix(fdemo): slide 10 shows the demo site's count only with the smoke's D4 positive controls` (`tools/build_deck.py`, `tests/test_deck.py`) · `2b31b7a` `docs(fdemo): the presenter script and the deviation log follow the merged scanner and Task 24's D4 proof` (`docs/demo/DEMO-SCRIPT.md`, `docs/superpowers/build/M5-DEVIATIONS.md`) |
| Pushed | not by me: the owner's autopush loop pushed `fdemo` `2b31b7a` at 22:44:37 after its author, trailer and gitleaks checks (`~/.cache/dps-build/autopush.log`, `held=0`) |
| Date | 2026-10-15, 22:05–22:55 IST |
| Evidence | `~/.cache/dps-build/fdemo-merge-fimg/`: `logs/` (gates, smoke, flow, D4 snapshots, LLM counter, privacy scans, source-vs-target, OCR), `probe/` (scripts, lane-adapted copies of S4b's), `flow-report-A.md`, `render/` (slide 10 fixture renders), `m3-shots-from-run/` |

## 1. Merge

`git merge --no-ff fimg -m "merge: fimg into fdemo (bring in Task 24 (demo_client, compose full with the
scanner, smoke) and the UI scanner (fimg contains m4) needed by the demo tools)"`. Two conflicts, both
resolved by keeping both sides:

- **`HANDOFF.md` §1, the `dps` package row:** `fimg` added the `scanner` extra, `fdemo` the `deck`
  extra; the result is one row naming both. Checked by script: every non-blank line of both sides is
  in the result except fdemo's one changed line (that row), which the combined row replaces; no line
  that is in neither side, besides the combined row.
- **`docs/superpowers/build/M5-DEVIATIONS.md`:** `fimg`'s "Task 24" section first, then this lane's
  "Task 26B", "Task 26" and "S6a review r1 fix" sections. Checked by script: 0 lines of either side
  missing, 0 lines in neither side.
- **Auto-merged and reviewed:** `pyproject.toml` (`scanner` and `deck` extras side by side) and
  contract §1's extras sentence (both). Everything else came from `fimg` unchanged: the merge commit
  differs from `fimg` by exactly this lane's 7 files (3319 insertions, 1 deletion, the same as
  `fdemo`'s own diff from the merge base `64c46e3`).
- The merge commit holds only the resolutions (the index was staged with the resolution-only
  deviation log); the adaptations are the two commits after it.

## 2. Consistency with what came in (test first)

| What | Why | Change | Red → green |
|---|---|---|---|
| **Deck S9 contract and slide 10** (`tools/build_deck.py`) | S6a's contract read `posts_received`, a key the demo site does not have (`/__received` counts `non_get`, `form_get`, `logout_hits`, `admin_hits` and, since S4b, `get`), and printed an emerald 0 without proof that the counters belong to the scanned site, the gap S4b closed in the smoke with two positive controls | S9 facts now mirror the smoke's D4 check: `scan.{pages,fields,personas,submissions,non_get_allowed}` (the pack's scan block), `received.{non_get,form_get,logout_hits,admin_hits,get}` (counter after less before the scan), `sends_blocked` (the pack's `blocked_requests` less GET/HEAD). Card "Non-GET requests received by the site" shows the count only when `received.get >= max(1, scan.pages)` and `sends_blocked >= 1`, caption "counted by the demo site, which served the scan N GETs; the browser blocked M sends"; otherwise placeholder `S9 D4 positive controls`, listed missing, `--strict` exits 1. Emerald only when no counter moved and `non_get_allowed` is 0, rose otherwise. Docstring contract updated | `tests/test_deck.py` 30 → **42**: 1 positive (emerald 0 + controls, `--strict` 0), 7 unproven/malformed → placeholder + strict 1 (no GET served, fewer GETs than pages, no send blocked, the old `posts_received` shape, non-number counter, non-number `non_get_allowed`, `sends_blocked` absent), 4 moved counts → rose. On the old code **20 failed, 22 passed** (`logs/deck-red.log`); after: **42 passed** |
| **Presenter script** (`docs/demo/DEMO-SCRIPT.md`) | written before M4 was on the branch | Every console label it names re-checked in the merged frontend source (Playground, Policies, Data sources, Reports, Admin, top bar, scanner pages: "UI scanner", "Start URL", "Synthesize persona fill", "Start crawl", "Pages with forms", "Parallel view", "Filled with", "Synthetic persona", "Obfuscated tokens", "Export inventory", "No submissions · POST interception on"): all present. Status line says so. The scanner beat follows the New scan page's defaults (`DEFAULT_CONFIG`: demo URL and stored session `dps/ui/demo-onboarding` pre-filled; Personas default 20 → set to 3). The 7:00 proof, the beat's **Proof** and slide 10's notes speak of non-GET requests and forms sent by GET, received 0 while the site served the scan | notes still equal the script blocks (deck test) |
| **Deviation log** | the contract change must be recorded for S6b | Task 26B's S9 bullet updated; new section "fimg merged into fdemo (2026-10-15)": conflicts, the S9 change and S6b's rule (S9 passes only on the smoke's D4 rule and records these facts), the label re-check, N3 closed by Task 24's `.gitignore` (`/reports/`, `var/`), the tests | — |

Visual QA of slide 10 (LibreOffice headless, fixture numbers only): complete, unproven, moved and
long-number variants render inside the card (`render/*-s10-10.png`); the caption wraps to two lines.

## 3. Gates (lane ports: app 8418, dps-db 5503, source-pg 5504, MinIO 9090/9091, Oracle 1528, Mongo 27024, OCR 5086, demo site 8428, tokenizer 8438; `DPS_IMAGE_TAG=fdemo`)

| Gate | Result |
|---|---|
| ruff format/check `dps tests tools`; `mypy dps`; `mypy tools/build_deck.py tests/test_deck.py` | clean: 446 files formatted, all checks passed, 150 + 2 files no issues |
| `docker compose --profile full up --build -d` | 15 services in 3:31 (build included); one-shots migrate, minio-init, demo-secrets, seed-sources exited 0; `/readyz` ready, every component ok except `ner` (patterns-only by design), `browser: 1 online` |
| **Whole suite, every service up**: `DPS_TEST_REQUIRE_SERVICES=1 DPS_TEST_TESSERACT_CMD=<app image tesseract> ./lane.sh pytest -q` | **3714 passed, 15 skipped, 3 errors** in 28:15. The 3 errors are `tests/console/test_console_m2.py`'s precondition ("demo connections missing: run python tools/smoke_test.py first" — the suite ran on a fresh stack); re-run after the smoke: **3 passed**. Skips: 1 Mongo composite key (by design), 2 need a host tesseract, 6 opt-in image runtime tests, 3 MOSTLY/pandas, 2 NER, 1 `frontend not built` (re-run after the build: `tests/test_app_m2.py` + `tests/test_flow_report.py` **21 passed**, no skip) |
| Frontend | `npm ci`; **44** node tests + **377** Vitest; `typecheck`; `build` (bundle check: no dev fixture code) |
| Smoke A (after `seed_sources.py --target all --rows 20000 --seed 7`): `DPS_SMOKE_SCANNER=on DPS_SMOKE_REQUIRE_OCR=1 DPS_SMOKE_REQUIRE_M5=1 … --m3` | **59/60**: the only failure is S11 `api responses are not cacheable` (fsec). Scanner 11/11 incl. "no submission reached the site — non_get +0, form_get +0, logout_hits +0, admin_hits +0; pages served +175 (21 visited); non-GET blocked 60; non_get_allowed 0"; M3 runs 3/3 at 20 000 rows each, consistency 3/3 pairs |
| Flow A: `flow_report.py --require-scanner` | **PASS, 92 calls, 92 as expected, 0 skipped** |

## 4. Proofs (counts only)

- **D4** (`/__received` of this lane's demo site): fresh site 0/0/0/0. The whole suite moved
  `non_get/form_get/logout_hits/admin_hits` by +8/+2/+1/+1, exactly its own positive-control test, as
  on m4, main and fimg. Smoke A, flow A and a privacy probe scan moved them by **+0** each, while `get`
  moved +175, +21 and +175 (the site served those scans).
- **LLM:** `dps_llm_raw_pii_sent_total` 0 before and after the whole suite and the smoke.
- **Logs and artefacts** (lane copy of S4b's `privacy_scan.py`): every container log of the stack and
  every saved tool output (59 sources, 5.8 MB) plus the artefact and export volumes (0.36 MB): **0**
  hits of the sample record, the SSN probe, the bootstrap key, the tokenizer token and key, the demo
  KEK, a probe scan's DEK and seed (held in memory only), the demo site's values and session cookie,
  2 000 sampled values per personal column of PG, Oracle and Mongo, raw `dps_` keys, `tk_` tokens,
  storage errors, tracebacks. (A first run counted hits only in my own `pytest --collect-only`
  listing: 37 test node ids of the repo's synthetic fixtures; I moved that file out of the evidence
  folder and re-ran: 0.)
- **Source vs target** (`svt.py`): `bank_uat.customers_smoke` (20 000), `customers_flow` and
  `customers_flow_b` (500 each): **0 same-row survivors** in every personal column; Oracle
  `CRM_MASKED.CUSTOMERS_SMOKE` and Mongo `crm_uat.profiles_smoke` (20 000 each): 0 set-equal values in
  every personal column. Domain collisions only (a customer id equal to another customer's, 2 in
  20 000; shifted dates of birth on occupied days, 0 same-row).
- **OCR** of smoke scan A's stored screens (`s_01M508X5CBP92AV11QV7FMMKF3`, lane copy of S4b's
  `ocr_probe.py`, the app image's tesseract, psm 11 and 3; `logs/48-ocr-*.json`, field names only):
  the filled screens (12 synthesize, 12 obfuscate) hold **0** demo values, and the positive control
  holds (the scan's own personas are readable: 162 and 10 hits). The 3 masked captures hold 0 except
  `application_ref` on page 25 (`/apply/review`), which is also the only demo value readable in that
  page's original and annotated images. That is M4's known N8/N9 (not a detected entity), seen on
  lanes m4, main and fimg too.

## 5. Open items and notes for the orchestrator

1. **S11** (`no-store` on `/api/`) stays red until `fsec` merges (as on `fimg`).
2. **For S6b** (`tools/e2e_demo.py`): S9 must PASS only on the smoke's D4 rule and record
   `scan.*`, `received.*` and `sends_blocked` as in the `build_deck.py` docstring; `/__received` is
   read from the host at `DPS_TEST_DEMO_SITE`, the scan reaches `http://demo-site:8410`. Still open
   from S6a: N2 (strict for the committed deck path), N4 (Keynote PDF review, needs the owner's GUI).
3. The M3 console tests rewrote 54 tracked `m3-shots` (A-N2, as on every lane): copied to
   `m3-shots-from-run/` and restored with `git checkout --`; none committed.
4. Pre-existing, not mine: 3 `mypy` errors in `tools/smoke_test.py` (the gate is `mypy dps`).
5. Only this lane's compose project and files were touched. The stack ran images built from the
   merged tree before the deck edit (the only difference to HEAD: `tools/build_deck.py` in the app
   image's `COPY tools` layer, unused at runtime). Afterwards `docker compose --profile full build`
   retagged `data-privacy-services:fdemo` at HEAD (cached, 3 s; its `/app/tools/build_deck.py` hash
   equals HEAD's); the browser and OCR-mock images were unchanged.

## 6. State left behind

- `fdemo` = `origin/fdemo` = `2b31b7a`; `git status` clean.
- `.env`, `.env.lane`, `lane.sh`, `.venv` reused unchanged (they already covered every compose
  variable and every `DPS_TEST_*` endpoint, checked without printing values).
- Stack: `docker compose --profile full stop`: 15 containers exited, volumes kept (not `down -v`).
