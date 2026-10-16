# M4 console lane (m4ui): review round 2 fix (B3)

Worktree `/Users/neelu/document_intelligence/dps-wt-m4ui`, branch `m4ui`. Base `018ab71`, fix commit **`e7a5188`**: `fix(m4): captures tab, crawl map and navigation log show a refused or loading list as such, never as empty`.
- Author: NilenduGanguli <nilendu.ganguli@gmail.com>. No trailer.
- Committed with an explicit pathspec. The working tree is clean afterwards.
- Only `frontend/` and `docs/superpowers/build/M4-DEVIATIONS-console.md` were touched.
- `usePoll.ts` is unchanged (`git diff --stat` empty).
- Probes and logs are in `/Users/neelu/.cache/dps-build/reports/probe-m4ui-UI-fix-r2/`.

## Resume check
The tree was clean at `018ab71`. No earlier r2 attempt existed, so nothing was redone. `frontend/node_modules` was already a real directory, not a symlink, so I did not re-run `npm ci`.

## Red first
I added 10 static-render tests before the fix. All 10 failed: `red.log` shows 10 failed and 155 passed.

`ScanDetail.test.tsx` (4 tests):
1. Captures tab with a refused `/pages`: the `Refused` callout shows, and "No page of this scan rendered personal data" does not.
2. Captures tab while loading: a skeleton shows, and the empty sentence does not.
3. A list that loaded empty keeps the sentence. It drops "so far" for a terminal scan and keeps it for a running one.
4. The pages card does not say "None so far" before `/pages` has loaded.

`liveCrawl.test.tsx` (6 tests):
1. CrawlMap with an error shows the callout, and "No pages discovered yet" does not.
2. CrawlMap while loading shows a skeleton.
3. CrawlMap with stale pages and an error shows the callout and keeps the tree.
4. NavLog with an error shows the callout. It shows neither "No navigation yet" nor "Showing 0 of 0 entries".
5. NavLog while loading shows a skeleton.
6. ScanNew wiring, through the new pure `LiveCrawl`, covers four states: a pages error, an events error, both loading, and both loaded empty.

## Fix
- **`components/scanner/Refused.tsx` (new).** I moved the value-free callout here from `ScanDetail.tsx`: "The X could not be loaded: <status> <code>: <server message>. Retrying.", with `role="alert"`.
- **`CapturesTab`.** New optional props: `loaded` (default `true`), `error` and `final` (default `false`).
  - On an error it shows the callout, and on a list that has not loaded it shows a skeleton.
  - The empty sentence appears only for a list that loaded empty, and it drops "so far" when `final`.
  - Stale captures stay visible under the callout.
- **`ScanDetail.tsx`.**
  - It passes `loaded={!!s.pages}`, `error={s.pagesError}` and `final={!running}` to `CapturesTab`.
  - **Beyond the review:** the pages card's "Rendered personal data" empty line now appears only once `/pages` has loaded, and reads "None." for a terminal scan. It had the same defect.
- **`CrawlMap` and `NavLog`.** New optional props: `loaded` (default `true`) and `error`.
  - Each shows the callout above its tree or log, and a skeleton while loading.
  - The empty texts appear only for a list that loaded empty.
  - Before the feed has loaded, the log footer reads "No entries loaded yet".
- **`ScanNew.tsx`.** A new exported pure `LiveCrawl` takes `usePoll`'s `{data, error}` for pages and events. The container renders `LiveCrawl`, and its polling is unchanged.
- **Contract names.** I renamed nothing. All new props are optional and additive, so existing callers and tests behave the same.

## Gates (after the fix)
| Gate | Result |
|---|---|
| `npm run typecheck` | exit 0 |
| `npm test` | node tests 42/42 pass. Vitest: 10 files, **165/165** pass (155 before, plus 10 new). |
| `npm run build` | exit 0. `check-bundle: no dev fixture code in dist/assets (4 markers checked)` |

### Browser check
Private Playwright 1.55.0 (`~/.cache/dps-build/pw-m4ui`) against vite on 127.0.0.1:5184, with `VITE_DPS_FIXTURES=1`, `DPS_APP_PORT=8499` and no backend.

**Injected refusals.** `errors.py` is the reviewer's r2 probe plus a `live_slow` case and a skeleton count. Output is in `errors.out`, screenshots in `err-shots/`.

| Case | Result |
|---|---|
| `captures_pages500` | alert "The pages could not be loaded: 500 internal: injected refusal. Retrying." No "no captures" sentence. |
| `captures_pages_slow` | 1 skeleton, no alert, no "no captures" sentence |
| `live_pages500` | crawl map alert, no "No pages discovered yet" (screenshot checked) |
| `live_events500` | navigation log alert "The navigation log could not be loaded: …", no "No navigation yet" |
| `live_slow` | 2 skeletons, and neither empty sentence |

The r1 cases are unchanged:
- `parallel_pages500`, `inventory403` and `detail500` show their alerts.
- `page999` shows "This page is not part of the scan".
- `artefact403` shows 2 "This capture could not be loaded" frames.
- There were 0 page errors in all cases.

**Regression shots.** `shots.py`, the reviewer's script, rendered 40 shots: the routes plus mockups 12 and 04, at {1440, 390} × {dark, light}.
- 0 console errors, 0 page errors and 0 failed requests.
- `scrollWidth == clientWidth` in all 40, so there is no horizontal page scroll.

**Log scan.** `vite.log` has 0 matches for `12345|ref=ABC|BEGIN PUBLIC KEY|meera|Venkatesan|9173`.

**Privacy.** The new code has no `console.*`, no storage and no `innerHTML`. The callouts show only `errorText` (status, code, the server's value-free message).

## Deviations
`M4-DEVIATIONS-console.md` has a new section, "Review round 2 fix (B3)", with one row each for `Refused.tsx`, `CapturesTab`, `ScanDetail`, `CrawlMap`/`NavLog` and `LiveCrawl`.

## Open issues (not blocking, unchanged)
- **Undercounted chips.** While `/events` is refused, the per-page "POST blocked" chips on the crawl map count only the events already fetched. The refusal is reported in the log card.
- **Earlier non-blocking items 1–13 still stand.** These are the r2 review's own items, from the fragment drop on restart to the badge overlap. Item 3 is the stale `pageError` after a page switch. Item 12 is the stale map for one poll after Restart.
- **Compose-level walk still owed.** It runs after the merge into `m4`.
