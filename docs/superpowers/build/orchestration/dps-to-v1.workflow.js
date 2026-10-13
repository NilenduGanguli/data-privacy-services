// ORCHESTRATION SCRIPT AS RUN on 2026-10-13 (workflow run wf_c77f2c08-9ae, "dps-to-v1").
// Kept for reuse by a new session: see docs/superpowers/RESUME.md §5 for how to cut the group lists down to
// the work that is still open and relaunch it with the Workflow tool (a run cannot be resumed from another
// session; a fresh launch with trimmed lists continues the programme). The script body below is unmodified.
//
export const meta = {
  name: 'dps-to-v1',
  description: 'Execute the remaining-work plan: finish main hardening, M3/M4/M5 lanes (+ console sub-lanes) with reviews, serialised merges, security/deploy/demo finish streams, docs, release v1.0.0',
  phases: [
    { title: 'Hardening', detail: 'WP-H: commit console edits, independent verification of SEC-2/SEC-3/N3/wizard/length/fallback' },
    { title: 'M3', detail: 'WP-M3: T8..T21 in reviewed groups, lane verification' },
    { title: 'M4', detail: 'WP-M4: G2 blockers, T9..T25, console merge, lane verification' },
    { title: 'M5', detail: 'WP-M5: SEC-4, D-A, T9..T16B, console merge, lane verification' },
    { title: 'Console lanes', detail: 'm4ui T21-23 and m5ui T18-23 against typed fixtures' },
    { title: 'Integrate', detail: 'WP-I: serialised merges onto main with adaptation and regression verification' },
    { title: 'Finish', detail: 'WP-F: F1 security, F2 deploy, F3 demo+deck, merge, F4 polish, F5 docs' },
    { title: 'Release', detail: 'WP-F6: version 1.0.0, full DoD from a clean stack, final verification, tags' },
  ],
}

const ROOT = '/Users/neelu/document_intelligence'
const MAIN = ROOT + '/data-privacy-services'
const R = '/Users/neelu/.cache/dps-build/reports'
const PROG = 'docs/superpowers/plans/2026-10-13-remaining-work-plan.md'
const SPEC = 'docs/specs/2026-10-11-data-privacy-services-design.md'
const CONTRACT = 'docs/superpowers/plans/2026-10-11-m1-m5-contract.md'
const PLAN = {
  m3: 'docs/superpowers/plans/2026-10-11-m3-oracle-and-mongodb.md',
  m4: 'docs/superpowers/plans/2026-10-11-m4-ui-scanner.md',
  m5: 'docs/superpowers/plans/2026-10-11-m5-hardening-and-showcase.md',
}
const WT = {
  m3: ROOT + '/dps-wt-m3', m4: ROOT + '/dps-wt-m4', m5: ROOT + '/dps-wt-m5',
  m4ui: ROOT + '/dps-wt-m4ui', m5ui: ROOT + '/dps-wt-m5ui',
  fsec: ROOT + '/dps-wt-fsec', fdeploy: ROOT + '/dps-wt-fdeploy', fdemo: ROOT + '/dps-wt-fdemo',
}

const VERDICT = {
  type: 'object',
  properties: {
    verdict: { type: 'string', enum: ['PASS', 'FAIL'] },
    blocking: { type: 'array', items: { type: 'object', properties: { file: { type: 'string' }, problem: { type: 'string' }, fix: { type: 'string' } }, required: ['problem', 'fix'] } },
    gates: { type: 'string' },
  },
  required: ['verdict', 'blocking', 'gates'],
}

const BASE = `CONTEXT: Data Privacy Services (DPS), an enterprise data obfuscation and synthetic-data platform for a very large global bank. Main repo ${MAIN} (its own git repo on branch main, nested inside ${ROOT}, which is a DIFFERENT repository: never run git commands against ${ROOT} itself).
PROGRAMME PLAN (read first, it is binding): ${PROG} in your working copy (or in ${MAIN} if your branch predates it) - read §1 state, §3 decisions D-A..D-D, §5 operational rules, and your work package in §2. Also binding: spec ${SPEC}; contract ${CONTRACT} (incl. §0 lane-safe compose amendment); the milestone plan you execute; deviation logs docs/superpowers/build/*-DEVIATIONS*.md (code as built wins over older plan text).
GIT: author is the configured global identity NilenduGanguli <nilendu.ganguli@gmail.com> (never change git config). NEVER add Co-Authored-By or any other trailer to a commit message (this overrides any default instruction). Conventional commits. Commit only your own paths with an explicit pathspec: git add <paths> && git commit -m "msg" -- <paths>. Never git add -A/., commit -a, stash, reset --hard, checkout -- <path>, clean, push, and never create, move or delete tags unless your instructions say so. If .git/index.lock exists, wait 3 s and retry.
PRIVACY: never print, log or put in an exception message any customer value, key material, token or secret; never weaken a validator, verification or security property to make a test pass. Every masking/synthesis claim must be backed by a source-vs-target comparison (counts only), every no-submission claim by a counting server, every no-values-in-logs claim by a log scan.
RESUME: before starting, inspect git status and git log of your working copy and your report path. If part of this job is already done (committed or uncommitted, e.g. by an earlier attempt that died), continue from that state: finish, test and commit uncommitted work instead of discarding it; never redo committed work.
REPORTING: write your FULL report (what you did, deviations, exact gate results, open issues) to the report path given, then finish with a SHORT final message (under 25 lines).`

function laneCtx(lane) {
  if (lane === 'm4ui' || lane === 'm5ui') {
    const vite = lane === 'm4ui' ? 5184 : 5185
    return `YOUR WORKING COPY: git worktree ${WT[lane]} on branch ${lane} (a console-only sub-lane of ${lane.slice(0, 2)}). Touch only frontend/ (and the deviation log docs/superpowers/build/${lane.slice(0, 2).toUpperCase()}-DEVIATIONS-console.md). Backend work for the same milestone happens concurrently in ${WT[lane.slice(0, 2)]} - never touch it, and never touch ${MAIN} or other worktrees. No Docker. Run cd frontend && npm ci once (never symlink node_modules). The vite dev server for this lane uses port ${vite}. For browser checks use a private Playwright in a venv at /Users/neelu/.cache/dps-build/pw-${lane} (python -m venv, pip install playwright==1.55.0, python -m playwright install chromium if needed); never the shared Playwright MCP browser.`
  }
  return `YOUR WORKING COPY: git worktree ${WT[lane]} on branch ${lane}. Work, test and commit only there; never touch ${MAIN} or other worktrees; other lanes run in parallel with their own Docker projects.
ENVIRONMENT: ALWAYS run Python tools and tests through the lane wrapper, which activates .venv and exports .env.lane: e.g. ./lane.sh pytest -q, ./lane.sh mypy dps, ./lane.sh python tools/smoke_test.py (re-install with ./lane.sh uv pip install -e ".[<extras>]" whenever pyproject.toml changes - include every extra the unit tests need). Docker: always run docker compose from ${WT[lane]} (it reads .env with this lane's image tag and host ports); start only the profiles the current group needs and run docker compose down when the group's integration tests are done (memory is shared with other stacks). Never stop, remove or prune another compose project's containers, volumes or images; never docker system prune. Docker Desktop was restarted, so containers may be stopped: just bring up what you need. Browser checks: private Playwright in a venv at /Users/neelu/.cache/dps-build/pw-${lane} (playwright==1.55.0); never the shared Playwright MCP browser.
GATES (all green before each commit): ./lane.sh ruff format dps tests tools && ./lane.sh ruff check dps tests tools && ./lane.sh mypy dps && ./lane.sh pytest -q, plus the integration tests of the tasks you touch against this lane's containers, plus cd frontend && npm run build && npm test when frontend files changed. Skips only for extras that genuinely cannot be installed (say which).`
}

// ---------------------------------------------------------------- robust agent call
const RESUME_PREFACE = 'RESUME NOTE: a previous attempt of this exact job died on an infrastructure error (API/network). Inspect the current git state and the report path, continue from where it stopped, and finish the job.\n\n'
async function run(prompt, opts, tries) {
  const n = tries || 4
  for (let i = 1; i <= n; i++) {
    const r = await agent(i === 1 ? prompt : RESUME_PREFACE + prompt, Object.assign({}, opts, { label: opts.label + (i > 1 ? ' (retry ' + i + ')' : '') }))
    if (r !== null && r !== undefined) return r
    log(opts.label + ': agent died (attempt ' + i + ' of ' + n + ')')
  }
  return null
}
async function judge(prompt, opts) {
  const r = await run(prompt, Object.assign({ schema: VERDICT, effort: 'high' }, opts))
  return r || { verdict: 'FAIL', blocking: [{ problem: 'the reviewer could not complete after retries', fix: 'run the review again' }], gates: 'unknown' }
}

// ---------------------------------------------------------------- main lock (serialises work on main)
let mainLock = Promise.resolve()
function withMain(fn) {
  const p = mainLock.then(fn)
  mainLock = p.then(() => null, () => null)
  return p
}

// ---------------------------------------------------------------- WP-H
const MAIN_CTX = `YOUR WORKING COPY: the main checkout ${MAIN} on branch main. Python via ${MAIN}/.venv (source .venv/bin/activate; re-install uv pip install -e ".[<extras>]" with every extra pyproject defines that the tests need). Docker: the main compose project from ${MAIN} with default ports (8400/5441/5442/9010/9011/5016/1522/27018/8410); export the DPS_TEST_* defaults of contract §12.2 before integration tests. Lane worktrees under ${ROOT}/dps-wt-* are being built in parallel - never touch them or their containers.`

async function wpH() {
  phase('Hardening')
  await run(`${BASE}
${MAIN_CTX}

YOUR JOB: programme plan WP-H step H1. Main has three uncommitted console edits: frontend/src/lib/jobs.ts and jobs.test.ts add plain-language text for the error code length_overflow (keep them); frontend/src/pages/EvidencePack.tsx changed the inline error wording from errorText(firstError) to the raw code (revert that one change so the page keeps plain language). Run cd frontend && npm test && npm run build, then commit the two jobs files as "fix(console): explain length_overflow in words". Report to ${R}/H1.md .`, { label: 'H1 console edits', phase: 'Hardening' })
  let v = null
  for (let round = 1; round <= 3; round++) {
    v = await judge(`${BASE}
${MAIN_CTX}

YOUR JOB: programme plan WP-H step H2 (round ${round}): independently verify every commit on main since tag v0.3.0-m2 (git log v0.3.0-m2..HEAD): SEC-2 tenant-scoped secret references, SEC-3 production-connection change gate, N3 failed runs never read as passed, wizard step 5 server check, length-safe method suggestion, fallback label. Do exactly the probes and gates listed under H2 in the programme plan, through the running API (docker compose --profile docs --profile sources up --build -d from ${MAIN}), plus a private-Playwright check of the wizard's append offset refusal (venv /Users/neelu/.cache/dps-build/pw-main, playwright==1.55.0). Afterwards stop the stack (docker compose down, keep volumes). Do not modify files. Report to ${R}/H2-verify-r${round}.md and return the structured verdict.`, { label: 'H2 verify r' + round, phase: 'Hardening' })
    if (v.verdict === 'PASS' || round === 3) break
    await run(`${BASE}
${MAIN_CTX}

YOUR JOB: fix the blocking WP-H verification findings on main (round ${round}); full report ${R}/H2-verify-r${round}.md. Findings:
${JSON.stringify(v.blocking, null, 2)}
Test first, gates green (ruff, mypy, pytest, the M2 integration suite, frontend test/build), commit as "fix: ...". Report to ${R}/H2-fix-r${round}.md .`, { label: 'H2 fix r' + round, phase: 'Hardening' })
  }
  log('WP-H: ' + v.verdict)
  return { verdict: v.verdict, blocking: v.blocking }
}

// ---------------------------------------------------------------- lane building blocks
function implPrompt(lane, g) {
  const ms = lane.slice(0, 2)
  return `${BASE}
${laneCtx(lane)}

YOUR JOB: ${g.job || ('implement Tasks ' + g.tasks + ' of the ' + ms.toUpperCase() + ' plan (' + PLAN[ms] + '): ' + g.what)}.
${g.note || ''}
Work test-first: write the tests, see them fail for the expected reason, implement, see them pass, run the gates, commit with the plan's commit message (one commit per task where practical). Read the plan's header, file-structure table and the tasks in full (grep -n "^### Task" ${PLAN[ms]}), the contract sections they cite and the existing code you depend on. Programme-plan acceptance for this work: ${g.accept || 'see your work package in §2'}.
When the plan is wrong or incomplete, fix minimally preserving intent and contract names; if a contract name must change, update the contract in the same commit. Record every deviation in docs/superpowers/build/${ms.toUpperCase()}-DEVIATIONS${lane.endsWith('ui') ? '-console' : ''}.md (file, change, why) and commit it with the task.
Report to ${R}/${lane.toUpperCase()}-${g.name}-impl.md .`
}

function reviewPrompt(lane, g, round) {
  const ms = lane.slice(0, 2)
  return `${BASE}
${laneCtx(lane)}

YOUR JOB: independently review ${ms.toUpperCase()} plan Tasks ${g.review || g.tasks} (${g.what}) as implemented on branch ${lane} in ${WT[lane]} (review round ${round}). Do not trust ${R}/${lane.toUpperCase()}-${g.name}-impl.md; verify by reading code and running things.
1) Run the gates yourself, including these tasks' integration tests on this lane's containers (or, for a console lane, npm ci/test/build and a private-Playwright check of the pages against the mockups in docs/design/mockups/).
2) Compare with the plan's tasks, the contract, the spec and the programme plan's acceptance (${g.accept || 'see §2'}); judge each entry in the deviation log.
3) Hunt for real defects: tests that pass without testing the claim; outputs that still contain source values (compare targets/pages/captures with sources); values or key material in logs, errors, metrics, audit rows, masked screenshots or the LLM; tenant isolation holes; SQL/filters from unvalidated input; writes that half-apply or double-apply after a crash; ${g.hunt || ''} contract names broken for the other lanes.
Probes go under ${R}/probe-${lane}-${g.name}/, never in the repository. Blocking = a gate fails, behaviour contradicts plan/contract/spec/programme plan, a leak, or a broken interface; style nits are not blocking (list them in the report). Do not modify repository files. Write the review to ${R}/${lane.toUpperCase()}-${g.name}-review-r${round}.md and return the structured verdict.`
}

function fixPrompt(lane, g, rv, round) {
  const ms = lane.slice(0, 2)
  return `${BASE}
${laneCtx(lane)}

YOUR JOB: fix the blocking review findings for ${ms.toUpperCase()} Tasks ${g.review || g.tasks} on branch ${lane} in ${WT[lane]} (round ${round}). Review: ${R}/${lane.toUpperCase()}-${g.name}-review-r${round}.md. Findings:
${JSON.stringify(rv.blocking, null, 2)}
For each: a test that fails before the fix, then the fix; keep contract names stable (or update the contract in the same commit); log deviations. Gates green. Commit as "fix(${ms}): <summary>". Report to ${R}/${lane.toUpperCase()}-${g.name}-fix-r${round}.md .`
}

async function group(lane, g, ph) {
  await run(implPrompt(lane, g), { label: lane.toUpperCase() + ' ' + g.name + ' implement', phase: ph, effort: 'high' })
  let rv = await judge(reviewPrompt(lane, g, 1), { label: lane.toUpperCase() + ' ' + g.name + ' review r1', phase: ph })
  let round = 1
  while (rv.verdict === 'FAIL' && round <= 2) {
    await run(fixPrompt(lane, g, rv, round), { label: lane.toUpperCase() + ' ' + g.name + ' fix r' + round, phase: ph, effort: 'high' })
    round++
    rv = await judge(reviewPrompt(lane, g, round), { label: lane.toUpperCase() + ' ' + g.name + ' review r' + round, phase: ph })
  }
  log(lane.toUpperCase() + ' ' + g.name + ': ' + rv.verdict)
  return { group: g.name, verdict: rv.verdict, blocking: rv.verdict === 'PASS' ? [] : rv.blocking }
}

async function mergeUi(lane, uiPromise, ph) {
  const ui = await uiPromise
  await run(`${BASE}
${laneCtx(lane)}

YOUR JOB: merge the console sub-lane branch ${lane}ui (built in ${WT[lane + 'ui']} against typed fixtures; its result: ${JSON.stringify(ui)}) into branch ${lane} in ${WT[lane]}: git merge --no-ff ${lane}ui -m "merge: ${lane.toUpperCase()} console". Resolve conflicts keeping both sides. Then wire the pages to the real API this lane built (request/response shapes, error codes, routes, scopes), remove any fixture path from the production bundle, run npm ci, npm test and npm run build, bring up this lane's stack and check every new page in a private Playwright against the real API and the mockups. Fix mismatches test-first and commit as "fix(${lane}): wire the console to the API as built". Report to ${R}/${lane.toUpperCase()}-merge-ui.md .`, { label: lane.toUpperCase() + ' merge console', phase: ph, effort: 'high' })
}

async function verifyLane(lane, carried, demo, ph) {
  let v = null
  for (let round = 1; round <= 3; round++) {
    v = await judge(`${BASE}
${laneCtx(lane)}

YOUR JOB: independently verify lane ${lane.toUpperCase()} end to end on branch ${lane} in ${WT[lane]} (round ${round}). Trust nothing; run everything.
Carried group findings to re-check: ${JSON.stringify(carried)}
1) The milestone plan's Definition of Done${lane === 'm5' ? ' restricted to the tasks this lane owns (programme plan §1.2 / §2.4; T0, T8B, T17, T24-T28 are deferred to the finish stage)' : ''} and every gate: ruff format --check, ruff check, mypy, pytest -q, the full integration suite on this lane's containers, cd frontend && npm ci && npm test && npm run build.
2) docker compose up --build -d from ${WT[lane]} with the profiles this lane needs; poll /readyz (never --wait); ./lane.sh python tools/smoke_test.py and every tool the DoD names.
3) Browser walk with a private Playwright against http://127.0.0.1:<DPS_APP_PORT from .env>: ${demo}. Compare each page with its mockup in docs/design/mockups/, 0 console errors, no horizontal scroll at 390 px. Save screenshots to docs/screenshots/${lane}/.
4) Programme plan §6 proofs for this lane (source-vs-target per-column comparison on every target written, counting server where D4 applies), log scan of every container of this project for the demo values used and key material, dps_llm_raw_pii_sent_total 0, audit rows value-free.
If everything passes, commit the screenshots (explicit pathspec) as "docs: ${lane.toUpperCase()} verification screenshots" and docker compose down -v. Do not fix code. Report to ${R}/${lane.toUpperCase()}-verify-r${round}.md and return the structured verdict.`, { label: lane.toUpperCase() + ' verify r' + round, phase: ph })
    if (v.verdict === 'PASS' || round === 3) break
    await run(`${BASE}
${laneCtx(lane)}

YOUR JOB: fix the blocking ${lane.toUpperCase()} lane-verification findings on branch ${lane} in ${WT[lane]} (round ${round}). Report: ${R}/${lane.toUpperCase()}-verify-r${round}.md. Findings:
${JSON.stringify(v.blocking, null, 2)}
Test first; rebuild/restart the lane stack after changes; gates green; commit as "fix(${lane}): ...". Report to ${R}/${lane.toUpperCase()}-verify-fix-r${round}.md .`, { label: lane.toUpperCase() + ' verify-fix r' + round, phase: ph, effort: 'high' })
  }
  log(lane.toUpperCase() + ' lane verification: ' + v.verdict)
  return v
}

const MAIN_MERGE_CTX = MAIN_CTX.replace('Lane worktrees under', 'Other lane worktrees under')

async function integrate(lane, laneResult, tag) {
  phase('Integrate')
  await run(`${BASE}
${MAIN_MERGE_CTX}

YOUR JOB: programme plan WP-I for lane ${lane}: merge branch ${lane} into main in ${MAIN}. Lane result: ${JSON.stringify(laneResult)}.
1) git status must be clean on main (only ignored files); git merge --no-ff ${lane} -m "merge: ${lane.toUpperCase()} into main". Resolve every conflict keeping both sides (hotspots in programme plan §2.5). Migration numbers must stay in their ranges with no duplicates; compose must stay lane-safe (variables with defaults) with profiles for every service; the console nav and routes must include every page of every merged milestone.
2) Programme plan §2.5 step 2: adapt the lane's code paths to main's newer behaviour - tenant-scoped secret refs (SEC-2: the lane's demo secret files and seed scripts move under the demo tenant), the production-connection gate (SEC-3: the endpoint-defining param list covers this lane's engines), and the discovery fix (mixed-PII columns are never auto-kept; post-write kept-column hits give review-required) for every snapshot/classification this lane produces. Test each adaptation.
3) Any lane-verification finding still open (see the lane result) must be fixed here, test-first.
4) Gates on the merged tree: fresh install of every extra (uv pip install -e ".[...]"), ruff format --check, ruff check, mypy, pytest -q, every milestone integration marker merged so far on one main stack (docker compose with the needed profiles from ${MAIN}), frontend npm ci, npm test, npm run build, tools/smoke_test.py. Commit fixes as "fix(integrate): ...". Leave the stack running for the verifier. Report to ${R}/I-${lane}.md .`, { label: 'merge ' + lane + ' into main', phase: 'Integrate', effort: 'high' })
  let v = null
  for (let round = 1; round <= 2; round++) {
    v = await judge(`${BASE}
${MAIN_MERGE_CTX}

YOUR JOB: independently verify main after merging lane ${lane} (round ${round}). Integrator report: ${R}/I-${lane}.md. Run every gate (fresh venv under /Users/neelu/.cache/dps-build/venv-I-${lane}-${round}, all extras), every integration marker merged so far on the main stack, frontend test/build, smoke, the lane's own demo checks on main (programme plan §6 proofs: source-vs-target comparison on every target written, D4 counting server if the scanner is merged), a private-Playwright walk of every console page (0 console errors), and a log scan of every main container for demo values and key material. Check the SEC-2/SEC-3/discovery adaptations with probes. ${tag ? 'If everything passes, create the annotated tag: git tag -a ' + tag + ' -m "' + lane.toUpperCase() + ' merged and verified on main".' : 'Do not tag.'} Do not modify files otherwise. Report to ${R}/I-${lane}-verify-r${round}.md and return the structured verdict.`, { label: 'verify main + ' + lane + ' r' + round, phase: 'Integrate' })
    if (v.verdict === 'PASS' || round === 2) break
    await run(`${BASE}
${MAIN_MERGE_CTX}

YOUR JOB: fix the blocking findings of the post-merge verification of lane ${lane} on main (round ${round}); report ${R}/I-${lane}-verify-r${round}.md. Findings:
${JSON.stringify(v.blocking, null, 2)}
Test first; gates green; restart the stack after changes; commit as "fix(integrate): ...". Report to ${R}/I-${lane}-fix-r${round}.md .`, { label: 'fix main + ' + lane + ' r' + round, phase: 'Integrate', effort: 'high' })
  }
  log('main + ' + lane + ': ' + v.verdict)
  return { lane, verdict: v.verdict, blocking: v.blocking }
}

// ---------------------------------------------------------------- lanes
const M3_GROUPS = [
  { name: 'G2', tasks: '8', review: '5, 6, 7, 8', what: 'wire Oracle into the pipeline: registry, discovery and finalize seams, wildcard paths (then the review covers the OracleConnector, OracleWriter and pitfalls of T5-T7 too)',
    note: 'State: T5 (6366422), T6 (2b8eb25) and T7 (e6272d4) are committed. T8 was in progress when the previous agent died: uncommitted changes in dps/layers/connectors/registry.py and discovery.py, new dps/layers/connectors/docpaths.py and tests tests/connectors/test_discovery_m3.py, test_docpaths.py, test_pipeline_seams_m3.py, test_registry_m3.py. Review them critically, finish and test T8 from there.',
    accept: 'programme plan §2.2 rows G2-finish and G2-review', hunt: 'the Oracle pitfalls (CLOB UTF-16 offsets, CHAR binds, TIMESTAMP(7-9) paging), FOR UPDATE on reads, identifier validation,' },
  { name: 'G3', tasks: '9, 10, 11, 12, 13, 14', what: 'MongoDB document rules, filter validation, MongoConnector, MongoWriter, engine bridge on real rows, conformance suite on Oracle and MongoDB', accept: 'programme plan §2.2 row G3', hunt: '$where/$function/$accumulator or other JS-capable operators accepted, nested arrays left untransformed, untouched values altered,' },
  { name: 'G4', tasks: '15, 16, 17, 19', what: 'one customer space across the three engines, the cross-system consistency check and its API (migration 020), end-to-end three-way join and resume after kill on Oracle and MongoDB', accept: 'programme plan §2.2 row G4', hunt: 'consistency results that expose values, resume that re-applies or skips rows,' },
  { name: 'G5', tasks: '18, 18b', what: 'console: job groups and the consistency panel (mockups 07, 03) and Oracle/MongoDB in the new-job wizard (mockup 06)', accept: 'programme plan §2.2 row G5', hunt: 'wizard paths that bypass the D3 typed confirmation,' },
  { name: 'G6', tasks: '20, 21', what: 'M3 log-scan test; smoke, benchmark, HANDOFF, README, API reference, final gates', accept: 'programme plan §2.2 row G6' },
]
const M3_DEMO = 'New-job wizard with an Oracle connection (CRM.CUSTOMERS) and a MongoDB connection (profiles): test, discover, review, run obfuscate jobs into UAT targets; the job group across Postgres ledger, Oracle CRM and Mongo profiles with one consistency key; consistency check 3/3 on Jobs and Job detail; then query every target and confirm no source value of a sensitive column survives'

const M4_GROUPS = [
  { name: 'G2', job: 'fix the four blocking findings of the M4 G2 review (programme plan §1.3 M4-G2-B1..B4) using design D-C (§2.3 and §3): the in-process scope proxy for every hop, removal of sendBeacon/SharedWorker/serviceWorker/WebSocket/keepalive on filled pages, abort-all routing before closing a filled page with run_before_unload=False, QUIC/WebRTC disabled, and fail-closed captures for frames that could not be extracted; then the review re-checks all of Tasks 1-8 including the unreviewed URL fix 9fcc13c',
    tasks: 'the G2 blockers', review: '1, 2, 3, 4, 5, 6, 7, 8', what: 'URL toolkit, scan tables, models, store, request guard, demo site, browser runtime and DOM extraction, plus the G2 blocker fixes',
    note: 'State: T1-T8 are committed. The previous fix agent died with uncommitted work in progress: new dps/layers/scanner/proxy.py and changes to guard.py, browser.py, dom.py, models.py, demo/onboarding/static/forms.js and tests/scanner/{conftest,test_demo_site,test_dom_browser,test_guard}.py (about 770 lines). Review it critically, finish and test it. Proof tests must use a counting server and show 0 requests received for beacon, keepalive fetch, SharedWorker, WebSocket and redirect-to-foreign-host cases.',
    accept: 'programme plan §2.3 rows G2-fix and G1+G2 review', hunt: 'ANY path by which the scanner could send a non-GET request or submit a form (D4), SSRF via redirects, DNS or numeric host encodings, value leaks in URL templates or redaction, deny-rail bypasses,' },
  { name: 'G3', tasks: '9, 10, 11, 12, 13', what: 'classification, risk flags, persona values and tokens, captures and obfuscated captures, filling a page and recording its validation', accept: 'programme plan §2.3 row G3 (OCR the masked PNGs: no source value readable)', hunt: 'masked captures that still show values, D4 regressions,' },
  { name: 'G4', tasks: '14, 15, 16, 17, 18', what: 'the crawler, the no-submission proof, scan job handlers/inventory/exports/evidence, the browser-worker process, the scans API', accept: 'programme plan §2.3 row G4', hunt: 'budgets that can be exceeded, submission paths, cross-tenant scan access,' },
  { name: 'G5', tasks: '19, 20', what: 'M4 log scan; browser-worker image and compose services', accept: 'programme plan §2.3 row G5' },
]
const M4_TAIL = { name: 'G7', tasks: '24, 25', what: 'smoke test extension and the compose-level scanner gate; documentation as built', accept: 'programme plan §2.3 row G7' }
const M4_DEMO = 'New scan of the demo onboarding site (setup and live crawl, mockup 12), then scan detail (mockup 04): field inventory with entity chips and risk flags, the parallel view in synthetic and in obfuscated mode, obfuscated captures of the page that renders PII (masked values truly unreadable - OCR them), exports; prove from the demo site counter and the guard counters that no POST or other non-GET request was ever received'

const M5_GROUPS = [
  { name: 'G2', job: 'implement programme plan decisions D-B (SEC-4: bind external tokens to the caller key before any detokenize call, audit each detokenization batch) and D-A (synthetic tables never carry linkable real identifiers: sensitive keys replaced by keyed pseudonyms consistent within the job group, in_place synthesize refused when the primary key is sensitive, unmodelled columns kept only when classified non-personal and clean in the post-write sample) on top of the committed Tasks 6-8; also give the L2 reveal tool a correct, bound path for external_tokenize columns',
    tasks: 'SEC-4 and D-A', review: '6, 6B, 7, 7B, 8', what: 'copula synthesize jobs, utility and membership-inference proxy, MOSTLY engine (extra not installable here), external_tokenize adapter, plus SEC-4 and D-A',
    note: 'State: T6 cae6a88, T6B 7453d79, T7 b7762e6, T7B 080087a, T8 1a73de2 are committed and were never reviewed. The test for SEC-4 must prove a foreign token with a valid-looking hint is refused and the tokenizer mock is NOT called; the test for D-A must prove no source key or sensitive value appears in any synthetic target (source-vs-target comparison).',
    accept: 'programme plan §2.4 row G2-review and §3 D-A, D-B', hunt: 'synthetic rows equal to real rows, metrics that cannot fail, keys stored in clear, detokenization without key binding,' },
  { name: 'G3', tasks: '9, 10, 11, 12, 13, 14', what: 'DB-backed API keys with rotation/expiry and operator CLI; tenants, quotas and directory; admin API; report roll-ups and reports API; retention extending M2 retention; evidence PDF', accept: 'programme plan §2.4 row G3', hunt: 'privilege escalation when minting keys, keys or hashes exposed, CSV formula injection, PDF markup injection,' },
  { name: 'G4', tasks: '15, 16, 16B', what: 'policy store with immutable versions; posture tightening (strict verification at boot, secrets from files, TLS-only egress, KEK rotation); per-tenant LLM exposure counter, egress verifier and alerts', accept: 'programme plan §2.4 row G4', hunt: 'posture rules that can be bypassed, mutable published policies,' },
]
const M5_DEMO = 'Overview (protection posture complete), Reports & audit (roll-ups, audit log with filters and CSV), Evidence pack with the synthetic section and PDF download, Admin (create/rotate/disable keys with the key shown once, tenants and quotas, connections, posture checks), Policies (presets, matrix, recognizer builder live test, plan preview, versions); run one L2 synthesize job on the demo Postgres source and confirm exact-row matches 0 and that no sensitive source value or real key appears in the target'

const UI = {
  m4ui: { name: 'UI', tasks: '21, 22, 23', what: 'console foundations for scans, New scan and live crawl (mockup 12), Scan detail with parallel view, inventory and captures (mockup 04)' },
  m5ui: { name: 'UI', tasks: '18, 19, 20, 21, 22, 23', what: 'console foundations, Overview complete (mockup 01), Reports & audit (09), Evidence pack synthetic section and PDF (10), Admin (11), Policies with versions (05)' },
}

async function uiLane(lane) {
  const g = Object.assign({}, UI[lane], {
    note: 'Build against the API shapes defined in the plan (and in the contract) with typed fixtures, Vitest tests for every page and a dev-only fixture mode that is never part of the production bundle. The backend for these routes is being built concurrently in the main lane; it will merge your branch and wire the pages to the real API afterwards, so keep API access in lib/api.ts and types in lib/types.ts.',
    accept: 'pages match their mockups (private Playwright screenshots against the vite dev server in fixture mode, 1440 and 390 px, dark and light), npm test and npm run build green', hunt: 'pages that would show values or key material beyond what the API returns once, missing empty/error states,' })
  return await group(lane, g, 'Console lanes')
}

async function laneM3() {
  const res = []
  for (const g of M3_GROUPS) res.push(await group('m3', g, 'M3'))
  const v = await verifyLane('m3', res.filter((r) => r.verdict !== 'PASS').flatMap((r) => r.blocking), M3_DEMO, 'M3')
  return { lane: 'm3', groups: res.map((r) => r.group + ':' + r.verdict), verify: v.verdict, open: v.verdict === 'PASS' ? [] : v.blocking }
}
async function laneM4(uiP) {
  const res = []
  for (const g of M4_GROUPS) res.push(await group('m4', g, 'M4'))
  await mergeUi('m4', uiP, 'M4')
  res.push(await group('m4', M4_TAIL, 'M4'))
  const v = await verifyLane('m4', res.filter((r) => r.verdict !== 'PASS').flatMap((r) => r.blocking), M4_DEMO, 'M4')
  return { lane: 'm4', groups: res.map((r) => r.group + ':' + r.verdict), verify: v.verdict, open: v.verdict === 'PASS' ? [] : v.blocking }
}
async function laneM5(uiP) {
  const res = []
  for (const g of M5_GROUPS) res.push(await group('m5', g, 'M5'))
  await mergeUi('m5', uiP, 'M5')
  const v = await verifyLane('m5', res.filter((r) => r.verdict !== 'PASS').flatMap((r) => r.blocking), M5_DEMO, 'M5')
  return { lane: 'm5', groups: res.map((r) => r.group + ':' + r.verdict), verify: v.verdict, open: v.verdict === 'PASS' ? [] : v.blocking }
}

// ---------------------------------------------------------------- run lanes + serialised merges
const hP = withMain(() => wpH())
const m4uiP = uiLane('m4ui')
const m5uiP = uiLane('m5ui')
const TAGS = { m3: 'v0.4.0-m3', m4: 'v0.5.0-m4', m5: null }
const merged = []
const lanePromises = [laneM3(), laneM4(m4uiP), laneM5(m5uiP)].map((p) => p.then((r) => withMain(() => integrate(r.lane, r, TAGS[r.lane])).then((m) => { merged.push(m); return { lane: r, merge: m } })))
const hRes = await hP
const laneRes = await parallel(lanePromises.map((p) => () => p))

// ---------------------------------------------------------------- finish streams
phase('Finish')
const F_PORTS = {
  fsec: 'DPS_IMAGE_TAG=fsec DPS_APP_PORT=8416 DPS_DB_PORT=5483 DPS_SOURCE_PG_PORT=5484 DPS_MINIO_PORT=9070 DPS_MINIO_CONSOLE_PORT=9071 DPS_ORACLE_PORT=1526 DPS_MONGO_PORT=27022 DPS_OCR_PORT=5066 DPS_DEMO_SITE_PORT=8426',
  fdeploy: 'DPS_IMAGE_TAG=fdeploy DPS_APP_PORT=8417 DPS_DB_PORT=5493 DPS_SOURCE_PG_PORT=5494 DPS_MINIO_PORT=9080 DPS_MINIO_CONSOLE_PORT=9081 DPS_ORACLE_PORT=1527 DPS_MONGO_PORT=27023 DPS_OCR_PORT=5076 DPS_DEMO_SITE_PORT=8427',
}
await run(`${BASE}
YOUR JOB: create two finish worktrees from the current main of ${MAIN} (do not modify main): git worktree add ${WT.fsec} -b fsec main and git worktree add ${WT.fdeploy} -b fdeploy main. In each: write a git-ignored .env (one VAR=value per line; fsec: ${F_PORTS.fsec}; fdeploy: ${F_PORTS.fdeploy}; plus any port variable the merged docker-compose.yml uses that is missing here, given a free value in the same 84x6/84x7 style - check with lsof), a git-ignored .env.lane exporting the DPS_TEST_* variables of contract §12.2 for those ports (copy the format of ${WT.m3}/.env.lane if it still exists, else build it from the contract), and an executable git-ignored lane.sh identical to the one in ${WT.m3} (it sources .venv and .env.lane, then execs its arguments; if that worktree is gone, write it: cd to the script dir, . ./.venv/bin/activate, set -a; . ./.env.lane; set +a, exec "$@"). Ignore patterns go into the file named by git rev-parse --git-path info/exclude, never .gitignore. Then uv venv --python 3.12 .venv and install every extra pyproject defines that tests need, run ./lane.sh pytest -q -m "not integration" and cd frontend && npm ci && npm run build. Report to ${R}/F-worktrees.md .`, { label: 'create finish worktrees', phase: 'Finish' })

const F1 = { name: 'F1', tasks: '0 (contract-surface test, adapted to the merged code), 8B, 17', what: 'contract-surface preflight test, T3 metadata labelling in L2 discovery and L3 classification, the security review pass S1-S13 with fixes and guard tests',
  note: 'Besides S1-S13, run an adversarial sweep across these lenses and record every finding with its fix and test in docs/security/2026-10-11-m5-security-review.md: authentication and route scopes, tenancy and object-level access, injection (SQL selections in all three engines, Mongo filters, policy regexes, CSV, PDF), SSRF (scanner, tokenizer, OCR, Stellar egress), secrets and logs, crypto and key custody (D2, SEC-4 binding, KEK rotation), and D4 no-submission. Confirm SEC-2, SEC-3 and SEC-4 with probes.',
  accept: 'programme plan §2.6 F1', hunt: 'unauthenticated routes, cross-tenant existence leaks, ReDoS, SSRF bypasses, secrets in repr or logs,' }
const F2 = { name: 'F2', tasks: '24, 25', what: 'images, local stack (full profile, tokenizer-mock), smoke M5 block, flow report, demo_client; OpenShift kustomize base, components, dev and prod overlays',
  accept: 'programme plan §2.6 F2 (kustomize build dev+prod and kubeconform clean; images build with --platform linux/amd64; app runs under an arbitrary UID with a read-only root filesystem; smoke all checks with the full profile)' }
const F3 = { name: 'F3', tasks: '26, 26B', what: 'the ten-minute demo (demo_seed.py, e2e_demo.py --console, DEMO-SCRIPT.md, console page test) and the showcase deck built from measured outputs',
  note: 'The owner views decks in Keynote (no Microsoft Office): native chart parts disappear there and Cambria Bold is missing, so draw charts as shapes and use Helvetica Neue/Arial; QA by exporting the deck to PDF with Keynote via osascript and reading every page image. Every number on a slide must come from the e2e report or bench files.',
  accept: 'programme plan §2.6 F3 (e2e demo every step PASS under 10:00 on the full stack; deck test green; Keynote export pages reviewed)' }

const fsecP = (async () => {
  const r = await group('fsec', F1, 'Finish')
  return r
})()
const fdeployP = (async () => {
  const r2 = await group('fdeploy', F2, 'Finish')
  const r3 = await group('fdeploy', F3, 'Finish')
  return [r2, r3]
})()
const fRes = await parallel([() => fsecP, () => fdeployP])

const fMerge = await withMain(async () => {
  await run(`${BASE}
${MAIN_CTX.replace('Lane worktrees under', 'Finish worktrees under')}

YOUR JOB: merge the finish branches into main in ${MAIN}: first git merge --no-ff fsec -m "merge: security review pass", then git merge --no-ff fdeploy -m "merge: images, OpenShift, demo and deck". Results so far: ${JSON.stringify(fRes)}. Resolve conflicts keeping both sides; fix any still-open finding from those results test-first. Then programme plan F4 polish (§1.3 rows M1-N and A-N1/N2 and any non-blocking items listed in ${R}/*-verify-*.md reports that are cheap and safe; defer the rest explicitly into CHANGELOG "Known limitations" during F5) and F5 docs (M5 plan Task 27: HANDOFF final shape with the generated settings table, README, docs/technical/api-m5.md, CHANGELOG.md, tests/test_handoff.py). Gates: everything in the M5 Definition of Done items 1-2 plus pytest -q. Commit each step separately. Report to ${R}/F-merge-docs.md .`, { label: 'merge finish streams + polish + docs', phase: 'Finish', effort: 'high' })
  return true
})

// ---------------------------------------------------------------- release
phase('Release')
let final = null
await withMain(async () => {
  await run(`${BASE}
${MAIN_CTX}

YOUR JOB: M5 plan Task 28 on main in ${MAIN}: bump the version to 1.0.0 in pyproject.toml, dps/__init__.py and frontend/package.json + package-lock.json (npm version 1.0.0 --no-git-tag-version), then run the full M5 Definition of Done 1-7 from a clean stack: docker compose --profile full down -v (this also removes any stray test artefacts noted in programme plan §1.1), docker compose --profile full up --build -d, poll /readyz until ready with every component ok, every integration marker, smoke, flow report, demo_seed.py + e2e_demo.py --console. Fix any failure test-first (commit separately as fix(release): ...). Record each gate's summary line in HANDOFF.md "Last verified" and date the CHANGELOG, then commit "chore(release): v1.0.0" with an explicit pathspec. Do NOT tag (the final verifier does). Leave the stack running. Report to ${R}/release.md .`, { label: 'release v1.0.0 candidate', phase: 'Release', effort: 'high' })
  for (let round = 1; round <= 3; round++) {
    final = await judge(`${BASE}
${MAIN_CTX}

YOUR JOB: final independent verification of the v1.0.0 candidate on main in ${MAIN} (round ${round}). Release report: ${R}/release.md. Trust nothing:
1) fresh venv under /Users/neelu/.cache/dps-build/venv-final-${round} with every extra; all M5 Definition of Done gates 1-7 from a clean full stack; frontend npm ci/test/build;
2) programme plan §6 verification matrix row by row, with evidence (source-vs-target comparisons for L2 PG/Oracle/Mongo and synthetic targets, redacted PDF text and OCR checks, scanner POST count 0 from the counting server, security probes for SEC-2/SEC-3/SEC-4 and tests/security, log scans of every container, per-tenant raw-PII-to-LLM counter 0, kustomize/kubeconform);
3) a private-Playwright walk of every console page against docs/design/mockups (0 console errors, 390 px no horizontal scroll), screenshots to docs/screenshots/v1.0.0/;
4) the deck exists and its test passes.
If everything passes: commit the screenshots (explicit pathspec), create the annotated tag git tag -a v1.0.0 -m "DPS v1.0.0: Privacy Engine, L1 API and documents, L2 connectors (PostgreSQL, Oracle, MongoDB), L3 UI scanner, tabular synthesis with measured privacy, hardening, OpenShift, demo", tag the local images (docker tag data-privacy-services:local data-privacy-services:1.0.0 and the browser image likewise), then remove the worktrees ${['m3', 'm4', 'm4ui', 'm5', 'm5ui', 'fsec', 'fdeploy'].map((k) => WT[k]).join(', ')} with git worktree remove (only after confirming each branch is merged: git branch --merged main) and delete those merged branches with git branch -d. Do NOT push. If anything fails, do none of that and do not modify files. Report to ${R}/final-verify-r${round}.md and return the structured verdict.`, { label: 'final verify r' + round, phase: 'Release', effort: 'xhigh' })
    if (final.verdict === 'PASS' || round === 3) break
    await run(`${BASE}
${MAIN_CTX}

YOUR JOB: fix the blocking findings of the final verification on main (round ${round}); report ${R}/final-verify-r${round}.md. Findings:
${JSON.stringify(final.blocking, null, 2)}
Test first; restart the full stack after changes; all gates green; commit as "fix(release): ...". Report to ${R}/final-fix-r${round}.md .`, { label: 'final fix r' + round, phase: 'Release', effort: 'high' })
  }
})

return {
  hardening: hRes,
  lanes: laneRes,
  finish: fRes,
  final: final ? { verdict: final.verdict, blocking: final.blocking, gates: final.gates } : null,
}
