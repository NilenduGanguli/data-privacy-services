# M4 console lane (m4ui) — review round 1 of Tasks 21, 22, 23

Reviewer: independent review agent · 2026-10-13 · worktree `/Users/neelu/document_intelligence/dps-wt-m4ui`, branch `m4ui`
Commits reviewed: `3a0cb60` (T21), `bc766ad` (T22), `cb8bd24` (T23) on top of `986ff89`. Working tree clean before and after.
No repository file was modified. Probes: `/Users/neelu/.cache/dps-build/reports/probe-m4ui-UI/` (`shots.py`, `interact.py`, `create_body.py`, `shots/*.png`, `facts.json`, `vite.log`).

## Verdict: FAIL (2 blocking, both narrow)

## 1. Gates (run by me)

| Gate | Result |
|---|---|
| `npm ci` (real `node_modules` dir, not a symlink) | ok |
| `npm run typecheck` | clean |
| `npm test` | node tests 42/42 pass; Vitest 10 files, 147/147 pass |
| `npm run build` | tsc + vite build ok; `check-bundle: no dev fixture code in dist/assets (4 markers checked)` |
| `git diff main -- frontend/src/hooks/usePoll.ts` | empty (M2's hook untouched) |
| `grep -c "export type JobState\|interface PageOf" src/lib/types.ts` | `1` |
| `styles.css` untouched; `scanner.css` has no raw hex/rgb colours | ok |
| Private Playwright 1.55.0 (`~/.cache/dps-build/pw-m4ui`) vs vite dev server 5184, `VITE_DPS_FIXTURES=1`, `DPS_APP_PORT=8499` (no backend touched) | 8 routes × {1440, 390} × {dark, light} = 32 page shots + mockups 12/04 in the same 4 variants: **0 console errors, 0 page errors, 0 failed requests, scrollWidth == clientWidth everywhere (no horizontal page scroll at 390)**; theme attribute follows dark/light |

Integration tests on lane containers: not applicable (console lane, no Docker by instruction).

## 2. Visual comparison against the mockups

- **Mockup 12 vs `/scanner/new?scan=…`** (1440 dark): card order Target/Scope/Authentication/Fill mode/Request, no-submit banner with two stats, stepper, Crawl·live (chip, visited/discovered, budget, bar, pipe, 4 stats), Crawl map (tree, depth chips, PII/crit/risk/POST-blocked chips, skipped rows struck through, footer with risk sentences), Navigation log (5 columns, 13 rows, "Showing 13 of N", Download JSONL) — all match. Two-column `.setup` collapses below 1200 px; 390 px layout stacks cleanly.
- **Mockup 04 vs `/scanner/scans/:id`**: head sentence, Live crawl / Export inventory / Export personas / Evidence pack, stepper with "persona 1 of 20", tabs, Pages-with-forms list with selected row, Rendered personal data, Risk flags, Parallel view (two framed screens, amber/violet chips, legend, callout), Field inventory — match. Captures tab renders per-page masked capture cards and the withheld callout.
- Interactive (fixture mode): host_not_registered error line shows the plan's exact text; bad deny path shows the mockup message; Check session runs a probe and prints "✓ Session valid · /apply returned 200 without a redirect"; Start crawl navigates to `?scan=`, crawl runs to "finished · 0:47"; key card shown once and gone after reload; `localStorage` holds only `dps.theme`, `sessionStorage` empty; the request preview never contains `consistency`/seed/PEM; the actual POST body carries `consistency.synthesis_seed` + `wrap_key` (when RSA chosen) and an `Idempotency-Key` header; no `submit` anywhere; Fields/Obfuscated flip writes `fill=obfuscate` to the URL; inventory "Risk flags only" filter works (4 of 44).

## 3. Contract / type conformance

`types.ts` additions checked field-for-field against the backend lane's pydantic models (`dps-wt-m4/dps/layers/scanner/models.py`, `dps/jobs_models.py`, read-only): `ScanSummary`, `ScanPage`/`ScanPageOut`, `ScanField`/`FieldRecord`, `RiskFlag`, `NumberedRiskFlag`, `ValidationMessage`, `ScanEvent`, `RequestCount`, `InventoryRow`, `ScanCreated`, `ScanDetail`, `ScanPageDetail`, `PersonaPreview`, `ScanScope`, `FillSpec`, `CaptureSpec`, `ScanPurpose` — names and shapes agree. `wrap_key {type:'rsa-oaep-256', public_key_pem}` matches `crypto.wrap_key_material`; key card reads `wrapped_dek` which the backend sets. `api.scans.*` routes are exactly the plan's. `field_no` is unique per page (dom.py offsets by `startField`), so the React keys are safe. No contract name broken for other lanes.

## 4. Blocking findings

### B1 — "Restart crawl with these settings" after opening a scan by link re-sends the redacted start URL and silently drops the RSA key wrap
`frontend/src/lib/scans.ts:91-102` (`configFromSpec`) and `frontend/src/pages/ScanNew.tsx:141-146` (hydration effect).
When `/scanner/new?scan=<id>` is opened by link (reload, or the "Live crawl" button of ScanDetail), the form is hydrated from `scan.spec`, which is the value-free `dps_job.spec` written by `redact_scan_spec`: `url` is the **redacted** start URL (`redact_url`: path IDs → `{id}`, entity segments → `<ENTITY>`, query values → `<v>`/`<ENTITY>`, fragment dropped) and `consistency` keeps only flags (`key_wrapped`, `caller_seed`, …). `configFromSpec` copies `spec.url` verbatim and takes `wrap: 'clear', pem: ''` from `DEFAULT_CONFIG`, ignoring `consistency.key_wrapped`.
Failure: original scan `http://host/apply/12345?ref=ABC`, mode obfuscate, key wrapped with the caller's RSA key → reopen by link → Restart → POST `/scans` with `url: "http://host/apply/{id}?ref=<v>"` (a different target, literal placeholders) and **no `wrap_key`**, so the new scan's key material comes back in clear. The Check-session probe uses the same redacted URL. This contradicts the plan's "Restart = cancel current then Start" with *these settings* and quietly downgrades key custody. The round-trip unit test (`scans.test.ts:52`) only uses a placeholder-free URL and no wrap, so it cannot catch it.
Fix (narrow): in `configFromSpec`, hydrate `wrap: 'rsa'` when `spec.consistency?.key_wrapped` (pem stays empty, so `configError` forces the operator to paste the PEM again); in `configError`, refuse a start URL that contains a redaction placeholder (`<…>` or a `{id}` segment) with a message asking to re-enter the full start URL (and keep the probe behind the same check). Add unit tests for both cases.

### B2 — Scan detail never shows an error for the selected page, the pages list or the inventory: it spins forever
`frontend/src/pages/ScanDetail.tsx:175-179, 143-145, 152-154` — only `detail.error` reaches `DetailState`; `pages.error`, `pageDetail.error` and `inventory.error` are dropped.
Probe: `/scanner/scans/<id>?tab=parallel&page=999` → the API answers 404; the page shows "Loading the page…" with a skeleton indefinitely (`interact.py`, `page999_text: ["Loading the page…"]` after 5 s, usePoll retrying every 4 s). Same for the Inventory tab on any refusal (403 for a key without the scan scope, 5xx): permanent skeleton. The review brief lists missing error states as defects; a permanent loading state hides a refusal from the operator.
Fix (narrow): pass `pageError`, `pagesError`, `inventoryError` into `DetailState`; render "This page is not part of the scan" for a 404 page detail and a value-free `errorText` callout otherwise (pages card, parallel pane, inventory tab), with a static-render test for each.

## 5. Non-blocking findings (should fix or note)

1. Field inventory at 1440 px: the added "Filled value" column is clipped (horizontal scroll inside `.tbl-scroll`, "FILLED VA…", "meera.ve…" cut). Not a page scroll, but the desktop layout no longer fits like mockup 04; consider wrapping the value or dropping the Evidence column width.
2. Fixture inaccuracy: `scanFixtures.ts:223` gives every captured page a `capture_json`, also in synthesize mode; the backend (plan Task 12/14) emits `capture_json` only in obfuscate mode. The visual check therefore showed "Download reversible map" next to "synthetic replacement", a state the real API cannot produce. Component logic itself is correct (button hidden without `capture_json`).
3. `MAX_PAGES = 500` (UI bound and `scope_invalid` text) vs the deployment cap `scan_max_pages` default 50: 51–500 passes the client check and is refused by the server. Acceptable (refusal shown), but the hint is misleading.
4. Deviation T22 "each Start sends a fresh Idempotency-Key — the key makes a double click or retry safe": double clicks are prevented by `busy`, not by the key; a retry gets a new key, so it is not retry-safe. Wording only.
5. `checkSession` keeps polling up to 60 s after the component unmounts (no abort); harmless but wasteful.
6. `createErrorText` has no words for `start_url_denied` / `fill_invalid` (falls back to "422 code: detail"). Value-free, fine.
7. `blockedByPage` (`lib/scans.ts:192`) is unused in production code (CrawlMap counts POSTs inline); dead helper kept alive by its test.
8. `usePoll` keeps the previous scan's `data` after `scanId` changes (M2 hook behaviour), so after Restart the old crawl map shows for ~one poll. Cosmetic.
9. The FIXTURES dev badge overlaps the crawl map/inventory edge in dev shots (dev only, not in the bundle).

## 6. Privacy / leak hunt

- No `console.*`, `localStorage`/`sessionStorage`, `innerHTML`/`dangerouslySetInnerHTML` in the new production code (only the dev fixture banner `console.info`). Key material and seed live in React state only; confirmed by storage dump after create; gone on reload.
- Original side never shows a value: Fields view shows `(empty)` / `••• masked`; Screens view shows `annotated ?? original`, both masked by the worker (plan Task 12: `unmasked` only with the local-only `scan_store_unmasked_originals`). Unit test asserts no fill value appears on the left frame.
- Request preview and "Copy request" exclude `consistency` (seed, PEM): verified in the DOM and by recording the actual POST body.
- URLs are shown only from `*_redacted` fields; `pathOf` masks `<v>`/`<ENTITY>` (also percent-encoded) as `•••`.
- Artefacts are fetched with the API key and shown as blob URLs (revoked on change/unmount); no public `src`.
- Dev fixture mode cannot reach production: `import.meta.env.DEV` gate + `check-bundle.mjs` marker scan in `npm run build` (passed).
- Tenant isolation / SQL: not applicable to this console lane (no server code); the page shows "not found for the tenant" on a 404 scan.

## 7. Deviation log judgement (`docs/superpowers/build/M4-DEVIATIONS-console.md`)

| Entry | Judgement |
|---|---|
| T21 `download.ts` re-exports M2 `saveBlob` | accept (avoids a second implementation) |
| T21 `scansApi` reuses `enc`, declared above `api` | accept (TS2451 / TDZ) |
| T21 `ScanSpecIn.purpose?`, `ScanArtefactKind` | accept (backend `ScanSpec.purpose`, additive) |
| T21 `styles/scanner.css` nested under `.scanner-page` instead of appending to `styles.css` | accept (M2 as-built pattern; prevents generic class bleed); Vault tokens only verified |
| T21 dev fixture mode + `check-bundle.mjs` | accept (programme plan §2.3 says build vs typed fixtures); see non-blocking 2 for one fixture inaccuracy |
| T21 Vitest `*.test.tsx`, SSR render helper, no jsdom | accept; consequence: interactive behaviour (polling, hydration, restart) is untested — B1 slipped through for this reason |
| T21 extra `format.ts` helpers | accept (value-free) |
| T22 `lib/scans.ts` pure logic | accept |
| T22 `ScanChrome` shared | accept |
| T22 depth 0–5, personas 1–50 | accept (backend bounds) |
| T22 `en_CA` locale | accept |
| T22 auth datalist + "none" button | accept |
| T22 key card (kid, wrap state, seed fingerprint, Copy/Download, React state only) | accept |
| T22 recent scans in empty live column | accept (additive) |
| T22 restart/idempotency | accept behaviour; justification overstated (non-blocking 4); restart hydration defect is B1 |
| T22 CrawlMap counts POST only | accept (mockup wording) |
| T22 NavLog "local time" | accept |
| T22 visual check vs fixtures, not compose | accept (programme plan §2.3/§4: m4ui has no Docker); compose walk remains due at G7/verify |
| T23 `ScanDetailBody` / container split | accept; but the container drops three error channels (B2) |
| T23 tabs via `?tab=` (replace), Screens/Fields local | accept (contract §14 names tab/page/fill) |
| T23 head actions (Evidence pack per scan, "so far", disabled personas) | accept |
| T23 pages list `N fields` chip, queued line | accept |
| T23 Captures tab without masked-DOM text / replacement facts | accept (no route returns them, contract §9) |
| T23 Fields view never shows originals | accept (value-free rule) |
| T23 Filled value column, validation toggle, ScanInventory filters/downloads | accept; layout clipping noted (non-blocking 1) |
| T23 `inputText` one qualifier | accept |

## 8. Open issues for later gates

- The compose-level walk (plan Task 22/23 Step 3 against the real scans API and demo site) is still owed after the merge into `m4` (G7 / lane verify), including the real `capture_json` behaviour per mode and the CSP `blob:` image source from Task 18.
