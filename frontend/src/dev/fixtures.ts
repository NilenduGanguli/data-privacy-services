/**
 * Typed fixtures for the M5 console pages (Overview, Reports & audit, Evidence pack, Admin,
 * Policies). Used by the Vitest page tests and by the dev server's fixture mode
 * (`npm run dev:fixtures`, src/dev/fixtureServer.ts) — never by a production build.
 *
 * Every value here is fictional display data in the shape of the mockups (docs/design/mockups):
 * key names, tenant names, counts, hashes and timestamps. There are no customer values; the only
 * "keys" are prefixes, and the one raw key the fake server returns on create/rotate is generated
 * from a fixed fictional alphabet at request time.
 */
import type {
  ApiKeyView, ApiRollup, AuditRow, Connection, EntityInfo, EvidenceItem, EvidenceOut, FailedSweep, Job, PolicyDoc,
  PostureReport, PresetView, QueueOut, Readiness, ReportsJobsOut, SummaryOut, SweepDay, TenantPolicyDetail, TenantView,
} from '../lib/types';

const DAY = 86_400_000;
const iso = (d: Date): string => d.toISOString();
const ago = (now: Date, ms: number): string => iso(new Date(now.getTime() - ms));
const isoDay = (d: Date): string => d.toISOString().slice(0, 10);
export const FIXTURE_TENANT = 'retail-banking-apac';

/** The last `n` UTC days ending today, oldest first. */
export function lastDays(now: Date, n: number): string[] {
  const out: string[] = [];
  for (let i = n - 1; i >= 0; i -= 1) out.push(isoDay(new Date(now.getTime() - i * DAY)));
  return out;
}

export function readiness(): Readiness {
  const c = (detail: string, ok = true): { ok: boolean; detail: string } => ({ ok, detail });
  return {
    ready: true,
    components: {
      'api-core': c('stateless · 3 replicas'), db: c('postgres 16 · migrations verified'), jobs: c('7 workers online · queue depth 23'),
      'llm-gateway': c('stellar · placeholders only'), ocr: c('azure read via gateway · tesseract fallback'), fpe: c('ff1 enabled'),
      ner: c('presidio en, es'), browser: c('2 browser workers'), tokenizer: c('bank tokenization service reachable'), keys: c('11 keys loaded'),
    },
  };
}

export const METRICS_TEXT = [
  '# HELP dps_requests_total Requests served', '# TYPE dps_requests_total counter',
  'dps_requests_total{route="/api/v1/obfuscate"} 1363200', 'dps_requests_total{route="/api/v1/synthesize"} 460800',
  'dps_requests_total{route="/api/v1/detect"} 96000', 'dps_findings_total{entity_type="EMAIL"} 934061',
  'dps_findings_total{entity_type="PHONE"} 819702', 'dps_llm_raw_pii_sent_total 0', '',
].join('\n');

// Mockup 01: protected values per day (millions), oldest first.
const OBF = [18.2, 12.6, 22.4, 8.9, 15.1, 9.8, 4.0, 4.2, 4.3, 5.4, 7.3, 29.5, 4.2, 28.1];
const SYN = [1.5, 6.2, 1.4, 9.7, 1.5, 1.3, 1.3, 1.4, 1.4, 1.4, 1.4, 1.4, 6.2, 15.7];

export function summary(now: Date, days = 14): SummaryOut {
  const dayList = lastDays(now, days);
  return {
    tenant_id: FIXTURE_TENANT, days, generated_at: iso(now),
    tiles: {
      values_protected: 45_791_546, values_protected_prev: 35_774_645, findings: 5_951_880, entity_types: 41, jobs_running: 4,
      jobs_completed: 23, jobs_failed: 0, snapshots_awaiting_review: 2, raw_pii_sent_to_llm: 0, llm_calls: 2_184, llm_blocked: 3,
    },
    trend: dayList.map((day, i) => {
      const k = (i + OBF.length - dayList.length + OBF.length * 4) % OBF.length;
      return { day, obfuscated: Math.round(OBF[k] * 1e6), synthesized: Math.round(SYN[k] * 1e6) };
    }),
    findings_by_type: [
      { entity_type: 'PAN_IN', findings: 1_412_204, sensitivity: 'CRITICAL' }, { entity_type: 'AADHAAR', findings: 1_028_117, sensitivity: 'CRITICAL' },
      { entity_type: 'EMAIL', findings: 934_061, sensitivity: 'HIGH' }, { entity_type: 'PHONE', findings: 819_702, sensitivity: 'HIGH' },
      { entity_type: 'PERSON_NAME', findings: 751_180, sensitivity: 'HIGH' }, { entity_type: 'DATE_OF_BIRTH', findings: 482_126, sensitivity: 'HIGH' },
      { entity_type: 'API_KEY', findings: 37, sensitivity: 'CRITICAL' },
    ],
    layers: {
      api: { requests: 1_920_000, by_mode: { obfuscate: 1_363_200, synthesize: 460_800, detect: 96_000 } },
      conn: { rows: 5_470_000, by_engine: { oracle: 3_446_100, postgres: 875_200, mongo: 1_148_700 }, groups: 1 },
      ui: { scans: 3, pages: 38, fields: 104, personal_fields: 81, risk_flags: 3, submissions: 0, last_scan: { id: 's_01K7ZRB2M4XQ', pages: 38, fields: 104, personas: 20 } },
    },
    posture: { sweeps_passed_pct: 100, key_held_by_caller_pct: 100, snapshots_reviewed_pct: 94, policies_latest_pct: 81, score: 93.75 },
    recent_jobs: [
      { id: 'j_01K8C2T5W9QX7Q', kind: 'run', mode: 'obfuscate', state: 'running', source: 'CRM.CUSTOMERS', target: 'in place', progress_pct: 41, note: 'in place · 41%' },
      { id: 'j_01K7ZQ3R8D5W9Q', kind: 'run', mode: 'obfuscate', state: 'running', source: 'CRM.CUSTOMERS', target: 'CRM_MASKED.CUSTOMERS', progress_pct: 72, note: '8 partitions' },
      { id: 'j_01K8B7N2K6P34K', kind: 'run', mode: 'synthesize', state: 'running', source: 'ledger_perf.transactions', target: 'ledger_perf.transactions', progress_pct: 18, note: '4 partitions' },
      { id: 's_01K7ZRB2M4XQ', kind: 'scan', mode: 'synthesize', state: 'running', source: 'onboarding-uat.bank.internal', target: null, progress_pct: 82, note: '31 of 38 pages' },
      { id: 'j_01K8A9H4D2F63C', kind: 'run', mode: 'synthesize', state: 'succeeded', source: 'kyc.documents_meta', target: 'files', progress_pct: 100, note: 'fidelity 0.97' },
      { id: 'j_01K6Y2V8E4R1ZT', kind: 'discover', mode: 'obfuscate', state: 'succeeded', source: 'LOANS.APPLICANTS', target: null, progress_pct: null, note: 'awaiting review' },
    ],
  };
}

// Mockup 09: 30 days of sweeps by layer (failures included in the totals), oldest first.
const SWEEPS: Record<string, number[]> = {
  conn: [28, 30, 96, 90, 84, 97, 95, 30, 32, 89, 103, 97, 91, 94, 25, 38, 80, 98, 78, 89, 100, 30, 25, 99, 84, 93, 97, 78, 29, 33],
  api: Array.from({ length: 30 }, () => 24),
  ui: [0, 0, 18, 0, 0, 0, 0, 0, 0, 9, 18, 18, 9, 18, 0, 0, 18, 0, 12, 18, 0, 0, 0, 12, 18, 12, 0, 18, 0, 0],
};
const FAILS: Record<string, Record<number, number>> = { conn: { 27: 2 }, api: { 21: 1 }, ui: {} };

export function reportsJobs(now: Date, days = 14): ReportsJobsOut {
  const dayList = lastDays(now, days);
  const sweeps: SweepDay[] = [];
  dayList.forEach((day, i) => {
    const k = 30 - days + i;
    for (const layer of ['conn', 'api', 'ui']) {
      const failed = FAILS[layer][k] ?? 0;
      sweeps.push({ day, layer, passed: SWEEPS[layer][k] - failed, failed });
    }
  });
  const passed = sweeps.reduce((a, s) => a + s.passed, 0);
  const failed = sweeps.reduce((a, s) => a + s.failed, 0);
  const failedSweeps: FailedSweep[] = [];
  if (days >= 3) failedSweeps.push({ where: 'job', day: dayList[dayList.length - 3], job_id: 'j_01K7F2W8Q4LMF2', partitions: [3, 4], route: null, error_code: 'leakage', paths: ['$.EMPLOYER_NAME'] });
  if (days >= 9) failedSweeps.push({ where: 'api', day: dayList[dayList.length - 9], job_id: null, partitions: [], route: 'POST /api/v1/synthesize', error_code: 'verification_failed', paths: [] });
  return {
    days, sweeps, failed_sweeps: failedSweeps,
    totals: { packs: 62, run_packs: 48, scan_packs: 9, api_rollups: 5, sweeps_passed: passed, sweeps_failed: failed, audit_rows: Math.round(24_611_402 * days / 14) },
  };
}

export function evidenceItems(now: Date): EvidenceItem[] {
  const e = (id: string, job: string, kind: string, outcome: string, h: number, layer: string, s: EvidenceItem['summary']): EvidenceItem =>
    ({ id, job_id: job, job_kind: kind, outcome, created_at: ago(now, h * 3_600_000), pack_sha256: `${id.replace(/[^0-9a-f]/gi, '').toLowerCase()}9f3c2a7b18d24931`.padEnd(64, '0'), layer, summary: s });
  return [
    e('ev_01K7ZQ9QA1', 'j_01K7ZQ3R8D5W9Q', 'run', 'succeeded', 5, 'conn', { mode: 'obfuscate', policy: 'gdpr-pseudonymise', policy_version: 3, ticket: 'CHG-48213', source: 'CRM.CUSTOMERS', target: 'CRM_MASKED.CUSTOMERS', write_mode: 'table', rows_written: 2_706_118, verification_passed: true, hits: 0 }),
    e('ev_01K7ZQS7B2', 'j_01K7ZQS7C9H2S7', 'run', 'succeeded', 7, 'conn', { mode: 'synthesize', policy: 'synthetic-realistic', policy_version: 1, ticket: 'CHG-48213', source: 'CRM.CUSTOMERS', target: 'CRM_SYNTH.CUSTOMERS', write_mode: 'table', rows_written: 500_000, verification_passed: true, hits: 0, fidelity_score: 0.96, exact_row_matches: 0 }),
    e('ev_01K7Y1A1C3', 'j_01K7Y1A2B8N91A', 'run', 'succeeded', 34, 'conn', { mode: 'synthesize', policy: 'synthetic-realistic', policy_version: 1, ticket: 'CHG-48177', source: 'ledger.transactions', target: 'files', write_mode: 'files', rows_written: 1_200_000, verification_passed: true, hits: 0, fidelity_score: 0.97, exact_row_matches: 0 }),
    e('ev_01K7X8HD44', 'j_01K7X8H6V2C48H', 'run', 'succeeded', 46, 'conn', { mode: 'obfuscate', policy: 'gdpr-pseudonymise', policy_version: 3, ticket: 'CHG-48213', source: 'ledger.transactions', target: 'ledger_uat.transactions', write_mode: 'table', rows_written: 4_821_193, verification_passed: true, hits: 0 }),
    e('ev_01K7F2E5D5', 'j_01K7F2W8Q4LMF2', 'run', 'failed', 47, 'conn', { mode: 'obfuscate', policy: 'gdpr-pseudonymise', policy_version: 3, ticket: 'CHG-48168', source: 'LOANS.APPLICANTS', target: 'files', write_mode: 'files', rows_written: 0, verification_passed: false, hits: 2 }),
    e('ev_01K7W4DF66', 'j_01K7W4D1T7Y54D', 'run', 'succeeded', 51, 'conn', { mode: 'obfuscate', policy: 'gdpr-pseudonymise', policy_version: 3, ticket: 'CHG-48213', source: 'crm.profiles', target: 'profiles_uat', write_mode: 'table', rows_written: 2_698_402, verification_passed: true, hits: 0 }),
    e('ev_01K7V6WA77', 'j_01K7V6W3P5K26W', 'run', 'succeeded', 66, 'conn', { mode: 'obfuscate', policy: 'pci-dss', policy_version: 2, ticket: 'CHG-48160', source: 'cards_uat.cardholders', target: 'cards_uat.cardholders', write_mode: 'in_place', rows_written: 611_240, verification_passed: true, hits: 0 }),
    e('ev_01K6MXSC88', 's_01K6MX4B7N2QMX', 'scan', 'succeeded', 170, 'ui', { mode: 'synthesize', policy: 'synthetic-realistic', policy_version: 1, ticket: 'CHG-48102', source: 'onboarding-uat.bank.internal', target: null, verification_passed: true, hits: 0, pages: 38, forms: 12, personas: 20, submissions: 0 }),
    e('ev_01K6KNON99', 'j_01K6KN0E4M3NON', 'run', 'succeeded', 190, 'conn', { mode: 'synthesize', policy: 'synthetic-strict', policy_version: 1, ticket: 'CHG-48090', source: 'hr.employees', target: 'hr_uat.employees', write_mode: 'table', rows_written: 12_400, verification_passed: true, hits: 0 }),
  ];
}

export function apiRollups(now: Date, days = 14): ApiRollup[] {
  const d = lastDays(now, Math.max(days, 1));
  const out: ApiRollup[] = [];
  if (d.length >= 2) out.push({ day: d[d.length - 2], action: 'obfuscate', calls: 1_862_404, findings: 5_204_118, values: 5_190_442, verify_failed: 0 });
  if (d.length >= 2) out.push({ day: d[d.length - 2], action: 'synthesize', calls: 431_207, findings: 902_114, values: 899_310, verify_failed: 0 });
  if (d.length >= 10) out.push({ day: d[d.length - 10], action: 'synthesize', calls: 211_930, findings: 610_002, values: 608_877, verify_failed: 1 });
  return out;
}

export function auditRows(now: Date): AuditRow[] {
  let id = 9_100_000;
  const r = (h: number, tenant: string, key: string | null, route: string, action: string, layer: string, mode: string | null, policy: string | null,
    outcome: string, status: number | null, findings: number | null, methods: Record<string, number>, job: string | null = null): AuditRow =>
    ({ id: (id -= 7), ts: ago(now, h * 60_000), tenant_id: tenant, key_id: key ? `k_${key.slice(0, 6)}` : null, key_name: key, trace_id: null, route, action, layer, mode, policy, outcome, status, job_id: job, findings, methods });
  return [
    r(9, FIXTURE_TENANT, 'kyc-docs-pipeline', 'POST /api/v1/reveal', 'reveal', 'api', 'reveal', null, 'ok', 200, 12, { fpe_digits: 6, fpe_text: 3, email: 1, phone: 1, date_shift: 1 }),
    r(10, FIXTURE_TENANT, 'kyc-docs-pipeline', 'POST /api/v1/obfuscate', 'obfuscate', 'api', 'obfuscate', 'gdpr-pseudonymise@v3', 'ok', 200, 12, { fpe_digits: 5, fpe_text: 2, email: 2, phone: 1, date_shift: 1, keep: 1 }),
    r(15, 'cards-in', 'cards-dispute-desk', 'POST /api/v1/obfuscate', 'obfuscate', 'api', 'obfuscate', 'pci-dss@v2', 'ok', 200, 3, { fpe_digits: 2, redact: 1 }),
    r(22, 'wealth-nri', 'wealth-nri-analytics', 'POST /api/v1/synthesize', 'synthesize', 'api', 'synthesize', 'synthetic-realistic@v1', 'ok', 200, 41, { surrogate: 39, keep: 1, bucket: 1 }),
    r(26, 'collections-in', 'platform-admin', 'POST /api/v1/admin/keys', 'admin.key.create', 'admin', null, null, 'ok', 201, null, {}),
    r(35, FIXTURE_TENANT, 'crm-uat-provisioner', 'job j_01K7ZQ3R8D5W9Q evidence written', 'job.evidence', 'conn', 'obfuscate', 'gdpr-pseudonymise@v3', 'ok', null, 24_311_870, { fpe_digits: 9, fpe_text: 6, email: 2, phone: 2, date_shift: 1, keep: 2, det_token: 2 }, 'j_01K7ZQ3R8D5W9Q'),
    r(39, FIXTURE_TENANT, 'worker w-06', 'job j_01K7ZQ3R8D5W9Q p6 lease reclaimed', 'job.partition', 'conn', 'obfuscate', 'gdpr-pseudonymise@v3', 'ok', null, null, {}, 'j_01K7ZQ3R8D5W9Q'),
    r(41, FIXTURE_TENANT, 'qa-onboarding-scanner', 'scan s_01K7ZRB2M4XQ form POST', 'scan.request', 'ui', 'synthesize', 'synthetic-realistic@v1', 'blocked', null, null, { surrogate: 1 }, 's_01K7ZRB2M4XQ'),
    r(42, FIXTURE_TENANT, 'crm-uat-provisioner', 'POST /api/v1/jobs/run', 'job.create', 'conn', 'obfuscate', 'gdpr-pseudonymise@v3', 'ok', 202, null, {}, 'j_01K8C2T5W9QX7Q'),
    r(43, FIXTURE_TENANT, 'crm-uat-provisioner', 'POST /api/v1/jobs/run', 'job.create', 'conn', 'obfuscate', 'gdpr-pseudonymise@v3', 'rejected', 422, null, {}),
    r(92, FIXTURE_TENANT, 'crm-uat-provisioner', 'POST /api/v1/snapshots/snap_01K7ZP4N2C/accept', 'snapshot.accept', 'conn', null, 'gdpr-pseudonymise@v3', 'ok', 200, 22, {}),
    r(104, '—', null, 'POST /api/v1/reveal', 'reveal', 'api', 'reveal', null, 'denied', 401, null, {}),
    r(127, FIXTURE_TENANT, 'crm-uat-provisioner', 'job j_01K7ZQS7C9H2S7 completed', 'job.run', 'conn', 'synthesize', 'synthetic-realistic@v1', 'ok', null, 22, { surrogate: 10, copula: 12 }, 'j_01K7ZQS7C9H2S7'),
    r(2_867, FIXTURE_TENANT, 'crm-uat-provisioner', 'job j_01K7F2W8Q4LMF2 leakage sweep', 'job.partition', 'conn', 'obfuscate', 'gdpr-pseudonymise@v3', 'leakage', null, 2, { fpe_text: 1, det_token: 1 }, 'j_01K7F2W8Q4LMF2'),
    r(3_100, FIXTURE_TENANT, 'kyc-docs-pipeline', 'POST /api/v1/detect', 'detect', 'api', 'detect', 'gdpr-pseudonymise@v3', 'ok', 200, 7, {}),
    r(3_300, 'cards-in', 'cards-dispute-desk', 'POST /api/v1/reveal', 'reveal', 'api', 'reveal', null, 'forbidden', 403, null, {}),
    r(4_000, FIXTURE_TENANT, 'platform-admin', 'PATCH /api/v1/admin/tenants/cards-in', 'admin.tenant.update', 'admin', null, null, 'ok', 200, null, {}),
  ];
}

export function apiKeys(now: Date): ApiKeyView[] {
  const k = (id: string, name: string, prefix: string, tenants: string[], scopes: string[], createdDays: number, lastUsedMs: number | null,
    expiresDays: number | null, extra: Partial<ApiKeyView> = {}): ApiKeyView => ({
    id, name, key_prefix: prefix, tenant_ids: tenants, scopes, source: 'db', status: 'active', created_by: 'platform-admin',
    created_at: ago(now, createdDays * DAY), expires_at: expiresDays === null ? null : iso(new Date(now.getTime() + expiresDays * DAY)),
    last_used_at: lastUsedMs === null ? null : ago(now, lastUsedMs), disabled_at: null, rotated_from: null, rotated_at: null, age_days: createdDays, ...extra,
  });
  return [
    k('k_platform', 'platform-admin', 'dps_9Fq2', ['*'], ['admin'], 12, 2_000, 19),
    k('k_kycdocs', 'kyc-docs-pipeline', 'dps_k7Hq', [FIXTURE_TENANT], ['detect', 'obfuscate', 'synthesize'], 29, 12_000, 152),
    k('k_crmuat', 'crm-uat-provisioner', 'dps_Xm41', [FIXTURE_TENANT], ['detect', 'obfuscate', 'jobs'], 72, 240_000, 112),
    k('k_qascan', 'qa-onboarding-scanner', 'dps_c2Rv', [FIXTURE_TENANT], ['scan', 'synthesize'], 13, DAY + 3_600_000, 78),
    k('k_cardsdd', 'cards-dispute-desk', 'dps_pW8n', ['cards-in'], ['detect', 'obfuscate', 'reveal'], 53, 7_200_000, 39),
    k('k_collvr', 'collections-voice-redaction', 'dps_Tz5s', ['collections-in'], ['detect', 'obfuscate'], 0, null, 90),
    k('k_wealth', 'wealth-nri-analytics', 'dps_e3Lk', ['wealth-nri'], ['detect', 'synthesize', 'jobs'], 64, 3 * DAY, 84),
    k('k_ledger', 'ledger-recon-batch', 'dps_Q0vb', [FIXTURE_TENANT, 'cards-in'], ['detect', 'obfuscate', 'jobs'], 113, 7_200_000, null),
    k('k_hrattr', 'hr-attrition-model', 'dps_m6Yd', ['hr-people-analytics'], ['synthesize'], 25, 6 * DAY, 66),
    k('k_legacy', 'legacy-etl-poc', 'dps_a1Nn', [FIXTURE_TENANT], ['detect', 'obfuscate'], 215, 41 * DAY, 150, { status: 'disabled', disabled_at: ago(now, 41 * DAY) }),
    { ...k('static:ops', 'ops-bootstrap', 'dps_s7Op', ['*'], ['detect'], 0, 3_600_000, null), source: 'static', created_at: null, created_by: null, age_days: null },
  ];
}

export function tenants(now: Date): TenantView[] {
  const t = (id: string, name: string, running: number, maxRunning: number, rows: number, quota: number | null, keys: Record<string, number>, hosts: string[], updatedDays: number): TenantView => ({
    tenant_id: id, display_name: name, note: null, overrides: { max_running: maxRunning, daily_row_quota: quota },
    effective: { tenant_id: id, retention_days: 30, max_running: maxRunning, max_queued: 200, daily_row_quota: quota, registered_hosts: hosts },
    usage: { running, queued: 0, rows_today: rows }, keys, exists: true, created_at: ago(now, 120 * DAY), updated_at: ago(now, updatedDays * DAY),
  });
  return [
    t(FIXTURE_TENANT, 'Retail Banking Technology', 4, 6, 5_470_000, 50_000_000, { active: 4, disabled: 1 }, ['onboarding-uat.bank.internal', 'netbanking-sit.bank.internal', 'loans-origination-uat.bank.internal'], 4),
    t('cards-in', 'Cards & Payments', 2, 4, 17_200_000, 20_000_000, { active: 2 }, ['cards-apply-uat.bank.internal'], 7),
    t('collections-in', 'Collections Operations', 0, 2, 0, 10_000_000, { active: 1 }, [], 10),
    t('wealth-nri', 'Wealth & NRI Services', 0, 2, 0, 5_000_000, { active: 1 }, [], 20),
    t('hr-people-analytics', 'HR Analytics · internal', 0, 1, 120_000, 1_000_000, { active: 1 }, [], 30),
  ];
}

export function posture(now: Date): PostureReport {
  const c = (id: string, name: string, ok: boolean, detail: string, boot = false): PostureReport['checks'][number] => ({ id, name, ok, detail, enforced_at_boot: boot });
  const checks = [
    // ids, names and the boot flags as dps/posture.py CHECKS builds them (failing first in the view)
    c('auth_enabled', 'API authentication is on', true, 'DPS_AUTH_ENABLED=true · every /api/v1 call needs X-API-KEY', true),
    c('bootstrap_key_absent', 'No bootstrap API key', true, 'DPS_BOOTSTRAP_API_KEY is unset', true),
    c('api_keys_from_file', 'API keys from the database or a mounted file', true, 'database keys · hashes only', true),
    c('tls_egress', 'TLS-only egress with the organisation CA bundle', true, 'https only · org CA bundle DPS_SSL_CERT_FILE', true),
    c('audit_strict', 'Audit on and strict', true, 'append-only, partitioned · a failed write fails the request', true),
    c('leakage_strict', 'Strict leakage verification everywhere', true, 'leakage_check: strict in every active policy', true),
    c('llm_not_stub', 'A real (or no) LLM provider', true, 'stellar · placeholder text only', true),
    c('kek_from_secret', 'Job KEK from a mounted secret (32 bytes)', true, 'DPS_JOB_KEK_FILE · 32 bytes', true),
    c('keys_rotated', 'API keys rotated within the maximum age', false, 'ledger-recon-batch is 113 days old', false),
    c('rls_posture', 'Row-level security on every tenant table', true, 'FORCE + tenant_isolation', true),
    c('migrations_verified', 'Database migrations verified', true, '44 applied · checksums match'),
  ];
  return { env: 'production', enforced: true, passed: checks.filter((x) => x.ok).length, total: checks.length, checked_at: ago(now, 120_000), checks };
}

export function queue(now: Date): QueueOut {
  const w = (n: number, role: string, busy: boolean, draining = false): QueueOut['workers'][number] =>
    ({ worker_id: `w-${String(n).padStart(2, '0')}`, role, version: '0.5.0', online: true, busy: busy ? 2 : 0, draining, last_seen: ago(now, 4_000) });
  return {
    queues: [{ table: 'dps_job', state: 'running', n: 5, oldest_s: 41 }, { table: 'dps_job_partition', state: 'queued', n: 23, oldest_s: 41 }],
    workers: [w(1, 'worker', true), w(2, 'worker', true), w(3, 'worker', true), w(4, 'worker', true), w(5, 'worker', true), w(6, 'worker', true),
      w(7, 'worker', false, true), w(8, 'browser-worker', true), w(9, 'browser-worker', false)],
  };
}

export function connections(now: Date): Connection[] {
  const conn = (id: string, name: string, engine: Connection['engine'], params: Connection['params'], secretRef: string | null, tags: string[],
    roles: Connection['roles'], check: string): Connection => ({
    id, tenant_id: FIXTURE_TENANT, name, engine, params, secret_ref: secretRef, has_secret: true, secret_expires_at: null, roles, tags, capabilities: {},
    last_test: { ok: true, engine, server_version: engine === 'oracle' ? '19c' : engine === 'postgres' ? '16.4' : engine === 'mongo' ? '7.0' : null,
      checks: [{ name: check, ok: true }], capabilities: {}, error_code: null },
    created_by: 'platform-admin', created_at: ago(now, 40 * DAY), updated_at: ago(now, 3 * 3_600_000),
  });
  return [
    conn('c_crmprod', 'crm-prod-ro', 'oracle', { host: 'crm-db.bank.internal', service_name: 'CRM' }, `${FIXTURE_TENANT}/dps/src/crm-prod-ro`, ['production', 'read-only'], ['source'], 'SELECT ok'),
    conn('c_loansprod', 'loans-prod', 'oracle', { host: 'loans-db.bank.internal', service_name: 'LOANS' }, `${FIXTURE_TENANT}/dps/src/loans-prod`, ['production'], ['source', 'target'], 'UPDATE ok'),
    conn('c_ledgerprod', 'ledger-prod', 'postgres', { host: 'ledger-db.bank.internal', database: 'ledger' }, `${FIXTURE_TENANT}/dps/src/ledger-prod`, ['production'], ['source', 'target'], 'INSERT ok'),
    conn('c_crmuatclone', 'crm-uat-clone', 'oracle', { host: 'crm-uat.bank.internal', service_name: 'CRMUAT' }, `${FIXTURE_TENANT}/dps/src/crm-uat-clone-rw`, ['uat'], ['source', 'target'], 'UPDATE ok'),
    conn('c_crmuat', 'crm-uat', 'oracle', { host: 'crm-uat.bank.internal', service_name: 'CRMUAT' }, `${FIXTURE_TENANT}/dps/tgt/crm-uat`, ['uat'], ['target'], 'CREATE, INSERT ok'),
    conn('c_profilesuat', 'profiles-uat', 'mongo', { host: 'profiles-uat.bank.internal', database: 'profiles' }, `${FIXTURE_TENANT}/dps/tgt/profiles-uat`, ['uat'], ['target'], 'insert ok'),
    conn('c_exports', 'exports-aps1', 's3', { bucket: 'dps-exports-aps1', prefix: '', region: 'ap-south-1' }, `${FIXTURE_TENANT}/dps/tgt/exports-aps1`, ['files'], ['target'], 'PUT ok'),
    { ...conn('c_scanonb', 'onboarding-uat-session', 'pvc', { base_path: '/var/dps/ui' }, `${FIXTURE_TENANT}/dps/ui/onboarding-uat`, ['ui-scanner', 'host:onboarding-uat.bank.internal'], ['source'], 'read ok') },
  ];
}

// ---- Evidence packs (mockup 10): an obfuscate pack, a copula synthesize pack, a synthesize pack
// without tabular synthesis.
function packBase(now: Date, id: string, jobId: string, mode: 'obfuscate' | 'synthesize'): EvidenceOut['pack'] {
  return {
    pack_version: 'dps.evidence.v1', id, tenant_id: FIXTURE_TENANT, job_id: jobId, job_kind: 'run', group_id: null, outcome: 'succeeded', mode,
    label: mode === 'synthesize' ? 'synthetic — measured, not claimed anonymous' : 'pseudonymised — still personal data', created_at: ago(now, 7 * 3_600_000),
    engine_version: '0.5.0', image_digest: null, purpose: { ticket: 'CHG-48213', legal_basis: 'Legitimate interest — UAT provisioning' }, requested_by: 'crm-uat-provisioner',
    source: { engine: 'oracle', connection_name: 'crm-prod-ro', object: 'CRM.CUSTOMERS', server_version: 'Oracle 19c', selection: { where_template: 'CREATED_AT >= :p1', binds: 1 } },
    target: { engine: 'oracle', connection_name: 'crm-uat', object: mode === 'synthesize' ? 'CRM_SYNTH.CUSTOMERS' : 'CRM_MASKED.CUSTOMERS', write_mode: 'table', table_load: 'truncate_insert', created: true },
    policy: { name: mode === 'synthesize' ? 'synthetic-realistic' : 'gdpr-pseudonymise', hash: mode === 'synthesize' ? '7c152f805644305ded5ad65e2b01e46f15444b7c7c8d14578660f11b5ec47bd6' : '18d2493121bae95d38598115b80269bd6c86088a59562dd9b7d0b34b1c5e3a66', version: mode === 'synthesize' ? '1' : '3' },
    snapshot: { id: 'snap_01K7ZP4N2C', content_hash: '5d1e0c7a9b2f43e18c6d7a0b9e4f2c1d3a5b7c9e0f1a2b3c4d5e6f7a8b9c0d1e', accepted_by: 'crm-uat-provisioner', accepted_at: ago(now, 8 * 3_600_000) },
    consistency: mode === 'synthesize' ? { scope: 'key', seed_fingerprint: 'sf_9b41c2', key_custody: { returned: true, wrapped: true } } : { scope: 'key', kid: 'k_01K7ZQ4M8V', key_custody: { returned: true, wrapped: false } },
    columns: mode === 'synthesize'
      ? [
        { path: '$.FULL_NAME', entity_type: 'PERSON_NAME', method: 'surrogate', params: {}, reversible: false, values: 500_000, data_type: 'VARCHAR2(120)', confidence: 0.98, override: false },
        { path: '$.PAN_NO', entity_type: 'PAN_IN', method: 'surrogate', params: {}, reversible: false, values: 500_000, data_type: 'CHAR(10)', confidence: 0.99, override: false },
        { path: '$.EMAIL', entity_type: 'EMAIL', method: 'surrogate', params: {}, reversible: false, values: 499_812, data_type: 'VARCHAR2(254)', confidence: 0.99, override: false },
        { path: '$.RM_NOTES', entity_type: 'FREE_TEXT', method: 'surrogate', params: {}, reversible: false, values: 211_402, data_type: 'CLOB', confidence: 0.81, override: true, text_column: true },
      ]
      : [
        { path: '$.CUSTOMER_ID', entity_type: 'CUSTOMER_ID', method: 'fpe_digits', params: {}, reversible: true, values: 2_706_118, data_type: 'NUMBER(12)', confidence: 1, override: false },
        { path: '$.FULL_NAME', entity_type: 'PERSON_NAME', method: 'fpe_text', params: {}, reversible: true, values: 2_706_118, data_type: 'VARCHAR2(120)', confidence: 0.97, override: false },
        { path: '$.PAN_NO', entity_type: 'PAN_IN', method: 'fpe_text', params: {}, reversible: true, values: 2_699_310, data_type: 'CHAR(10)', confidence: 0.99, override: false },
        { path: '$.EMAIL', entity_type: 'EMAIL', method: 'email', params: { domain: 'keep' }, reversible: true, values: 2_604_881, data_type: 'VARCHAR2(254)', confidence: 0.99, override: false },
        { path: '$.RISK_SCORE', entity_type: 'BUSINESS_SENSITIVE', method: 'bucket', params: { ranges: [0, 300, 600, 900] }, reversible: false, values: 2_706_118, data_type: 'NUMBER(4)', confidence: 1, override: true },
      ],
    reveal_map: [],
    counts: mode === 'synthesize'
      ? { rows_read: 2_706_118, rows_written: 500_000, rows_skipped: 0, values_transformed: 1_711_214, batches: 100, resumed_partitions: 0 }
      : { rows_read: 2_706_118, rows_written: 2_706_118, rows_skipped: 0, values_transformed: 13_429_235, batches: 542, resumed_partitions: 1 },
    verification: { passed: true, hits: 0, values_checked: mode === 'synthesize' ? 1_711_214 : 13_429_235, batches: mode === 'synthesize' ? 100 : 542, mode: 'strict', post_sample: { rows: 1000, kept_column_hits: 0, detector_hits: 12 } },
    timings: { started_at: ago(now, 8 * 3_600_000), finished_at: ago(now, 7 * 3_600_000 + 60_000), duration_s: 1521, rows_per_s: 1780 },
    partitions: Array.from({ length: 8 }, (_, i) => ({ no: i + 1, rows: mode === 'synthesize' ? 62_500 : 338_265, attempts: i === 5 ? 2 : 1, resumed: i === 5 ? 1 : 0, state: 'done' })),
    errors: [], config_hash: 'a3f9e1c27b5d4086f1e2d3c4b5a6978877665544332211009988776655443322', scan: null,
  };
}

export function evidencePacks(now: Date): Record<string, EvidenceOut> {
  const art = (id: string): EvidenceOut['artefacts'] => [{ id: `a_${id}_json`, kind: 'evidence_json', bytes: 18_204, content_type: 'application/json' }, { id: `a_${id}_html`, kind: 'evidence_html', bytes: 40_118, content_type: 'text/html' }];
  const obf = packBase(now, 'ev_01K7ZQ9QA1', 'j_01K7ZQ3R8D5W9Q', 'obfuscate');
  const syn = packBase(now, 'ev_01K7ZQS7B2', 'j_01K7ZQS7C9H2S7', 'synthesize');
  const cols = ['RISK_SCORE', 'CREDIT_LIMIT', 'AVG_BALANCE_90D', 'TENURE_MONTHS', 'PRODUCT_COUNT', 'CREATED_AT', 'SEGMENT', 'KYC_STATUS', 'CHANNEL', 'BRANCH_STATE'];
  const dist = [0.021, 0.034, 0.047, 0.018, 0.015, 0.012, 0.009, 0.004, 0.011, 0.038];
  const nulls = [0, 0.001, 0, 0, 0, 0, 0, 0, -0.002, 0];
  syn.fidelity = {
    ks: {}, tvd: {}, null_rate_delta: {},
    columns: cols.map((c, i) => ({ path: `$.${c}`, kind: i < 6 ? 'numeric' : 'categorical', metric: i < 6 ? 'ks' : 'tvd', distance: dist[i], null_rate_delta: nulls[i], within_limit: true })),
    corr_delta: 0.019, corr_max: 0.058, corr_within_limit: true,
    corr_matrix: {
      columns: ['$.RISK_SCORE', '$.CREDIT_LIMIT', '$.AVG_BALANCE_90D', '$.TENURE_MONTHS', '$.PRODUCT_COUNT', '$.CREATED_AT'],
      delta: [[0, 0.024, 0.031, 0.012, 0.008, 0.006], [0.024, 0, 0.058, 0.017, 0.021, 0.009], [0.031, 0.058, 0, 0.026, 0.019, 0.011],
        [0.012, 0.017, 0.026, 0, 0.014, 0.022], [0.008, 0.021, 0.019, 0.014, 0, 0.007], [0.006, 0.009, 0.011, 0.022, 0.007, 0]],
    },
    limits: { column: 0.05, corr: 0.1 }, score: 0.96, engine: 'copula',
    measured_on: { real_rows: 20_000, synthetic_rows: 20_000, holdout_rows: 2_000, basis: 'deterministic preview of the rows this job writes' },
  };
  syn.privacy = {
    enforced: true, engine: 'copula', measured: true, exact_row_matches: 0, exact_row_resamples: 3, exact_row_resamples_run: 41,
    nndr: { ratio: 1.04, synth_to_real_median: 0.412, real_to_real_median: 0.398, synth_rows: 2000, real_rows: 2000, holdout: 1, zero_distance_share: 0, measured: 1, target: 1, passed: true },
    nndr_enforced: false, qi_uniqueness: { columns: ['$.AGE_BAND', '$.PIN_PREFIX', '$.GENDER', '$.SEGMENT'], source: 0.029, synthetic: 0.008, unique_overlap: 0 },
    membership_inference: { run: true, method: 'dcr_auc', auc: 0.52, members: 2000, non_members: 2000, target: 0.6, passed: true },
  };
  syn.utility = { run: true, model: 'knn5', metric: 'accuracy', train_on_real: 0.81, train_on_synthetic: 0.77, ratio: 0.9506, test_rows: 2000, label: 'SEGMENT', task: 'classification' };
  syn.tabular = {
    engine: 'copula', columns: cols.map((c) => `$.${c}`), excluded: [{ path: '$.ACCOUNT_OPENED_BRANCH', reason: 'high cardinality: 2,418 categories' }],
    constraints: [{ kind: 'range', path: '$.RISK_SCORE' }, { kind: 'positive', path: '$.CREDIT_LIMIT' }], fit_rows: 18_000, holdout_rows: 2_000, preview_rows: 20_000,
  };
  const none = packBase(now, 'ev_01K6KNON99', 'j_01K6KN0E4M3NON', 'synthesize');
  none.policy = { name: 'synthetic-strict', hash: '8dd3d85d5754e7442bb8aff11913a81f77a0a34b20dd8182aca0636e84c4e88f', version: '1' };
  none.privacy = { engine: 'none', measured: false, reason: 'options.tabular=none: non-sensitive columns were copied from the source' };
  const out: Record<string, EvidenceOut> = {};
  for (const pack of [obf, syn, none]) {
    out[pack.id] = { id: pack.id, job_id: pack.job_id, created_at: pack.created_at, pack, pack_sha256: `${pack.id.slice(3).toLowerCase()}c0ffee18d2493121bae95d38598115b80269bd6c86088a59562dd9`.slice(0, 64).padEnd(64, '0'), artefacts: art(pack.id) };
  }
  return out;
}

export function evidenceJob(now: Date, jobId: string): Job | null {
  if (!jobId.startsWith('j_')) return null;
  return {
    id: jobId, tenant_id: FIXTURE_TENANT, kind: 'run', state: 'succeeded', phase: 'finalize', mode: 'obfuscate', group_id: null, connection_id: 'c_crmprod',
    target_connection_id: 'c_crmuat', object_ref: 'CRM.CUSTOMERS', snapshot_id: 'snap_01K7ZP4N2C', spec: {}, purpose: { ticket: 'CHG-48213' }, kid: null,
    progress: { outcome: 'succeeded', stage: '', rows_estimated: null, rows_read: 0, rows_written: 0, rows_skipped: 0, partitions_total: 8, partitions_done: 8, partitions_failed: 0, rows_per_second: null, throughput: [], throughput_mark: null, values_by_method: {}, verification: {}, snapshot_ids: [], evidence_id: null, pages: null, fields: null, blocked_requests: null },
    plan: null, error_code: null, error: null, created_by: 'crm-uat-provisioner', created_at: ago(now, 8 * 3_600_000), updated_at: ago(now, 7 * 3_600_000),
    started_at: ago(now, 8 * 3_600_000), finished_at: ago(now, 7 * 3_600_000), expires_at: iso(new Date(now.getTime() + 23 * DAY)),
  };
}

// ---- Policies (mockup 05).
export const ENTITIES: EntityInfo[] = [
  ['SSN', 'national_id', 'CRITICAL', ['US']], ['AADHAAR', 'national_id', 'CRITICAL', ['IN']], ['PAN_IN', 'national_id', 'CRITICAL', ['IN']],
  ['NINO', 'national_id', 'CRITICAL', ['UK']], ['PASSPORT', 'national_id', 'CRITICAL', []], ['CARD_PAN', 'financial', 'CRITICAL', []],
  ['CVV', 'financial', 'CRITICAL', []], ['IBAN', 'financial', 'HIGH', []], ['ACCOUNT_NUMBER', 'financial', 'HIGH', []],
  ['PERSON_NAME', 'identity', 'HIGH', []], ['EMAIL', 'contact', 'HIGH', []], ['PHONE', 'contact', 'HIGH', []], ['DATE_OF_BIRTH', 'identity', 'HIGH', []],
  ['STREET_ADDRESS', 'location', 'HIGH', []], ['POSTAL_CODE', 'location', 'MEDIUM', []], ['CUSTOMER_ID', 'business', 'HIGH', []],
  ['EMPLOYEE_ID', 'business', 'MEDIUM', []], ['ORG_NAME', 'business', 'LOW', []], ['BUSINESS_SENSITIVE', 'business', 'MEDIUM', []],
  ['API_KEY', 'secret', 'CRITICAL', []], ['GENDER', 'demographic', 'LOW', []], ['CITY', 'location', 'LOW', []],
].map(([name, family, sensitivity, j]) => ({ name: name as string, family: family as string, sensitivity: sensitivity as EntityInfo['sensitivity'], description: '', jurisdictions: j as string[], builtin: true }));

const m = (method: string, params: Record<string, unknown> = {}): { method: string; params: Record<string, unknown> } => ({ method, params });

/** The shipped gdpr-pseudonymise body (dps/engine/presets/gdpr-pseudonymise.yaml, abridged) as v1. */
export function gdprBody(version: number): PolicyDoc {
  return {
    name: 'gdpr-pseudonymise', version, description: 'Reversible pseudonymisation that keeps formats and joins. Output remains personal data (GDPR Art. 4(5)).',
    locale: { default: null, by_country_field: null },
    detect: { tiers: ['structural', 'patterns', 'ner'], min_score: 0.55, custom_recognizers: [], declared_fields: [] },
    treat: {
      default_sensitive: m('det_token'), SSN: m('fpe_digits', { keep_suffix: 4 }), AADHAAR: m('fpe_digits', { keep_suffix: 4 }), PAN_IN: m('fpe_text'),
      NINO: m('fpe_text'), PASSPORT: m('fpe_text'), CARD_PAN: m('fpe_digits', { keep_prefix: 6, keep_suffix: 4 }), CVV: m('det_token'),
      IBAN: m('fpe_text'), ACCOUNT_NUMBER: m('fpe_digits'), PHONE: m('phone', { keep_country_code: true }), EMAIL: m('email', { domain: 'keep' }),
      PERSON_NAME: m('fpe_text'), DATE_OF_BIRTH: m('date_shift', { window_days: 365 }), POSTAL_CODE: m('fpe_digits'), CUSTOMER_ID: m('fpe_digits'),
      EMPLOYEE_ID: m('fpe_text'), ORG_NAME: m('keep'), API_KEY: m('redact'), GENDER: m('keep'), CITY: m('keep'),
    },
    synthesize: { default: m('surrogate'), overrides: {}, keep_non_sensitive: true, llm_rewrite_text: false },
    consistency: { scope: 'key', field_scoped: false }, output: { token_style: 'natural' }, verify: { leakage_check: 'strict' },
  };
}

function withChanges(base: PolicyDoc, version: number, f: (b: PolicyDoc) => void): PolicyDoc {
  const next = JSON.parse(JSON.stringify(base)) as PolicyDoc;
  next.version = version;
  f(next);
  return next;
}

export function policyVersions(): PolicyDoc[] {
  const v1 = gdprBody(1);
  const v2 = withChanges(v1, 2, (b) => {
    b.detect.declared_fields = [{ path: 'CUSTOMER_ID', entity_type: 'CUSTOMER_ID' }, { path: '$.loan.internal_score', entity_type: 'BUSINESS_SENSITIVE' }, { path: 'LOANS.APPLICANTS.CIBIL_SCORE', entity_type: 'BUSINESS_SENSITIVE' }];
    b.treat.BUSINESS_SENSITIVE = m('bucket', { ranges: [0, 300, 600, 900] });
  });
  const v3 = withChanges(v2, 3, (b) => {
    b.detect.min_score = 0.6;
    b.detect.declared_fields = [...b.detect.declared_fields, { path: 'CRM.CUSTOMERS.RISK_SCORE', entity_type: 'BUSINESS_SENSITIVE' }];
    b.treat.POSTAL_CODE = m('generalize', { keep_chars: 3 });
    b.treat.CVV = m('redact');
  });
  return [v1, v2, v3];
}

export function gdprDraft(): PolicyDoc {
  const v3 = policyVersions()[2];
  return withChanges(v3, 4, (b) => {
    b.detect.custom_recognizers = [{ name: 'employee-id', supported_entity: 'EMPLOYEE_ID', patterns: [{ name: 'employee-id', regex: '\\bEMP-\\d{6}\\b', score: 0.9 }], context: ['staff id', 'employee'], deny_list: [], allow_list: ['EMP-000000'] }];
    b.detect.declared_fields = [...b.detect.declared_fields, { path: 'LOANS.APPLICANTS.EMPLOYER_NAME', entity_type: 'ORG_NAME' }];
    b.treat.ORG_NAME = m('det_token');
  });
}

export const PRESET_HASHES: Record<string, string> = {
  'gdpr-pseudonymise': 'e8fe058a6b1c4d7e9f0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4ea1fb',
  'pci-dss': 'eab8ad7310faa2d7b75f54badc755031a67f64b092c4492c30fd728a346e3567',
  'dpdp-pseudonymise': 'aca7e3078bb444860d9369d1d78cd0181335231d985250a343e41adb523ebbdb',
  'analytics-safe': '3c0b1e4f42ee5138112374bd2f2757bcd2923b09e5485f48206e6b223e6dda0b',
  'synthetic-realistic': '7c152f805644305ded5ad65e2b01e46f15444b7c7c8d14578660f11b5ec47bd6',
  'synthetic-strict': '8dd3d85d5754e7442bb8aff11913a81f77a0a34b20dd8182aca0636e84c4e88f',
};
export const VERSION_HASHES = ['e8fe058a6b1c4d7e9f0a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4ea1fb', '7275ea41c3b2a1908f7e6d5c4b3a29180f1e2d3c4b5a69788796a5b4c3d21a73', '18d2493121bae95d38598115b80269bd6c86088a59562dd9b7d0b34b1c5e3a66'];

export function presetViews(): PresetView[] {
  const p = (name: string, mode: PresetView['mode'], reversible: boolean, jobs: number, description: string): PresetView =>
    ({ name, version: 1, hash: PRESET_HASHES[name], description, mode, reversible, jobs });
  return [
    p('gdpr-pseudonymise', 'obfuscate', true, 3, 'Reversible pseudonymisation that keeps formats and joins.'),
    p('pci-dss', 'obfuscate', true, 6, 'Card data: PAN format-preserving, CVV removed.'),
    p('dpdp-pseudonymise', 'obfuscate', true, 4, "India DPDP Act: Aadhaar and PAN pseudonymised."),
    p('analytics-safe', 'obfuscate', false, 2, 'One-way generalisation for analytics.'),
    p('synthetic-realistic', 'synthesize', false, 3, 'Realistic synthetic personas.'),
    p('synthetic-strict', 'synthesize', false, 1, 'Synthetic output with strict privacy gates.'),
  ];
}

export function gdprDetail(now: Date): TenantPolicyDetail {
  const v3 = policyVersions()[2];
  const draft = gdprDraft();
  return {
    name: 'gdpr-pseudonymise', base_preset: 'gdpr-pseudonymise', active_version: 3, active_hash: VERSION_HASHES[2], draft: true, draft_changes: 3,
    published_at: ago(now, 11 * DAY), published_by: 'platform-admin', jobs: 14,
    active_body: v3 as unknown as Record<string, unknown>, draft_body: draft as unknown as Record<string, unknown>, draft_by: 'platform-admin', draft_updated_at: ago(now, 2 * 3_600_000),
    versions: [
      { version: 3, hash: VERSION_HASHES[2], change_ticket: 'CHG-47950', note: 'PIN codes generalised; CVV redacted', published_by: 'platform-admin', published_at: ago(now, 11 * DAY), jobs_pinned: 11 },
      { version: 2, hash: VERSION_HASHES[1], change_ticket: 'CHG-47102', note: 'Declared business-sensitive fields', published_by: 'crm-uat-provisioner', published_at: ago(now, 31 * DAY), jobs_pinned: 3 },
      { version: 1, hash: VERSION_HASHES[0], change_ticket: null, note: 'shipped preset', published_by: null, published_at: ago(now, 70 * DAY), jobs_pinned: 0 },
    ],
  };
}
