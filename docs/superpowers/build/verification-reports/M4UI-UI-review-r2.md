# M4 console lane (m4ui): review round 2 of Tasks 21, 22 and 23

Reviewer: an independent review agent, 2026-10-13. Worktree: `/Users/neelu/document_intelligence/dps-wt-m4ui`, branch `m4ui`, HEAD `018ab71`. The commits reviewed are `3a0cb60` (T21), `bc766ad` (T22), `cb8bd24` (T23) and the round 1 fix `018ab71`.
- The working tree was clean before and after the review. No repository file was modified.
- The fix commit's author is NilenduGanguli <nilendu.ganguli@gmail.com>, and it has no trailer.
- All probes are under `/Users/neelu/.cache/dps-build/reports/probe-m4ui-UI-r2/`:
  - scripts: `shots.py`, `errors.py`, `fixcheck_r1copy.py`
  - output: `shots/`, `err-shots/`, `fix-shots/`
  - logs: `vite.log`, `npm-ci.log`, `typecheck.log`, `test.log`, `build.log`

I did not trust `M4UI-UI-impl.md` or `M4UI-UI-fix-r1.md`. Every claim below comes from reading the code or running it myself.

## Verdict: FAIL (1 blocking finding, narrow)

The round 1 blockers are fixed and verified:
- **B1** (restart from a scan opened by link)
- **B2** (error states in the parallel and inventory tabs)

The B2 class of defect is still present on two surfaces the fix did not reach (B3 below): the Captures tab, and the live crawl page's crawl map and navigation log. On those surfaces a refused or still-loading list is shown as a confident empty state, and that empty state contradicts the scan's own counters.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `npm ci` | ok. `node_modules` is a real directory, not a symlink. |
| `npm run typecheck` | clean |
| `npm test` | node tests 42/42 pass. Vitest: 10 files, **155/155** pass. |
| `npm run build` | tsc + vite build ok. `check-bundle: no dev fixture code in dist/assets (4 markers checked)` |
| `usePoll.ts` | untouched (M2's hook; no change in the diff) |
| Private Playwright 1.55.0 (`~/.cache/dps-build/pw-m4ui`) against vite on 5184 (`VITE_DPS_FIXTURES=1`, `DPS_APP_PORT=8499`, no backend) | 8 routes × {1440, 390} × {dark, light} = 32 page shots. Mockups 12 and 04 were shot in the same 4 variants. **0 console errors, 0 page errors, 0 failed requests**. `scrollWidth == clientWidth` everywhere, so there is no horizontal page scroll at 390. The `data-theme` attribute follows dark and light. |
| Integration tests on lane containers | not applicable: console lane, no Docker by instruction |

## 2. Visual comparison with the mockups

**Mockup 12 vs `/scanner/new?scan=…`** (1440, dark and light). These all match the mockup:
- card order Target / Scope / Authentication / Fill mode / Request
- the no-submit banner with its two stats, and the stepper
- Crawl · live: chip, visited/discovered/queued line, budget, bar, pipe, four stats
- Crawl map: tree, depth chips, PII/crit/risk/POST-blocked chips, skipped rows struck through, footer with risk sentences
- Navigation log: 5 columns, 13 rows, "Showing 13 of 50", Download JSONL
- the Request pane

Below 1200 px the setup grid collapses to one column. At 390 px everything stacks without page scroll.

**Mockup 04 vs `/scanner/scans/:id`**. These all match the mockup:
- crumb with origin, head sentence, no-submit line
- the Live crawl / Export inventory / Export personas / Evidence pack actions
- the stepper with "persona 1 of 20" and the tabs
- the Pages with forms list (selected row), Rendered personal data, Risk flags A1–A5
- the Parallel view: two frames, amber/violet chips, Screens/Fields toggle, legend, callout
- the Field inventory

The "Filled value" column is still clipped inside `.tbl-scroll` at 1440 (r1 non-blocking 1). The mockup's capture block (DOM-text pane) is absent, as the deviation log says. That absence is accepted.

## 3. Round 1 fixes: verified

- **B1** (`lib/scans.ts`):
  - **Code:** `configFromSpec` maps `consistency.key_wrapped` to `wrap:'rsa'` with an empty PEM. `startUrlError` refuses `<…>`, `%3C…%3E`, `{id}` and `%7Bid%7D`, and both `configError` and `checkSession` use it.
  - **Placeholder coverage:** I checked the placeholders against the backend redactor (`dps-wt-m4/dps/layers/scanner/urls.py::redact_url`, read only). It writes `<ENTITY>`, `<v>`, `<k>`, `;<v>` and `{id}`. Entity tokens match `^<[A-Z][A-Z0-9_]{0,40}>$`, and no engine entity name contains a digit, so the regex `<[A-Za-z_]+>` covers every placeholder in practice.
  - **Browser re-run (in-app navigation):**
    - The form hydrates `http://demo-site:8410/apply/{id}?ref=<v>` with wrap `rsa` and an empty PEM.
    - Check session prints the redacted-URL refusal and makes 0 POSTs.
    - Restart is refused with 0 POSTs. After the full URL is re-entered, Restart is refused for the missing PEM, still 0 POSTs.
    - After the PEM is entered, Restart sends 1 create POST carrying `wrap_key` and the full URL, with no placeholder.
  - **Tests:** the unit tests assert the claims, including 0 fetch calls for the probe.
- **B2** (`pages/ScanDetail.tsx`), injected refusals:
  - `page=999` shows "This page is not part of the scan" and no skeleton.
  - `/pages` 500 on the parallel tab shows "The pages could not be loaded: 500 internal: …. Retrying."
  - `/inventory` 403 shows "The inventory could not be loaded: 403 forbidden: …. Retrying."
  - `GET /scans/{id}` 500 shows "The scan could not be loaded…".
  - An artefact 403 shows "This capture could not be loaded" in both frames.
- **Storage and logs:**
  - After the run, localStorage holds only `["dps.theme"]` and sessionStorage is empty.
  - The vite log has 0 matches for `12345`, `ref=ABC`, `BEGIN PUBLIC KEY`, `meera`, `Venkatesan` and `9173`.

## 4. Blocking finding

### B3: the Captures tab and the live crawl page still turn a refused or loading list into a false empty state

**Defect 1: Captures tab.** In `frontend/src/pages/ScanDetail.tsx:170`, `CapturesTab` gets `pages={s.pages ?? []}` and neither `s.pagesError` nor the "not loaded yet" state. In `frontend/src/components/scanner/CapturesTab.tsx:34-36`, an empty list therefore renders "No page of this scan rendered personal data that needed a masked capture so far."
- Probe `errors.py` / `captures_pages500`: `/pages` answers 500 persistently. The tab shows that sentence, with no alert, indefinitely. Meanwhile the head reads "Crawl finished · 15 pages crawled · … · 5 risk flags", including A3 "rendered unmasked".
- Screenshot: `err-shots/captures_pages500.png`.
- Probe `captures_pages_slow` (pages delayed 4 s, read at 1.5 s): the same false sentence shows while the list is still loading.
- This is the tab the crawl map's "pages that render personal data open their masked capture" links into.
- An operator is told that no page rendered personal data. That is a privacy-relevant false statement, not a cosmetic one.

**Defect 2: live crawl page.** In `frontend/src/pages/ScanNew.tsx:355-356`, `CrawlMap` and `NavLog` get `pages.data ?? []` and `events.data ?? []`. `pages.error` and `events.error` are dropped.
- Probe `live_pages500`: the crawl map says "No pages discovered yet." directly under "15 of 19 discovered pages visited".
- Probe `live_events500`: the log says "No navigation yet." No alert in either case.

This is the same defect class as r1 B2: an error from `usePoll` is swallowed and the page shows a wrong state. The B2 fix covered only the parallel and inventory tabs.

**Fix (narrow, console only):**
1. Pass `error={s.pagesError}` and `loaded={!!s.pages}` into `CapturesTab`. Render the existing `Refused` callout ("The pages could not be loaded: … Retrying.") on error and a skeleton while not loaded. Keep the "No page … so far" sentence only for a loaded empty list, and drop "so far" for a terminal scan.
2. In `ScanNew`, render a value-free `errorText` callout in the Crawl map card on `pages.error` and in the Navigation log card on `events.error`. Show "No pages discovered yet" / "No navigation yet" only when the list loaded and is empty.
3. Add static-render tests for each case: captures with a pages error, captures while loading, and crawl map / log with an error.

## 5. Non-blocking findings (style or robustness; list only)

1. (new) **Start URL fragment dropped on restart.** `redact_url` drops the fragment and sorts query pairs. A start URL with a hash route (`/spa#/kyc`) and no other placeholder therefore hydrates without the fragment, and Restart silently crawls a different start page. Consider treating a `url_origin`-only or fragment-less stored URL as "re-enter", or say so in the hint.
2. (new) **The session probe inherits the form's mode.** In obfuscate mode the server mints scan key material for the probe, returns it, and the console discards it. Forcing `mode: 'synthesize'` in `probeSpec` would avoid issuing a key nobody stores.
3. (new) **Stale page error after switching pages.** `pageError` from the previously selected page survives a page switch until the new answer arrives (usePoll keeps the last error). Switching from `page=999` to a valid page briefly shows "This page is not part of the scan". Cosmetic.
4. (new) **Noisy live region.** `aria-live="polite"` on the request pane re-announces the whole JSON on every keystroke in the setup form. Consider `aria-live="off"` while typing, or announce only on Copy.
5. (r1, unchanged) **Inventory column clipped.** Field inventory "Filled value" is clipped at 1440 (horizontal scroll inside `.tbl-scroll`).
6. (r1, unchanged) **Fixture `capture_json` in synthesize mode.** `scanFixtures.ts:223` sets `capture_json` for every captured page, also in synthesize mode. Dev only.
7. (r1, unchanged) **Misleading page cap.** `MAX_PAGES = 500` in the UI vs the server cap `scan_max_pages` (default 50); the hint misleads.
8. (r1, unchanged) **Overstated idempotency wording.** The deviation T22 says the Idempotency-Key makes retries safe; it does not (a fresh key per Start). Wording only.
9. (r1, unchanged) **`checkSession` keeps polling after unmount** (up to 60 s).
10. (r1, unchanged) **No words for two refusal codes.** `createErrorText` has none for `start_url_denied` / `fill_invalid` (value-free fallback).
11. (r1, unchanged) **Dead helper.** `blockedByPage` in `lib/scans.ts` is used only by its test.
12. (r1, unchanged) **Stale map after Restart.** `usePoll` keeps the previous scan's data, so the old crawl map shows for about one poll.
13. (r1, unchanged) **Badge overlap.** The FIXTURES dev badge overlaps the right edge of the crawl map and the inventory (dev only).

## 6. Privacy and leak hunt

- **No storage, logging or HTML injection in production code.** The new production code has no `console.*`, `localStorage`/`sessionStorage`, `innerHTML` or `dangerouslySetInnerHTML`.
- **Key material and seed live in React state only.** They appear only in `ScanKeyCard`: kid, wrap state, and the first 4 bytes of the seed's SHA-256. Copy and Download use a filename built from the scan id.
- **Nothing sensitive goes in the URL.** Navigation carries only the scan id.
- **The request preview and "Copy request" exclude `consistency`** (seed, PEM). In the B1 run, the recorded POST body carried the PEM only in `consistency.wrap_key`.
- **The original side never shows a value.** It shows "(empty)" or "••• masked"; Screens shows the worker's annotated or masked artefacts.
- **The filled side shows only `fill_synthesize`/`fill_obfuscate`.** These are persona values or tokens; plan Task 3 says "never page data".
- **URLs are shown only from `*_redacted` fields and event `target`.** `pathOf` masks `<v>`/`<ENTITY>` (also percent-encoded) as •••.
- **Artefacts are fetched with the API key** and shown as blob URLs that are revoked on change or unmount.
- **Log scan:** the vite dev log has 0 hits for the probe values, the PEM, or the persona values (counts above).
- **Fixture code cannot reach production:** it sits behind `import.meta.env.DEV` and the `check-bundle.mjs` marker scan, which passed.
- **Not applicable to this lane (no server code):** tenant isolation, SQL from input, crash half-apply. The auth headers come from `useAuth`, which carries the tenant, and the console adds no tenant parameter of its own.

## 7. Contract and interfaces

- **`api.scans.*` matches the plan exactly.** The routes and parameters agree with M4 plan §API: `pages?after=&limit=`, where the cursor is the last `page_no`, and `events?after=&limit=`, where the cursor is the last `seq`.
- **`types.ts` adds only new names.** `grep "export type JobState\|interface PageOf"` gives 1.
- **The backend routes do not exist yet** in `dps-wt-m4` (HEAD `97695cd`). The route-shape check was therefore against the plan and contract §9/§14, plus `dps/layers/scanner/models.py` for the redaction semantics.
- **The per-page POST-blocked chip is correct.** It counts one event per blocked request, which is what plan Task 14 `_finish` emits.
- No contract name is broken for the other lanes.

## 8. Deviation log judgement (`docs/superpowers/build/M4-DEVIATIONS-console.md`)

- **T21–T23 entries:** accepted as in r1. The T22 idempotency justification is overstated (non-blocking 8).
- **Round 1 fix entries** (`configFromSpec` wrap flag, `startUrlError`, `DetailState` errors, fixture URL redaction): accepted.
  - The `DetailState` entry claims error states for "the pages card, the parallel pane and the Inventory tab". That is accurate, but the Captures tab and ScanNew are not covered (B3).
  - Refusing a literal `{id}`/`<word>` in a typed URL is acceptable: such characters are not valid unencoded in real start URLs.

## 9. Open issues for later gates

- **Compose-level walk still owed.** The walk of plan Task 22/23 Step 3 runs against the real scans API and the demo site after the merge into `m4` (G7 / verify). It includes `capture_json` per mode, the CSP `blob:` image source, and a real redacted `spec.url` for the B1 path.
