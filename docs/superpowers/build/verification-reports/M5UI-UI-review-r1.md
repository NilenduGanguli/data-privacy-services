# M5 console lane (m5ui) — independent review r1 of Tasks 18–23

Worktree `/Users/neelu/document_intelligence/dps-wt-m5ui`, branch `m5ui`, HEAD `451ee7d` (7 lane commits on
`1a73de2`: 210c96a T18, dbd5954 T19, 5d984b5 T20, 4766362 T21, 2fac5a4 T22, 651f593 T23, 451ee7d test helper).
Working tree clean before and after the review (no repository file modified). Probes:
`/Users/neelu/.cache/dps-build/reports/probe-m5ui-UI/`.

**Verdict: FAIL** — gates green and the pages match their mockups, but two behaviour defects (one key-custody
lockout, one false "passed" verification claim) block; one contract interop defect is listed as blocking for the
diff view (low effort to fix).

## 1. Gates (run by the reviewer)

| Gate | Result |
|---|---|
| `npm ci` (once, real `node_modules`, not a symlink) | ok |
| `npm test` | node --test 42/42 pass; Vitest **14 files, 156 tests pass** |
| `npm run build` (`tsc --noEmit` + vite build) | ok (only the >500 kB chunk advisory) |
| fixture code absent from `dist` (`dps-fixture-server`, `fixtureServer`) | none found |
| `dangerouslySetInnerHTML` / `innerHTML` in `src` | none; `localStorage` only M0 settings + M2 wizard draft (unchanged) |
| `console.log/warn/error` in non-test source | none |
| Private Playwright 1.55.0 (`pw-m5ui` venv), vite fixture mode on :5185 | 13 page/scenario URLs × {1440, 390} × {dark, light} = 52 loads: **0 console errors, 0 page errors** (only React Router v7 future-flag warnings, pre-existing), **0 px horizontal overflow at 390** |
| Visual comparison vs mockups 01, 09, 10, 11, 05 (screenshots `probe-m5ui-UI/shots/`, `MOCK-*` side by side) | same sections, order and labels; differences are the logged deviations (crumb bar row, UTC days, no 30-day strips, etc.) |
| Degraded / empty / noadmin / nostore scenarios | render without errors; callouts present (degraded summary, admin-scope callout, read-only/no-store callout, empty-state rows) |

No integration tests apply (console lane, no Docker by instruction).

## 2. Blocking findings

### B1 — Rotating the caller's own key with "Old key stops working: now" loses the new key (shown-once violated)
`frontend/src/pages/Admin.tsx`: `actions.rotateKey = after(api.admin.rotateKey(...))` awaits `load()` before the
dialog stores the result. With grace 0 the server invalidates the old key (plan Task 9/11: `invalidate` +
`refresh`), so the reload's `GET /admin/keys` answers 401, `load()` sets `forbidden`, and AdminView renders the
forbidden branch — but `{rotated && <KeyOnceCard …/>}` lives only inside the non-forbidden branch. The raw new
key is held in state yet never displayed; the API returns it once, so it is lost, and the operator is locked out
of Admin with a key nobody saw (the posture card's own "Rotate <name>" leads to the same path).
Proof (`probe-m5ui-UI/rotate_self.py`, real page in fixture mode, later admin GETs answered 401 as the real API
does after a grace-0 rotation): `rotate POST succeeded: True · forbidden callout visible: True · new raw key
visible: False · 'shown once' card visible: False`. Control without the 401 (`rotate_control.py`): key card
visible: True.
Fix: render the one-time key cards (rotate and create) outside the `forbidden` branch (or store the rotate result
before awaiting the reload and keep the card mounted when the reload is refused); when the rotated key is the
caller's own (prefix match) and grace is 0, say so in the dialog ("this is the key this console uses; paste the
new key above after storing it"). Add a test that a `rotated` card renders with `forbidden=true` (extract the card
state or test via a pure helper).

### B2 — Evidence list words a canceled/dead run as "passed · 0 leaks"
`frontend/src/lib/reports.ts::verificationChip`: `passed = s.verification_passed !== false && outcome !== 'failed'`.
Probe (`probe-m5ui-UI/vt/probe.test.ts`): outcome `canceled` or `dead` with `verification_passed`
`undefined | null | true` → emerald **"passed · 0 leaks"** (6/6 cases). A run that did not finish has an
incomplete output; main's committed hardening N3 (`6ed65be`, programme plan §1.1: "failed/canceled run never reads
as passed") and the M5 plan's own outcome grouping (`failed → failed|canceled`) both say it must not read as
passed. This is a false verification claim in the evidence/reports surface and will regress N3 when m5ui merges.
Also `failed` without hits reads "failed closed", which implies a caught leak for e.g. a `length_overflow`
refusal (main words it "not verified · job failed (<code>)").
Fix: passed only when `outcome ∈ {succeeded, dry_run}` (scans: `succeeded`) **and** `verification_passed === true`;
otherwise slate "not verified · job <outcome>" (rose "failed · N leaks caught" when hits > 0). Reuse main's
`verificationText/verificationTone` from `lib/jobs.ts` at integration; add the probe's cases as tests.

### B3 — Version diff mis-renders the policy store's real diff paths (contract interop)
`frontend/src/lib/policyEdit.ts::diffLines` splits `PolicyChange.path` on every `.`. The binding backend
(`flatten_policy`, M5 plan Task 15, full code given) keys list items as `…[<name or path>]`, and declared-field
paths contain dots. Probe output for `detect.declared_fields[LOANS.APPLICANTS.EMPLOYER_NAME].entity_type`:
```
ctx   declared_fields[LOANS:
ctx     APPLICANTS:
ctx       EMPLOYER_NAME]:
add         entity_type: ORG_NAME
```
The page was only exercised against the dev fixture's own `flatten` (method specs and lists as leaves), which
does not match the plan's backend shape; Task 23's mockup acceptance (v1→v2 diff with a declared
`LOANS.APPLICANTS.EMPLOYER_NAME`) will render broken against the real API. `changedEntities` is fine for both shapes.
Fix: split paths with a bracket-aware tokenizer (`/\.(?![^[]*\])/`), render `name[label]` segments as list items
(`- name: <label>` context), and make the fixture server's `flatten` reproduce `flatten_policy` exactly (keys,
`[name|path|index]` items, `name`/`version` skipped) so walks test the real shape; add a test with the plan's
`test_flatten_keys_lists_by_name_or_path` paths.

## 3. Non-blocking findings and nits

- N1 `FidelityPanel` exact-row card always says "No synthetic row equals a source row… / enforced" regardless of
  `exact_row_matches`; word it from the number (and amber when > 0). Denominator is `rows_written` while the count
  is measured on the preview (say "measured on the N-row preview").
- N2 Evidence sheet section 2 now has both the M2 "Seed/Key id" custody sub-line and the new "Key custody" row
  (duplicated text; for a synthesize pack the M2 line still says "Data key returned… holds the key id only").
- N3 `Policies.tsx`: `void actions.saveDraft(...)` — a refused draft save (400 `invalid_policy`) is shown in the
  page error but also becomes an unhandled promise rejection (console error). Catch in the call sites.
- N4 Stale responses on quick policy switch remain (`loadDetail` and the draft-diff fetch have no live/abort
  guard) — the known M1-N item, now in M5 code; `readOnly` never resets when the key changes.
- N5 Client `KEY_NAME` `^[a-z0-9][a-z0-9-]{2,62}$` vs server `{1,62}` (2-character names refused client-side).
- N6 `callerKey` matches on the 8-char prefix (`dps_` + 4 chars); collisions are possible but only affect a pill.
- N7 Host → storage-state link via `host:<host>` tag is a console convention M4 has not defined (logged as an
  integration open item — keep it on the WP-I list).
- N8 Read-only on Policies is entered only after a refused write (plan-compliant), so non-admin keys see Publish /
  Discard until the first 403.

## 4. Deviation log judgement (`docs/superpowers/build/M5-DEVIATIONS-console.md`)

All entries are justified and accepted: reuse of M2's `ApiError/request/json/post/qs` (contract §14), `download`
through `request` with base-name filenames, `evidence.json` via the sealed artefact (no JSON route exists),
`m5.css` scoped under page roots (integration lesson `ef40417`), `__APP_VERSION__` from package.json, `Soon` kept for
`/scanner`, SSR view tests (no new dependency), dev-only fixture mode (verified absent from `dist`), Overview
degraded mode widened, Reports fallback to M2 `/evidence`, roll-up dating, Admin rotation-due from `age_days`,
Policies no-store mode and server-only live test. Two entries are contradicted by findings: Task 23 "diff as
YAML-like lines… works for either flattening" holds for `changedEntities` only, not `diffLines` (B3); the Task 22
browser walk did not cover rotating the caller's own key with grace 0 (B1).

## 5. Privacy / security checks performed

- Raw key: React state only; no storage/URL/log writes (grep), dropped on "I have stored the key"; fingerprint is
  prefix + 4…4 hex of SHA-256; config export carries API views only (no secret fields exist in them).
- No `innerHTML`; recognizer matches built as `<mark>` from spans; recognizer/plan samples sent only to the M1
  routes (not stored); no customer value is rendered beyond what the API returns.
- Filters reach the API only as query parameters (server validates `q`, cursors, tenants); URL filters are
  whitelisted in `parseFilters` (q capped at 120 chars).
- Types checked field-for-field against plan Tasks 9–16 models and against the m5 lane's built
  `dps/engine/metrics.py` / `dps/layers/connectors/tabular.py` / `evidence.py` (privacy, fidelity, utility, tabular
  blocks match, incl. `exact_row_resamples_run`, `nndr.target/passed`, `membership_inference`).
