# M4 console lane (m4ui): review round 3 of Tasks 21, 22 and 23

Reviewer: an independent review agent, 2026-10-13. Worktree: `/Users/neelu/document_intelligence/dps-wt-m4ui`, branch `m4ui`, HEAD `e7a5188`.

Commits reviewed:
- T21 `3a0cb60`
- T22 `bc766ad`
- T23 `cb8bd24`
- round 1 fix `018ab71`
- round 2 fix `e7a5188` (the new commit in this round)

Hygiene:
- The working tree was clean before and after the review. I modified no repository file.
- The `e7a5188` author is NilenduGanguli <nilendu.ganguli@gmail.com>, with no trailer (0 `Co-authored` lines).
- The commit touches only `frontend/` and `docs/superpowers/build/M4-DEVIATIONS-console.md`.
- `usePoll.ts` (M2) is unchanged.

All probes are under `/Users/neelu/.cache/dps-build/reports/probe-m4ui-UI-r3/`:
- scripts: `shots.py`, `errors.py` (r2 probe plus 4 r3 cases), `running.py` (new)
- outputs: `shots/`, `err-shots/`, `facts.json`, `errors.out`, `running.out`
- logs: `npm-ci.log`, `typecheck.log`, `test.log`, `build.log`, `vite.log`

I did not trust `M4UI-UI-impl.md` or `M4UI-UI-fix-r2.md`. Every claim below comes from reading the code or running it myself.

## Verdict: PASS (0 blocking findings)

r2 B3 is fixed:
- The Captures tab, the live crawl map and the navigation log show a refused list as a refusal and a loading list as a skeleton.
- They keep stale data under the callout and recover when the API answers again.
- The empty sentences appear only for a list that loaded empty.

No new blocking defect was found. The r1 fixes (B1, B2) still hold.

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `npm ci` | exit 0. `node_modules` is a real directory, not a symlink. |
| `npm run typecheck` | exit 0 |
| `npm test` | node tests **42/42** pass. Vitest: 10 files, **165/165** pass. |
| `npm run build` | exit 0. `check-bundle: no dev fixture code in dist/assets (4 markers checked)`. `git status` is clean afterwards (`dist` is ignored). |
| Private Playwright 1.55.0 (`~/.cache/dps-build/pw-m4ui`) | vite on 127.0.0.1:5184 with `VITE_DPS_FIXTURES=1`, `DPS_APP_PORT=8499` and no backend. **40 shots**: 8 routes plus mockups 12 and 04, each at {1440, 390} × {dark, light}. **0 console errors, 0 page errors, 0 failed requests.** `scrollWidth == clientWidth` in all 40 (no horizontal page scroll at 390). `data-theme` matches the requested theme in every shot. |
| Integration tests on lane containers | not applicable: console lane, no Docker by instruction |

## 2. Round 2 fix verification (B3)

**Code read.** `Refused.tsx` is the shared value-free callout: `errorText` gives the status, code and server message, with `role="alert"`.

`CapturesTab` handles three states:
- not loaded: a skeleton, or the callout on an error
- loaded and empty: the empty sentence, without "so far" once the scan is terminal
- stale data: the callout above the captures

`CrawlMap` and `NavLog` take `loaded` and `error`. The log footer reads "No entries loaded yet" before the feed loads. `ScanNew` routes `usePoll`'s `{data, error}` through the new pure `LiveCrawl`.

The new tests assert the claim from both sides: the callout text and `role="alert"` are present, and the false empty sentences are absent. They are not vacuous.

**Browser probes** (`errors.out`, `running.out`):

| Case | Observed |
|---|---|
| `captures_pages500` (finished scan, `/pages` 500 persistently) | alert "The pages could not be loaded: 500 internal: injected refusal. Retrying."; no "No page … rendered personal data" |
| `captures_pages_slow` (read at 1.5 s, pages delayed 4 s) | 1 skeleton, no empty sentence |
| `live_pages500` | crawl map alert; no "No pages discovered yet"; log intact (13 rows, "Showing 13 of 50 entries") |
| `live_events500` | log alert ("navigation log could not be loaded"); no "No navigation yet"; footer "No entries loaded yet"; tree intact (19 rows) |
| `live_slow` | 2 skeletons, neither empty sentence |
| `pagescard_pages500` | no "None so far" |
| **running scan, lists loaded then refused** (`running.py`): Start crawl in the UI, then after 9 s `/pages` 500 and `/events` 503 | both alerts show, and the stale 6 tree rows and 9 log rows stay. After the refusal is lifted, the alerts clear and the lists grow (12 rows / 13 rows) while the crawl continues. Screenshot: `err-shots/run_refused.png` |
| running scan, in-app navigation to the detail Captures tab with `/pages` 500 | alert shown, no empty sentence; recovers on release |
| running scan, captures loaded, then refused | alert above the 2 stale captures and the withheld card. Screenshot: `err-shots/run_detail_captures_stale.png` |
| r1 regressions: `parallel_pages500`, `inventory403`, `detail500`, `page999`, `artefact403` | unchanged and correct: alerts, "This page is not part of the scan", and 2 "This capture could not be loaded" frames |

Every case had 0 page errors.

## 3. Mockup comparison

**Mockup 12 vs `/scanner/new?scan=…`** matches:
- card order Target / Scope / Authentication / Fill mode / Request
- the no-submit banner with its stats, and the stepper
- the live progress with four stats
- the crawl map: chips, depth, POST-blocked, legend, risk footer
- the 5-column navigation log with "Showing 13 of 50" and Download JSONL
- the key-material card

At 390 px the page stacks without horizontal scroll.

**Mockup 04 vs `/scanner/scans/:id`** matches:
- the crumb, head sentence and no-submit line
- the four actions and the stepper ("persona 1 of 20")
- the tabs, the Pages with forms list, Rendered personal data and Risk flags A1–A5
- the Parallel view: amber/violet chips, Screens/Fields, legend, callout
- the Field inventory

Two differences, both accepted:
- **Capture block moved.** The mockup's capture block (DOM-text pane) is not on the parallel tab. It lives in the Captures tab, as the deviation log records.
- **Masked original frame.** The original frame shows the worker's annotated or masked screenshot, not original values. This is privacy-correct.

## 4. Leak and privacy hunt

**No values or keys in storage, logs or the DOM.**
- New and changed production code has no `console.*`, `localStorage`/`sessionStorage`, `innerHTML` or `dangerouslySetInnerHTML`. The only grep hit is the word "console." in the NoSubmitBanner prose.
- After the running-scan walk, localStorage holds only `dps.theme`. sessionStorage holds only the probe's own `__rules`.
- The vite log has 0 matches for `12345|ref=ABC|BEGIN PUBLIC KEY|meera|Venkatesan|9173|Tilqoe|injected`.
- The refusal callouts render only `errorText`: status, code and the server's value-free message. No request URL, query or body appears.
- The key card shows the kid, the wrap state and a short seed hash. Key material stays in React state only (contract §14).

**Not applicable to this lane:** tenant isolation, SQL from unvalidated input, and crash half-apply all need server code. The console adds no tenant parameter beyond `useAuth`.

## 5. Contract and interfaces

- **Additive props only.** `CapturesTab`, `CrawlMap` and `NavLog` gain only optional props with backward-compatible defaults. The one new export is `LiveCrawl`, which is pure.
- **Unchanged:** `api.scans.*`, `types.ts` and `usePoll`. No contract name used by the other lanes is broken.

## 6. Deviation log judgement (`docs/superpowers/build/M4-DEVIATIONS-console.md`)

- **T21–T23 and round 1 entries:** accepted, as in r2. The T22 idempotency wording is still overstated (non-blocking 8 below).
- **Round 2 fix (B3) section:** all five rows are accepted and accurate (`Refused.tsx`, `CapturesTab`, `ScanDetail`, `CrawlMap`/`NavLog`, `LiveCrawl`). The undercount noted for the POST-blocked chip while events are refused is honest and acceptable, because the refusal is shown in the log card.

## 7. Non-blocking findings (carried over; list only)

1. **Start URL fragment dropped on restart.** `redact_url` drops the fragment, so a hash-route start URL hydrates without it and Restart crawls a different start page. Consider saying so in the hint.
2. **The session probe inherits the form's mode.** In obfuscate mode the server mints key material for the probe, and the console discards it.
3. **Stale page error after switching pages.** `pageError` from the previously selected page survives a page switch until the new answer arrives.
4. **Noisy live region.** `aria-live="polite"` on the request pane re-announces the JSON on every keystroke.
5. **Inventory column clipped.** Field inventory "Filled value" is clipped at 1440 (horizontal scroll inside `.tbl-scroll`). It is visible again in the r3 shot.
6. **Fixture `capture_json` in synthesize mode.** The fixture sets `capture_json` for every captured page, also in synthesize mode. Dev only.
7. **Misleading page cap.** The UI's `MAX_PAGES = 500` differs from the server default cap `scan_max_pages` (50).
8. **Overstated idempotency wording.** The T22 deviation says the Idempotency-Key makes retries safe; it does not (a fresh key per Start). Wording only.
9. **`checkSession` keeps polling after unmount** (up to 60 s).
10. **No words for two refusal codes.** `createErrorText` has none for `start_url_denied` / `fill_invalid` (value-free fallback).
11. **Dead helper.** `blockedByPage` is used only by its test.
12. **Stale map after Restart.** The previous scan's crawl map shows for about one poll.
13. **Badge overlap.** The FIXTURES dev badge overlaps the right edge of some cards (dev only).
14. **(new, cosmetic) Sticky top bar in full-page screenshots.** In full-page screenshots the sticky top bar appears mid-page (`run_refused.png`). This is a capture artefact of `position: sticky`, not a layout bug.

## 8. Open issues for later gates

**Compose-level walk still owed.** It runs after the merge into `m4` (programme plan G7 / verify), against the real scans API and the demo site. It covers:
- real redacted `spec.url` hydration
- `capture_json` per mode
- the CSP `blob:` image source
- POSTs received == 0
