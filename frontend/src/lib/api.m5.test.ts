// The M5 API groups (contract §9): exact paths, methods, bodies, auth headers, coded errors and
// file downloads, against a recording fetch.
import { afterEach, describe, expect, it, vi } from 'vitest';
import { api, ApiError, dispositionName, type Auth } from './api';

interface Call { url: string; method: string; headers: Record<string, string>; body: unknown }
const auth: Auth = { apiKey: 'dps_test_key_value', tenant: 'retail-banking-apac' };

function record(respond: (c: Call) => Response = () => Response.json({ items: [], next_cursor: null })): Call[] {
  const calls: Call[] = [];
  vi.stubGlobal('fetch', vi.fn(async (url: string, init: RequestInit = {}) => {
    const c: Call = { url, method: init.method ?? 'GET', headers: (init.headers ?? {}) as Record<string, string>, body: init.body ? JSON.parse(String(init.body)) : undefined };
    calls.push(c);
    return respond(c);
  }));
  return calls;
}
afterEach(() => vi.unstubAllGlobals());

describe('api.admin', () => {
  it('sends the key and tenant headers and the documented paths and bodies', async () => {
    const calls = record((c) => Response.json(c.url.includes('rotate') ? { new: { key: 'k', record: {} }, old: {} } : { items: [], next_cursor: null }));
    await api.admin.keys(auth);
    await api.admin.createKey(auth, { name: 'collections-voice-redaction', tenant_ids: ['collections-in'], scopes: ['detect'], expires_in_days: 90 });
    await api.admin.rotateKey(auth, 'k_1', 24);
    await api.admin.disableKey(auth, 'k_1');
    await api.admin.enableKey(auth, 'k_1');
    await api.admin.tenants(auth);
    await api.admin.createTenant(auth, { tenant_id: 'cards-in', daily_row_quota: 20_000_000 });
    await api.admin.patchTenant(auth, 'cards-in', { retention_days: 45 });
    await api.admin.queue(auth);
    await api.admin.posture(auth);
    await api.admin.runRetention(auth, true);
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'GET /api/v1/admin/keys',
      'POST /api/v1/admin/keys',
      'POST /api/v1/admin/keys/k_1/rotate',
      'POST /api/v1/admin/keys/k_1/disable',
      'POST /api/v1/admin/keys/k_1/enable',
      'GET /api/v1/admin/tenants',
      'POST /api/v1/admin/tenants',
      'PATCH /api/v1/admin/tenants/cards-in',
      'GET /api/v1/admin/queue',
      'GET /api/v1/admin/posture',
      'POST /api/v1/admin/retention/run',
    ]);
    expect(calls[0].headers['X-API-KEY']).toBe(auth.apiKey);
    expect(calls[0].headers['X-DPS-Tenant']).toBe(auth.tenant);
    expect(calls[1].body).toEqual({ name: 'collections-voice-redaction', tenant_ids: ['collections-in'], scopes: ['detect'], expires_in_days: 90 });
    expect(calls[1].headers['Content-Type']).toBe('application/json');
    expect(calls[2].body).toEqual({ grace_hours: 24 });
    expect(calls[7].body).toEqual({ retention_days: 45 });
    expect(calls[10].body).toEqual({ dry_run: true });
  });

  it('puts audit filters in the query, never the key; CSV drops cursor and limit', async () => {
    const calls = record((c) => (c.url.includes('format=csv')
      ? new Response('a,b\n', { headers: { 'content-disposition': 'attachment; filename="dps-audit-2026-09-29-2026-10-13.csv"' } })
      : Response.json({ items: [], next_cursor: null })));
    await api.admin.audit(auth, { layer: 'api', outcome: 'failed', q: 'CHG-1', tenants: '*', limit: 50, cursor: 'c1' });
    const file = await api.admin.auditCsv(auth, { layer: 'api', limit: 50, cursor: 'c1' });
    expect(calls[0].url).toBe('/api/v1/admin/audit?layer=api&outcome=failed&q=CHG-1&tenants=*&limit=50&cursor=c1');
    expect(calls[1].url).toBe('/api/v1/admin/audit?layer=api&format=csv');
    expect(calls.every((c) => !c.url.includes(auth.apiKey))).toBe(true);
    expect(file.filename).toBe('dps-audit-2026-09-29-2026-10-13.csv');
  });

  it('raises ApiError with the server code and detail', async () => {
    record(() => Response.json({ detail: 'a key cannot grant a scope it does not hold', code: 'scope_escalation' }, { status: 403 }));
    const err = await api.admin.createKey(auth, { name: 'x', tenant_ids: ['*'], scopes: ['admin'], expires_in_days: 30 }).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(403);
    expect((err as ApiError).code).toBe('scope_escalation');
    expect((err as ApiError).message).toBe('a key cannot grant a scope it does not hold');
  });
});

describe('api.reports and evidence downloads', () => {
  it('reads the report routes with their windows', async () => {
    const calls = record(() => Response.json({}));
    await api.reports.summary(auth, 14);
    await api.reports.jobs(auth, 7);
    await api.reports.evidence(auth, { days: 30, layer: 'conn', outcome: 'ok', q: 'CHG', limit: 50 });
    await api.reports.apiRollups(auth, 14);
    expect(calls.map((c) => c.url)).toEqual([
      '/api/v1/reports/summary?days=14',
      '/api/v1/reports/jobs?days=7',
      '/api/v1/reports/evidence?days=30&layer=conn&outcome=ok&q=CHG&limit=50',
      '/api/v1/reports/api-rollups?days=14',
    ]);
  });

  it('downloads files with the key header and the server name', async () => {
    const calls = record((c) => (c.url === '/api/v1/evidence/ev_1'
      ? Response.json({ id: 'ev_1', artefacts: [{ id: 'a_json', kind: 'evidence_json', bytes: 10, content_type: 'application/json' }] })
      : new Response('%PDF', { headers: { 'content-disposition': 'attachment; filename="evidence-ev_1.pdf"' } })));
    const pdf = await api.evidence.pdf(auth, 'ev_1');
    const pack = await api.evidence.json(auth, 'ev_1');
    const roll = await api.reports.apiRollupFile(auth, '2026-10-10', 'obfuscate', 'pdf');
    expect(calls.map((c) => c.url)).toEqual(['/api/v1/evidence/ev_1/pdf', '/api/v1/evidence/ev_1', '/api/v1/artefacts/a_json',
      '/api/v1/reports/api-rollups/2026-10-10?action=obfuscate&format=pdf']);
    expect(calls[0].headers['X-API-KEY']).toBe(auth.apiKey);
    expect(pdf.filename).toBe('evidence-ev_1.pdf');
    expect(pack.filename).toBe('evidence-ev_1.json');
    expect(roll.filename).toBe('evidence-ev_1.pdf');
    expect(await pdf.blob.text()).toBe('%PDF');
  });

  it('refuses a pack without a JSON artefact', async () => {
    record(() => Response.json({ id: 'ev_2', artefacts: [] }));
    await expect(api.evidence.json(auth, 'ev_2')).rejects.toMatchObject({ code: 'artefact_not_found' });
  });

  it("saves a scan's pack (no JSON artefact) in its canonical form", async () => {
    record(() => Response.json({ id: 'ev_3', artefacts: [], pack: { job_kind: 'scan', scan: { submissions: 0 }, b: 1, a: 2 } }));
    const pack = await api.evidence.json(auth, 'ev_3');
    expect(pack.filename).toBe('evidence-ev_3.json');
    expect(await pack.blob.text()).toBe('{"a":2,"b":1,"job_kind":"scan","scan":{"submissions":0}}');
  });

  it('keeps only a base name from Content-Disposition', () => {
    expect(dispositionName('attachment; filename="../../etc/x.pdf"')).toBe('x.pdf');
    expect(dispositionName(null)).toBeNull();
  });
});

describe('api.policies (store)', () => {
  it('uses the store routes', async () => {
    const calls = record((c) => (c.url.includes('/versions/') ? new Response('name: x\n', { headers: { 'content-type': 'text/yaml' } }) : Response.json({})));
    await api.policies.list(auth);
    await api.policies.create(auth, 'retail-kyc', 'gdpr-pseudonymise');
    await api.policies.get(auth, 'retail-kyc');
    await api.policies.saveDraft(auth, 'retail-kyc', { treat: {} });
    await api.policies.discardDraft(auth, 'retail-kyc');
    await api.policies.publish(auth, 'retail-kyc', ' CHG-1 ', '');
    const y = await api.policies.versionYaml(auth, 'retail-kyc', 2);
    await api.policies.diff(auth, 'retail-kyc', 1, 'draft');
    expect(calls.map((c) => `${c.method} ${c.url}`)).toEqual([
      'GET /api/v1/policies', 'POST /api/v1/policies', 'GET /api/v1/policies/retail-kyc', 'PUT /api/v1/policies/retail-kyc/draft',
      'DELETE /api/v1/policies/retail-kyc/draft', 'POST /api/v1/policies/retail-kyc/publish',
      'GET /api/v1/policies/retail-kyc/versions/2?format=yaml', 'GET /api/v1/policies/retail-kyc/diff?from=1&to=draft',
    ]);
    expect(calls[1].body).toEqual({ name: 'retail-kyc', base_preset: 'gdpr-pseudonymise' });
    expect(calls[3].body).toEqual({ body: { treat: {} } });
    expect(calls[5].body).toEqual({ change_ticket: 'CHG-1', note: null });
    expect(y.filename).toBe('retail-kyc-v2.yaml');
  });
});
