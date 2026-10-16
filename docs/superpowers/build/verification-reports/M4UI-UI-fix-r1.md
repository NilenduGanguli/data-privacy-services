# M4 console lane (m4ui): fix round 1 for review B1 and B2

Worktree `/Users/neelu/document_intelligence/dps-wt-m4ui`, branch `m4ui`. Commit **`018ab71`** `fix(m4): restart from a saved scan asks again for the full start URL and the RSA key; scan detail shows refused page, pages and inventory loads` (author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailer). Working tree clean afterwards. Paths touched: frontend/ and `docs/superpowers/build/M4-DEVIATIONS-console.md` only.

## Resume check
At start: HEAD `cb8bd24` (T23), tree clean, no earlier fix report. Nothing to resume. `node_modules` was already a real directory (npm ci from an earlier run), so it was not reinstalled.

## B1: restart after opening a scan by link (`frontend/src/lib/scans.ts`)
Tests first (`src/lib/scans.test.ts`). All 3 failed before the fix:
- `keeps the RSA key wrap of a scan opened by link`: a spec with `mode: obfuscate` and `consistency.key_wrapped: true` gives `wrap: 'rsa', pem: ''`. `configError` then asks for the PEM, and `buildSpec` sends no `consistency`. `key_wrapped: false` and an absent flag give `clear`.
- `refuses a redacted start URL`: these URLs are refused with "The start URL of a saved scan is stored redacted; enter the full start URL again.": `{id}`, `<v>`, `<EMAIL>`, `<k>=<v>`, `%3CACCOUNT_NUMBER%3E` and `%7Bid%7D`. The same check applies to a config built by `configFromSpec`. The full URL passes.
- `never probes a redacted start URL`: `checkSession` returns the same message with an ✗ and makes **0 fetch calls**.

Fix:
- `configFromSpec` sets `wrap = spec.consistency?.key_wrapped === true ? 'rsa' : 'clear'` with `pem: ''`.
- New export `startUrlError(url)` holds the existing http(s) check plus the placeholder regex `/<[A-Za-z_]+>|%3C[A-Za-z_]+%3E|\{id\}|%7Bid%7D/i`. `configError` uses it (Start and Restart). `checkSession` calls it before it creates the probe scan.

## B2: scan detail error states (`frontend/src/pages/ScanDetail.tsx`)
Tests first (`src/pages/ScanDetail.test.tsx`, static render). All 4 failed before the fix:
- A page detail answering 404 shows "This page is not part of the scan" and no "Loading the page".
- A page detail answering 500 shows `class="callout amber" role="alert"` "The page could not be loaded: 500 internal: internal error. Retrying." with no skeleton.
- A pages list answering 403 shows "The pages could not be loaded: 403 forbidden: scope missing. Retrying." in the pages card.
- An inventory answering 403 shows "The inventory could not be loaded: … Retrying." in the Inventory tab, with no skeleton.

Fix:
- `DetailState` gains `pagesError`, `pageError` and `inventoryError`. The container passes `pages.error`, `pageDetail.error`, and `inventory.error` when the Inventory tab is open.
- A new `Refused` component renders the value-free `errorText` callout. The 404 page case gets a card: "This page is not part of the scan · Choose a page from the list."
- While the pages list is missing, the pages card no longer says "No page with forms has been filled yet". The parallel pane says "Pages appear here once the list of pages has loaded." instead of "This scan found no page with a form."
- `usePoll` (M2) is untouched (`git diff main -- frontend/src/hooks/usePoll.ts` is empty). It keeps retrying, and the callout says "Retrying."

## Extra (dev only)
`src/dev/fixtureServer.ts` now stores a scan's `spec.url` roughly the way `redact_url` writes it: numeric segments become `{id}`, query values become `<v>`, and the fragment is dropped. Before, it only dropped the query. Without this, fixture mode could not show the B1 path. A test was added (`stores the start URL value-free and the wrap as a flag`). It asserts that the stored spec contains none of `12345`, `ABC` or the PEM. The fixture change was written just before this test, so the test was not seen failing first. Not in the bundle: check-bundle passed.

## Gates (after the commit's content, run in `frontend/`)
| Gate | Result |
|---|---|
| `npm run typecheck` | clean |
| `npm test` | node tests 42/42 pass; Vitest 10 files, **155/155** pass (147 before + 8 new) |
| `npm run build` | tsc + vite build ok; `check-bundle: no dev fixture code in dist/assets (4 markers checked)` |
| `git diff main -- frontend/src/hooks/usePoll.ts` | empty |

## Browser check (private Playwright 1.55.0, `~/.cache/dps-build/pw-m4ui`)
Setup: vite on 5184 with `VITE_DPS_FIXTURES=1 DPS_APP_PORT=8499`, viewport 1440. The script is `probe-m4ui-UI-fix-r1/fixcheck.py`; screenshots are under `probe-m4ui-UI-fix-r1/shots/`. The in-page fetch was wrapped to record calls, because fixture mode answers inside the page.
- `/scanner/scans/<id>?tab=parallel&page=999` shows "This page is not part of the scan" with no "Loading the page" (`page999-1440.png`).
- Created an obfuscate scan for `http://demo-site:8410/apply/12345?ref=ABC` with an RSA wrap. The first POST carried `wrap_key`. The page was then reopened through in-app navigation: ScanDetail, then "Live crawl", so ScanNew remounted and hydrated from the spec. The fixture lives in the page, so a full reload would lose the scan.
  - The form hydrated with URL `http://demo-site:8410/apply/{id}?ref=<v>`, wrap `rsa` and an empty PEM.
  - Check session showed "✗ The start URL of a saved scan is stored redacted; enter the full start URL again." Restart showed the same message. 0 POSTs were made.
  - After the full URL was entered again, Restart showed "Paste the RSA public key (PEM)…" and still made 0 POSTs.
  - After the PEM was pasted, Restart made 1 create POST. It carried `wrap_key` and the full URL, with no `<v>` or `{id}` placeholder.
- The Inventory tab still renders.
- Storage: localStorage keys `["dps.theme"]`; sessionStorage is empty.
- 0 console errors and 0 page errors.
- Log scan: the vite log has 0 matches for `12345`, `ref=ABC` or `BEGIN PUBLIC KEY`.

## Deviations
Logged in `docs/superpowers/build/M4-DEVIATIONS-console.md` under "Review round 1 fixes (B1, B2)":
- `startUrlError` is a new additive export. No contract name changed.
- A start URL containing a literal `{id}` or `<word>` is refused even when typed by the operator.
- The wording of the error-state callouts.
- The dev fixture now redacts the stored URL.

## Open issues (unchanged, non-blocking from the review)
Non-blocking items 1–9 of review r1 were not addressed in this round. They include:
- the clipped inventory column
- `capture_json` being set in synthesize-mode fixtures
- the `MAX_PAGES` hint
- the idempotency wording
- the `checkSession` abort on unmount
- `blockedByPage` being dead code
- stale `usePoll` data after Restart

The compose-level walk against the real scans API is still owed at G7 / lane verify.
