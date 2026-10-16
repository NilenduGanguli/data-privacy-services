# S7 fix r1: blocking findings of S7-review-r1 (finish-stage docs and F4 polish), branch `fdocs`

**Verdict: DONE.** Both blocking findings are fixed test-first (B1 HANDOFF §5/§3.3, B2 the Policies quick switch).
Seven of the ten non-blocking items are fixed as well (N1–N8; N9 is about the implementer report, N10 is the owner's
`.env.example`). Every gate is green and the browser re-check passes.

- Worktree `/Users/neelu/document_intelligence/dps-wt-fdocs`, branch `fdocs`, from `3dc5d73` (the reviewed head) to
  **`f4970da`**. Five commits, each by `NilenduGanguli <nilendu.ganguli@gmail.com>`, no trailer, explicit pathspecs. The
  working tree is clean.

  | Commit | What |
  |---|---|
  | `7bb202c` | fix(fdocs): the Policies page never shows or edits the previous policy while the next one loads (B2) |
  | `a6ebfa7` | fix(fdocs): HANDOFF names every role that needs the API login and the API keys (B1) |
  | `08949db` | fix(fdocs): wizard step 4 refuses a fractional append key offset (N3) |
  | `616f63a` | fix(fdocs): HANDOFF and api-m5 carry the facts the S7 review found missing or wrong (N1, N2, N4–N8) |
  | `f4970da` | docs(fdocs): M5 deviation log records the S7 review r1 fixes and the browser re-check |

- Not pushed by me. I did not run `git push` or `autopush.sh`; the orchestrator's autopush loop picks the branch up.
  A diff scan of `3dc5d73..f4970da` for key or secret patterns found nothing.
- Logs, probes and screenshots are in `~/.cache/dps-build/reports/S7-fix-r1/`.

## B2: Policies quick switch (M1-N / console review N4, second half)

**Cause.** In `loadPolicyDetail`, the tenant-policy branch changed nothing until `GET /policies/{name}` answered. Two
things followed:

- `PoliciesView` rendered whatever `detail` the page held under the new route name, with `editable = true`.
- The container's `saveDraft` PUT the edited body to the route's name.

The page therefore showed gdpr's v3 or draft v4 under `pci-dss` and let the user edit it. One save wrote gdpr's
`treat` into pci-dss's draft. The preset branch had the same flaw for the preset body, in read-only form.

**Fix** (`frontend/src/lib/policyLoad.ts`, `frontend/src/pages/Policies.tsx`):

- **`onScreen(name, detail, presetBody)`.** The page shows only a tenant detail whose `name` equals the route name, or
  a preset body whose `name` equals it. A preset body's name is the preset YAML's `name` (`POST /policies/validate`
  returns `PolicyOut(**policy.model_dump())`). Both the container and the view apply it:
  - The container feeds only the route's own detail to the diff/version effects, the actions and the view.
  - The view guards itself again, which is the layer the SSR tests check.
- **Synchronous clear on a switch.** `loadPolicyDetail(…, keep)` clears `detail` and `presetBody` before it awaits
  anything, whenever the load is a switch. A reload of the same policy with the same key (`keep`) keeps the page until
  the answer: after publish, and when an adopted preset turns up in the list as a tenant policy.
  - The container decides `keep` from a `loadedFor` ref holding `{auth, name}`.
  - A change of API key or tenant also counts as a switch, so one tenant's policy is never left editable under another
    tenant's key.
- **Pending state.** A tenant policy whose answer has not arrived (or that failed) shows only its name and "Loading
  <name>…", or the error. The same holds for a name the lists do not know yet (first load) or at all. Nothing else is
  shown:
  - no preset chip, version, hash or facts, and no obfuscation/synthesis callout;
  - no "Not adopted… Adopt" line;
  - no matrix and no declared-field form;
  - "Add recognizer to draft" is hidden, and Publish and Discard are absent.

  A shipped preset still shows its list entry while its body loads, as before.
- **Writes name their policy.** `PolicyActions.saveDraft`, `discardDraft`, `publish` and `adopt` now take the policy
  name.
  - `editTarget(detail, editable)` returns `{policy: detail.name, base}`, or null when no editable detail is on screen.
    `saveEdit(save, target, edit)` saves only to `target.policy`.
  - Publish and Discard confirm with `detail.name`. Their dialogs open only while editable, and a change of route name
    closes them (browser back or forward).
  - The answer to a write, and its follow-up (publish's reload), applies only while `loadedFor` is still
    `{auth, policy}`. The late answer for a policy the user has left never replaces the policy now shown, so the page
    cannot get stuck on "Loading".
  - The draft diff and the version bodies are used only together with their detail.
- Unchanged: `downloadYaml` and `runPlan` read with the route name. Both are reads, and `PolicyVersions` renders only
  with the matching detail.

**Tests first.** All of them failed before the fix (`S7-fix-r1/red-frontend.log`: 9 failed, 19 passed).

- `src/lib/policyLoad.test.ts`:
  - tenant A to tenant B with B pending: A is cleared synchronously; `onScreen` and `editTarget` give nothing for A
    under B's name; B shows after its answer;
  - preset to tenant: the preset body is cleared at once and never shown under the tenant name;
  - a same-policy reload keeps the detail until the answer;
  - `onScreen` for preset bodies;
  - `editTarget`/`saveEdit` save to the detail's own name, and save nothing with no editable detail. This replaces the
    old `saveEdit` test, whose signature changed.
- `src/pages/Policies.test.tsx`, new describe "PoliciesView while the next policy loads":
  - tenant A to tenant B pending: the header shows "Loading pci-dss" and none of gdpr's draft, hash, published line,
    description or facts. There is no pill, no Publish or Discard, no `#matrix`, no `#versions`, no declare form and
    no add-recognizer, and **0 enabled method buttons**. A control render on gdpr's own page shows more than 5, so the
    count is not vacuous.
  - preset to tenant pending;
  - a load error shows the error, not "Loading";
  - an unknown name, or the first load before the lists: no "shipped" and no adopt. I checked that this test fails
    without the unknown-name clause and passes with it.
  - a shipped preset with another policy's draft diff still held marks no row `drafted`.

**Browser re-check.** Private Playwright 1.55.0 (`~/.cache/dps-build/pw-fdocs`) against the console in fixture mode
(Vite on 127.0.0.1:5196, stopped afterwards). Probes and outputs are in `S7-fix-r1/`.

- `probe_policy_switch.py` (the review's probe, re-run): adopt `pci-dss`, open `gdpr-pseudonymise`, delay pci-dss's
  GET by 4 s, click `pci-dss`.
  - Inside the window: crumb `pci-dss`, header "pci-dss / Loading pci-dss…". No matrix, 0 editable methods; Publish,
    Discard, adopt, the declare form and add-recognizer are all absent. Pills: only "FF1 on".
  - Afterwards the page shows pci-dss's own v1 (hash `eab8ad73…`, 20 editable methods), and `pci_has_draft` is false.
  - Writes sent: none. The only POSTs are the recognizer tester and the plan preview, both reads.
  - Screenshots: `policy-switch-window.png`, `policy-switch-after.png`.
- `probe_preset_to_tenant.py`, at 1440 and 390 px: from analytics-safe to gdpr-pseudonymise, the window shows "Loading
  gdpr-pseudonymise…" with no matrix. Then gdpr's v3 with draft v4 (21 editable). No horizontal scroll at 390 px.
  Screenshots: `preset-to-tenant-window-{desktop,mobile}.png`.
- `probe_write_then_switch.py`:
  - **Save in flight, then a switch.** A gdpr save (PUT delayed 3 s) is followed at once by a switch to pci-dss. The
    PUT went to `/policies/gdpr-pseudonymise/draft` with a body named `gdpr-pseudonymise`. After the late answer the
    page still shows pci-dss "v1 active", with no "Loading".
  - **Dialog across back and forward.** "Publish v4" was opened on gdpr; after `history.back()` no dialog is open, and
    after forward none is open either. gdpr's active version is still 3.
- 0 console errors in every probe.

## B1: HANDOFF §5 / §3.3 against the code

**Checked in code first.**
- `dps/worker.py` `main()` runs `assert_production_posture`, and `_amain()` opens `db.init_pool` (the API login)
  before `db.init_worker_pool`.
- `dps/rekey.py` opens both pools, and `dps/keys_cli.py` opens the API pool.
- `posture_problems` requires a non-empty `DPS_API_KEYS`. It is a raw string; the `_FILE` content is stripped, so `[]`
  passes, and `StaticKeyStore` parses it as no keys.
- On branch `m4`, the browser-worker runs the guard with role `browser-worker`, which exempts API keys, CORS, the OCR
  key and the tokenizer token. It requires the KEK, opens both pools, and saves scan artefacts through
  `ctx.artefacts` (S3).

**HANDOFF changes.**
- §5 rows:
  - `dps-pg-app`: app; worker and (at the M4 merge) browser-worker, which open this pool at start besides their own;
    the operator's `dps.keys_cli` and `dps.rekey` runs.
  - `dps-pg-worker`: names `dps.rekey`; the browser-worker is marked "at the M4 merge".
  - `dps-job-kek`: the browser-worker (at the M4 merge; it does not start without it) and `dps.rekey`.
  - `dps-api-keys`: **required at boot in staging/prod** by the guard of the app and of the worker, and may hold `[]`
    once every key is DB-backed. Read by app and worker; the Task 25 manifests also mount it in the browser-worker,
    whose guard (at the M4 merge) does not require it.
  - `dps-s3`: the browser-worker, at the M4 merge (scan artefacts).
  - `dps-pg-owner`, plus §7: also the daily `dps-migrate-maintain` CronJob of Task 25 as built on `fos`.
- §5 bootstrap paragraph: it names the API login.
- §3.3: the worker runs the same guard at start, before it opens the database, so it needs `DPS_API_KEYS_FILE`. The
  browser-worker runs the guard at the M4 merge, without the API rules. The `DPS_API_KEYS` bullet says the file may
  hold `[]`.

**Tests first** (`tests/test_handoff.py`). Both new tests failed before the HANDOFF change (`S7-fix-r1/red-handoff.log`).
- `test_secret_rows_name_every_role_that_logs_in_with_them`: the worker's `_amain` and `rekey.amain` open the app login,
  then the worker login (pools monkeypatched, stopped after opening). The §5 rows must name the worker and `dps.rekey`.
- `test_api_keys_secret_is_required_at_boot_by_the_app_and_the_worker`: `worker.main()` with a prod `Settings` without
  keys raises `PostureError` naming `DPS_API_KEYS`. The row must name app and worker, must not say "optional" and must
  say "required at boot". §3.3 must name `python -m dps.worker`.
- `test_handoff_names_every_workload_that_mounts_each_secret`: skips until `deploy/openshift/base` is merged. Once it
  runs, every Deployment, Job and CronJob that mounts a Secret must be named in that Secret's row. I ran the real test
  functions against branch `fos`'s base (`git archive fos deploy/openshift/base`):
  - with the fixed HANDOFF, both manifest tests pass;
  - with HANDOFF as of `3dc5d73`, the new test fails on `dps-pg-app <- dps-worker` and `dps-api-keys <- dps-worker`
    (exactly B1), and on the browser-worker rows (`pg-app`, `job-kek`, `api-keys`, `s3`) and the CronJob row.

## Non-blocking items

| Item | Done |
|---|---|
| N3 wizard offset fractions | `offsetProblem` accepts digits and digit-group separators only. `"0.5"`, `"1.5"`, `"1e3"` and `"0x10"` joined the bad list of `offset.test.ts`, which failed first. `parseCount` is unchanged for its other callers. |
| N4 "Since" for the three SEC-1 COIN settings | M1. Verified: absent at `v0.1.0-m0`, added in `d8f5543`, an ancestor of `v0.2.0-m1`. The table is regenerated, and `--check` failed before the regeneration. |
| N6 lost facts | Restored, each checked against the code: <br>• `DPS_COIN_CA_FILE` falls back to httpx's default store. With httpx 0.28.1 and `verify=True` that is `SSL_CERT_FILE`, then `SSL_CERT_DIR`, else certifi. <br>• The `no_sql_string_functions` advisory and its list (`postgres.py` `_SQL_STRING_*`). <br>• `target.ledger_owner` (PostgreSQL and Oracle) and the ledger privileges (`_create_ledger`). <br>• The KEK generation command (the validator requires 32 bytes). |
| N5 tool variables | `DPS_SMOKE_JOB_TIMEOUT` (180 s per M2 job run) and `DPS_TEST_REQUIRE_TESSERACT=1`, which together with `DPS_TEST_REQUIRE_SERVICES=1` makes a missing tesseract fail. |
| N1 §12 security gate | Two lines added: `pytest -q tests/test_no_values_in_logs_m4.py # M4 (at its merge)`, and DoD 9 (`tests/security tests/test_no_values_in_logs_m5.py` plus the security review doc, at the fsec merge). Checked that the files exist on `m4` and `fsec`. |
| N2 `host_forbidden` | Added to the api-m5.md error-code table, "at the security stream's merge": the connection endpoints and the job `error_code` at use, from `fsec`'s contract §5.1 and `dps/netguard.py`. |
| N7 Task 25 traps | §11 gains two traps. The first is kustomize ordering: resources, then the kustomization's own generators, then components, then patches; an overlay's merge cannot reach a ConfigMap that a component generates. The second is that `components/dev-mocks/server.py` is a byte-identical copy of `docker/tokenizer-mock/server.py`, kept equal by `fos`'s `tests/deploy/test_openshift.py`. |
| N8 `bench_connector.py` docstring | The usage block is whole again (`tests/test_bench_defaults.py` green). |
| N9 / N10 | Not repo items. N9 is the wording of the implementer's report; N10 is `.env.example`, which agents cannot write and which stays in Known limitations. |

## Gates (final, at `f4970da`)

| Gate | Result |
|---|---|
| `./lane.sh ruff format --check dps tests tools` · `ruff check` · `mypy dps` · `mypy tools/settings_table.py tests/test_handoff.py` | 385 files formatted · all checks passed · no issues in 134 files · no issues in 2 files (`lint.log`) |
| `./lane.sh python tools/settings_table.py --check` | settings table current (165 settings) |
| `./lane.sh pytest -q -rs` (lane `dps-db` up on 5523, nothing else running) | **2384 passed, 267 skipped**, 1 warning, 100 s (`pytest-full.log`). That is 2 more passes than the review's 2382: the two new HANDOFF tests. The skips are services that were down (oracle 89, source-pg 75, mongo 56, app 6, OCR mock 3, …) plus 2 by design: `deploy/openshift/base` is absent, which affects the old and the new manifest test. |
| `tests/test_handoff.py` | 8 passed, 2 skipped (the two manifest tests; both pass against `fos`'s base) |
| `cd frontend && npm test` | node `--test` 42/42 + Vitest **261/261** (20 files; 10 more than the review's 251) (`frontend-test.log`) |
| `cd frontend && npm run build` | `tsc --noEmit` and the vite build pass; only the existing warning about chunks over 500 kB remains (`frontend-build.log`) |

## For the next reviewer

- The view's pending rule: no detail and no preset body for this name, and the name is either a tenant policy in the
  list or absent from the preset list. A shipped, unadopted preset still shows its list entry and adopt button while
  its body loads; that is the existing behaviour, and the old "offers to adopt" test is unchanged.
- `keep` (a same-policy reload) needs the same `auth` object. `useAuth` memoises it on `(apiKey, tenant)`, so a key or
  tenant change clears the page as a switch does.
- The integrator note in `M5-DEVIATIONS.md` (item 3 under "For the integrator") covers the per-workload secret test and
  the new "(at the …)" lines in §11, §12 and api-m5.md.

## Housekeeping

- Lane compose project `dps-wt-fdocs`: I started only `dps-db` for the db tests, then ran `docker compose down`
  (no `-v`). No other project was touched.
- Vite in fixture mode on 127.0.0.1:5196: my own process, stopped.
- Scratch files (the fos base extract, simulation scripts) are in the session scratchpad. No repository file outside
  my commits changed.
