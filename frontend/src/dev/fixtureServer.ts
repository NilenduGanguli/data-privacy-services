/**
 * Dev-only fixture mode for the console (`npm run dev:fixtures`, i.e. `VITE_DPS_FIXTURES=1 vite`).
 *
 * `installFixtureServer()` wraps `window.fetch` so the console's API calls (`/api/v1/…`, `/readyz`,
 * `/metrics`) are answered from the typed fixtures in ./fixtures.ts, with in-memory state so the
 * Admin and Policies flows (create, rotate, disable, publish…) can be walked without a backend.
 * main.tsx imports this module only behind `import.meta.env.DEV`, so a production build contains
 * none of it (checked after every build: the bundle has no `dps-fixture-server` marker).
 *
 * Scenarios (`?fixture=<name>` on any page): `degraded` (no metadata database: reports 503),
 * `noadmin` (the key lacks the admin scope: admin routes and policy writes 403), `nostore` (an
 * older backend without the policy store: `/policies` 404), `empty` (a new tenant: no packs, no
 * audit rows, no jobs).
 */
import type {
  ApiKeyView, AuditRow, CreatedKey, EvidenceItem, EvidenceOut, PolicyChange, PolicyDoc, PolicyVersionView, PresetView,
  RecognizerSpec, TenantPolicyDetail, TenantPolicyView, TenantView,
} from '../lib/types';
import { toYaml } from '../lib/yaml';
import * as fx from './fixtures';

export const FIXTURE_MARKER = 'dps-fixture-server';
const DAY = 86_400_000;

interface StoredPolicy { detail: TenantPolicyDetail; bodies: Record<number, PolicyDoc> }
export interface FixtureState {
  now: Date; keys: ApiKeyView[]; tenants: TenantView[]; audit: AuditRow[]; packs: Record<string, EvidenceOut>; items: EvidenceItem[];
  policies: Map<string, StoredPolicy>; presets: PresetView[]; seq: number;
}

export function createFixtureState(now: Date = new Date()): FixtureState {
  const versions = fx.policyVersions();
  const gdpr = fx.gdprDetail(now);
  gdpr.draft_body = fx.gdprDraft() as unknown as Record<string, unknown>;
  return {
    now, keys: fx.apiKeys(now), tenants: fx.tenants(now), audit: fx.auditRows(now), packs: fx.evidencePacks(now), items: fx.evidenceItems(now),
    policies: new Map([['gdpr-pseudonymise', { detail: gdpr, bodies: { 1: versions[0], 2: versions[1], 3: versions[2] } }]]),
    presets: fx.presetViews(), seq: 0,
  };
}

// ---- helpers ------------------------------------------------------------------------------------

function respond(status: number, body: unknown, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json', ...headers } });
}
const refuse = (status: number, code: string, detail: string): Response => respond(status, { detail, code });
const file = (body: string, type: string, name: string): Response =>
  new Response(body, { status: 200, headers: { 'content-type': type, 'content-disposition': `attachment; filename="${name}"` } });
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

const isMapping = (v: unknown): v is Record<string, unknown> => !!v && typeof v === 'object' && !Array.isArray(v);
const byCodepoint = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

/** The backend's `flatten_policy` (M5 plan Task 15, full code there), reproduced exactly so the
 *  fixture diff has the real shape: dotted leaves in sorted key order; a non-empty list whose items
 *  are all mappings is keyed per item by its `name`, else its `path`, else its index
 *  (`declared_fields[LOANS.APPLICANTS.EMPLOYER_NAME].entity_type`); other lists are leaves; an
 *  empty mapping yields nothing. Python's `or` treats '', 0, null and false as missing. */
export function flatten(body: Record<string, unknown>, prefix = ''): Record<string, unknown> {
  const out: Record<string, unknown> = {};
  for (const key of Object.keys(body).sort(byCodepoint)) {
    const value = body[key];
    const path = prefix ? `${prefix}.${key}` : key;
    if (isMapping(value)) Object.assign(out, flatten(value, path));
    else if (Array.isArray(value) && value.length && value.every(isMapping)) {
      value.forEach((item, index) => {
        const label = (item.name || item.path || index) as string | number;
        Object.assign(out, flatten(item, `${path}[${String(label)}]`));
      });
    } else out[path] = value;
  }
  return out;
}

/** The backend's `diff_policies`: sorted paths, top-level `name` and `version` ignored. */
export function diffBodies(before: unknown, after: unknown): PolicyChange[] {
  const a = flatten(isMapping(before) ? before : {});
  const b = flatten(isMapping(after) ? after : {});
  const changes: PolicyChange[] = [];
  for (const path of [...new Set([...Object.keys(a), ...Object.keys(b)])].sort(byCodepoint)) {
    if (path === 'name' || path === 'version') continue;
    const x = a[path];
    const y = b[path];
    if (!(path in a)) changes.push({ op: 'add', path, after: y });
    else if (!(path in b)) changes.push({ op: 'remove', path, before: x });
    else if (JSON.stringify(x) !== JSON.stringify(y)) changes.push({ op: 'change', path, before: x, after: y });
  }
  return changes;
}

function rawKey(): string {
  const alphabet = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
  const bytes = new Uint8Array(32);
  globalThis.crypto.getRandomValues(bytes);
  return `dps_${Array.from(bytes, (b) => alphabet[b % alphabet.length]).join('')}`;
}

function normalizeHost(value: string): string | null {
  const v = value.trim().toLowerCase().replace(/^[a-z]+:\/\//, '').split(/[/?#]/)[0].replace(/:\d+$/, '');
  return /^[a-z0-9]([a-z0-9-]*[a-z0-9])?(\.[a-z0-9]([a-z0-9-]*[a-z0-9])?)+$/.test(v) ? v : null;
}

function tenantView(s: FixtureState, id: string): TenantView | undefined { return s.tenants.find((t) => t.tenant_id === id); }

function policyView(d: TenantPolicyDetail): TenantPolicyView {
  const { name, base_preset, active_version, active_hash, draft, draft_changes, published_at, published_by, jobs } = d;
  return { name, base_preset, active_version, active_hash, draft, draft_changes, published_at, published_by, jobs };
}

function presetBody(name: string): PolicyDoc {
  const body = fx.gdprBody(1);
  body.name = name;
  return body;
}

function fakeHash(seed: string): string {
  let h = 2166136261;
  let out = '';
  for (let round = 0; out.length < 64; round += 1) {
    for (const ch of `${seed}:${round}`) h = Math.imul(h ^ ch.charCodeAt(0), 16777619) >>> 0;
    out += h.toString(16).padStart(8, '0');
  }
  return out.slice(0, 64);
}

// ---- the plan preview and the recognizer test (approximations of the engine for dev only) -------

const KEY_ENTITY: Record<string, string> = {
  customer_id: 'CUSTOMER_ID', full_name: 'PERSON_NAME', dob: 'DATE_OF_BIRTH', in_pan: 'PAN_IN', uk_nino: 'NINO', us_ssn: 'SSN', email: 'EMAIL',
  pan: 'CARD_PAN', cvv: 'CVV', branch_city: 'CITY', phone: 'PHONE',
};
const REVERSIBLE = new Set(['det_token', 'fpe_digits', 'fpe_text', 'email', 'phone', 'date_shift', 'external_tokenize']);

function leaves(value: unknown, path: string, out: [string, unknown][]): void {
  if (Array.isArray(value)) value.forEach((v, i) => leaves(v, `${path}[${i}]`, out));
  else if (value && typeof value === 'object') for (const [k, v] of Object.entries(value)) leaves(v, `${path}.${k}`, out);
  else out.push([path, value]);
}

function planFor(data: unknown, policy: unknown, fpeOn: boolean): unknown[] {
  const body = (policy && typeof policy === 'object' ? policy : presetBody(String(policy ?? 'gdpr-pseudonymise'))) as PolicyDoc;
  const declared = new Map((body.detect?.declared_fields ?? []).map((d) => [d.path, d.entity_type]));
  const rows: unknown[] = [];
  const all: [string, unknown][] = [];
  leaves(data, '$', all);
  for (const [path, value] of all) {
    const key = path.replace(/\[\d+\]/g, '').split('.').pop() ?? '';
    const decl = declared.get(path) ?? declared.get(path.replace(/^\$\./, '$.'));
    let entity = decl ?? KEY_ENTITY[key];
    let span: [number, number] | null = null;
    if (!entity && typeof value === 'string') {
      for (const rec of body.detect?.custom_recognizers ?? []) {
        const rx = safeRegex(rec.patterns[0]?.regex ?? '');
        const hit = rx?.exec(value);
        if (hit && !(rec.allow_list ?? []).includes(hit[0])) { entity = rec.supported_entity; span = [hit.index, hit.index + hit[0].length]; break; }
      }
    }
    if (!entity) continue;
    const spec = body.treat?.[entity] ?? body.treat?.default_sensitive ?? { method: 'det_token', params: {} };
    let method = spec.method;
    let reason = decl ? `declared:${path}` : `policy:${entity}`;
    if (method.startsWith('fpe_') && !fpeOn) { method = 'det_token'; reason += '|fallback:fpe_disabled'; }
    rows.push({ path, span, entity_type: entity, score: decl ? 1 : 0.85 + (entity.length % 10) / 100, method, params: spec.params ?? {}, reason, reversible: REVERSIBLE.has(method) });
  }
  return rows;
}

function safeRegex(pattern: string): RegExp | null {
  try { return new RegExp(pattern.replace(/\(\?P</g, '(?<'), 'g'); } catch { return null; }
}

function testRecognizer(rec: RecognizerSpec, text: string): Response {
  const pattern = rec.patterns?.[0];
  if (!pattern?.regex) return refuse(422, 'invalid_recognizer', 'patterns: at least one pattern is required');
  if (/\((?:[^()]*[+*])\)[+*]/.test(pattern.regex)) return refuse(422, 'unsafe_pattern', 'Nested quantifiers are not allowed: the pattern could take exponential time.');
  const rx = safeRegex(pattern.regex);
  if (!rx) return refuse(422, 'invalid_recognizer', 'This pattern does not compile: check for an unclosed group or a stray backslash.');
  const matches: { start: number; end: number; entity_type: string; score: number; evidence: string; context: boolean }[] = [];
  const allow = new Set(rec.allow_list ?? []);
  for (let mt = rx.exec(text); mt !== null; mt = rx.exec(text)) {
    if (mt[0] === '') { rx.lastIndex += 1; continue; }
    if (allow.has(mt[0])) continue;
    const before = text.slice(Math.max(0, mt.index - 40), mt.index).toLowerCase();
    const context = (rec.context ?? []).some((w) => w && before.includes(w.toLowerCase()));
    matches.push({ start: mt.index, end: mt.index + mt[0].length, entity_type: rec.supported_entity, score: Math.min(1, pattern.score + (context ? 0.05 : 0)), evidence: `custom-pattern:${pattern.name}`, context });
  }
  for (const word of rec.deny_list ?? []) {
    for (let i = text.indexOf(word); word && i >= 0; i = text.indexOf(word, i + word.length)) {
      matches.push({ start: i, end: i + word.length, entity_type: rec.supported_entity, score: 1, evidence: `custom-deny:${rec.name}`, context: false });
    }
  }
  matches.sort((a, b) => a.start - b.start);
  return respond(200, { matches, count: matches.length, yaml: toYaml({ recognizers: [rec] }) });
}

// ---- the router ---------------------------------------------------------------------------------

function parseBody(body: BodyInit | null | undefined): Record<string, any> {
  if (typeof body !== 'string' || !body) return {};
  try { return JSON.parse(body) as Record<string, any>; } catch { return {}; }
}

/** Answer one request from the fixtures, or `null` to let it through to the network. */
export function route(s: FixtureState, method: string, url: URL, rawBody: BodyInit | null | undefined, scenario: string): Response | null {
  const p = url.pathname;
  const q = url.searchParams;
  const body = parseBody(rawBody);
  const empty = scenario === 'empty';
  const noAdmin = scenario === 'noadmin';
  const days = Math.min(90, Math.max(1, Number(q.get('days') ?? 14) || 14));

  if (p === '/readyz') return respond(200, fx.readiness());
  if (p === '/health') return respond(200, { status: 'ok' });
  if (p === '/metrics') return new Response(fx.METRICS_TEXT, { headers: { 'content-type': 'text/plain' } });
  if (!p.startsWith('/api/v1/')) return null;
  const parts = p.slice('/api/v1/'.length).split('/').map(decodeURIComponent);

  // reports
  if (parts[0] === 'reports') {
    if (scenario === 'degraded') return refuse(503, 'db_unavailable', 'the metadata database is not configured');
    if (parts[1] === 'summary') {
      const sum = fx.summary(s.now, days);
      if (empty) { sum.recent_jobs = []; sum.findings_by_type = []; }
      return respond(200, sum);
    }
    if (parts[1] === 'jobs') return respond(200, fx.reportsJobs(s.now, Math.min(days, 30)));
    if (parts[1] === 'api-rollups' && parts.length === 2) return respond(200, { items: empty ? [] : fx.apiRollups(s.now, days) });
    if (parts[1] === 'api-rollups' && parts[2]) {
      const action = q.get('action') ?? 'obfuscate';
      const fmt = q.get('format') ?? 'json';
      const doc = fx.apiRollups(s.now, 30).find((r) => r.day === parts[2] && r.action === action);
      if (!doc) return refuse(404, 'rollup_not_found', 'no roll-up for that day and action');
      return fmt === 'pdf' ? file('%PDF-1.4\n% DPS fixture API roll-up\n', 'application/pdf', `api-rollup-${doc.day}-${action}.pdf`)
        : file(JSON.stringify({ ...doc, generated_at: s.now.toISOString() }, null, 2), 'application/json', `api-rollup-${doc.day}-${action}.json`);
    }
    if (parts[1] === 'evidence') {
      const since = s.now.getTime() - Math.min(90, Number(q.get('days') ?? 30) || 30) * DAY;
      const needle = (q.get('q') ?? '').toLowerCase();
      const outcome = q.get('outcome');
      let items = (empty ? [] : s.items).filter((i) => new Date(i.created_at).getTime() >= since)
        .filter((i) => !q.get('layer') || i.layer === q.get('layer'))
        .filter((i) => !outcome || (outcome === 'ok' ? ['succeeded', 'dry_run'] : ['failed', 'canceled']).includes(i.outcome))
        .filter((i) => !needle || [i.job_id, i.summary.ticket, i.summary.policy, i.summary.source].some((v) => String(v ?? '').toLowerCase().includes(needle)));
      const limit = Number(q.get('limit') ?? 50);
      const start = Number(q.get('cursor') ?? 0);
      const next = start + limit < items.length ? String(start + limit) : null;
      items = items.slice(start, start + limit);
      return respond(200, { items, next_cursor: next });
    }
  }

  // admin
  if (parts[0] === 'admin') {
    if (noAdmin) return refuse(403, 'forbidden', 'this key lacks the admin scope');
    if (parts[1] === 'keys' && parts.length === 2 && method === 'GET') return respond(200, { items: s.keys, next_cursor: null });
    if (parts[1] === 'keys' && parts.length === 2 && method === 'POST') {
      const name = String(body.name ?? '');
      if (!/^[a-z0-9][a-z0-9-]{2,62}$/.test(name)) return refuse(422, 'invalid_name', 'use 3–63 lowercase letters, digits and dashes');
      if (s.keys.some((k) => k.name === name)) return refuse(409, 'key_name_exists', 'a key with this name exists');
      const exp = Number(body.expires_in_days);
      if (!(exp >= 1 && exp <= 365)) return refuse(422, 'invalid_expiry', 'expiry must be 1 to 365 days');
      if (!Array.isArray(body.scopes) || body.scopes.length === 0) return refuse(422, 'invalid_scopes', 'choose at least one scope');
      const raw = rawKey();
      s.seq += 1;
      const record: ApiKeyView = { id: `k_new${s.seq}`, name, key_prefix: raw.slice(0, 8), tenant_ids: body.tenant_ids, scopes: body.scopes, source: 'db', status: 'active',
        created_by: 'platform-admin', created_at: new Date().toISOString(), expires_at: new Date(Date.now() + exp * DAY).toISOString(), last_used_at: null,
        disabled_at: null, rotated_from: null, rotated_at: null, age_days: 0 };
      s.keys = [record, ...s.keys];
      s.audit = [{ id: (s.audit[0]?.id ?? 0) + 1, ts: new Date().toISOString(), tenant_id: body.tenant_ids?.[0] ?? '*', key_id: 'k_platform', key_name: 'platform-admin', trace_id: null, route: 'POST /api/v1/admin/keys', action: 'admin.key.create', layer: 'admin', mode: null, policy: null, outcome: 'ok', status: 201, job_id: null, findings: null, methods: {} }, ...s.audit];
      const out: CreatedKey = { key: raw, record };
      return respond(201, out, { 'cache-control': 'no-store' });
    }
    if (parts[1] === 'keys' && parts[2]) {
      const key = s.keys.find((k) => k.id === parts[2]);
      if (!key) return refuse(404, 'key_not_found', 'no such key');
      if (key.source === 'static') return refuse(409, 'static_key', 'environment keys are managed in their secret file');
      if (parts[3] === 'disable') { Object.assign(key, { status: 'disabled', disabled_at: new Date().toISOString() }); return respond(200, key); }
      if (parts[3] === 'enable') { Object.assign(key, { status: 'active', disabled_at: null }); return respond(200, key); }
      if (parts[3] === 'rotate') {
        const grace = Number(body.grace_hours ?? 24);
        if (!(grace >= 0 && grace <= 168)) return refuse(422, 'invalid_grace', 'grace must be 0 to 168 hours');
        const raw = rawKey();
        s.seq += 1;
        const fresh: ApiKeyView = { ...clone(key), id: `k_rot${s.seq}`, key_prefix: raw.slice(0, 8), created_at: new Date().toISOString(), last_used_at: null, rotated_from: key.id,
          rotated_at: null, age_days: 0, expires_at: key.expires_at ?? new Date(Date.now() + 90 * DAY).toISOString() };
        Object.assign(key, { rotated_at: new Date().toISOString(), expires_at: new Date(Date.now() + grace * 3_600_000).toISOString(), status: grace === 0 ? 'expired' : 'active' });
        s.keys = [fresh, ...s.keys];
        return respond(201, { new: { key: raw, record: fresh }, old: key }, { 'cache-control': 'no-store' });
      }
    }
    if (parts[1] === 'tenants' && parts.length === 2 && method === 'GET') return respond(200, { items: s.tenants, next_cursor: null });
    if (parts[1] === 'tenants' && (method === 'POST' || method === 'PATCH')) {
      const hosts: string[] = [];
      for (const h of (body.registered_hosts ?? []) as string[]) {
        const n = normalizeHost(h);
        if (!n) return refuse(422, 'invalid_host', 'a registered host is a DNS name such as host.bank.internal');
        if (!hosts.includes(n)) hosts.push(n);
      }
      if (method === 'POST') {
        const id = String(body.tenant_id ?? '');
        if (!/^[a-z0-9][a-z0-9_-]{0,62}$/.test(id)) return refuse(422, 'invalid_tenant', 'tenant ids are lowercase letters, digits, dash and underscore');
        if (tenantView(s, id)) return refuse(409, 'tenant_exists', 'the tenant exists');
        const t: TenantView = { tenant_id: id, display_name: body.display_name ?? null, note: body.note ?? null, overrides: {},
          effective: { tenant_id: id, retention_days: body.retention_days ?? 30, max_running: body.max_running ?? 2, max_queued: body.max_queued ?? 200, daily_row_quota: body.daily_row_quota ?? null, registered_hosts: hosts },
          usage: { running: 0, queued: 0, rows_today: 0 }, keys: {}, exists: true, created_at: new Date().toISOString(), updated_at: new Date().toISOString() };
        s.tenants = [...s.tenants, t];
        return respond(201, t);
      }
      const t = tenantView(s, parts[2]);
      if (!t) return refuse(404, 'tenant_not_found', 'no such tenant');
      if ('display_name' in body) t.display_name = body.display_name;
      if ('note' in body) t.note = body.note;
      for (const f of ['retention_days', 'max_running', 'max_queued', 'daily_row_quota'] as const) {
        if (f in body) { (t.effective as unknown as Record<string, unknown>)[f] = body[f]; t.overrides[f] = body[f]; }
      }
      if ('registered_hosts' in body) t.effective.registered_hosts = hosts;
      t.updated_at = new Date().toISOString();
      return respond(200, t);
    }
    if (parts[1] === 'audit') {
      const tenantsParam = q.get('tenants');
      const outcome = q.get('outcome');
      const needle = (q.get('q') ?? '').toLowerCase();
      const groups: Record<string, string[]> = { ok: ['ok'], failed: ['error', 'failed', 'verification_failed', 'leakage', 'dead', 'upstream_error'], blocked: ['rejected', 'bad_request', 'denied', 'blocked', 'forbidden', 'canceled'] };
      const rows = (empty ? [] : s.audit)
        .filter((r) => tenantsParam === '*' || r.tenant_id === fx.FIXTURE_TENANT)
        .filter((r) => !q.get('layer') || r.layer === q.get('layer'))
        .filter((r) => !outcome || (groups[outcome] ?? []).includes(r.outcome))
        .filter((r) => !needle || [r.route, r.key_name, r.job_id, r.policy].some((v) => String(v ?? '').toLowerCase().includes(needle)));
      if (q.get('format') === 'csv') {
        const head = 'ts,tenant_id,key_name,route,action,layer,mode,policy,outcome,status,job_id,findings';
        const lines = rows.map((r) => [r.ts, r.tenant_id, r.key_name, r.route, r.action, r.layer, r.mode, r.policy, r.outcome, r.status, r.job_id, r.findings].map((v) => `"${String(v ?? '').replace(/"/g, '""')}"`).join(','));
        return file(`${[head, ...lines].join('\n')}\n`, 'text/csv', `dps-audit-${fx.lastDays(s.now, 14)[0]}-${fx.lastDays(s.now, 1)[0]}.csv`);
      }
      const limit = Math.min(200, Number(q.get('limit') ?? 50) || 50);
      const after = q.get('cursor');
      const start = after ? rows.findIndex((r) => String(r.id) === after) + 1 : 0;
      const page = rows.slice(start, start + limit);
      return respond(200, { items: page, next_cursor: start + limit < rows.length ? String(page[page.length - 1].id) : null });
    }
    if (parts[1] === 'queue') return respond(200, fx.queue(s.now));
    if (parts[1] === 'posture') {
      const report = fx.posture(s.now);
      const ledger = s.keys.find((k) => k.id === 'k_ledger');
      if (ledger?.rotated_at || ledger?.status === 'disabled') {
        report.checks[0] = { ...report.checks[0], ok: true, detail: 'every active key is younger than 90 days or rotated' };
        report.passed = report.checks.filter((c) => c.ok).length;
      }
      return respond(200, report);
    }
    if (parts[1] === 'retention' && method === 'POST') return respond(202, { job: { ...fx.evidenceJob(s.now, 'j_retention_fixture'), kind: 'retention', tenant_id: '_system', state: 'queued' } });
  }

  // M2 connections, evidence, artefacts, jobs, snapshots (the subset the M5 pages read)
  if (parts[0] === 'connections' && parts.length === 1 && method === 'GET') return respond(200, { items: empty ? [] : fx.connections(s.now), next_cursor: null });
  if (parts[0] === 'evidence' && parts[1]) {
    const ev = s.packs[parts[1]];
    if (!ev) return refuse(404, 'evidence_not_found', 'no such evidence pack');
    if (parts[2] === 'pdf') return file('%PDF-1.4\n% DPS fixture evidence pack\n', 'application/pdf', `evidence-${ev.id}.pdf`);
    return respond(200, ev);
  }
  if (parts[0] === 'evidence') return respond(200, { items: [], next_cursor: null });
  if (parts[0] === 'artefacts' && parts[1]) {
    const ev = Object.values(s.packs).find((e) => e.artefacts.some((a) => a.id === parts[1]));
    if (!ev) return refuse(404, 'artefact_not_found', 'no such artefact');
    return file(JSON.stringify(ev.pack, null, 2), 'application/json', `${parts[1]}.json`);
  }
  if (parts[0] === 'jobs' && parts[1] === 'stats') return respond(200, { workers_online: 7, workers_busy: 6, workers_draining: 1, partitions_waiting: 23, oldest_waiting_s: 41, rows_per_s: 8120, by_state: { running: 4 }, running: 4, tenant_max_running: 6, lease_reclaims_24h: 1 });
  if (parts[0] === 'jobs' && parts[1]) {
    const job = fx.evidenceJob(s.now, parts[1]);
    return job ? respond(200, job) : refuse(404, 'job_not_found', 'no such job');
  }
  if (parts[0] === 'jobs') return respond(200, { items: [], next_cursor: null });
  if (parts[0] === 'snapshots') return refuse(404, 'snapshot_not_found', 'no such snapshot');

  // M1 entities and policy tooling, M5 policy store
  if (parts[0] === 'entities') return respond(200, { items: fx.ENTITIES, families: [...new Set(fx.ENTITIES.map((e) => e.family))] });
  if (parts[0] === 'policies') {
    const fpeOn = true;
    if (parts[1] === 'presets') return respond(200, s.presets.map(({ name, description, version, hash, mode, reversible }) => ({ name, description, version, hash, mode, reversible })));
    if (parts[1] === 'validate') return respond(200, presetBody(typeof body.policy === 'string' ? body.policy : 'gdpr-pseudonymise'));
    if (parts[1] === 'plan') return respond(200, { findings: [], plan: planFor(body.data, body.policy, fpeOn), record_sensitivity: 'CRITICAL', trace_id: 'fixture' });
    if (parts[1] === 'test-recognizer') return testRecognizer(body.recognizer as RecognizerSpec, String(body.text ?? ''));
    if (scenario === 'nostore') return refuse(404, 'not_found', 'Not Found');
    if (parts.length === 1 && method === 'GET') return respond(200, { presets: s.presets, tenant: [...s.policies.values()].map((x) => policyView(x.detail)) });
    if (parts.length === 1 && method === 'POST') {
      if (noAdmin) return refuse(403, 'forbidden', 'this key lacks the admin scope');
      const name = String(body.name ?? '');
      const base = s.presets.find((x) => x.name === body.base_preset);
      if (!base) return refuse(404, 'policy_not_found', 'no such preset');
      if (!/^[a-z0-9][a-z0-9-]{1,62}$/.test(name)) return refuse(422, 'invalid_name', 'policy names are lowercase letters, digits and dashes');
      if (s.policies.has(name)) return refuse(409, 'policy_exists', 'the tenant already has this policy');
      const v1 = presetBody(base.name);
      v1.name = name;
      const detail: TenantPolicyDetail = { name, base_preset: base.name, active_version: 1, active_hash: base.hash, draft: false, draft_changes: 0,
        published_at: new Date().toISOString(), published_by: 'platform-admin', jobs: 0, active_body: v1 as unknown as Record<string, unknown>, draft_body: null,
        draft_by: null, draft_updated_at: null, versions: [{ version: 1, hash: base.hash, change_ticket: null, note: 'shipped preset', published_by: 'platform-admin', published_at: new Date().toISOString(), jobs_pinned: 0 }] };
      s.policies.set(name, { detail, bodies: { 1: v1 } });
      return respond(201, detail);
    }
    const stored = s.policies.get(parts[1]);
    if (!stored) return refuse(404, 'policy_not_found', 'the tenant has no policy with this name');
    const d = stored.detail;
    const active = d.active_version ?? 1;
    if (parts.length === 2) return respond(200, d);
    if (parts[2] === 'draft') {
      if (noAdmin) return refuse(403, 'forbidden', 'this key lacks the admin scope');
      if (method === 'DELETE') Object.assign(d, { draft: false, draft_changes: 0, draft_body: null, draft_by: null, draft_updated_at: null });
      else {
        const next = body.body as Record<string, unknown>;
        Object.assign(d, { draft: true, draft_body: next, draft_by: 'platform-admin', draft_updated_at: new Date().toISOString(), draft_changes: diffBodies(stored.bodies[active], next).length });
      }
      return respond(200, d);
    }
    if (parts[2] === 'publish') {
      if (noAdmin) return refuse(403, 'forbidden', 'this key lacks the admin scope');
      if (!d.draft_body) return refuse(409, 'no_draft', 'there is no draft to publish');
      const version = Math.max(...Object.keys(stored.bodies).map(Number)) + 1;
      const body2 = { ...(d.draft_body as unknown as PolicyDoc), version };
      const hash = fakeHash(`${d.name}:${version}:${JSON.stringify(body2)}`);
      stored.bodies[version] = body2;
      const v: PolicyVersionView = { version, hash, change_ticket: body.change_ticket ?? null, note: body.note ?? null, published_by: 'platform-admin', published_at: new Date().toISOString(), jobs_pinned: 0 };
      Object.assign(d, { active_version: version, active_hash: hash, active_body: body2, draft: false, draft_changes: 0, draft_body: null, draft_by: null, draft_updated_at: null,
        published_at: v.published_at, published_by: v.published_by, versions: [v, ...d.versions] });
      return respond(201, v);
    }
    if (parts[2] === 'versions' && parts[3]) {
      const body2 = stored.bodies[Number(parts[3])];
      if (!body2) return refuse(404, 'policy_not_found', 'no such version');
      return q.get('format') === 'yaml' ? file(toYaml(body2), 'text/yaml', `${d.name}-v${parts[3]}.yaml`) : respond(200, body2);
    }
    if (parts[2] === 'diff') {
      const from = stored.bodies[Number(q.get('from'))];
      const toParam = q.get('to');
      if (toParam === 'draft' && !d.draft_body) return refuse(409, 'no_draft', 'there is no draft');
      const to = toParam === 'draft' ? d.draft_body : stored.bodies[Number(toParam)];
      if (!from || !to) return refuse(404, 'policy_not_found', 'no such version');
      return respond(200, { from: Number(q.get('from')), to: toParam === 'draft' ? 'draft' : Number(toParam), changes: diffBodies(from, to) });
    }
  }
  return refuse(404, 'not_found', `no fixture for ${method} ${p}`);
}

/** Patch `window.fetch` to answer from the fixtures (dev server only; see the module comment). */
export function installFixtureServer(): void {
  const realFetch = window.fetch.bind(window);
  const state = createFixtureState(new Date());
  const scenario = new URLSearchParams(window.location.search).get('fixture') ?? '';
  window.fetch = async (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const href = typeof input === 'string' ? input : input instanceof URL ? input.href : input.url;
    const url = new URL(href, window.location.origin);
    const now = new URLSearchParams(window.location.search).get('fixture') ?? scenario;
    const res = url.origin === window.location.origin ? route(state, (init?.method ?? 'GET').toUpperCase(), url, init?.body, now) : null;
    if (!res) return realFetch(input, init);
    await new Promise((r) => window.setTimeout(r, 60));
    return res;
  };
  document.documentElement.setAttribute('data-fixtures', FIXTURE_MARKER);
  console.info(`[${FIXTURE_MARKER}] fixture mode: API answers come from src/dev/fixtures.ts${scenario ? ` (scenario ${scenario})` : ''}`);
}
