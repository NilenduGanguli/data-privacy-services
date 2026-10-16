# M4 console lane (m4ui) — Tasks 21, 22, 23 — implementation report

Worktree `/Users/neelu/document_intelligence/dps-wt-m4ui`, branch `m4ui`. Only `frontend/` and
`docs/superpowers/build/M4-DEVIATIONS-console.md` were touched. No Docker.

## Commits (author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers, explicit pathspecs)

| Commit | Task | Message |
|---|---|---|
| `3a0cb60` | 21 | feat(console): scan types, api.scans client, authenticated screenshot images, scanner styles |
| `bc766ad` | 22 | feat(console): UI scanner setup and live crawl — scope, session check, persona preview, crawl map, navigation log |
| `cb8bd24` | 23 | feat(console): scan detail — parallel view (synthetic/obfuscated), field inventory, risk flags, captures, exports |

Starting state: clean worktree at `986ff89`, no earlier attempt (nothing to resume).

## What was built

**Task 21 — foundations**
- `lib/types.ts`: every scan type of the plan (`SynthesisSeed`, `ScanSpecIn`, `ScanCreated`, `RiskFlag`, `NumberedRiskFlag`,
  `ScanSummary`, `ScanDetail`, `ScanPage`, `ScanField`, `ScanRequestCount`, `ScanPageDetail`, `ScanEvent`, `InventoryRow`,
  `PersonaPreview`, …) mirroring `dps/layers/scanner/models.py` / `dps/jobs_models.py::ScanSpec`; no M2 name redeclared
  (`grep -c "export type JobState\|interface PageOf"` = 1).
- `lib/api.ts`: `scansApi` (create with Idempotency-Key, list, get, pages, page, events, inventory, download, cancel,
  personaPreview, artefact) and `api.scans`; uses M2's `request/json/post/qs/enc`.
- `lib/download.ts` (`saveText`, re-exports M2's `saveBlob`), `components/scanner/ArtefactImage.tsx` (authenticated blob
  URLs, revoked on change/unmount), `components/scanner/format.ts` (value-free display helpers), `styles/scanner.css`
  (mockups 12 + 04 ported, nested under `.scanner-page`).
- **Dev-only fixture mode**: `lib/scanFixtures.ts` (typed fixtures: a 19-page scan of the demo onboarding site, synthetic
  values only), `dev/fixtureServer.ts` (answers `/api/v1/scans*` and `/api/v1/artefacts/*` like the API, plays a created
  scan over time, refuses `host_not_registered` / `har_not_supported` / `scope_invalid` / extra keys such as `submit`),
  `dev/fixtureMode.ts` (fetch wrapper + canvas-painted screenshots). Enabled only by `VITE_DPS_FIXTURES=1` in the dev server;
  `main.tsx` imports it behind `import.meta.env.DEV`. `scripts/check-bundle.mjs` now ends `npm run build` and fails if any
  fixture marker reaches `dist/assets` (proved: a planted marker → exit 1).
- Test infra: Vitest includes `*.test.tsx`; `src/test/render.tsx` renders with `react-dom/server` + `StaticRouter` inside
  `SettingsProvider` (no new dependency).

**Task 22 — `/scanner`, `/scanner/new` (mockup 12)**: `pages/ScanNew.tsx`; `components/scanner/{NoSubmitBanner, CrawlProgress,
CrawlMap, NavLog, PersonaPreview, ScanChrome}.tsx`; `lib/scans.ts` (pure page logic: spec building, request preview without
`consistency`, deny-path and range checks, refusal texts, one-page session probe + verdict, crawl tree, per-page blocked
POSTs, incremental events, JSONL). Polling via M2's `usePoll` (2 s while non-terminal, final fetch after). Key material and
seed only in React state.

**Task 23 — `/scanner/scans/:scanId` (mockup 04)**: `pages/ScanDetail.tsx` (`ScanDetailBody` pure + `ScanDetail` container;
`?tab=parallel|inventory|captures&page=&fill=`), `components/scanner/{ParallelView, FieldInventory (+ScanInventory),
RiskFlags, CapturesTab}.tsx`. Original side never shows a value (`(empty)` / `••• masked`).

## Gates (final run, after the last commit)

- `npm run typecheck` → clean.
- `npm test` → node tests `# pass 42 # fail 0`; Vitest **147 passed** (11 files; was 60 before this lane).
  New test files: `lib/scansApi.test.ts` (7), `components/scanner/format.test.ts` (12), `components/scanner/ArtefactImage.test.tsx` (2),
  `dev/fixtureServer.test.ts` (10), `lib/scans.test.ts` (18), `components/scanner/liveCrawl.test.tsx` (12),
  `pages/ScanNew.test.tsx` (7), `pages/ScanDetail.test.tsx` (19).
- `npm run build` → built; `check-bundle: no dev fixture code in dist/assets (4 markers checked)`; dist has a single JS chunk
  (the fixture dynamic import is dropped).
- `git diff --stat frontend/src/hooks/usePoll.ts` → empty (M2's hook untouched).
- `pytest` not run: no Python file changed in this lane.

## Visual / browser checks (private Playwright 1.55.0 venv `~/.cache/dps-build/pw-m4ui`, Vite dev server on 5184, fixture mode)

Screenshots in `~/.cache/dps-build/shots-m4ui/` — pages `12-setup-*`, `12-live-*`, `04-parallel-*`, `04-fields-obf-*`,
`04-inventory-*`, `04-captures-*`, `04-live-running-dark-1440` and the mockups at the same sizes `mock12-*`, `mock04-*`,
for {dark, light} × {1440, 390}. Scripts in `~/.cache/dps-build/pw-scripts/`.
- All 4 combinations, both pages, all tabs: **0 console errors / page errors**, `scrollWidth == innerWidth` (no horizontal
  page scroll at 390 px).
- Side-by-side vs mockups: same card order and titles, no-submit banner, stepper, two-column `.setup` / `.scan` / `.parallel`
  grids collapsing below 1200 px, crawl map statuses/chips/skip reasons, navigation-log columns and mobile layout,
  parallel-view frames and chips, field inventory columns, risk-flag list.
- Interactions: session check (valid → "✓ Session valid · /apply returned 200 without a redirect"; no session →
  "✗ Redirected to /login — the stored session is not valid"), deny-path validation message, deny path lands in the request
  preview, mode switch updates the request, unregistered host → the plan's error line, Start crawl → live crawl with rising
  blocked-POST counter, Open parallel view while running (Export personas disabled, Evidence pack disabled) → after finish
  Evidence pack link + Export personas enabled, Export inventory downloads `dps-scan-<id>-inventory.csv`, Synthetic/Obfuscated
  flip writes `?fill=obfuscate`, Inventory "risk flags only" filter (44 → 4 rows).

## Privacy checks
- No `console.*`, `localStorage` or `sessionStorage` use in the new scanner code (grep); the only log line is the fixture
  mode's own marker in dev. Key material/seed live in React state only; the request preview/copy excludes `consistency`.
- Tests assert: the spec has no submit field; the fixture server refuses one (422); the original side of the parallel view
  never contains a fill value; the persona preview never renders the seed.
- Fixtures contain synthetic values only (persona/test-range shapes); no customer value anywhere.

## Deviations
All recorded in `docs/superpowers/build/M4-DEVIATIONS-console.md` (committed with each task). Highlights: styles in
`styles/scanner.css` nested under `.scanner-page` (not appended to `styles.css`); `download.ts` re-exports M2's `saveBlob`;
`ScanSpecIn.purpose?` added; dev fixture mode + bundle check; SSR-based component tests (no jsdom, avoids a lockfile clash
with the m5ui lane); shared `ScanChrome` (crumb + stepper); max depth 0–5 / personas 1–50 per backend validators; recent
scans list on `/scanner`; captures tab omits the mockup's masked-DOM-text pane (no route serves it); inventory Input column
shows one qualifier. **No contract name changed.**

## Open issues / for the main lane at merge
1. Wire-up is already done through `api.scans` — the main lane only needs the real routes; verify field names against the
   final `ScanSummary`/`ScanPageOut`/`InventoryRow` (built from the plan + current `models.py`).
2. Plan Task 22/23 Step 3 (visual check against the compose stack with the real demo site) is still to do after merge;
   it was done here against fixtures only.
3. `package.json` `build` script and `vite.config.ts` test include changed (small merge hotspots); `main.tsx` gained the
   guarded fixture import and the `scanner.css` import.
4. CSP `img-src 'self' data: blob:` (Task 18, backend lane) is required for `ArtefactImage` in production.
5. Assumed refusal codes for POST /scans: `host_not_registered`, `url_invalid`, `scope_invalid`, `har_not_supported`,
   `auth_invalid`, `inline_auth_forbidden` (from `validate_scan`); event `note_code`s per plan Task 14.
