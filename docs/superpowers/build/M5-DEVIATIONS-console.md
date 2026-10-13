# M5 console lane (Tasks 18–23) — deviations from the plan

Lane: worktree `dps-wt-m5ui`, branch `m5ui` (console only; the backend for the same routes is built
concurrently in `dps-wt-m5`). Pages are built against the API shapes of the M5 plan (Tasks 9–16) and
the contract (§9, §14), with typed fixtures, Vitest tests and a dev-only fixture mode. Code as built
wins over older plan text; each row says file, change and why.

## Task 18 — foundations

| File | Change | Why |
|---|---|---|
| `frontend/src/lib/api.ts` | The plan's `ApiError`/`toError`/`qs`/`headers` code block is **not** re-declared: M2's `ApiError(status, message, code?, extra?)`, `toError`, `request`, `json`, `post` and `qs` are reused. `qs` additionally accepts booleans. | Contract §14: "new groups use M2's helpers and never redeclare `qs`"; M2's `ApiError` already carries `code`. |
| `frontend/src/lib/api.ts` | `download(path, auth, fallback, fixedName?)` returns `Download {blob, filename}` through `request()`; the server's `Content-Disposition` name is reduced to a base name (`dispositionName`). | One request path for auth headers and coded errors; a server name can never carry a path. |
| `frontend/src/lib/api.ts` | `api.evidence.json(id)` downloads the pack's sealed `evidence_json` artefact (via `GET /evidence/{id}` → `GET /artefacts/{id}`), saved as `evidence-<id>.json`; refuses with `artefact_not_found` when the pack has none. | There is no `/evidence/{id}/json` route; the artefact is the byte-exact JSON `pack_sha256` was computed over. |
| `frontend/src/lib/api.ts` | `api.policies.testRecognizer(auth, rec, text, signal?)` — optional `AbortSignal` (additive). | Task 23's live test aborts superseded requests. |
| `frontend/src/lib/types.ts` | Besides the plan's list: `KeyScope`, `CreateKeyBody`, `AuditFilters`, `AuditLayer`, `AuditOutcome`, `SummaryLayers`, `EvidenceItemSummary`, `PolicyList`, `PolicyDiff`, `FidelityColumn`, `NndrBlock`, `MiaBlock`, `UtilityBlock`. `SummaryOut.layers` and `EvidenceItem.summary` are typed with the keys `dps/reports.py` writes (pydantic: `dict[str, Any]`). `EvidencePack.fidelity/privacy/utility` were `Record<string, any> \| null` (M2) and are now the typed, optional blocks; `tabular?` added. The pack's column fields (`data_type`, `confidence`, `override`, `params`) need no type change (columns are `Record<string, any>[]`). | The blocks mirror `dps/engine/metrics.py` and `dps/layers/connectors/tabular.py` as built (Tasks 2, 6, 6B), so the page reads them without casts. |
| `frontend/src/styles/m5.css` (new; imported by `main.tsx`) | The mockup classes M5 pages need live here, scoped under `.m5-page` (every M5 page root) or the page root (`.overview-page`, `.reports-page`, `.admin-page`, `.evidence-page`, `.policies-page`) — not in `styles.css`. Mockup 11's `dialog.confirm` and `.checks` are `dialog.cdlg` and `.scopechecks`; the download wrapper is `.dlb`. | The M1/M2 integration had to scope page CSS so it stopped restyling other pages (`ef40417`); M2's `ConfirmWriteModal` already owns `.confirm`/`.checks` in `components.css`. |
| `frontend/vite.config.ts` | `__APP_VERSION__` = `npm_package_version`, else the `version` read from `package.json`. Vitest `include` gains `src/**/*.test.tsx`. | `npx vite` (the lane's dev server) does not set `npm_package_version`; component and page tests are `.tsx`. |
| `frontend/src/App.tsx` | The `Soon` placeholder stays for `/scanner` (the M4 console lane builds it) and the catch-all route; `/admin → Admin` is wired in the Task 22 commit. | Removing it would leave `/scanner` blank until M4 merges; the Admin page does not exist at Task 18. |
| `frontend/src/components/BarChart.tsx` | Extra props `xFormat` (ISO day → "Oct 11"), `initialTable`, `legend`; the table view lists newest first (mockup 09); class `m5chart`. | Mockup axis labels and table order; `.chart` is mockup 09's absolute-positioned box. |
| `frontend/src/components/ConfirmDialog.tsx` | Extra props `busy`, `error` (inline API error under the body). | Admin and Policies show refusals inside the dialog that caused them. |
| `frontend/src/components/KeyOnceCard.tsx` | `rawKey: string \| null`; "I have stored the key" computes `keyFingerprint()` (prefix + short SHA-256 via `crypto.subtle`), keeps only that in the card's state and calls `onStored()`, which drops the raw key from the parent's state. | The parent owns the raw key (it came from the create/rotate response); the card must keep showing the hidden state after the key is gone. |
| page crumb bars | The mockups' topbar crumb and status pills render as a `.crumbbar` row at the top of each page. | `Layout`'s topbar holds the tenant/API-key settings shared by every page (M0); M2 pages already put their crumbs in the page. |
| tests | Pages are tested by server-rendering pure view components (`react-dom/server`, already a dependency) under Vitest's Node environment; interactions are tested through pure helpers and walked in the browser check. | Rule "no new npm dependency": no jsdom/testing-library. |
| `frontend/src/dev/{fixtures,fixtureServer}.ts`, `main.tsx`, `package.json` (`dev:fixtures`) | **Dev-only fixture mode** (not in the plan): with `VITE_DPS_FIXTURES=1` the dev server's `window.fetch` is wrapped and the console's API calls are answered from typed fixtures with in-memory state (create/rotate/disable keys, tenants, hosts, policy drafts/publish, audit paging). Scenarios via `?fixture=degraded\|noadmin\|nostore\|empty`. `main.tsx` imports it only under `import.meta.env.DEV`, so a build drops it (checked: no `dps-fixture-server` marker in `dist`). | Programme plan WP-M5 UI: pages are built and browser-checked against fixtures while the backend is built in the other lane. |

## Task 19 — Overview (mockup 01)

| File | Change | Why |
|---|---|---|
| `frontend/src/pages/Overview.tsx` | Split into a pure `OverviewView` (tested) and the `Overview` loader; helpers `readinessPill`, `postureBars`, `mostUsedPolicy` exported. | Testable without a DOM library (Task 18 row "tests"). |
| same | "policy pack <name vN>" = the policy with the most jobs from `GET /policies` (tenant policies, then presets); omitted when the store answers nothing. | `SummaryOut` carries no policy; the store's `jobs` counts do. |
| same | Degraded mode also covers a summary refused for another reason (401/403/5xx): the M0 tiles render with the refusal named, instead of the database callout. | "The page never errors." |
| same | Layer 3 card's big number is the scan count (`ui.scans`) with pages, fields and risk flags in the line under it; the last scan id links to `/scanner/scans/:id`. Recent jobs link scans to `/scanner/scans/:id` (M4) and runs/discovers to `/data-sources/jobs/:id`. | The mockup's "forms so far" has no field in `SummaryOut`. |
| same | Days are labelled UTC (mockup: IST) and the chart's y axis is compact (45M). | Roll-ups are by UTC day (Task 12). |
| `frontend/src/components/BarChart.tsx` | `yFormat` prop (default `compact`). | Axis labels such as 43,800,000 crowd the chart. |
