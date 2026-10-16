export const meta = {
  name: 'dps-finish-fast',
  description: 'Maximum-parallel finish to v1.0.0: M3 verify on main, M4 pre-merge + merge, security/images/OpenShift/demo/docs streams in parallel from now, panel verifications by lens, release',
  phases: [
    { title: 'Main', detail: 'S1: M3 post-merge verification panel, tag; then M4 merge + panel, tag' },
    { title: 'M4 pre-merge', detail: 'S2: finish merging main into m4, adapt scanner, review panel' },
    { title: 'Security', detail: 'S3: parallel finders by lens, adversarial verification, fixes, T17/T0/T8B' },
    { title: 'Images', detail: 'S4: T24 images, compose full, smoke, flow report, demo client' },
    { title: 'OpenShift', detail: 'S5: T25 kustomize base, components, overlays' },
    { title: 'Demo', detail: 'S6: deck builder, demo seed and e2e demo, real run, deck' },
    { title: 'Docs', detail: 'S7: T27 docs tooling, polish items' },
    { title: 'Release', detail: 'merge all streams, T28, final verification panel by lens, v1.0.0' },
  ],
}

const ROOT = '/Users/neelu/document_intelligence'
const MAIN = ROOT + '/data-privacy-services'
const R = '/Users/neelu/.cache/dps-build/reports'
const PROG = 'docs/superpowers/plans/2026-10-13-remaining-work-plan.md'
const M5PLAN = 'docs/superpowers/plans/2026-10-11-m5-hardening-and-showcase.md'
const WT = {
  main: MAIN, m4: ROOT + '/dps-wt-m4', fsec: ROOT + '/dps-wt-fsec', fimg: ROOT + '/dps-wt-fimg',
  fos: ROOT + '/dps-wt-fos', fdemo: ROOT + '/dps-wt-fdemo', fdocs: ROOT + '/dps-wt-fdocs',
}
const PORTS = {
  fsec: { app: 8416, db: 5483, src: 5484, minio: 9070, minioc: 9071, ora: 1526, mongo: 27022, ocr: 5066, demo: 8426, tok: 8436, wm: 9416 },
  fimg: { app: 8417, db: 5493, src: 5494, minio: 9080, minioc: 9081, ora: 1527, mongo: 27023, ocr: 5076, demo: 8427, tok: 8437, wm: 9417 },
  fos: { app: 8419, db: 5513, src: 5514, minio: 9100, minioc: 9101, ora: 1529, mongo: 27025, ocr: 5096, demo: 8429, tok: 8439, wm: 9419 },
  fdemo: { app: 8418, db: 5503, src: 5504, minio: 9090, minioc: 9091, ora: 1528, mongo: 27024, ocr: 5086, demo: 8428, tok: 8438, wm: 9418 },
  fdocs: { app: 8422, db: 5523, src: 5524, minio: 9110, minioc: 9111, ora: 1530, mongo: 27026, ocr: 5106, demo: 8432, tok: 8442, wm: 9422 },
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
const FINDINGS = {
  type: 'object',
  properties: { findings: { type: 'array', items: { type: 'object', properties: {
    title: { type: 'string' }, file: { type: 'string' }, severity: { type: 'string', enum: ['critical', 'high', 'medium', 'low'] },
    claim: { type: 'string' }, exploit: { type: 'string' }, fix: { type: 'string' } }, required: ['title', 'file', 'severity', 'claim', 'fix'] } } },
  required: ['findings'],
}
const REFUTE = { type: 'object', properties: { real: { type: 'boolean' }, reason: { type: 'string' } }, required: ['real', 'reason'] }

const BASE = `CONTEXT: Data Privacy Services (DPS), an enterprise obfuscation and synthetic-data platform for a large bank, in the final stretch to v1.0.0. The owner asked to finish as fast as possible with maximum effort and parallelism. Read first (in your working copy, or in ${MAIN} if missing): docs/superpowers/RESUME.md §0-§3 and §10, the programme plan ${PROG} (§2.5-§3, §5, §6), the contract docs/superpowers/plans/2026-10-11-m1-m5-contract.md, the M5 plan ${M5PLAN} for the M5 tasks you execute, and the deviation logs docs/superpowers/build/*-DEVIATIONS*.md (code as built wins over plan text).
STATE: main = M0+M1+M2+M5+M3 merged (M3 merged as b878c50, post-merge verification pending). Branch m4 (worktree ${WT.m4}) has a merge of main INTO m4 in progress (MERGE_HEAD present, conflicts partly resolved) by an earlier stopped agent. Several streams run in parallel in separate worktrees: main (${WT.main}), m4, fsec, fimg, fos, fdemo, fdocs (all under ${ROOT}/dps-wt-*). Touch only your own worktree and its own Docker compose project. Docker Desktop was just restarted with 36 GiB / 16 CPUs; if docker info fails, wait for it with bounded polling loops (up to 15 minutes).
LANE RULES: in a lane worktree run every Python tool through ./lane.sh (activates .venv, exports .env.lane) and docker compose only from that worktree (it reads .env). In the main checkout use .venv/bin/... and export the DPS_TEST_* defaults of contract §12.2. Start only the compose profiles you need; never stop/remove/prune another project's containers, volumes or images; never docker system prune. Browser checks: private Playwright (playwright==1.55.0) in a venv at /Users/neelu/.cache/dps-build/pw-<stream>; never the shared Playwright MCP browser.
GIT: author NilenduGanguli <nilendu.ganguli@gmail.com>; NEVER any Co-Authored-By or other trailer; conventional commits; explicit pathspec commits (merge commits excepted); never stash, reset --hard, clean, rebase, force-push, or create/move/delete tags unless told. Pushing is done by the owner's checked auto-push script; you may run tools/dev/autopush.sh from ${MAIN} after a milestone commit, never git push directly.
PRIVACY/QUALITY: never print or log customer values, key material or secrets; never weaken a validator, verification or security property (D2 stateless keys, D4 no submissions, P1 no raw value to an LLM, D-A no real identifiers in synthetic data) to pass a test. Claims need proofs: source-vs-target comparisons (counts only), counting servers for D4, OCR for masked captures, log scans.
RESUME: inspect git status/log and your report path first; continue from any partial state an earlier attempt left; never redo committed work; never discard uncommitted work without reading it.
REPORTING: full report to the given path; short final message (under 25 lines).`

function lane(name) {
  if (name === 'main') return `YOUR WORKING COPY: the main checkout ${MAIN} (branch main). Python via ${MAIN}/.venv; compose project data-privacy-services with default ports (8400/5441/5442/9010/9011/1522/27018/5016/8410).`
  if (name === 'm4') return `YOUR WORKING COPY: ${WT.m4} (branch m4); ./lane.sh; compose reads .env (image tag m4, ports 8414/5463/5464/9050-9051/1524/27020/5046/8420; add any port variable the merged compose needs that .env lacks - e.g. DPS_TOKENIZER_MOCK_PORT 8434, DPS_WORKER_METRICS_PORT 9414, DPS_PG_PORT, DPS_EXPORT_ROOT - with free values, mirrored in .env.lane; both are git-ignored).`
  const p = PORTS[name]
  return `YOUR WORKING COPY: ${WT[name]} (branch ${name}). If it does not exist yet, create it: from ${MAIN} run git worktree add ${WT[name]} -b ${name} main (if branch ${name} already exists, git worktree add ${WT[name]} ${name}; retry on lock errors). Then create git-ignored files (patterns into the file named by git rev-parse --git-path info/exclude, never .gitignore): .env with DPS_IMAGE_TAG=${name} DPS_APP_PORT=${p.app} DPS_DB_PORT=${p.db} DPS_SOURCE_PG_PORT=${p.src} DPS_MINIO_PORT=${p.minio} DPS_MINIO_CONSOLE_PORT=${p.minioc} DPS_ORACLE_PORT=${p.ora} DPS_MONGO_PORT=${p.mongo} DPS_OCR_PORT=${p.ocr} DPS_DEMO_SITE_PORT=${p.demo} DPS_TOKENIZER_MOCK_PORT=${p.tok} DPS_WORKER_METRICS_PORT=${p.wm} plus every other variable docker-compose.yml interpolates (DPS_PG_PORT, DPS_EXPORT_ROOT, ...) with free/lane-local values (check ports with lsof); .env.lane exporting the DPS_TEST_* variables of contract §12.2 for those ports (copy the format of ${ROOT}/dps-wt-m3/.env.lane, changing only the ports); lane.sh copied from ${ROOT}/dps-wt-m3/lane.sh (chmod +x). Then uv venv --python 3.12 .venv and ./lane.sh uv pip install -e ".[<every extra pyproject defines that tests need>]", and cd frontend && npm ci (never symlink node_modules). From then on use ./lane.sh for every Python tool and docker compose only from this worktree.`
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

// Panel verification: one gates agent (brings the stack up, runs every gate, leaves the stack running), then lens verifiers in parallel; fixer between rounds; later rounds re-run gates + failing lenses only.
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
    if (round === (o.rounds || 3)) break
    await run(`${BASE}
${lane(o.where)}

YOUR JOB: ${o.title} — FIX round ${round}. Fix every blocking finding below, each with a test that fails before the fix; keep contract names stable (or update the contract in the same commit); record deviations in the relevant deviation log. Reports: ${R}/${o.tag}-*-r${round}.md. Findings:
${JSON.stringify(blocking, null, 2)}
Rebuild/restart the stack after changes; all gates green; commit as "fix(${o.commitScope}): ...". Report to ${R}/${o.tag}-fix-r${round}.md .`, { label: o.tag + ' fix r' + round, phase: o.phase })
    if (!failing.length) failing = o.lenses.map((l) => l.key)
  }
  return last
}

// ------------------------------------------------------------------ S2: finish the M4 pre-merge
const L_M4 = [
  { key: 'd4-network', what: 'D4 no-submission and scope: counting-server probes and the demo site /__received counter during a live scan in synthetic and obfuscated modes (non-GET == 0); beacons, keepalive fetch, SharedWorker, WebSocket, popups during arm, redirect hops to foreign hosts, deny/allow rail on scanner-caused navigations (incl. handler-driven hash/pushState routes), SSRF via numeric hosts or DNS.' },
  { key: 'captures-privacy', what: 'masked captures and obfuscated captures OCR-checked: no readable source value (incl. display:contents text, attribute-drawn strings, iframes); inventory/exports/evidence/logs value-free; persona fill values never leave the page.' },
  { key: 'adaptations', what: 'SEC-5 (storage_state_ref resolved by main resolve_secret_ref(tenant_id, ref, settings), validated at ScanSpec accept, cross-tenant refused), SEC-2 for scanner secrets and seeds, M5 DB-backed keys and scope scan on every scans route, M5 posture rules for the browser-worker role, M5 policy @name references in ScanSpec, discovery semantics (mixed-PII never non-personal) in scanner classification; the merge kept both sides of every conflict (diff the merge commit against both parents for hotspot files).' },
  { key: 'regression', what: 'regression of M1/M2/M3/M5 on the merged code: one L2 obfuscate job on PG, Oracle and Mongo targets and one synthesize job with per-column source-vs-target counts (0 survivors, D-A), redacted PDF 0 extractable chars, consistency check overlap > 0, log scan of all containers for the demo values used, dps_llm_raw_pii_sent_total 0.' },
  { key: 'console', what: 'private-Playwright walk of every console page of M1-M5 (Overview, Playground incl. Documents, Policies, Data sources & jobs incl. Oracle/Mongo wizard and job groups, UI scanner setup/live crawl/scan detail parallel view, Reports & audit, Evidence pack, Admin) against docs/design/mockups: 0 console errors, no horizontal scroll at 390 px.' },
]
async function s2() {
  await run(`${BASE}
${lane('m4')}

YOUR JOB (S2): finish merging main INTO m4 in ${WT.m4}. A merge is in progress (MERGE_HEAD): read the staged and unstaged state, resolve every remaining conflict keeping both sides (pyproject extras, dps/config.py, dps/posture.py, dps/app.py routers + lifespans of every milestone, docker-compose.yml with profiles docs/sources/oracle/scanner/tokenizer/full and every host port/image a variable, Dockerfile + Dockerfile.browser, tests/conftest.py + fixtures, frontend App/Layout/api/types with every page of M1-M5, contract, HANDOFF, README, deviation logs; migrations M3 020-029, M4 030-039, M5 040-059 with no duplicates), and commit the merge ("merge: main (M5 + M3) into m4 ahead of the M4 integration"). If main gained commits since the merge started, merge main again afterwards. Then adapt the scanner to main's newer behaviour, each with a failing test first: SEC-5 (main's resolve_secret_ref(tenant_id, ref, settings) for storage_state_ref; validation at ScanSpec accept; cross-tenant test), SEC-2 for scanner secrets/seeds, M5 DB-backed keys with scope scan on scans routes, M5 posture rules for the browser-worker role, M5 policy @name references in ScanSpec, main's discovery semantics in scanner classification. All gates green on this lane's stack (all markers incl. browser and "oracle or mongo"), D4 proofs passing; commit; docker compose down. Report to ${R}/S2-premerge.md .`, { label: 'S2 finish pre-merge + adapt', phase: 'M4 pre-merge' })
  return await panelLoop({ where: 'm4', tag: 'S2', phase: 'M4 pre-merge', commitScope: 'm4', title: 'verify branch m4 after main was merged into it', context: 'Branch m4 now contains main (M0-M3, M5) plus all of M4; it will be merged into main next.', profiles: 'profiles docs, sources, oracle, scanner (and tokenizer if defined)', markers: '"docs or ocr", "db or pg_source or s3", "oracle or mongo", browser', lenses: L_M4 })
}

// ------------------------------------------------------------------ S1: main
const L_M3 = [
  { key: 'engines', what: 'L2 on PostgreSQL, Oracle and MongoDB on the merged main: obfuscate jobs into targets for each engine and a synthesize job (where supported) with per-column source-vs-target counts (0 survivors), D-A identifier check (no real key or government id in synthetic targets, ObjectId/ROWID handling), the three-way consistency check (overlap > 0), resume-after-kill on Oracle and Mongo.' },
  { key: 'security', what: 'SEC-2 and SEC-3 for Oracle and MongoDB connections through the API (cross-tenant secret refs refused; non-admin endpoint changes refused; production gate), M3-B6 selection column re-mapping refused (PG alias column list, Oracle UNPIVOT/PIVOT), Mongo filter allow-list ($where, $function, $accumulator refused), audit rows value-free.' },
  { key: 'privacy-console', what: 'log scan of every main container for the demo values used and key material (incl. pymongo/oracledb loggers at DEBUG), dps_llm_raw_pii_sent_total 0 per tenant, and a private-Playwright walk of the console pages touched by M3 and M5 (wizard Oracle/Mongo, job groups, consistency panel, Admin, Reports, Policies, Evidence) vs mockups with 0 console errors.' },
]
async function s1M3() {
  const r = await panelLoop({ where: 'main', tag: 'S1-M3', phase: 'Main', commitScope: 'integrate', title: 'post-merge verification of main after merging M3 (b878c50)', context: 'main contains M0-M3 and M5; the M3 integrator already adapted the code and wrote HANDOFF/contract notes.', profiles: 'profiles docs, sources, oracle (and tokenizer if defined)', markers: '"docs or ocr", "db or pg_source or s3", "oracle or mongo"', lenses: L_M3 })
  if (r.verdict === 'PASS') {
    await run(`${BASE}
${lane('main')}

YOUR JOB: create the annotated tag on main for the verified M3 merge: git tag -a v0.4.0-m3 -m "M3: Oracle and MongoDB connectors, cross-system consistency, merged and verified on main" (on the current main HEAD), then run tools/dev/autopush.sh from ${MAIN}. Report to ${R}/S1-tag-m3.md .`, { label: 'tag v0.4.0-m3', phase: 'Main', effort: 'low' })
  }
  return r
}
async function s1M4(s2res) {
  await run(`${BASE}
${lane('main')}

YOUR JOB: merge branch m4 into main in ${MAIN}. Branch m4 already contains main as of the pre-merge (S2 result: ${JSON.stringify({ verdict: s2res.verdict, open: s2res.blocking })}); main may have gained a few commits since. git status must be clean (only ignored files): git merge --no-ff m4 -m "merge: M4 into main". Resolve conflicts keeping both sides; fix any S2 finding still open, test-first. Gates: fresh install of every extra, ruff, mypy, pytest -q, every integration marker on the main stack (profiles docs, sources, oracle, scanner, tokenizer), frontend test/build, smoke. Leave the stack running. Commit fixes as "fix(integrate): ...". Report to ${R}/S1-merge-m4.md .`, { label: 'merge m4 into main', phase: 'Main' })
  const r = await panelLoop({ where: 'main', tag: 'S1-M4', phase: 'Main', commitScope: 'integrate', title: 'post-merge verification of main after merging M4', context: 'main now contains every milestone M0-M5 (finish streams still to merge).', profiles: 'profiles docs, sources, oracle, scanner (and tokenizer if defined)', markers: '"docs or ocr", "db or pg_source or s3", "oracle or mongo", browser', lenses: L_M4 })
  if (r.verdict === 'PASS') {
    await run(`${BASE}
${lane('main')}

YOUR JOB: create the annotated tag on main: git tag -a v0.5.0-m4 -m "M4: UI scanner with the parallel view, merged and verified on main" (current HEAD), then run tools/dev/autopush.sh from ${MAIN}. Report to ${R}/S1-tag-m4.md .`, { label: 'tag v0.5.0-m4', phase: 'Main', effort: 'low' })
  }
  return r
}

// ------------------------------------------------------------------ finish streams (start now from current main)
function implThenReview(o) {
  return (async () => {
    await run(`${BASE}
${lane(o.where)}

YOUR JOB (${o.tag}): ${o.job}
Work test-first (failing test, implement, pass, gates, commit with the plan's commit message). Record deviations in docs/superpowers/build/M5-DEVIATIONS.md (section per task; mark it as finish-stage work). Report to ${R}/${o.tag}-impl.md .`, { label: o.tag + ' implement', phase: o.phase })
    let v = null
    for (let round = 1; round <= 3; round++) {
      v = await judge(`${BASE}
${lane(o.where)}

YOUR JOB (${o.tag}): independently review the work "${o.job.slice(0, 300)}..." on branch ${o.where} (round ${round}). Implementer report ${R}/${o.tag}-impl.md. Run the gates yourself (${o.gates}); check the plan tasks' acceptance and the programme plan §2.6 row; hunt for real defects (${o.hunt}). Do not modify files. Report to ${R}/${o.tag}-review-r${round}.md and return the structured verdict.`, { label: o.tag + ' review r' + round, phase: o.phase })
      if (v.verdict === 'PASS' || round === 3) break
      await run(`${BASE}
${lane(o.where)}

YOUR JOB (${o.tag}): fix the blocking review findings (round ${round}); review ${R}/${o.tag}-review-r${round}.md. Findings:
${JSON.stringify(v.blocking, null, 2)}
Failing test first; gates green; commit as "fix(${o.where}): ...". Report to ${R}/${o.tag}-fix-r${round}.md .`, { label: o.tag + ' fix r' + round, phase: o.phase })
    }
    log(o.tag + ': ' + v.verdict)
    return v
  })()
}

async function mergeInto(where, branch, why, phase) {
  await run(`${BASE}
${lane(where)}

YOUR JOB: merge branch ${branch} into branch ${where} in ${WT[where]} (git merge --no-ff ${branch} -m "merge: ${branch} into ${where} (${why})"), keeping both sides of every conflict; then make this stream's work consistent with what came in (${why}); all gates green on this stream's stack; commit; docker compose down. Report to ${R}/${where.toUpperCase()}-merge-${branch}.md .`, { label: where + ' <- ' + branch, phase })
}

const SEC_LENSES = [
  { key: 'auth-tenancy', what: 'authentication and route scopes (every /api/v1 route behind require_scope; M5 DB keys, rotation, no privilege/tenant escalation), tenancy and object-level access (every object route 404s for another tenant; RLS on every tenant table; cross-tenant secret refs).' },
  { key: 'injection', what: 'injection: SQL selections in PostgreSQL and Oracle dialects (validator bypasses, identifier quoting, column re-mapping), MongoDB filters and pipelines, policy-supplied regexes (ReDoS), CSV exports (formula injection), PDF generation (markup), HTML evidence (XSS), path handling for artefacts and exports.' },
  { key: 'ssrf-egress', what: 'SSRF and egress: tokenizer adapter, OCR client, Stellar/COIN and Vertex gateway, S3 endpoints, connection hosts, webhook-like URLs; TLS-only egress posture; metadata IP and numeric-host encodings; DNS rebinding.' },
  { key: 'secrets-logs', what: 'secrets and logs: settings repr, exception messages, 422 bodies echoing input, third-party loggers at DEBUG (pymongo, oracledb, httpx, botocore), metrics labels, audit rows, evidence packs, Cache-Control on API responses carrying key material, docs/OpenAPI exposure and CORS in production posture.' },
  { key: 'crypto-custody', what: 'crypto and key custody: D2 (no key material stored; idempotency cache value-free; job KEK sealing and rotation; key purge at terminal states), AES-SIV/FF1 usage and domain guard, HKDF info separation, SEC-4 external-token binding and detokenization audit, synthesis seeds, RSA wrap.' },
]

async function s3() {
  phase('Security')
  await run(`${BASE}
${lane('fsec')}

YOUR JOB (S3 setup): create and prepare this worktree as described, run ./lane.sh pytest -q -m "not integration" and npm run build to confirm it works. Report to ${R}/S3-setup.md .`, { label: 'S3 setup fsec', phase: 'Security', effort: 'medium' })
  const found = await parallel(SEC_LENSES.map((l) => () => run(`${BASE}
${lane('fsec')}

YOUR JOB (S3 security finder, lens "${l.key}"): find real, exploitable security defects in the code on branch fsec (= current main: M0-M3 + M5; the UI scanner M4 is reviewed separately later) within your lens: ${l.what}
Read the code, write probes under ${R}/S3-${l.key}/ (never in the repository), run them against this worktree's stack if needed (start only what you need). Do not modify repository files. Only report findings you have evidence for; for each give the file, a concrete exploit or failure scenario and the fix. Also check M5 plan Task 17's S1-S13 items that fall in your lens and report each one still unaddressed as a finding. Report to ${R}/S3-${l.key}.md and return the structured findings.`, { label: 'S3 finder ' + l.key, phase: 'Security', schema: FINDINGS })))
  const seen = new Set()
  const all = found.filter(Boolean).flatMap((f) => f.findings).filter((f) => { const k = f.file + '|' + f.title; if (seen.has(k)) return false; seen.add(k); return true })
  log('S3: ' + all.length + ' candidate findings')
  const judged = await parallel(all.map((f, i) => () => run(`${BASE}
${lane('fsec')}

YOUR JOB (S3 adversarial verifier ${i + 1}): try to REFUTE this security finding on branch fsec. Default to real=false when the evidence does not hold up; real=true only if you can reproduce or clearly demonstrate the problem in this code. Do not modify repository files; probes under ${R}/S3-verify-${i + 1}/.
FINDING: ${JSON.stringify(f)}`, { label: 'S3 refute ' + (i + 1), phase: 'Security', schema: REFUTE, effort: 'high' }).then((v) => ({ f, real: !!(v && v.real), reason: v ? v.reason : 'verifier died' }))))
  const confirmed = judged.filter((x) => x.real).map((x) => x.f)
  log('S3: ' + confirmed.length + ' confirmed findings')
  return await implThenReview({ where: 'fsec', tag: 'S3', phase: 'Security',
    job: `execute M5 plan Task 0 (the contract-surface test, adapted to the merged code: names that moved are updated, never dropped) and Task 17 (security review pass S1-S13 with the guard tests under tests/security/), and fix every confirmed finding below, each with a test that fails before the fix. Write docs/security/2026-10-11-m5-security-review.md listing every S-item and every confirmed finding with its fix and test, plus the refuted candidates in a short "considered and refuted" list. Confirmed findings: ${JSON.stringify(confirmed)}. Refuted candidates: ${JSON.stringify(judged.filter((x) => !x.real).map((x) => ({ title: x.f.title, file: x.f.file, reason: x.reason })))}`,
    gates: 'ruff, mypy, pytest -q, tests/security, the integration markers touched on this stream\'s stack, frontend build if touched',
    hunt: 'fixes that only hide a symptom, tests that do not exercise the exploit, regressions in tenancy or D2' })
}
async function s3Scanner(s2res) {
  await mergeInto('fsec', 'm4', 'bring in the UI scanner for its security review and T8B', 'Security')
  return await implThenReview({ where: 'fsec', tag: 'S3b', phase: 'Security',
    job: 'execute M5 plan Task 8B (T3 metadata labelling in L2 discovery and L3 scanner classification, value-free, opt-in) and the scanner part of Task 17 (S5 netguard: check_scan_url at POST /scans, Chromium --host-resolver-rules for registered hosts only; numeric host encodings, IPv4-mapped IPv6, metadata IPs, DNS resolving to link-local), plus a D4 re-check; extend docs/security/2026-10-11-m5-security-review.md',
    gates: 'ruff, mypy, pytest -q, tests/security, pytest -m browser and the scanner integration tests on this stream\'s stack',
    hunt: 'LLM prompts containing values, SSRF bypasses, D4 regressions' })
}

async function s4() {
  phase('Images')
  const a = await implThenReview({ where: 'fimg', tag: 'S4', phase: 'Images',
    job: 'create and prepare this worktree as described, then execute M5 plan Task 24 on the code as merged so far (images with EXTRAS build arg, arbitrary-UID and read-only-root safety, --platform linux/amd64 builds, compose full profile with tokenizer-mock and every role, the smoke M5 block, tools/demo_client.py exactly per the plan signature, tools/flow_report.py, .gitignore var/ and reports/). The browser-worker image and compose scanner services come from M4 later; design the full profile so they slot in.',
    gates: 'ruff, mypy, pytest -q, docker build of the images (amd64), docker compose --profile full up on this stream\'s ports with /readyz ready, smoke, flow_report',
    hunt: 'images running as root, writable root filesystem needs, secrets baked into images, smoke checks that cannot fail' })
  return a
}
async function s4Scanner() {
  await mergeInto('fimg', 'm4', 'bring in the browser-worker image and scanner compose services; the full profile and smoke must cover them', 'Images')
  return await implThenReview({ where: 'fimg', tag: 'S4b', phase: 'Images', job: 'complete Task 24 for the scanner: full profile includes browser-worker and demo-site, smoke covers a scan (POSTs received 0), images (app and browser) build for amd64 and run under an arbitrary UID with a read-only root filesystem',
    gates: 'image builds, compose --profile full up, smoke all checks, flow_report', hunt: 'missing scanner coverage in smoke, D4 regressions' })
}
async function s5() {
  phase('OpenShift')
  return await implThenReview({ where: 'fos', tag: 'S5', phase: 'OpenShift',
    job: 'create and prepare this worktree as described, then execute M5 plan Task 25 (OpenShift kustomize base, components for network policies and monitoring, dev and prod overlays, Wega layout, release placeholders listed in deploy/openshift/README.md, roles app, worker, browser-worker, migrate Job, dps-db ExternalName). Use kustomize (or kubectl kustomize) and kubeconform via Docker images if not installed; lessons: generator/component ordering, JSON6902 patches guarded by tests, warn-placeholder + --fail-on-warn release overlays, arbitrary UID, read-only root.',
    gates: 'kustomize build of dev and prod overlays, kubeconform -strict on the output, the deploy-marked tests',
    hunt: 'privileged pods, missing NetworkPolicies, secrets in ConfigMaps, placeholders that silently reach prod' })
}
async function s6Early() {
  phase('Demo')
  return await implThenReview({ where: 'fdemo', tag: 'S6a', phase: 'Demo',
    job: 'create and prepare this worktree as described, then execute M5 plan Task 26B as far as it does not need a real demo run: tools/build_deck.py with fixture inputs, tests/test_deck.py (12 slides, notes, no ppt/charts part, only Helvetica Neue/Arial, no invented numbers, placeholders for missing inputs), the pyproject dev-only deck extra; and draft docs/demo/DEMO-SCRIPT.md per Task 26 (spec §9 ten-minute story). The owner views decks in Keynote: draw charts as shapes.',
    gates: 'ruff, mypy, pytest -q incl. tests/test_deck.py', hunt: 'chart parts Keynote drops, invented figures, fonts Keynote lacks' })
}
async function s6Demo() {
  await mergeInto('fdemo', 'fimg', 'bring in Task 24 (demo_client, compose full, smoke) needed by the demo tools', 'Demo')
  await mergeInto('fdemo', 'm4', 'bring in the UI scanner for demo steps S9', 'Demo')
  return await implThenReview({ where: 'fdemo', tag: 'S6b', phase: 'Demo',
    job: 'execute M5 plan Task 26 (tools/demo_seed.py, tools/e2e_demo.py with --console and the JSON report, tests/console/test_console_pages.py, final docs/demo/DEMO-SCRIPT.md) and run the whole demo on this stream\'s full stack: demo_seed then e2e_demo --console, every step PASS in under 10:00, screenshots in reports/demo-screens; then build the deck from the real report and screenshots (tools/build_deck.py -> docs/demo/dps-showcase.pptx, committed) and QA it by exporting to PDF with Keynote via osascript and reading every page image (if Keynote cannot run headless, say so and rely on the structural test).',
    gates: 'ruff, mypy, pytest -q, compose --profile full up, demo_seed + e2e_demo --console all PASS, tests/test_deck.py', hunt: 'demo steps that skip their assertion, numbers on slides not in the report, values in reports' })
}
async function s7() {
  phase('Docs')
  return await implThenReview({ where: 'fdocs', tag: 'S7', phase: 'Docs',
    job: 'create and prepare this worktree as described, then execute M5 plan Task 27 tooling and drafts (tools/settings_table.py rendering the HANDOFF settings table from Settings.model_fields, tests/test_handoff.py, docs/technical/api-m5.md for the routes as built, CHANGELOG.md draft with one paragraph per milestone M0-M5 from the shipped code, README run/develop blocks, HANDOFF restructured to its final 13 sections as far as the merged code allows - the release step regenerates it) and the polish items of programme plan §2.6 F4 / RESUME §1.3: Policies page stale preset response after a quick switch, the stale demo group file, console_check overwriting tracked screenshots (write to reports/ instead), plus cheap non-blocking items from ${R}/*-review-*.md and *-verify-*.md; anything not fixed goes to CHANGELOG "Known limitations".',
    gates: 'ruff, mypy, pytest -q incl. tests/test_handoff.py, frontend test/build', hunt: 'docs that contradict the code, settings missing from the table' })
}

// ------------------------------------------------------------------ orchestration
const s2P = s2()
const s1P = (async () => {
  const m3 = await s1M3()
  const s2res = await s2P
  const m4 = await s1M4(s2res)
  return { m3, m4 }
})()
const s3P = (async () => { const a = await s3(); await s2P; const b = await s3Scanner(); return { a, b } })()
const s4P = (async () => { const a = await s4(); await s2P; const b = await s4Scanner(); return { a, b } })()
const s5P = s5()
const s6P = (async () => { const a = await s6Early(); await s4P; await s2P; const b = await s6Demo(); return { a, b } })()
const s7P = s7()

const [s1, s2r, s3r, s4r, s5r, s6r, s7r] = await parallel([() => s1P, () => s2P, () => s3P, () => s4P, () => s5P, () => s6P, () => s7P])

// ------------------------------------------------------------------ release
phase('Release')
await run(`${BASE}
${lane('main')}

YOUR JOB: merge the finish branches into main in ${MAIN}, in this order: fsec, fimg, fos, fdemo, fdocs (each git merge --no-ff <branch> -m "merge: <branch> into main"). Stream results: ${JSON.stringify({ s1: { m3: s1.m3 && s1.m3.verdict, m4: s1.m4 && s1.m4.verdict }, s3: s3r, s4: s4r, s5: s5r, s6: s6r, s7: s7r })}. Keep both sides of conflicts (HANDOFF, README, CHANGELOG, compose, pyproject, contract, deviation logs); fix any still-open finding from those results test-first. Then M5 plan Task 28 steps 1-2: version 1.0.0 (pyproject, dps/__init__.py, frontend package + lock via npm version 1.0.0 --no-git-tag-version), regenerate HANDOFF (tools/settings_table.py) to its final shape, docker compose --profile full down -v && docker compose --profile full up --build -d, poll /readyz, run the full M5 Definition of Done 1-7 (every marker with DPS_TEST_REQUIRE_SERVICES=1, smoke, flow report, demo_seed + e2e_demo --console), rebuild the deck from this final run, record each gate summary in HANDOFF "Last verified", date CHANGELOG, commit "chore(release): v1.0.0". Do NOT tag. Leave the full stack running. Run tools/dev/autopush.sh. Report to ${R}/release.md .`, { label: 'merge streams + release candidate', phase: 'Release' })

const FINAL_LENSES = [
  { key: 'l1-docs', what: 'Layer 1 and documents: detect/obfuscate/synthesize/reveal round trips incl. NDJSON and Idempotency-Key; redacted PDF 0 extractable characters and OCR re-read finds 0 originals; schema synthesis replay identical.' },
  { key: 'l2-engines', what: 'Layer 2 on PostgreSQL, Oracle and MongoDB: obfuscate jobs in every write mode with per-column source-vs-target counts (0 survivors), D3 typed confirmation enforced, consistency check overlap > 0, resume-after-kill.' },
  { key: 'l3-scanner', what: 'Layer 3: live scan of the demo site, parallel view both modes, masked captures OCR-checked, demo site /__received non-GET == 0, deny rail, SSRF netguard.' },
  { key: 'synthetic', what: 'synthetic: copula synthesize job fidelity >= 0.85, exact-row matches 0, NNDR measured, utility and MIA proxy reported, no real key or government id in synthetic targets (D-A identifier check).' },
  { key: 'security', what: 'security: tests/security green, SEC-2..SEC-5 probes, route-auth walk, cross-tenant 404s, ReDoS guard, external-token binding, posture refuses unsafe production configs.' },
  { key: 'privacy-ops', what: 'privacy operations: log scan of every container for the demo values used and key material, per-tenant dps_llm_raw_pii_sent_total 0, audit rows value-free, gitleaks over the whole history clean (with .gitleaksignore).' },
  { key: 'console', what: 'console: private-Playwright walk of every page vs docs/design/mockups 01-12, 0 console errors, no horizontal scroll at 390 px; screenshots to docs/screenshots/v1.0.0/ (commit them only if the whole panel passes - leave them uncommitted and say so).' },
  { key: 'deploy-demo', what: 'deploy and demo: kustomize dev+prod build and kubeconform clean, images amd64 + arbitrary UID + read-only root, e2e demo report all PASS under 10:00, deck test green and the deck present.' },
]
const fin = await panelLoop({ where: 'main', tag: 'FINAL', phase: 'Release', commitScope: 'release', title: 'final verification of the v1.0.0 candidate', context: 'main contains every milestone and finish stream; release report ' + R + '/release.md.', profiles: 'profile full', markers: '"docs or ocr", "db or pg_source or s3", "oracle or mongo", browser, deploy', extraGates: 'tools/flow_report.py, tools/demo_seed.py then tools/e2e_demo.py --console', lenses: FINAL_LENSES })

if (fin.verdict === 'PASS') {
  await run(`${BASE}
${lane('main')}

YOUR JOB: release v1.0.0 on main in ${MAIN}. The final verification panel passed. 1) Commit the v1.0.0 screenshots under docs/screenshots/v1.0.0/ if present (explicit pathspec). 2) git tag -a v1.0.0 -m "DPS v1.0.0: Privacy Engine, L1 API and documents, L2 connectors (PostgreSQL, Oracle, MongoDB), L3 UI scanner, tabular synthesis with measured privacy, hardening, OpenShift, demo". 3) Tag local images: docker tag data-privacy-services:local data-privacy-services:1.0.0 and the browser image likewise (check the names with docker images). 4) Update docs/superpowers/RESUME.md to its final state (programme complete, where everything is, how to run the demo) and the Claude memory file /Users/neelu/.claude/projects/-Users-neelu-document-intelligence/memory/data-privacy-services.md with a one-paragraph final status; commit RESUME.md. 5) Run tools/dev/autopush.sh and confirm main, every branch and every tag are on origin. 6) Remove the worktrees ${['m3', 'm4', 'm4ui', 'm5', 'm5ui', 'fsec', 'fimg', 'fos', 'fdemo', 'fdocs'].map((b) => ROOT + '/dps-wt-' + b).join(', ')} with git worktree remove after confirming each branch is merged into main (git merge-base --is-ancestor); stop their compose projects first (docker compose down -v from each); delete the merged local branches with git branch -d (remote branches stay). Report to ${R}/release-tag.md .`, { label: 'tag v1.0.0 + cleanup', phase: 'Release', effort: 'high' })
}

return {
  s1: { m3: s1.m3 && s1.m3.verdict, m4: s1.m4 && s1.m4.verdict }, s2: s2r && s2r.verdict, s3: s3r, s4: s4r, s5: s5r && s5r.verdict, s6: s6r, s7: s7r && s7r.verdict,
  final: { verdict: fin.verdict, blocking: fin.blocking },
}
