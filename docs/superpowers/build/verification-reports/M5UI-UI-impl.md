# M5 console lane (m5ui) — Tasks 18–23 implementation report

Worktree `/Users/neelu/document_intelligence/dps-wt-m5ui`, branch `m5ui` (based on `1a73de2`).
Author NilenduGanguli <nilendu.ganguli@gmail.com>, no trailers, explicit pathspecs. Only `frontend/`
and `docs/superpowers/build/M5-DEVIATIONS-console.md` were touched. No Docker, no push, no tags.

## Commits (one per task + one test-helper fix)

| Commit | Task |
|---|---|
| `210c96a` feat(console): M5 API groups and types, download/chart/dialog/key-once components, routes | 18 |
| `dbd5954` feat(console): Overview to mockup 01 — posture, trends, layer cards, findings, recent jobs | 19 |
| `5d984b5` feat(console): Reports & audit to mockup 09 — sweeps, failed sweeps, evidence packs, audit log, CSV export | 20 |
| `4766362` feat(console): evidence pack synthetic section, server PDF download and print to mockup 10 | 21 |
| `2fac5a4` feat(console): Admin to mockup 11 — keys shown once, rotation with grace, tenants and quotas, hosts, health, posture | 22 |
| `651f593` feat(console): Policies to mockup 05 — presets, matrix, recognizer builder with live test, plan preview, versions | 23 |
| `451ee7d` test(console): render views with StaticRouter so server rendering logs no layout-effect warning | test helper |

## What was built

- **Task 18 foundations**: `lib/types.ts` (every M5 model of Tasks 9–16 field for field + typed
  fidelity/privacy/utility/tabular blocks as built in Tasks 2/6/6B), `lib/api.ts` groups `api.admin`,
  `api.reports`, `api.evidence.pdf/json`, `api.policies.{list,create,get,saveDraft,discardDraft,publish,version,versionYaml,diff}`
  on M2's helpers + a `download()` helper (key header, Content-Disposition base name), `lib/format.ts`,
  components `BarChart` (SVG + table view), `ConfirmDialog` (native `<dialog>`, typed confirmation),
  `KeyOnceCard` (raw key in React state only; fingerprint after "I have stored the key"),
  `DownloadButton`, `Kpi`; `__APP_VERSION__` in the sidebar footer; `styles/m5.css` (page-scoped).
- **Dev-only fixture mode** (`npm run dev:fixtures` = `VITE_DPS_FIXTURES=1 vite`): `src/dev/fixtures.ts`
  (typed fictional data shaped like the mockups) and `src/dev/fixtureServer.ts` (wraps `window.fetch`,
  in-memory state for keys/tenants/hosts/policy drafts/publish/audit paging; scenarios
  `?fixture=degraded|noadmin|nostore|empty`). Loaded only under `import.meta.env.DEV`; the production
  bundle contains no fixture code (grep of `dist` for `dps-fixture-server`, `installFixtureServer`,
  `retail-banking-apac` → 0 files).
- **Task 19 Overview** (`OverviewView` + loader): crumb/readiness pills, 4 KPIs, trend chart, posture
  ring + 4 bars, 3 layer cards, findings by type, recent jobs/scans; degraded mode from `/metrics` when
  `/reports/summary` is 404/503 (or refused).
- **Task 20 Reports & audit**: URL-held filters (range/layer/outcome/search/scope), KPIs, sweeps chart
  with table view, failed-sweep cards, merged evidence packs + daily API roll-ups with JSON/PDF
  downloads and "Show older packs", admin-only audit log with scope switch, cursor paging and CSV export;
  callout without admin; fallback to M2's `/evidence` when `/reports/evidence` is 404.
- **Task 21 Evidence pack**: Download JSON (sealed artefact) / Download PDF / Print; policy `name vN`;
  key custody row; type/confidence/decision from pack columns; `FidelityPanel` (fidelity table with
  limit bars, lower-triangle correlation-delta grid + ramp, privacy cards: exact rows, NNDR, QI
  uniqueness, MIA proxy, utility card; amber "Privacy not measured" for `engine: none`); synthesize
  regulatory callout from the mockup; print CSS (Playwright print-to-PDF: 5 A4 pages, no clipping).
- **Task 22 Admin**: posture/caller pills, KPIs, keys table (prefixes, privileged scopes amber, rotate
  due chip, env keys without actions), rotate dialog with grace seg (now/24 h/7 days) → key shown once,
  disable/re-enable, create-key card with inline coded errors, tenants and quotas (amber ≥ 80 % +
  callout, edit/add dialogs), registered connections with "who may write", UI-scanner hosts (add/remove,
  server-side normalisation, inline `invalid_host`), service health (readyz + worker heartbeats),
  posture checks (failing first, "Rotate <key>"), configuration export (local JSON, no secrets).
- **Task 23 Policies**: store-backed preset/tenant list (adopt, new from preset), header with draft and
  active hash/publication facts, treatment matrix with "Changed in draft" and inline method editor
  writing the server draft, declared fields with declared-by/since, recognizer builder with debounced
  aborted live test via `/policies/test-recognizer`, plan preview with simulated FF1 switch, version
  history with YAML-like diffs and YAML download, publish/discard dialogs, read-only on 401/403,
  "no policy store" fallback to M1 routes.

## Gates (final, on `451ee7d`)

- `cd frontend && npm test` → exit 0: node tests `# pass 42 # fail 0`; Vitest **156 passed (156)**
  in 16 files (format, api M5 groups, fixture server, components, reports/admin/policyEdit helpers,
  Overview/Reports/EvidencePack/Admin/Policies pages) — no stderr warnings.
- `npm run build` (`tsc --noEmit && vite build`) → exit 0 (bundle 504 KB; Vite prints its >500 KB
  chunk-size advisory — not an error).
- `grep dangerouslySetInnerHTML src` → 0; no `localStorage` writes added (only M0 settings and M2's
  wizard draft remain).
- Browser (private Playwright 1.55.0 in `~/.cache/dps-build/pw-m5ui`, vite dev server on 5185 in
  fixture mode): final sweep of `/`, `/playground`, `/data-sources`, `/reports`,
  `/reports/evidence/ev_01K7ZQS7B2`, `/admin`, `/policies/gdpr-pseudonymise` at 1440 and 390 px,
  dark and light → **28 loads, 0 console errors, 0 px horizontal overflow**. Per-task sweeps also
  covered `?fixture=degraded|noadmin|nostore`, the obfuscate and `engine: none` packs, `pci-dss`.
  Screenshots: `~/.cache/dps-build/m5ui-shots/{t19,t20,t21,t22,t23,final}/`, mockup renders
  `mock-0{1,5,9}-1440.png`, `mock-11-1440.png`; compared side by side with mockups 01, 05, 09, 10, 11
  (same sections, order and labels; differences recorded in the deviation log).
- Interaction walks: `m5ui_admin_walk.py` (create key shown once → hidden; raw key absent from table,
  DOM after storing, local/session storage and URL; rotate 24 h grace; disable/re-enable; add/invalid/
  remove host; edit quota; 0 console errors) and `m5ui_policies_walk.py` (adopt, live test "1 match ·
  1 on the allow list", draft, plan 12 findings, FF1 simulated 8 fallbacks, publish v2, diff v1→v2,
  YAML download; 0 console errors).

## Deviations

All recorded with file, change and why in `docs/superpowers/build/M5-DEVIATIONS-console.md`
(committed with each task). Highlights: M2's ApiError/qs reused instead of the plan's redeclaration;
page CSS in `styles/m5.css` scoped by page; crumb bars inside pages; `/admin` wired in Task 22;
`api.policies.version()` and the optional `signal` on `testRecognizer` added (additive, no contract
name changed — contract not edited); M2/M1 fallbacks for a backend before M5; tests render pure views
server-side (no new npm dependency).

## Open issues / notes for the integration (main lane)

1. **Backend shape assumptions to confirm at merge** (all from the plan text, built against fixtures):
   `SummaryOut.layers` keys and `last_scan {id, pages, fields, personas}`; `EvidenceItem.summary`
   keys; `ApiRollup` (no `verify_passed`); `PolicyChange.path` granularity (UI handles `treat.X` and
   `treat.X.method`); `GET /policies/{name}/versions/{v}` default JSON; posture check name
   "…within N days" for the rotation age; the rotate response `{new, old}`.
2. **Host → storage-state link**: `TenantView` has no link from a registered host to a scanner
   connection; the console uses a connection tagged `host:<host>` (or `ui-scanner` + `params.host`).
   M4 should confirm or define the convention.
3. Read-only Policies mode is entered only after a write is refused (plan rule); there is no up-front
   admin probe, so a non-admin sees edit controls until the first save.
4. The tenant-scoped M5 Overview "Raw PII sent to LLM" tiles depend on Task 16B's roll-up columns.
5. Bundle crossed Vite's 500 KB advisory; code-splitting the pages is optional future work.
6. The lane's vite dev server (5185) was stopped at the end; restart with
   `cd frontend && VITE_DPS_FIXTURES=1 npx vite --port 5185 --strictPort`.
