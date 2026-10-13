# M4 console lane (`m4ui`) — deviations from the M4 plan (Tasks 21–23)

Built in worktree `dps-wt-m4ui` against the API shapes of the M4 plan and contract §9/§14, with typed
fixtures, while the scans API is built concurrently in `dps-wt-m4`. Code as built wins over the plan
text; each row says what changed and why. The contract needed no change (no contract name changed).

## Task 21 — console foundations

| File | Change | Why |
|---|---|---|
| `frontend/src/lib/download.ts` | Re-exports M2's `saveBlob` from `lib/api.ts` and adds `saveText`, instead of declaring `saveBlob` again | `saveBlob` already exists in `api.ts` (M2 Task 29); a second implementation would drift. |
| `frontend/src/lib/api.ts` | `scansApi` reuses the file's existing `enc` (not redeclared) and is declared above `api`; `api.scans = scansApi` | the plan's `const enc` would be TS2451; `api` reads `scansApi` at module load (TDZ). |
| `frontend/src/lib/types.ts` | `ScanSpecIn.purpose?` (optional) and the alias `ScanArtefactKind` added to the plan's names | the backend `ScanSpec` has `purpose: ScanPurpose \| None` (contract §9); the alias keeps `ScanPage.artefacts` and the components on one list. |
| `frontend/src/styles/scanner.css` (new; imported by `main.tsx`) | Scanner styles live in their own file, nested under `.scanner-page`, instead of a block appended to `styles.css` | as-built M2 pattern (`styles/*.css` nested under the page root, integration fix `ef40417`): the mockups' generic names (`.field`, `.err`, `.hint`, `.stats`, `.form`, `.tree`, `.flag`) must not restyle other pages. Keyframes are `scan-pulse`/`scan-fresh` for the same reason. The phone `table.stack` rules are a scoped copy of vault.css. |
| `frontend/src/dev/{fixtureServer,fixtureMode}.ts`, `frontend/src/lib/scanFixtures.ts`, `frontend/src/vite-env.d.ts`, `frontend/src/main.tsx` | **Dev-only fixture mode** (programme plan §2.3: the UI lane builds against typed fixtures). `VITE_DPS_FIXTURES=1 npm run dev` installs a `fetch` wrapper that answers `/api/v1/scans*` and `/api/v1/artefacts/*` from typed fixtures (a scan of the demo onboarding site, played over time; refusal codes `host_not_registered`, `har_not_supported`, `scope_invalid`, extra keys → 422); everything else passes to the real API. `main.tsx` imports it only behind `import.meta.env.DEV`, so production builds drop it. | pages must be built and visually checked before the backend exists; the main lane removes nothing when it wires the real API (the wrapper is inert without the env flag). |
| `frontend/scripts/check-bundle.mjs`, `frontend/package.json` (`build`) | `npm run build` ends with a scan of `dist/assets/*.js` for fixture markers and fails when one is found | "never part of the production bundle" is enforced on every build, not by review. Proven by planting a marker (exit 1). |
| `frontend/vite.config.ts`, `frontend/src/test/render.tsx` | Vitest includes `src/**/*.test.{ts,tsx}`; component/page tests render with `react-dom/server` inside the console providers | no new dependency (no jsdom/testing-library, which would also collide in `package-lock.json` with the parallel M5 console lane). Interactive logic is kept in pure functions (`components/scanner/format.ts`; `lib/scans.ts` from Task 22) and tested directly. |
| `frontend/src/components/scanner/format.ts` | Adds `originOf`, `entityName`, `entityList`, `flagTone`, `inputText`, `autocompleteText`, `fillMethod`, `plural`, `eventVerb`, `eventResultTone`, `fmtElapsed`; `hostOf` keeps the port; `eventNote` also words `navigate`, `popup`, `dialog`, `error` and `fill` leakage events | the crawler emits these event kinds (plan Task 14 `_event` calls); the inventory and log columns need the same value-free formatting in two pages. `autocompleteText` shows `on` for an absent attribute only when the field carries an `autocomplete_enabled_sensitive` flag (mockup 04 row 3). |

## Task 22 — New scan and live crawl (`/scanner`, `/scanner/new`, mockup 12)

| File | Change | Why |
|---|---|---|
| `frontend/src/lib/scans.ts` (new) | The page logic as pure functions: `ScanConfig`/`DEFAULT_CONFIG`, `buildSpec`, `previewBody`/`requestJson` (compact JSON as in the mockup), `probeSpec`, `checkSession`, `sessionVerdict`, `configFromSpec`, `denyPathError`, `configError`, `createErrorText`, `crawlTree`, `blockedByPage`, `mergeEvents`, `eventsJsonl`, `fetchAllPages`, `fetchNewEvents` | testable without a DOM (see Task 21 test approach). |
| `frontend/src/components/scanner/ScanChrome.tsx` (new) | `ScanCrumb` (crumb row + "No submissions" pill under the console topbar) and `ScanStepper`, shared by ScanNew and ScanDetail | both mockups carry the same crumb and five-step stepper; one implementation. |
| `ScanNew.tsx` scope inputs | Max depth 0–5 and personas 1–50 (mockup: 0–6 and 1–200) | the backend `ScanScope.max_depth le=5` and `FillSpec.personas le=50` (contract §9) refuse more. |
| `ScanNew.tsx` locale list | adds `en_CA` | `SUPPORTED_LOCALES` of the persona engine has it. |
| `ScanNew.tsx` Authentication card | text input with a `<datalist>` (`dps/ui/demo-onboarding`) and a "none · public pages only" button that clears it | the plan's input + "none" choice; a `<select>` cannot hold a free secret ref. The session probe starts at the scan's start URL (the mockup's `/profile` is that site's signed-in page). |
| `ScanNew.tsx` key card | `ScanKeyCard`: kid, "wrapped with your RSA public key" / "returned in clear (TLS only)", the seed's SHA-256 fingerprint (first 4 bytes), Copy key material, Download, "I have stored" — React state only | the plan's key card; M2's `KeyMaterialCard` shows one of key or seed, a scan returns both. |
| `ScanNew.tsx` empty live column | lists up to 6 recent scans (`api.scans.list`) under "Start a crawl to see pages appear here." | without it the console has no way back to an earlier scan from `/scanner`. Additive; same route group. |
| `ScanNew.tsx` restart / create | Restart cancels the current scan when non-terminal, then creates; each Start sends a fresh `Idempotency-Key` | the plan's Restart; the key makes a double click or retry safe. |
| `CrawlMap.tsx` | the per-page chip counts blocked **POST** events only ("2 POST blocked"); other blocked methods stay in the log | the mockup's chip names POST. |
| `NavLog.tsx` | header says "local time" (mockup: "times in IST") | times render in the browser's zone (`fmtClock`). |
| Visual check (plan Step 3) | done against the Vite dev server in fixture mode (private Playwright, 1440/390, dark/light; screenshots in `~/.cache/dps-build/shots-m4ui/`), not the compose stack | the scans API is built concurrently in `dps-wt-m4`; the compose-level walk runs after the lanes merge. |

## Task 23 — Scan detail (`/scanner/scans/:scanId`, mockup 04)

| File | Change | Why |
|---|---|---|
| `frontend/src/pages/ScanDetail.tsx` | Split into `ScanDetailBody` (pure: renders a `DetailState`) and `ScanDetail` (route container: polling, search params, downloads) | page tests render the body from the typed demo scan; the container is tested for its first frame. |
| `ScanDetail.tsx` tabs | `.seg.tabs` of `role="tab"` buttons writing `?tab=` (replace, no history entry); the Screens/Fields switch is local state, not in the URL | contract §14 names only `tab`, `page`, `fill` as URL state. |
| `ScanDetail.tsx` head actions | "Evidence pack" links to this scan's pack (`/reports/evidence/:id`; disabled with a tooltip until `evidence_id`); "Export inventory so far" while running; "Export personas" disabled with a tooltip until `export_artefacts.json` | the plan's table; the mockup's "Evidence packs" pointed at the reports list. |
| `ScanDetail.tsx` pages list | a page without personal fields shows a slate `N fields` chip; pages being visited are listed with the queued ones ("Queued: …") instead of a "filling" chip | a page row has no form count before it is processed, so "filling" cannot be shown for a form page only. |
| `CapturesTab.tsx` | per captured page: the masked capture PNG, a sentence naming the replaced entity types (from `rendered_findings`), "Download reversible map" (`capture_json`) with the reveal hint; `withheld` pages get the fail-closed sentence. The mockup's masked-DOM-text pane and replacement facts are not shown | no route returns the masked DOM text or per-value facts (contract §9); the reveal map is downloaded as the artefact it is. The selected page (`?page=`) is listed first and outlined. |
| `ParallelView.tsx` Fields view | the original side shows `(empty)` or `••• masked` (pre-filled) per field with its number; it never shows a value; a password reads "typed, never stored", a file input "listed, never filled" | value-free rule (plan conventions); the mockup's left-side values are illustrative originals that DPS never stores. |
| `FieldInventory.tsx` | adds a "Filled value" column (active side) and a per-field "N messages" toggle that expands the page's validation messages; `ScanInventory` (Inventory tab) with entity/sensitivity/"risk flags only" filters and CSV/JSON downloads | the plan's table (filled value column, expandable validation, whole-scan filters). |
| `components/scanner/format.ts` `inputText` | one qualifier per input — the pattern, else the maxlength, else the name (`text · pattern …`, `select · name=gender`) | mockup 04's Input column; three qualifiers overflowed the table at 1440 px. |
