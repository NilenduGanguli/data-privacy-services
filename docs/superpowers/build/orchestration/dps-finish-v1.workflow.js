export const meta = {
  name: 'dps-finish-v1',
  description: 'Continue DPS to v1.0.0 after the pause: M4 post-merge panel on main, security S3b + the open S3 findings, images S4b, demo S6b, the live-Vertex fixes (persona, schema spec, whole-table identifier check) integrated on fllm, release with a final panel',
  phases: [
    { title: 'Main', detail: 'S1: M4 post-merge panel on main, one fix round (the FINAL panel re-verifies)' },
    { title: 'Security', detail: 'S3b review on fsec and S3c (open S3 r3 findings) on fsecc, in parallel' },
    { title: 'Images', detail: 'S4b: scanner in the full profile, amd64 images, smoke' },
    { title: 'Demo', detail: 'S6b: demo seed, e2e demo, real deck' },
    { title: 'Fixes', detail: 'S8: persona, schema spec, identifier check; integrate on fllm with a live Vertex run' },
    { title: 'Release', detail: 'merge every finish branch, T28, final verification panel, v1.0.0' },
  ],
}

const ROOT = '/Users/neelu/document_intelligence'
const MAIN = ROOT + '/data-privacy-services'
const R = '/Users/neelu/.cache/dps-build/reports'
const RESULTS = '/Users/neelu/.cache/dps-build/stream-results-2026-10-15.json'
const VLIVE = '/Users/neelu/.cache/dps-build/vertex_live/vertex_live_test.py'
const VKEY = '/Users/neelu/Downloads/vertex-staging-sa-key.json'
const PROG = 'docs/superpowers/plans/2026-10-13-remaining-work-plan.md'
const M5PLAN = 'docs/superpowers/plans/2026-10-11-m5-hardening-and-showcase.md'
const LANES = ['fsec', 'fimg', 'fos', 'fdemo', 'fdocs', 'fllm', 'fpersona', 'fspec', 'fidcheck', 'fsecc']
const WT = { main: MAIN }
for (const l of LANES) WT[l] = ROOT + '/dps-wt-' + l
const PORTS = {
  fsec: { app: 8416, db: 5483, src: 5484, minio: 9070, minioc: 9071, ora: 1526, mongo: 27022, ocr: 5066, demo: 8426, tok: 8436, wm: 9416 },
  fimg: { app: 8417, db: 5493, src: 5494, minio: 9080, minioc: 9081, ora: 1527, mongo: 27023, ocr: 5076, demo: 8427, tok: 8437, wm: 9417 },
  fos: { app: 8419, db: 5513, src: 5514, minio: 9100, minioc: 9101, ora: 1529, mongo: 27025, ocr: 5096, demo: 8429, tok: 8439, wm: 9419 },
  fdemo: { app: 8418, db: 5503, src: 5504, minio: 9090, minioc: 9091, ora: 1528, mongo: 27024, ocr: 5086, demo: 8428, tok: 8438, wm: 9418 },
  fdocs: { app: 8422, db: 5523, src: 5524, minio: 9110, minioc: 9111, ora: 1530, mongo: 27026, ocr: 5106, demo: 8432, tok: 8442, wm: 9422 },
  fidcheck: { app: 8601, db: 5601, src: 5602, minio: 9601, minioc: 9602, ora: 1601, mongo: 27601, ocr: 5603, demo: 8602, tok: 8603, wm: 9603 },
  fpersona: { app: 8611, db: 5611, src: 5612, minio: 9611, minioc: 9612, ora: 1611, mongo: 27611, ocr: 5613, demo: 8612, tok: 8613, wm: 9613 },
  fspec: { app: 8621, db: 5621, src: 5622, minio: 9621, minioc: 9622, ora: 1621, mongo: 27621, ocr: 5623, demo: 8622, tok: 8623, wm: 9623 },
  fllm: { app: 8631, db: 5631, src: 5632, minio: 9631, minioc: 9632, ora: 1631, mongo: 27631, ocr: 5633, demo: 8632, tok: 8633, wm: 9633 },
  fsecc: { app: 8641, db: 5641, src: 5642, minio: 9641, minioc: 9642, ora: 1641, mongo: 27641, ocr: 5643, demo: 8642, tok: 8643, wm: 9643 },
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

const BASE = `CONTEXT: Data Privacy Services (DPS), an enterprise obfuscation and synthetic-data platform for a large bank, in the final stretch to v1.0.0. The owner asked to finish the important work first, with maximum effort and parallelism. Read first (in your working copy, or in ${MAIN} if missing): docs/superpowers/RESUME.md §0-§3 and §10, the programme plan ${PROG} (§2.5-§3, §5, §6), the contract docs/superpowers/plans/2026-10-11-m1-m5-contract.md, the M5 plan ${M5PLAN} for the M5 tasks you execute, and the deviation logs docs/superpowers/build/*-DEVIATIONS*.md (code as built wins over plan text).
STATE (2026-10-15 evening): main = M0-M5 merged: M3 (b878c50) and M4 (5959a75) with integrate fixes through 8ea220a. An earlier orchestration run was paused by the owner and is continued here; its reports are in ${R} (S1-*, S2-*, S3-*, S4*, S5-*, S6a-*, S7-*), and the latest verdict of every finished stream is in ${RESULTS}. Finish branches, each in its worktree ${ROOT}/dps-wt-<branch>: fsec (security; M4 merged in), fimg (images; M4 merged in), fos (OpenShift, done), fdemo (demo), fdocs (docs, done), and fllm (defects found by a live Vertex run: T3 egress verifier fix 0fd76d0, one persona per person 5cef802) with its feeder branches fpersona, fspec, fidcheck. Touch only your own worktree and its own Docker compose project. Docker Desktop was restarted (16 CPUs, 36 GiB): every compose stack is stopped; bring up only the profiles you need, and stop your own stack (docker compose stop) when your job ends unless your prompt says to leave it running. If docker info fails, wait with bounded polling loops (up to 15 minutes).
LANE RULES: in a lane worktree run every Python tool through ./lane.sh (activates .venv, exports .env.lane) and docker compose only from that worktree (it reads .env). In the main checkout use .venv/bin/... and export the DPS_TEST_* defaults of contract §12.2. Never run tests against another worktree's stack: a worktree without .env.lane silently targets main's default ports. Never stop/remove/prune another project's containers, volumes or images; never docker system prune. Browser checks: private Playwright (playwright==1.55.0) in a venv at /Users/neelu/.cache/dps-build/pw-<stream>; never the shared Playwright MCP browser.
LLM TESTS: fake an LLM at the provider seam (override LlmGateway._stellar or _vertex), never by overriding complete(): the egress verifier sits in complete(), and tests above it hid a bug that blocked every T3 call.
GIT: author NilenduGanguli <nilendu.ganguli@gmail.com>; NEVER any Co-Authored-By or other trailer; conventional commits; explicit pathspec commits (merge commits excepted); never stash, reset --hard, clean, rebase, force-push, or create/move/delete tags unless told. Pushing is done by the owner's checked auto-push script; you may run tools/dev/autopush.sh from ${MAIN} after a milestone commit, never git push directly.
PRIVACY/QUALITY: never print or log customer values, key material or secrets; never weaken a validator, verification or security property (D2 stateless keys, D4 no submissions, P1 no raw value to an LLM, D-A no real identifiers in synthetic data) to pass a test. Claims need proofs: source-vs-target comparisons (counts only), counting servers for D4, OCR for masked captures, log scans.
RESUME: inspect git status/log and your report path first; continue from any partial state an earlier attempt left; never redo committed work; never discard uncommitted work without reading it.
REPORTING: full report to the given path; short final message (under 25 lines).`

function lane(name) {
  if (name === 'main') return `YOUR WORKING COPY: the main checkout ${MAIN} (branch main). Python via ${MAIN}/.venv; compose project data-privacy-services with default ports (8400/5441/5442/9010/9011/1522/27018/5016/8410).`
  const p = PORTS[name]
  return `YOUR WORKING COPY: ${WT[name]} (branch ${name}). If the worktree does not exist, create it from ${MAIN} (git worktree add ${WT[name]} ${name}, or -b ${name} main if the branch is missing). If .env, .env.lane or lane.sh are missing, create them as git-ignored files (patterns into the file named by git rev-parse --git-path info/exclude, never .gitignore): .env with DPS_IMAGE_TAG=${name} DPS_APP_PORT=${p.app} DPS_DB_PORT=${p.db} DPS_SOURCE_PG_PORT=${p.src} DPS_MINIO_PORT=${p.minio} DPS_MINIO_CONSOLE_PORT=${p.minioc} DPS_ORACLE_PORT=${p.ora} DPS_MONGO_PORT=${p.mongo} DPS_OCR_PORT=${p.ocr} DPS_DEMO_SITE_PORT=${p.demo} DPS_TOKENIZER_MOCK_PORT=${p.tok} DPS_WORKER_METRICS_PORT=${p.wm} plus every other variable docker-compose.yml interpolates (DPS_PG_PORT, DPS_EXPORT_ROOT, ...) with free/lane-local values (check ports with lsof); .env.lane exporting the DPS_TEST_* variables of contract §12.2 for those ports (same format as ${ROOT}/dps-wt-m3/.env.lane, only the ports differ); lane.sh copied from ${ROOT}/dps-wt-m3/lane.sh (chmod +x). If .venv is missing: uv venv --python 3.12 .venv and ./lane.sh uv pip install -e ".[<every extra pyproject defines that tests need>]"; cd frontend && npm ci only if you need the frontend (never symlink node_modules). From then on use ./lane.sh for every Python tool and docker compose only from this worktree.`
}

async function run(prompt, opts) {
  for (let i = 1; i <= 4; i++) {
    const r = await agent(i === 1 ? prompt : 'RESUME NOTE: an earlier attempt of this job died on an infrastructure error; continue from the current git state and report path.\n\n' + prompt,
      Object.assign({ effort: 'max' }, opts, { label: opts.label + (i > 1 ? ' (retry ' + i + ')' : '') }))
    if (r !== null && r !== undefined) return r
    log(opts.label + ': agent died (attempt ' + i + ')')
  }
  return null
}
async function judge(prompt, opts) {
  const r = await run(prompt, Object.assign({ schema: VERDICT }, opts))
  return r || { verdict: 'FAIL', blocking: [{ problem: 'verifier could not complete', fix: 'run again' }], gates: 'unknown' }
}

async function panelLoop(o) {
  let failing = o.lenses.map((l) => l.key)
  let last = null
  for (let round = 1; round <= (o.rounds || 3); round++) {
    const gates = await judge(`${BASE}
${lane(o.where)}

YOUR JOB: ${o.title} — GATES verifier, round ${round}. ${o.context}
Bring the stack up (${o.profiles}), poll /readyz until ready (never --wait), then run every gate: ruff format --check, ruff check, mypy, pytest -q, and with DPS_TEST_REQUIRE_SERVICES=1 every integration marker that applies (${o.markers}), frontend npm ci && npm test && npm run build, tools/smoke_test.py${o.extraGates ? ', ' + o.extraGates : ''}. LEAVE THE STACK RUNNING for the lens verifiers that follow. Do not modify files. Report to ${R}/${o.tag}-gates-r${round}.md and return the structured verdict.`, { label: o.tag + ' gates r' + round, phase: o.phase })
    const lenses = o.lenses.filter((l) => failing.includes(l.key))
    const verdicts = await parallel(lenses.map((l) => () => judge(`${BASE}
${lane(o.where)}

YOUR JOB: ${o.title} — LENS "${l.key}" verifier, round ${round}. ${o.context}
The stack is already running (started by the gates verifier); do not restart or rebuild it unless it is down, never run docker compose down, and do not run the full test suite (the gates verifier does). Focus only on your lens and produce evidence: ${l.what}
Probes go under ${R}/${o.tag}-${l.key}-r${round}/ (never in the repository). Do not modify repository files. Report to ${R}/${o.tag}-${l.key}-r${round}.md and return the structured verdict (blocking = a real defect in your lens).`, { label: o.tag + ' lens ' + l.key + ' r' + round, phase: o.phase })))
    const all = [{ key: 'gates', v: gates }].concat(lenses.map((l, i) => ({ key: l.key, v: verdicts[i] || { verdict: 'FAIL', blocking: [{ problem: 'lens died', fix: 'rerun' }] } })))
    const blocking = all.flatMap((x) => (x.v.verdict === 'PASS' ? [] : x.v.blocking.map((b) => Object.assign({ lens: x.key }, b))))
    failing = all.filter((x) => x.key !== 'gates' && x.v.verdict !== 'PASS').map((x) => x.key)
    last = { verdict: blocking.length ? 'FAIL' : 'PASS', blocking, round }
    log(o.tag + ' panel r' + round + ': ' + last.verdict + (blocking.length ? ' (' + blocking.length + ' blocking; lenses ' + failing.join(',') + ')' : ''))
    if (last.verdict === 'PASS') break
    if (round === (o.rounds || 3) && !o.fixAfterLast) break
    await run(`${BASE}
${lane(o.where)}

YOUR JOB: ${o.title} — FIX round ${round}. Fix every blocking finding below, each with a test that fails before the fix; keep contract names stable (or update the contract in the same commit); record deviations in the relevant deviation log. Reports: ${R}/${o.tag}-*-r${round}.md. Findings:
${JSON.stringify(blocking, null, 2)}
Rebuild/restart the stack after changes; all gates green; commit as "fix(${o.commitScope}): ...". Report to ${R}/${o.tag}-fix-r${round}.md .`, { label: o.tag + ' fix r' + round, phase: o.phase })
    if (round === (o.rounds || 3)) break
    if (!failing.length) failing = o.lenses.map((l) => l.key)
  }
  return last
}

function implThenReview(o) {
  return (async () => {
    await run(`${BASE}
${lane(o.where)}

YOUR JOB (${o.tag}): ${o.job}${o.extra ? '\n' + o.extra : ''}
Work test-first (failing test, implement, pass, gates, commit). Record deviations in ${o.devlog || 'docs/superpowers/build/M5-DEVIATIONS.md (section per task; mark it as finish-stage work)'}. Report to ${R}/${o.tag}-impl.md .`, { label: o.tag + ' implement', phase: o.phase })
    if (o.onImpl) o.onImpl()
    const rounds = o.rounds || 2
    let v = null
    for (let round = 1; round <= rounds; round++) {
      v = await judge(`${BASE}
${lane(o.where)}

YOUR JOB (${o.tag}): independently review the work "${o.job.slice(0, 600)}..." on branch ${o.where} (round ${round}). Implementer report ${R}/${o.tag}-impl.md. Run the gates yourself (${o.gates}); check acceptance; hunt for real defects (${o.hunt}). Do not modify repository files. Report to ${R}/${o.tag}-review-r${round}.md and return the structured verdict.`, { label: o.tag + ' review r' + round, phase: o.phase })
      if (v.verdict === 'PASS') break
      if (round === rounds && !o.fixAfterLast) break
      await run(`${BASE}
${lane(o.where)}

YOUR JOB (${o.tag}): fix the blocking review findings (round ${round}); review ${R}/${o.tag}-review-r${round}.md. Findings:
${JSON.stringify(v.blocking, null, 2)}
Failing test first; gates green; commit as "fix(${o.where}): ...". Report to ${R}/${o.tag}-fix-r${round}.md .`, { label: o.tag + ' fix r' + round, phase: o.phase })
      if (round === rounds) { v = Object.assign({}, v, { fixedAfterLastReview: true }); break }
    }
    log(o.tag + ': ' + v.verdict)
    return v
  })()
}

async function mergeInto(where, branch, why, phase) {
  await run(`${BASE}
${lane(where)}

YOUR JOB: merge branch ${branch} into branch ${where} in ${WT[where]} (git merge --no-ff ${branch} -m "merge: ${branch} into ${where} (${why})"), keeping both sides of every conflict; then make this stream's work consistent with what came in (${why}); all gates green on this stream's stack; commit; docker compose stop. Report to ${R}/${where.toUpperCase()}-merge-${branch}.md .`, { label: where + ' <- ' + branch, phase })
}

// ------------------------------------------------------------------ S1: M4 post-merge panel on main
const L_M4 = [
  { key: 'd4-network', what: 'D4 no-submission and scope: counting-server probes and the demo site /__received counter during a live scan in synthetic and obfuscated modes (non-GET == 0); beacons, keepalive fetch, SharedWorker, WebSocket, popups during arm, redirect hops to foreign hosts, deny/allow rail on scanner-caused navigations (incl. handler-driven hash/pushState routes), SSRF via numeric hosts or DNS.' },
  { key: 'captures-privacy', what: 'masked captures and obfuscated captures OCR-checked: no readable source value (incl. display:contents text, attribute-drawn strings, iframes, closed selects with an empty-value option, an option past the first 300, and appearance: base-select); inventory/exports/evidence/logs value-free; persona fill values never leave the page except the known Cookie/Referer case N1 (owner decision pending: report it, do not block on it).' },
  { key: 'adaptations', what: 'SEC-5 (storage_state_ref resolved by main resolve_secret_ref(tenant_id, ref, settings), validated at ScanSpec accept, cross-tenant refused), SEC-2 for scanner secrets and seeds, M5 DB-backed keys and scope scan on every scans route, M5 posture rules for the browser-worker role, M5 policy @name references in ScanSpec, discovery semantics (mixed-PII never non-personal) in scanner classification; the merge 5959a75 kept both sides of every conflict (diff it against both parents for hotspot files).' },
  { key: 'regression', what: 'regression of M1/M2/M3/M5 on the merged code: one L2 obfuscate job on PG, Oracle and Mongo targets and one synthesize job with per-column source-vs-target counts (0 survivors, D-A), redacted PDF 0 extractable chars, consistency check overlap > 0, log scan of all containers for the demo values used, dps_llm_raw_pii_sent_total 0.' },
  { key: 'console', what: 'private-Playwright walk of every console page of M1-M5 (Overview, Playground incl. Documents, Policies, Data sources & jobs incl. Oracle/Mongo wizard and job groups, UI scanner setup/live crawl/scan detail parallel view, Reports & audit incl. a stopped run that wrote rows, Evidence pack, Admin) against docs/design/mockups: 0 console errors, no horizontal scroll at 390 px.' },
]
async function s1() {
  phase('Main')
  const r = await panelLoop({ where: 'main', tag: 'S1-M4', phase: 'Main', commitScope: 'integrate', title: 'post-merge verification of main after merging M4', context: 'main contains every milestone M0-M5 (finish streams still to merge). The merge agent (its report ' + R + '/S1-merge-m4.md stayed "in progress") committed 5959a75, 932075a, 40d8b34 and 8ea220a but did not finish its gate chain, so the gates here are the first full run on this HEAD. The S2 r3 open findings (console B1: a stopped run that wrote rows; captures-privacy B1a-c: closed selects) were fixed in 932075a and 40d8b34: verify them.', profiles: 'profiles docs, sources, oracle, scanner (and tokenizer if defined)', markers: '"docs or ocr", "db or pg_source or s3", "oracle or mongo", browser', lenses: L_M4, rounds: 1, fixAfterLast: true })
  // urgency: one panel round + one fix round; the FINAL panel re-verifies main (no v0.5.0-m4 tag)
  return r
}

// ------------------------------------------------------------------ S3: security
async function s3() {
  const bP = implThenReview({ where: 'fsec', tag: 'S3b', phase: 'Security', fixAfterLast: true,
    job: 'execute M5 plan Task 8B (T3 metadata labelling in L2 discovery and L3 scanner classification, value-free, opt-in) and the scanner part of Task 17 (S5 netguard: check_scan_url at POST /scans, Chromium --host-resolver-rules for registered hosts only; numeric host encodings, IPv4-mapped IPv6, metadata IPs, DNS resolving to link-local), plus a D4 re-check; extend docs/security/2026-10-11-m5-security-review.md',
    extra: 'An earlier attempt of this job was stopped by the owner pause: ' + R + '/S3b-impl.md may be partial and tests/test_llm_label_l2_l3.py is uncommitted in the worktree - read them and continue. First cherry-pick commit 0fd76d0 from branch fllm into fsec (git cherry-pick 0fd76d0, no -x): it fixes the egress verifier, which blocked every T3 labelling prompt under strict mode (the labelling system prompt lists USERNAME, which has a SWIFT BIC shape). T8B builds on that path, so its tests must go through the real complete() with the provider faked at _stellar.',
    gates: 'ruff, mypy, pytest -q, tests/security, pytest -m browser and the scanner integration tests on this stream\'s stack',
    hunt: 'LLM prompts containing values (check the actual messages handed to the provider), SSRF bypasses, D4 regressions' })
  const cP = implThenReview({ where: 'fsecc', tag: 'S3c', phase: 'Security', fixAfterLast: true,
    extra: 'Branch fsecc was cut from fsec at fee0041 (S3b committed) so this job runs in parallel with the S3b review on fsec; both branches merge into main at release. Keep your changes on the detection / ReDoS-guard / budget paths and stay off the scanner and T8B files unless the fix truly needs them. Use only the profiles you need on this lane (no oracle).',
    job: 'fix the two blocking findings still open after S3 review r3 (full report ' + R + '/S3-review-r3.md; also under "S3 review r3" in ' + RESULTS + '). R3-B1 (HIGH): F-02 is still exploitable through /synthesize and /documents/synthesize - detection runs on the event loop, so a policy-pattern run that leaves the 5 ms slice blocks the loop, first in an unbounded Condition.wait for a matcher process, then for the whole run (up to the 5 s ceiling); measured 26 of 29 /health checks failing under back-to-back synthesize, and /health blocked 19.5 s behind six /detect runs. R3-B2 (MEDIUM, a round-2 regression): building the deny-list trie sits outside every budget and costs about 10x the round-1 compile (one 2.7 MB /detect with 50 inline deny lists: 34.2 CPU-seconds vs 3.4 s), all before any pattern runs. Fix both without weakening the ReDoS guard, the per-request ceiling or the R2-B1/R2-B2 behaviour (concurrency causes no refusals, per-request ceiling holds, NDJSON items have their own budgets, the trie stays exact, a post-write sample that could not run fails closed): keep detection off the event loop on every route, bound every wait, bring the trie build inside the budget and at least back to round-1 cost. Reproduce each with the reviewer probe as a failing test first.',
    gates: 'ruff, mypy, pytest -q, tests/security, the S3 review r3 probes re-run (0 failed /health checks under back-to-back synthesize; /health never blocked behind /detect runs; trie build CPU at or below round-1 cost)',
    hunt: 'fixes that move the block elsewhere, budgets that can be bypassed, regressions of R2-B1/R2-B2, refusals under concurrency' })
  const [b, c] = await parallel([() => bP, () => cP])
  return { b, c }
}

// ------------------------------------------------------------------ S4 / S6: images, demo
let s4ImplDone = null
const s4Impl = new Promise((resolve) => { s4ImplDone = resolve })
async function s4() {
  return await implThenReview({ where: 'fimg', tag: 'S4b', phase: 'Images', fixAfterLast: true, onImpl: () => s4ImplDone(), job: 'complete Task 24 for the scanner: full profile includes browser-worker and demo-site, smoke covers a scan (POSTs received 0), images (app and browser) build for amd64 and run under an arbitrary UID with a read-only root filesystem',
    extra: 'An earlier attempt of this job was stopped by the owner pause with about 63 uncommitted files in the worktree (HANDOFF.md, demo/onboarding/server.mjs, docker-compose.yml, ...) and ' + R + '/S4b-impl.md partial - read them and continue. Tracked screenshots it re-rendered (docs/superpowers/build/m3-shots/*.png and any other tracked PNG outside this job) are not part of this job: restore those with git checkout -- <path>.',
    gates: 'image builds, compose --profile full up, smoke all checks, flow_report', hunt: 'missing scanner coverage in smoke, D4 regressions, images running as root or needing a writable root' })
}
async function s6() {
  await mergeInto('fdemo', 'fimg', 'bring in Task 24 (demo_client, compose full with the scanner, smoke) and the UI scanner (fimg contains m4) needed by the demo tools', 'Demo')
  return await implThenReview({ where: 'fdemo', tag: 'S6b', phase: 'Demo', fixAfterLast: true,
    extra: 'For speed this job starts right after S4b was implemented, while the S4b review runs on fimg in parallel: fixes that review produces reach main at release (fimg merges before fdemo), so do not wait for them.',
    job: 'execute M5 plan Task 26 (tools/demo_seed.py, tools/e2e_demo.py with --console and the JSON report, tests/console/test_console_pages.py, final docs/demo/DEMO-SCRIPT.md) and run the whole demo on this stream\'s full stack: demo_seed then e2e_demo --console, every step PASS in under 10:00, screenshots in reports/demo-screens; then build the deck from the real report and screenshots (tools/build_deck.py -> docs/demo/dps-showcase.pptx, committed) and QA it by exporting to PDF with Keynote via osascript and reading every page image (if Keynote cannot run headless, say so and rely on the structural test).',
    gates: 'ruff, mypy, pytest -q, compose --profile full up, demo_seed + e2e_demo --console all PASS, tests/test_deck.py', hunt: 'demo steps that skip their assertion, numbers on slides not in the report, values in reports, charts Keynote drops, fonts Keynote lacks' })
}

// ------------------------------------------------------------------ S8: the live-Vertex fixes
async function s8() {
  phase('Fixes')
  const lanes = await parallel([
    () => implThenReview({ where: 'fpersona', tag: 'S8-persona', phase: 'Fixes', fixAfterLast: true, devlog: 'docs/superpowers/build/M5-DEVIATIONS.md (section "finish-stage fixes from the live Vertex run")',
      job: 'synthesized people must be distinct and internally consistent. Base: commit 5cef802 (one persona per person: the first declared PERSON_NAME keeps persona(anchor), each other distinct person gets anchor|person:<seeded digest>; emails/usernames/MRZ follow the person whose name parts they hold). Still wrong: (1) {"first_name": "Arjun", "last_name": "Mehta", "email": "arjun.mehta@example.in"} under synthetic-realistic gives first_name Alfred, last_name Michael, email alfred.williams..., because surname keys get a first name and the two single tokens count as two people. Make structured surname keys (last_name, lastname, lname, surname, family_name, familyname; case-insensitive leaf key; middle-name keys too if cheap) get the person\'s LAST name and given-name keys (first_name, given_name, fname, forename) the first name - the role comes from the key name only, never the value, threaded through the transform context path without breaking surrogate()\'s purity guarantee; single-token first-name and last-name fields of one record are ONE person (the primary unless they match another declared person), so first_name, last_name and email agree (expected shape: Alfred / Williams / alfred.williams..@...). Customer + nominee full-name records stay two distinct people with their own emails. (2) In text, titles should agree with the persona gender (Mr -> male; Mrs, Ms, Miss -> female) if Persona generation can take a gender hint without a large change; otherwise document why not. (3) Layer 2 table synthesis goes through the same path: a synthesize job over a PostgreSQL table with first_name, last_name and email columns must give consistent rows (integration test on this lane\'s stack, profile sources).',
      extra: 'An earlier attempt was stopped by the owner pause and left 5 uncommitted files on top of 5cef802 (dps/engine/detect/names.py, dps/engine/transform/methods.py, ...): read git diff and continue from it.',
      gates: 'ruff, mypy, pytest -q (whole suite, isolated on this lane), DPS_TEST_REQUIRE_SERVICES=1 -m "db or pg_source" on this lane\'s stack', hunt: 'a surrogate that reveals or contains an original, order dependence, regressions of one-person records and of customer+nominee records, D-A on L2 targets' }),
    () => implThenReview({ where: 'fspec', tag: 'S8-spec', phase: 'Fixes', fixAfterLast: true, devlog: 'docs/superpowers/build/M1-DEVIATIONS.md (append at the end in its format)',
      job: 'schema synthesis from a sample must work at realistic sizes. Base: commit e1f3a3a (NER no longer types JSON numbers DATE; flagged keys aliased only inside the LLM prompt and mapped back; collapsed ranges span the order of magnitude; a model choice made of value shapes is ignored). Still wrong: (1) POST /api/v1/schema/synthesize with a 7-column 4-row sample (cust_id 1001-1004, full_name, email, city, segment, balance, opened) returns 422 leakage in 17-19 of 20 runs at 100 rows, on the base code too: CITY surrogates repeat the sampled cities, and the 4-digit cust_id values match as substrings inside generated dates and balances; the wider ID range can also reproduce an exact sample ID. Fix the false-positive classes in verify_against_sample and its helpers WITHOUT weakening real leak detection: numeric originals match as whole values/tokens (digit-run boundaries), never inside other numbers or dates, while a generated value equal to a sampled ID in any field still fails; for CITY (and STATE/COUNTRY if the taxonomy has them) prefer making the generator avoid the sample\'s own values when a sample is present over exempting them - if avoidance is impossible because generation is value-free by design, extend SWEEP_EXEMPT with the same documented rationale as gender/nationality and say so; when a sample is present, key/ID-like integer fields avoid exact sample values. Prove: 20 runs x 100 rows -> zero 422s, and a planted real leak (a sampled email, name or ID forced into the output) still fails 422. (2) DDL bounds are kept exactly: NUMBER(3) -> max 999, NUMBER(5,2) -> 999.99, not rounded. (3) Record the e1f3a3a rule change (flagged keys aliased only in the prompt and mapped back, replacing the M1 Task 12 rule) and any rule item 1 changes.',
      extra: 'An earlier attempt was stopped by the owner pause and left about 5 uncommitted files on top of e1f3a3a (dps/engine/verify.py, dps/engine/synth/schema_gen.py, dps/engine/synth/schema_profile.py, tests/test_schema_profile.py): read git diff and continue from it.',
      gates: 'ruff, mypy, pytest -q (whole suite, isolated on this lane), the 20x100 schema-synthesize run and the planted-leak run through the HTTP API (in-process app is fine)', hunt: 'a weakened leakage sweep, exemptions without rationale, P1 (prompts must never hold sample values or data-like keys)' }),
    () => implThenReview({ where: 'fidcheck', tag: 'S8-idcheck', phase: 'Fixes', fixAfterLast: true, devlog: 'docs/superpowers/build/M3-DEVIATIONS.md (append at the end in its format)',
      job: 'the D-A post-write identifier check must cover the whole table on every engine. Open blocker from S1-M3 engines r3 (report ' + R + '/S1-M3-engines-r3.md): on Oracle and MongoDB the check reads only the first 5 000 rows (CHECK_BATCH in dps/layers/connectors/identifier_check.py; the connectors\' read_partition with an unbounded KeyRange returns one page) on both the target and the source side, yet reports scope whole_table, so a real identifier in a later row is never flagged and the evidence pack says passed (live: Oracle NATIONAL_ID checked 5000 on a 20 000-row target while PostgreSQL checked 20000). Make the check page through every row (after_key pagination) on PostgreSQL, Oracle and MongoDB, also in key_floor; make the reported checked count equal the rows actually read and never claim whole_table for a partial read (fail closed if a read cannot complete). Prove with a scratch target of 20 000 rows and one real identifier planted at row 15 000 on Oracle and MongoDB: flagged, and checked == 20000.',
      gates: 'ruff, mypy, pytest -q (isolated on this lane), DPS_TEST_REQUIRE_SERVICES=1 -m "oracle or mongo" and -m "db or pg_source" on this lane\'s stack (profiles sources, oracle)', hunt: 'pagination that skips or repeats rows (ties, composite keys, ObjectId order, Oracle ROWID), counts that do not match rows read, any claim of whole_table on a partial read' }),
  ])
  const merged = await run(BASE + '\n' + lane('fllm') + '\n\nYOUR JOB (S8-integrate): integrate the live-Vertex fixes on branch fllm: merge fpersona, fspec and fidcheck into fllm (git merge --no-ff <branch> -m "merge: <branch> into fllm", keep both sides of conflicts) and fix test-first any finding their reviews left open: ' + JSON.stringify(lanes.map((v, i) => ({ lane: ['fpersona', 'fspec', 'fidcheck'][i], verdict: v && v.verdict, open: v && v.blocking }))) + '. Then prove the whole: ruff, mypy, the whole pytest suite, every integration marker on this lane\'s stack (profiles docs, sources, oracle), and the live LLM run: ' + WT.fllm + '/.venv must hold the [vertex,ner] extras and en_core_web_sm (install if missing), then ' + WT.fllm + '/.venv/bin/python -I ' + VLIVE + ' ' + VKEY + ' ziffy-staging (the owner provided this staging service-account key for exactly this test: never print, copy, mount into a container or commit it) must end PASS - T3 labels via llm_label, 0 raw values in every prompt, generator-spec field names are the caller\'s, schema/synthesize 200. Commit fixes as "fix(fllm): ..." with failing tests first; record deviations in the logs the feeder branches used. Report to ' + R + '/S8-integrate.md .', { label: 'S8-integrate', phase: 'Fixes' })
  return { lanes, merged }
}

// ------------------------------------------------------------------ orchestration
const s1P = s1()
const s3P = (async () => { phase('Security'); return await s3() })()
const s4P = (async () => { phase('Images'); return await s4() })()
const s6P = (async () => { await s4Impl; phase('Demo'); return await s6() })()
const s8P = s8()
const [s1r, s3r, s4r, s6r, s8r] = await parallel([() => s1P, () => s3P, () => s4P, () => s6P, () => s8P])

// ------------------------------------------------------------------ release
phase('Release')
await run(`${BASE}
${lane('main')}

YOUR JOB: merge the finish branches into main in ${MAIN}, in this order: fsec, fsecc, fimg, fos, fdemo, fdocs, fllm (each git merge --no-ff <branch> -m "merge: <branch> into main"). Earlier stream results are in ${RESULTS}; this run's results: ${JSON.stringify({ s1m4: s1r, s3: s3r, s4b: s4r, s6b: s6r, s8: s8r })}. fllm carries the fixes from the live Vertex run (egress verifier, one persona per person incl. first/last-name columns, schema spec and sample-synthesis leakage false positives, whole-table identifier check); fsec already holds a cherry-pick of 0fd76d0, which merges cleanly; fsecc (cut from fsec at fee0041) carries S3c, the event-loop ReDoS and trie-budget fixes. Keep both sides of conflicts (HANDOFF, README, CHANGELOG, compose, pyproject, contract, deviation logs); fix any still-open finding from those results test-first. Then M5 plan Task 28 steps 1-2: version 1.0.0 (pyproject, dps/__init__.py, frontend package + lock via npm version 1.0.0 --no-git-tag-version), regenerate HANDOFF (tools/settings_table.py) to its final shape, date CHANGELOG (include the live-Vertex fixes), run ruff, mypy and pytest -q once on the merged tree (fix what the merge broke, test-first), commit "chore(release): v1.0.0", then docker compose --profile full down -v && docker compose --profile full up --build -d and poll /readyz. Do NOT run the full Definition of Done here: the FINAL panel's gates verifier runs it next (urgency). Do NOT tag. Leave the full stack running. Run tools/dev/autopush.sh. Report to ${R}/release.md .`, { label: 'merge streams + release candidate', phase: 'Release' })

const FINAL_LENSES = [
  { key: 'l1-docs', what: 'Layer 1 and documents: detect/obfuscate/synthesize/reveal round trips incl. NDJSON and Idempotency-Key; redacted PDF 0 extractable characters and OCR re-read finds 0 originals; schema synthesis replay identical; schema/synthesize from a 7-column 4-row sample at 100 rows returns 200 in 20 of 20 runs while a planted sample value still fails 422. Live LLM: in a throwaway venv at /Users/neelu/.cache/dps-build/venv-final with -e ' + MAIN + '[vertex,ner,fpe,docs,db,connectors,tabular] and en_core_web_sm, run ' + VLIVE + ' ' + VKEY + ' ziffy-staging (never print, copy, mount or commit the key) - must end PASS.' },
  { key: 'l2-engines', what: 'Layer 2 on PostgreSQL, Oracle and MongoDB: obfuscate jobs in every write mode with per-column source-vs-target counts (0 survivors), D3 typed confirmation enforced, consistency check overlap > 0, resume-after-kill; the D-A identifier check reports checked == rows on a 20 000-row Oracle and MongoDB target and flags a planted real identifier at row 15 000.' },
  { key: 'l3-scanner', what: 'Layer 3: live scan of the demo site, parallel view both modes, masked captures OCR-checked (incl. closed selects), demo site /__received non-GET == 0, deny rail, SSRF netguard; report the N1 Cookie/Referer case as non-blocking (owner decision pending).' },
  { key: 'synthetic', what: 'synthetic: copula synthesize job fidelity >= 0.85, exact-row matches 0, NNDR measured, utility and MIA proxy reported, no real key or government id in synthetic targets (D-A identifier check); distinct people stay distinct and consistent: a customer+nominee record gives two different synthetic people whose emails follow them, a first_name/last_name/email record gives one consistent person, free text with three titled people gives three names - on L1 and on an L2 PostgreSQL synthesize job.' },
  { key: 'security', what: 'security: tests/security green, SEC-2..SEC-5 probes, route-auth walk, cross-tenant 404s, ReDoS guard incl. /synthesize and /documents/synthesize keeping /health responsive under back-to-back runs, deny-list trie build cost, external-token binding, posture refuses unsafe production configs.' },
  { key: 'privacy-ops', what: 'privacy operations: log scan of every container for the demo values used and key material, per-tenant dps_llm_raw_pii_sent_total 0, audit rows value-free, gitleaks over the whole history clean (with .gitleaksignore); T3 labelling (L1, L2 discovery, L3 classification) reaches a provider faked at _stellar under strict egress with value-free prompts.' },
  { key: 'console', what: 'console: private-Playwright walk of every page vs docs/design/mockups 01-12, 0 console errors, no horizontal scroll at 390 px; screenshots to docs/screenshots/v1.0.0/ (commit them only if the whole panel passes - leave them uncommitted and say so).' },
  { key: 'deploy-demo', what: 'deploy and demo: kustomize dev+prod build and kubeconform clean, images amd64 + arbitrary UID + read-only root, e2e demo report all PASS under 10:00, deck test green and the deck present.' },
]
const fin = await panelLoop({ where: 'main', tag: 'FINAL', phase: 'Release', commitScope: 'release', title: 'final verification of the v1.0.0 candidate', context: 'main contains every milestone and finish stream incl. fllm; release report ' + R + '/release.md.', profiles: 'profile full', markers: '"docs or ocr", "db or pg_source or s3", "oracle or mongo", browser, deploy', extraGates: 'tools/flow_report.py, tools/demo_seed.py then tools/e2e_demo.py --console (the full M5 Definition of Done 1-7)', lenses: FINAL_LENSES, rounds: 2 })

if (fin.verdict === 'PASS') {
  await run(`${BASE}
${lane('main')}

YOUR JOB: release v1.0.0 on main in ${MAIN}. The final verification panel passed. 1) Rebuild the deck from the FINAL gates run's e2e demo report and screenshots (tools/build_deck.py), record each gate summary of ${R}/FINAL-gates-r*.md in HANDOFF "Last verified", and commit them with the v1.0.0 screenshots under docs/screenshots/v1.0.0/ if present (explicit pathspec, "docs(release): v1.0.0 verification record"). 2) git tag -a v1.0.0 -m "DPS v1.0.0: Privacy Engine, L1 API and documents, L2 connectors (PostgreSQL, Oracle, MongoDB), L3 UI scanner, tabular synthesis with measured privacy, hardening, OpenShift, demo". 3) Tag local images: docker tag data-privacy-services:local data-privacy-services:1.0.0 and the browser image likewise (check the names with docker images). 4) Update docs/superpowers/RESUME.md to its final state (programme complete, where everything is, how to run the demo, open owner decisions: N1 Cookie/Referer, NER in the default image, the [vertex] extra in the image) and the Claude memory file /Users/neelu/.claude/projects/-Users-neelu-document-intelligence/memory/data-privacy-services.md with a one-paragraph final status; commit RESUME.md. 5) Run tools/dev/autopush.sh and confirm main, every branch and every tag are on origin. 6) Remove the worktrees ${['m3', 'm4', 'm4ui', 'm5', 'm5ui', 'fsec', 'fimg', 'fos', 'fdemo', 'fdocs', 'fllm', 'fpersona', 'fspec', 'fidcheck', 'fsecc'].map((b) => ROOT + '/dps-wt-' + b).join(', ')} with git worktree remove after confirming each branch is merged into main (git merge-base --is-ancestor); stop their compose projects first (docker compose down -v from each); delete the merged local branches with git branch -d (remote branches stay). Report to ${R}/release-tag.md .`, { label: 'tag v1.0.0 + cleanup', phase: 'Release', effort: 'high' })
}

return {
  s1m4: s1r && s1r.verdict, s3: s3r && { b: s3r.b && s3r.b.verdict, c: s3r.c && s3r.c.verdict }, s4b: s4r && s4r.verdict, s6b: s6r && s6r.verdict,
  s8: s8r && { lanes: s8r.lanes.map((v) => v && v.verdict), merged: s8r.merged ? 'done' : null },
  final: { verdict: fin.verdict, blocking: fin.blocking },
}
