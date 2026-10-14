# DPS — Remaining Work Plan to v1.0.0 (holistic, builder-ready)

> **For agentic workers:** this plan is the *programme* level above the milestone plans. Each work package
> (WP) below names its worktree, its inputs, the milestone-plan tasks it executes, its exact acceptance
> criteria and its dependencies. Execute milestone-plan tasks exactly as written there (TDD, gates, commit
> messages); this document adds what those plans cannot know: the state as of 2026-10-13, the findings still
> open, the integration, the release, and the rules for running five streams in parallel on one Mac.

**Date:** 2026-10-13 · **Baseline:** main `9d19f7b` (+3 uncommitted console edits), tags `v0.1.0-m0`,
`v0.2.0-m1`, `v0.3.0-m2` · **Target:** tag `v1.0.0` on main with every Definition-of-Done gate green.

Binding inputs: spec `docs/specs/2026-10-11-data-privacy-services-design.md`; contract
`docs/superpowers/plans/2026-10-11-m1-m5-contract.md` (incl. §0 amendment 2026-10-12, lane-safe compose);
plans `…-m3-oracle-and-mongodb.md`, `…-m4-ui-scanner.md`, `…-m5-hardening-and-showcase.md`; deviation logs
`docs/superpowers/build/*-DEVIATIONS*.md` (code as built wins over older plan text).

---

## 1. State inventory (verified from git on 2026-10-13)

### 1.1 Main (`/Users/neelu/document_intelligence/data-privacy-services`, branch `main`)

| Item | State |
|---|---|
| M0 Privacy Engine, L1 API, Playground | done, tagged `v0.1.0-m0` |
| M1 documents, true redaction, schema synthesis, NDJSON, Idempotency-Key, Tesseract, T3 label (L1) | done, tagged `v0.2.0-m1` |
| M2 metadata DB, queue, PG connector, 4 write modes, evidence, wizard/jobs/evidence console | done, tagged `v0.3.0-m2` (integrated verify r2 PASS: 1665 unit, M1 139 + M2 170 integration, smoke 34/34, per-column source≠target check 0 survivors) |
| Hardening after v0.3.0-m2 | **committed, not yet independently verified:** `e25209f` SEC-2 tenant-scoped secret refs · `34f4aa3` SEC-3 admin for any production-connection change / endpoint change needs credentials · `6ed65be` N3 failed/canceled run never reads as passed · `9fa6476` wizard step 5 asks the server · `b471266` length-safe method suggestion + pre-run refusal · `9d19f7b` fallback label prefix once; stray tag `v0.1.0-m2` deleted |
| Uncommitted on main | `frontend/src/lib/jobs.ts` (+`length_overflow` error text), `jobs.test.ts` (+test), `pages/EvidencePack.tsx` (inline error wording switched from `errorText(code)` to the raw code — **regression, revert that line**) |
| Remote | `origin` = github.com/NilenduGanguli/data-privacy-services (private); `origin/main` = `48d9425` (M0). Main is 119 commits ahead. **Pushing needs the owner's go-ahead.** |
| Possible stray test artefacts on main's stack (from two lane runs without `.env.lane`) | DB `dps_test_*` on `data-privacy-services-dps-db-1`, a `t_<hex>` schema in `bank`, MinIO key `tests/big.bin`. Removed by the final `docker compose down -v` before release verification. |

### 1.2 Lane worktrees (created from main `88eae2e`; must be rebased/merged onto current main at integration)

| Lane | Path / branch | Done (commits) | In progress (uncommitted) | Remaining milestone tasks |
|---|---|---|---|---|
| **M3** Oracle + MongoDB | `dps-wt-m3` / `m3` | T1–T4 (G1 reviewed PASS after fix `2d71084`), T5 `6366422`, T6 `2b8eb25`, T7 `e6272d4` | **T8** wiring: `registry.py`, `discovery.py`, new `docpaths.py` + 4 test files | T8 finish; review G2 (T5–T8); T9–T14; T15, T16, T17, T19; T18, T18b (console); T20, T21; lane verification |
| **M4** UI scanner | `dps-wt-m4` / `m4` | T1–T5 (+fixes `032b076`, `9fcc13c` for review r1/r2), T6 `06c6918`, T7 `9857cc3`, T8 `80b8790` (+`986ff89`) | **G2 fix r1** for 4 blockers: scope proxy `proxy.py` (new), `guard.py`, `browser.py`, `dom.py`, `models.py`, demo `forms.js`, tests (767 lines) | finish G2 fix; re-review G1 (fix `9fcc13c` unreviewed) + G2; T9–T13; T14–T18; T19, T20; T21–T23 (console); T24, T25; lane verification |
| **M5** hardening + showcase | `dps-wt-m5` / `m5` | T1–T5 (G1 PASS), T6 `cae6a88`, T6B `7453d79`, T7 `b7762e6`, T7B `080087a`, T8 `1a73de2` | — | review G2 (T6–T8) incl. **SEC-4** and **D-A** (§3); T9–T14; T15, T16, T16B; T18–T23 (console); lane verification. Deferred to §2.6: T0 (adapted), T8B, T17, T24, T25, T26, T26B, T27, T28 |

### 1.3 Open findings carried into this plan

| Id | Sev | Where | Finding | Owner WP |
|---|---|---|---|---|
| M4-G2-B1 | blocker | m4 `guard.py` | unload-time POSTs (`sendBeacon`, keepalive fetch on `pagehide`/`visibilitychange`) leave when a filled page is closed | WP-M4 |
| M4-G2-B2 | blocker | m4 `browser.py` | SharedWorker POST/PUT and WebSocket bypass the route guard | WP-M4 |
| M4-G2-B3 | blocker | m4 `guard.py` | redirect hops are not routed → SSRF to out-of-scope hosts | WP-M4 |
| M4-G2-B4 | blocker | m4 `dom.py` | a same-origin child frame that errors is skipped silently → a "masked" capture could show that frame unmasked | WP-M4 |
| M4-G1-r3 | verify | m4 `urls.py` | fix `9fcc13c` (digit-group redaction, deny-rail lenient readings) never re-reviewed | WP-M4 |
| SEC-4 | HIGH | m5 `apply.py` `_detokenize_prepass` | reveal sends any caller-named external token to the bank detokenizer (confused deputy) | WP-M5 |
| D-A | HIGH (privacy) | m5 synthesize jobs | source key (`customer_id`) and unmodelled columns are copied into "synthetic" tables → linkable to real customers | WP-M5 |
| M3-N | minor | m3 G1 review | non-blocking N1, N2, N4–N6 not done | WP-M3 (G2 review decides) |
| M1-N | minor | main console | Policies page can show a stale preset response after a quick preset switch | WP-F4 polish |
| A-N1/N2 | minor | main tools | stale demo group file; `console_check` overwrites tracked screenshots | WP-F4 polish |
| SEC-5 | defence in depth | m4 `dps/layers/scanner/__init__.py` | the scanner resolves storage-state secrets with its own copy of tenant scoping (`f"{tenant_id}/{ref}"` into the pre-SEC-2 resolver, containment checked against the whole secret dir); not exploitable today (`..`/absolute refs refused, tenant id regex-pinned) but duplicated logic | WP-I (m4 merge): replace with main's `resolve_secret_ref(tenant_id, ref, settings)` (containment inside the tenant folder), validate `storage_state_ref` at ScanSpec accept time with the same rule, and add a cross-tenant scan test |
| M5-G2-N | open | m5 | L2 `reveal_rows` cannot detokenize `external_tokenize` columns; tokenizer-mock container never run | WP-M5 / WP-F2 |

---

## 2. Work packages

Every WP ends with a structured verdict from an **independent** reviewer/verifier who runs the gates
itself. Implementation → review → fix → re-review (max 2 fix rounds per group; residuals go to the lane
verification, then to integration — never silently dropped).

### 2.1 WP-H — finish main hardening (main checkout; main compose project, default ports)

H1. Commit the console edits: keep `length_overflow` text + test; **revert** the `EvidencePack.tsx` line to
use `errorText(firstError)` (a raw code is not plain language). Gate: `npm test`, `npm run build`.
Commit `fix(console): explain length_overflow in words`.

H2. Independent verification of everything since `v0.3.0-m2`, through the running API
(`docker compose --profile docs --profile sources up --build -d`):
- SEC-2: tenant B cannot create / update / use a connection whose `secret_ref` names tenant A's ref (value-free
  refusal); `../x`, `/abs`, `a/../../b` refused; a `_shared/<ref>` works only when its `tenants` file lists the
  tenant.
- SEC-3: a jobs-scope key gets 403 on PATCH (host, tags, credentials) and DELETE of a production connection;
  host change on a non-production connection without credentials → 403
  `connection_endpoint_change_requires_credentials`, with credentials → 200 and the old sealed secret gone;
  audit rows carry field names only.
- N3, wizard step-5 server check (private Playwright: bad append offset refused before submit),
  length-safe suggestion (`bank_restore.customers` in place finishes, no `length_overflow`), fallback label.
- All gates (ruff, mypy, pytest, M1+M2 integration, frontend test/build, smoke, `demo_m2.py`).
Accept: verdict PASS; fixes test-first if not.

### 2.2 WP-M3 — Oracle + MongoDB (worktree `dps-wt-m3`, ports from its `.env`)

| Group | Tasks (M3 plan) | Acceptance beyond the task's own tests |
|---|---|---|
| G2-finish | **T8** (resume from the uncommitted files; they are a starting point, not a given) | Oracle flows through registry, discovery and finalize seams; wildcard doc paths |
| G2-review | T5–T8 | the three pitfalls (CLOB UTF-16 offsets, CHAR binds, TIMESTAMP(7–9) paging) proven on the lane's Oracle Free; reads never `FOR UPDATE`; ledger fencing survives kill |
| G3 | T9–T14 | `$where`/`$function`/`$accumulator`/JS operators rejected; nested arrays transformed; untouched values byte-identical; M2 conformance suite green on Oracle and Mongo |
| G4 | T15, T16, T17, T19 | one customer space across PG/Oracle/Mongo; consistency check reports overlap > 0 per pair with counts only; resume-after-kill on Oracle and Mongo for every write mode |
| G5 | T18, T18b | wizard creates Oracle and Mongo connections, owner listing, JSON filter step; job-group panel; browser check vs mockups 03/06/07 |
| G6 | T20, T21 | log-scan green; smoke + bench; HANDOFF/README/API reference updated |
| Verify | M3 DoD | plus **per-column source≠target check** on every sensitive column of every Oracle/Mongo target (0 survivors), D3 confirmation enforced, no values in logs |

### 2.3 WP-M4 — UI scanner (worktree `dps-wt-m4`; console in `dps-wt-m4ui`)

**G2-fix design (the in-progress approach, to be completed):** every connection of a scan browser context
goes through the in-process **scope proxy** (`proxy.py`, Chromium `--proxy-server` with bypass
`<-loopback>`): CONNECT tunnels only to allowed hosts (every hop, so redirects are covered), plain HTTP only
GET/HEAD without body/override/upgrade/fill value; plus, on filled pages, an init script that removes
`navigator.sendBeacon`, `SharedWorker`, `navigator.serviceWorker`, `WebSocket` and disables `keepalive`;
pages are closed with `run_before_unload=False` **after** routing is switched to "abort everything" so no
unload traffic can leave; QUIC and WebRTC disabled by launch flags. A child frame that fails extraction is
recorded (`frames_failed`) and the page's masked capture **fails closed** (no "masked" screenshot is
emitted for a page with an unextracted frame).
Proof tests (counting server): beacon/keepalive/SharedWorker/WebSocket/redirect-to-foreign-host → 0
received by the counting server and counted as blocked by the guard.

| Group | Tasks (M4 plan) | Acceptance beyond the task's own tests |
|---|---|---|
| G2-fix | the 4 blockers above | proof tests green; `M4-DEVIATIONS.md` updated |
| G1+G2 review | T1–T8 (incl. `9fcc13c`) | reviewer re-runs the URL redaction/deny-rail probes and the network probes |
| G3 | T9–T13 | classification via the shared M0/M2 detectors; risk flags; persona values; captures; fill records page validation; **masked captures contain no source value (OCR the PNG)** |
| G4 | T14–T18 | crawler budgets; the "nothing is ever submitted" proof (T15) against the demo site's `/__received` counter; scan jobs/evidence; browser-worker process; scans API |
| G5 | T19, T20 | log scan; browser-worker image (`mcr.microsoft.com/playwright:v1.55.0-noble` base) and compose services |
| UI (parallel, `dps-wt-m4ui`) | T21–T23 | built against the plan's API shapes with typed fixtures + Vitest; merged into `m4` after G5 |
| G7 | T24, T25 | smoke extension; docs as built |
| Verify | M4 DoD | live scan of the demo site; parallel view both modes; captures unreadable where masked; **POSTs received == 0** |

### 2.4 WP-M5 — hardening + showcase (worktree `dps-wt-m5`; console in `dps-wt-m5ui`)

| Group | Tasks (M5 plan) | Acceptance beyond the task's own tests |
|---|---|---|
| G2-review (+fix) | T6, T6B, T7, T7B, T8 | **SEC-4** (§3 D-B) and **D-A** (§3) implemented and tested; copula/linked outputs compared with sources (0 survivors) |
| G3 | T9–T14 | DB keys: rotation, expiry, no privilege escalation; tenants/quotas; admin API; roll-ups; retention extends M2's one scheduler; evidence PDF |
| G4 | T15, T16, T16B | policy store with immutable versions; strict verification is a **boot** rule; per-tenant LLM exposure counter + egress verifier + alert |
| UI (parallel, `dps-wt-m5ui`) | T18–T23 | Overview complete, Reports & audit, Evidence pack synthetic section + PDF, Admin (key shown once), Policies (versions) — typed fixtures + Vitest; merged after G4 |
| Verify | M5 in-scope DoD | live browser walk of the five pages; one L2 synthesize job with exact-row matches 0 and no sensitive source value in the target |

### 2.5 WP-I — integration onto main (serialised; one merge at a time)

Order: WP-H first, then lanes in completion order (expected m3 → m4 → m5). For each lane:
1. `git merge --no-ff <lane>` on main; resolve conflicts keeping both sides. **Hotspots:** `pyproject.toml`
   (extras: `connectors`, `scanner`, `tabular`, `synth-ml`, `deck`), `dps/config.py`, `dps/posture.py`,
   `dps/app.py` (routers, lifespan), `docker-compose.yml` (profiles `oracle`, `scanner`, `tokenizer`,
   `full`; lane-safe variables), `Dockerfile`, `tests/conftest.py`, `frontend/src/{App.tsx,components/Layout.tsx,lib/api.ts,lib/types.ts}`,
   contract, HANDOFF, README, `.env.example`, migration numbers (M3 020–029, M4 030–039, M5 040–059; no collisions).
2. Post-merge adaptations the lanes could not do: M3/M4/M5 code paths adopt **SEC-2/SEC-3** (tenant-scoped
   refs in Oracle/Mongo demo secrets, endpoint-param list covers oracle/mongo/s3), the main discovery fix
   (mixed-PII columns, review-required outcome) applies to Oracle/Mongo snapshots and M4 classification.
3. Gates: fresh venv, ruff/mypy/pytest, every milestone integration marker on one stack, frontend
   test/build, smoke. Tag the lane's milestone after its verification on main: `v0.4.0-m3`, `v0.5.0-m4`.

#### 2.5.1 M4 pre-merge gate (added 2026-10-14 16:30; **superseded 16:45** — both workflows were stopped and replaced by workflow `dps-finish-fast`, which finishes the M4 pre-merge in `dps-wt-m4` first and only then merges `m4` into main; the ordering is enforced by that orchestrator, so the wait below no longer applies)

Since 2026-10-14 16:25 a separate workflow (`wf_4dc1989c-3ce`, "dps-premerge-m4") is merging **main into `m4`** inside
`/Users/neelu/document_intelligence/dps-wt-m4` and adapting the scanner (SEC-5, SEC-2, M5 keys/posture/policy refs,
discovery semantics), with its own review loop. **Before running `git merge --no-ff m4` on main, wait until that work
is finished**, i.e. until ANY of these is true:
- the file `/Users/neelu/.cache/dps-build/PRE-m4.done` exists (written when the pre-merge workflow ends), or
- `/Users/neelu/.cache/dps-build/reports/PRE-m4-review-r*.md` contains `Verdict: PASS` **and** `git -C ../dps-wt-m4 status --short`
  is empty **and** `../dps-wt-m4/.git` shows no merge in progress.
Poll with a bounded loop (e.g. `for i in $(seq 1 10); do <check> && break; sleep 55; done`, repeated in successive
calls). If neither condition holds after 6 hours, proceed as originally planned and say so in the report. After the
gate opens, the merge of `m4` into main is expected to be small (only main commits made after the pre-merge);
still run every adaptation check and gate of §2.5.

### 2.6 WP-F — finish (parallel worktrees branched from integrated main, then merged)

| Stream | Worktree | Content | Acceptance |
|---|---|---|---|
| **F1 security** | `dps-wt-fsec` | M5 **T17** (S1–S13), **T8B** (T3 labelling in L2 discovery + L3 classify), T0 contract-surface test, SEC-4 confirmation, plus an adversarial multi-lens sweep (auth/tenancy, injection, SSRF, secrets/logs, crypto/key custody, D4) with independent refutation of each finding before fixing | `docs/security/2026-10-11-m5-security-review.md` lists every finding with fix + test; `tests/security/*` green |
| **F2 deploy** | `dps-wt-fdeploy` | M5 **T24** (images, compose `full`, tokenizer-mock, smoke M5 block, `flow_report.py`, `demo_client.py`) and **T25** (OpenShift kustomize base/components/overlays, Wega layout, release placeholders) | `kustomize build` of dev and prod overlays + kubeconform clean; images build `--platform linux/amd64`; arbitrary-UID run; smoke all checks |
| **F3 demo** | `dps-wt-fdemo` | M5 **T26** (`demo_seed.py`, `e2e_demo.py --console`, `DEMO-SCRIPT.md`, console page test) and **T26B** (deck from measured outputs; Keynote-safe shapes; QA by Keynote PDF export) | e2e demo all steps PASS under 10:00; deck test green; Keynote export pages reviewed |
| **F4 polish** | on main after F1–F3 merge | M1-N, A-N1/N2, any non-blocking items the verifiers logged | listed items closed or explicitly deferred in CHANGELOG "Known limitations" |
| **F5 docs** | main | M5 **T27** (HANDOFF final shape with generated settings table, README, `api-m5.md`, CHANGELOG) | `tests/test_handoff.py` green |
| **F6 release** | main | M5 **T28** + full DoD from a clean stack (`down -v`), independent final verification (max 3 rounds), tags `v1.0.0` | every DoD gate green; worktrees removed; no push without owner approval |

F2 depends on the merged compose of WP-I; F3 depends on F2's `demo_client.py` (F3 starts with the merged
main and rebases on F2 when F2 lands, or implements against T24's `DpsClient` signature and reconciles at merge).

---

## 3. Engineering decisions taken in this plan (owner may override)

**D-A — synthetic tables never carry linkable real identifiers.** Synthesize jobs (copula and surrogate)
must not copy a sensitive key (`CUSTOMER_ID`, `ACCOUNT_NUMBER`, … per taxonomy) or any column discovery
classified as personal. Keys are replaced by a keyed deterministic pseudonym under the job (group) key —
consistent across the tables of one job group so joins and foreign keys still hold. `in_place` synthesize
on a table whose primary key is sensitive is refused before the run
(`synthesize_in_place_sensitive_key`; use `table`, `files` or `append`). Non-modelled columns are kept only
when discovery classified them non-personal **and** the post-write sample finds no detector hit (otherwise
the existing review-required outcome). Rationale: spec §1 — synthetic output has "no path back to the
original".

**D-B — external tokens are bound to the caller's key (SEC-4).** At obfuscate time the hint carries
`tag = base32(HMAC(subkey(key, "external_tokenize|<entity>|<profile>"), token))[:26]`; at reveal, DPS
recomputes the tag with the presented key, constant-time compares, and refuses unbound/mismatched tokens
**before** calling the tokenizer (value-free `external_token_not_bound`). Each detokenization batch is
audited (tenant, key id, entity, count).

**D-C — scanner network policy is enforced below the page** (scope proxy for every hop + init-script
removal of beacon/worker/socket APIs on filled pages + abort-all before close + capture fails closed on
unextracted frames). Rationale: Playwright routing alone cannot see redirect hops, worker traffic or unload
traffic; D4 must hold even when a page is hostile.

**D-D — nothing is pushed or published** without the owner's explicit go-ahead.

---

## 4. Parallel schedule and resources

```
t ─────────────────────────────────────────────────────────────────────────────────────────►
WP-H  [H1·H2 verify]──┐
WP-M3 [T8][rev G2][G3][G4][G5][G6][verify]────────────┐
WP-M4 [fix G2][rev G1+G2][G3][G4][G5]─(merge m4ui)─[G7][verify]──┐
 m4ui [T21-23 vs fixtures]──────┘                                  │
WP-M5 [rev G2+SEC-4+D-A][G3][G4]─(merge m5ui)─[verify]──────┐      │
 m5ui [T18-23 vs fixtures]──────┘                             │      │
WP-I  ······(after H)··· merge m3 ──── merge m5 ──── merge m4 (completion order, serialised)
WP-F                                              [F1 sec ∥ F2 deploy ∥ F3 demo]→[F4][F5][F6 release]
```

| Stream | Docker footprint | Host ports |
|---|---|---|
| main (WP-H, WP-I, F4–F6) | full stack incl. Oracle at F6 (~7 GiB) | 8400 / 5441 / 5442 / 9010-9011 / 5016 / 1522 / 27018 / 8410 |
| m3 | Oracle Free 2.7 GiB + Mongo + PG + MinIO | 8413 / 5453 / 5454 / 9040-41 / 1523 / 27019 / 5036 / 8423 |
| m4 | PG + MinIO + demo site + Chromium | 8414 / 5463 / 5464 / 9050-51 / 5046 / 8420 |
| m5 | PG ×2 + MinIO + tokenizer mock | 8415 / 5473 / 5474 / 9060-61 / 5056 / 8425 |
| m4ui, m5ui | none (Vite + Vitest, private Playwright vs fixtures) | vite 5184 / 5185 |
| F worktrees | own `.env` (ports 84x6..) when they need a stack | assigned at creation |

Budget: 15.9 GiB for Docker. Rule: start only the profiles a group needs and `docker compose down` when the
group's integration tests are done; Oracle runs only in m3 (and on main at F6).

## 5. Operational rules for builder agents (learned the hard way)

1. **Always run tests through the lane wrapper** `./lane.sh <cmd>` (sources `.venv` and `.env.lane`). Two
   earlier slips ran without `.env.lane` and hit main's stack.
2. **Resume, never redo:** before starting, read `git status`/`git log`; uncommitted work from a crashed
   agent is a starting point to finish and test, not to discard silently.
3. **Retry on infrastructure failure:** an agent that dies on an API/network error is re-run with a resume
   preface (orchestrator does this up to 3 times).
4. Reports go to `~/.cache/dps-build/reports/` (durable; `/private/tmp` was wiped once).
5. Commits: explicit pathspec, author NilenduGanguli, **no trailers**, conventional messages; never stash,
   reset --hard, clean, push or move tags (only the release step tags).
6. Every masking/synthesis claim is backed by a **source vs target comparison** (counts only), every D4
   claim by a **counting server**, every "no values in logs" claim by a log scan.

## 6. Verification matrix (what proves "done")

| Area | Proof |
|---|---|
| L1 + documents | smoke, NDJSON/idempotency checks, redacted PDF 0 extractable chars + OCR re-read 0 originals |
| L2 PG / Oracle / Mongo | conformance suite on all three; resume-after-kill every write mode; per-column source≠target 0 survivors; consistency check overlap > 0 |
| L3 scanner | demo-site `/__received` POST count 0; masked captures OCR-checked; parallel view both modes |
| Synthetic | fidelity ≥ 0.85, exact-row matches 0, NNDR measured, utility + MIA proxy reported; no real key in synthetic targets (D-A) |
| Security | `tests/security/*`, SEC-2/3/4 probes, route-auth walk, cross-tenant 404s, ReDoS guard, SSRF netguard |
| Privacy ops | log scans all milestones; `dps_llm_raw_pii_sent_total` 0 per tenant; audit rows value-free |
| Console | browser walk of every page vs mockups 01–12, 0 console errors, 390 px no horizontal scroll |
| Deploy | kustomize dev/prod build + kubeconform; amd64 images; arbitrary UID |
| Demo | `e2e_demo.py --console` all PASS < 10:00; deck test + Keynote export |
| Release | M5 DoD 1–7 from a clean stack; tags `v0.4.0-m3`, `v0.5.0-m4`, `v1.0.0` |

## 7. Risks

| Risk | Mitigation |
|---|---|
| API/network outage kills agents mid-task (34 died on 2026-10-12) | orchestrator retries with resume preface; idempotent prompts; WIP is never discarded |
| Memory pressure with three stacks + Oracle | per-group start/stop; Oracle only in m3 until F6 |
| Merge conflicts across three lanes and main hardening | serialised merges, hotspot list §2.5, gates after each merge |
| Lane code predates SEC-2/SEC-3 and the discovery fix | WP-I step 2 adapts lane code paths after merge, with tests |
| Keynote QA needs the GUI | `osascript` export; if Keynote is unavailable the structural deck test still gates and the gap is reported |
| Hidden leaks behind "verified" labels (stage-A lesson) | §5 rule 6 is a gate in every verifier prompt |
