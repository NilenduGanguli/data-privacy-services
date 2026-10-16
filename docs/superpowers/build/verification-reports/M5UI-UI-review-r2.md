# M5 console lane (m5ui): independent review r2 of Tasks 18 to 23

- Worktree: `/Users/neelu/document_intelligence/dps-wt-m5ui`, branch `m5ui`.
- HEAD: `192652f`, the fix-r1 commit on top of `451ee7d`.
- The working tree was clean before and after the review. I modified no repository file.
- Probes are in `/Users/neelu/.cache/dps-build/reports/probe-m5ui-UI/r2/`: `gen.py`, `cases.json`, `probe.test.ts`, `walk.py`, `shots/`, plus screenshots.
- I wrote this review from the code and from runs I did myself. I read the impl and fix reports but did not rely on them.

**Verdict: PASS.**
- All three r1 blockers (B1, B2, B3) are fixed, and the fixes are backed by tests that would have failed before.
- Every gate is green.
- The browser checks show no regression.
- I found no new blocking defect. The remaining items are non-blocking (section 4).

## 1. Gates (I ran each one)

| Gate | Result |
|---|---|
| `npm ci` (run once; `node_modules` is a real directory, not a symlink) | ok |
| `npm test` | node --test: 42 tests, 42 pass, 0 fail. Vitest: 14 files, **183/183 pass** |
| `npm run build` (`tsc --noEmit` + vite build) | ok. The only warning is the advisory for the 516 kB chunk |
| Fixture code (`dps-fixture-server` / `fixtureServer`) in `dist/` | none |
| `innerHTML` / `dangerouslySetInnerHTML` / `console.*` in non-test `src` | none (one doc comment mentions innerHTML) |
| Web storage | only M0 settings (the API key in sessionStorage unless "remember" is ticked) and the M2 wizard draft. Unchanged |
| Private Playwright 1.55.0 (`pw-m5ui` venv); vite in fixture mode on :5185 (`VITE_DPS_FIXTURES=1`); the r1 sweep `check.py` → `r2/shots/report.json` | 13 page/scenario URLs × {1440, 390} × {dark, light} = **52 loads**. **0 console or page errors** apart from the React Router v7 future-flag warnings, which were already there. **0 px horizontal overflow** |
| Text of the Reports page (r2 shots) | no unfinished pack reads as passed. Only `succeeded` packs and roll-ups show "passed ·". A caught leak reads "failed · 2 leaks caught" |
| Dev-server log scan (`r2/vite.log`) | 0 occurrences of `dps_` |
| Integration tests on containers | not applicable: this is a console lane with no Docker, by instruction |

## 2. The r1 blockers, re-verified

### B1: the one-time key is lost when the console rotates its own key with "now". **Fixed.**

**What changed in the code:**
- The `Admin` container holds the raw keys (rotated and created) in its `oneTime` state, and `AdminView` receives them as props.
- The forbidden branch now renders the rotated `KeyOnceCard` and the created one.
- A rose `role="alert"` callout names the case where the console's own key was rotated.
- `load()` never rejects: 401/403 sets `forbidden`, any other error sets `error`. So `after(...)` always resolves, and `setOneTime` always runs after a successful POST.
- `keys` is not cleared when the page goes forbidden, so `me` still resolves and the own-key wording applies.

**r1 probe `rotate_self.py`, run again against HEAD:**
- The rotate POST succeeded.
- The forbidden callout was visible.
- **The raw key was visible: True. The "shown once" card was visible: True.** Before the fix both were False.
- The control run (`rotate_control.py`) still shows the card.

**My own walk (`r2/walk.py`, 390 dark and 1440 light, with a fictional console key whose prefix matches `platform-admin`):**
- The dialog warns that this is the console's own key.
- After the rotation: forbidden True, own-key callout True, raw key shown True.
- After "I have stored the key":
  - the raw key is no longer shown;
  - no new key is in localStorage, sessionStorage or the URL;
  - overflow is 0 px;
  - there are 0 console or page errors.
- Screenshot: `r2/admin-forbidden-390-dark.png`.

**Tests** (`Admin.test.tsx`, 4 new): the rotated key renders with `forbidden=true`; the created key renders while forbidden; each key renders exactly once when not forbidden; nothing renders once the key is stored. These tests exercise the claim itself: they render the forbidden branch with a key in state.

### B2: a canceled or dead run's evidence read "passed · 0 leaks". **Fixed.**

The new `verificationChip` rules:
- `finishedPack` means `succeeded`, or `dry_run` for non-scan packs.
- A pack reads as passed only when it is finished **and** `verification_passed === true`.
- Otherwise:
  - hits > 0 → rose "failed · N leaks caught";
  - unfinished → "not verified · job|scan <outcome>" (slate when canceled, rose otherwise);
  - finished with `false` → "failed closed";
  - finished with nothing recorded → slate "not verified · no verification recorded".

I checked this against main's `finishedOutcome`, `verificationText` and `verificationTone` (`data-privacy-services/frontend/src/lib/jobs.ts:922-950`). The semantics match, with two exceptions:
- Main has an amber "review required" state, driven by the post-write sample.
- Main appends an `(error_code)` suffix.

Neither field exists in the plan's `EvidenceItem.summary` key list (Task 12), so this is an integration item, not a defect here. `reports.test.ts` now covers the 9 r1 probe cases plus the hit, finished and scan cases.

### B3: the version diff split declared-field paths at their dots. **Fixed.**

**What changed:**
- `splitPath` is bracket-aware: it counts depth, so `declared_fields[$.customers[*].ssn]` stays one item.
- `diffLines` builds a tree from the paths and renders list items as `- …`.
- An item added or removed whole folds into one flow line.
- A partly changed item renders as `- [label]` with its changed fields beneath.

**Independent check** (`r2/gen.py` → `cases.json` → `r2/probe.test.ts`):
- I copied the plan's `flatten_policy` and `diff_policies` verbatim from Task 15, ran them in Python, and generated four realistic diffs:
  - add declared fields, including a JSONPath label with `[*]`, and a recognizer with nested patterns, context and deny list;
  - change a nested pattern score, add a pattern and a context word, change a treatment's method and params, change a declared field's entity;
  - remove all declared fields, and empty a deny list;
  - the reverse of all of the above.
- **The fixture's `diffBodies` equals Python's output for all 4 cases.**
- `diffLines` rendered every case correctly:
  - no `LOANS:` / `APPLICANTS:` keys;
  - JSONPath labels intact;
  - nested `patterns[emp]` rendered as `- [emp]` with the score `-`/`+` beneath;
  - an emptied list rendered as its removed items;
  - a whole recognizer folded with `name` first.
- In the browser (both widths and themes), the Policies draft diff shows `- { path: "LOANS.APPLICANTS.EMPLOYER_NAME", entity_type: ORG_NAME }` and no `LOANS:` key.

## 3. Deviation log (`docs/superpowers/build/M5-DEVIATIONS-console.md`)

The r1 entries were judged in r1 and are accepted. Judgement of the new section "Review r1 fixes":

| Entry | Judgement |
|---|---|
| B1: lifted one-time key state, forbidden view shows the cards, `rotateWarning` | **Accepted.** It supersedes the Task 22 "local state" row. The key is still held only in React state. |
| B2: `verificationChip` / `finishedPack`, main's tone kept (rose unless canceled) instead of the r1 suggestion of all-slate | **Accepted.** Matching main makes Reports and the evidence page agree after the merge, and r1 named main's helpers as the target. The integration item (`evidence_list` must set `summary.verification_passed` for scan packs) is correctly recorded. |
| B3: `splitPath` / tree `diffLines`; the fixture `flatten`/`diffBodies` reproduce the plan exactly | **Accepted, and verified by the Python equivalence probe above.** |

No contract name changed. The new names are page-internal props and exported helpers (`OneTimeKey`, `OneTimeKeys`, `NO_ONE_TIME_KEYS`, `rotateWarning`, `finishedPack`, `splitPath`).

## 4. Non-blocking findings and nits (open)

**Still open from r1** (the fix round addressed only B1 to B3):
- **N1.** `FidelityPanel`'s exact-row card always says "No synthetic row equals a source row…" whatever `exact_row_matches` is.
  - This is low risk: the m5 backend raises `TabularGateError("leakage")` when the count is above 0 (`dps-wt-m5/dps/layers/connectors/tabular.py:614,964`), so a sealed pack should never carry a count above 0.
  - Still, the sentence should be worded from the number and turn rose when the count is above 0.
- **N2.** The key custody text is duplicated in evidence section 2.
- **N3.** `void actions.saveDraft(...)` produces an unhandled rejection when a draft save is refused.
- **N4.** A quick policy switch can show a stale detail or diff, and `readOnly` is never reset when the key changes.
- **N5.** The client's `KEY_NAME` pattern requires 3 or more characters; the server allows 2.
- **N6.** `callerKey` matches on the 8-character prefix, which now also drives `rotateWarning`. A collision only produces a wrong warning, never a lost key, because the card is shown regardless.
- **N7.** The `host:<host>` tag convention is an open item for WP-I.
- **N8.** Policies switches to read-only only after the first 403.

**New in r2:**
- **R2-1.** Overview's "Raw PII sent to LLM" subtitle always says "placeholders only", even when the count is above 0 (`Overview.tsx:118,175`). The number itself is shown truthfully and the tile loses its `zero` style. The subtitle should change, and the tile should turn rose, when the count is above 0.
- **R2-2.** In the forbidden view after a self-rotation, the topbar still shows the stale "platform-admin · admin" caller pill and the posture pill from before the refusal. This is cosmetic.
- **R2-3.** The fixture seed's draft says "3 changes", but its own leaf-level diff (v3 → draft v4) has about 12 changes. The seed's `draft_changes` was not recomputed after `diffBodies` became leaf-level. This is fixture-only: the real value comes from the backend.
- **R2-4.** A folded removed recognizer lists its `patterns` in code-point path order (`emp2` before `emp`), not in source order. This is cosmetic: pattern order has no meaning for a recognizer.
- **R2-5.** `declaredSince` uses `d.path in out` on a plain object, so a declared path named `constructor` or `toString` would never get a "since". It is an edge case; use a `Map` or `Object.hasOwn`.
- **R2-6.** Rotating your own key with a grace period, or disabling your own key, also ends in the forbidden view with nothing lost. The rotate dialog warns about this; disabling your own key has no warning. This is a nit.

**Integration items** (for WP-I, not this lane):
- `summary.verification_passed` for scan packs.
- Main's "review required" state and `error_code` suffix on the Reports chip.
- Main's N3 change to `EvidencePack.tsx` must win when merging. 6ed65be is not on this branch, and the branch's M2 `EvidencePack.tsx` still uses the pre-N3 `verificationText(false, hits)`.

## 5. Privacy and security checks performed in r2

- **One-time keys:** shown only from React state. After "stored", the walk's regex finds no key on the page. No new key is in localStorage, sessionStorage or `location.href`. No key appears in console output: the walk captured 0 errors, and the source has no `console.*`. The walk prints booleans only.
- **Diff rendering:** shows only policy bodies, which are configuration (method names, patterns, entity types). No customer value passes through it.
- **Filters:** the Reports filters are still whitelisted in `parseFilters` (`q` is capped at 120 characters). The `outcome=blocked` filter never reaches `/reports/evidence`: the page short-circuits it to an empty list. The audit `outcome` set (`ok|failed|blocked`) matches Task 11.
