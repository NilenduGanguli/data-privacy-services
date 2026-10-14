// The dev server's fixture mode answers the console's API calls from typed fixtures. It must be
// faithful to the routes' shapes (the pages are checked against it in the browser) and must never
// echo a raw key after the create/rotate response.
import { describe, expect, it } from 'vitest';
import { createFixtureState, route } from './fixtureServer';

const NOW = new Date('2026-10-13T10:15:00Z');
async function call(state: ReturnType<typeof createFixtureState>, method: string, path: string, body?: unknown, scenario = ''): Promise<{ status: number; json: any; text: string; headers: Headers }> {
  const res = route(state, method, new URL(path, 'http://localhost:5185'), body === undefined ? undefined : JSON.stringify(body), scenario);
  if (!res) throw new Error(`no fixture for ${method} ${path}`);
  const text = await res.text();
  let json: unknown = null;
  try { json = JSON.parse(text); } catch { /* not JSON */ }
  return { status: res.status, json, text, headers: res.headers };
}

describe('fixture server', () => {
  it('answers only the console API and readiness; anything else goes to the network', () => {
    const s = createFixtureState(NOW);
    expect(route(s, 'GET', new URL('http://localhost:5185/src/main.tsx'), undefined, '')).toBeNull();
    expect(route(s, 'GET', new URL('http://localhost:5185/readyz'), undefined, '')).not.toBeNull();
  });

  it('serves the summary, and 503 in the degraded scenario', async () => {
    const s = createFixtureState(NOW);
    const ok = await call(s, 'GET', '/api/v1/reports/summary?days=14');
    expect(ok.status).toBe(200);
    expect(ok.json.trend).toHaveLength(14);
    expect(ok.json.trend[13].day).toBe('2026-10-13');
    expect((await call(s, 'GET', '/api/v1/reports/summary?days=14', undefined, 'degraded')).status).toBe(503);
  });

  it('creates a key whose raw value appears once and never in the listing', async () => {
    const s = createFixtureState(NOW);
    const created = await call(s, 'POST', '/api/v1/admin/keys', { name: 'demo-pipeline', tenant_ids: ['retail-banking-apac'], scopes: ['detect'], expires_in_days: 90 });
    expect(created.status).toBe(201);
    expect(created.headers.get('cache-control')).toBe('no-store');
    const raw = created.json.key as string;
    expect(raw.startsWith('dps_')).toBe(true);
    const list = await call(s, 'GET', '/api/v1/admin/keys');
    expect(list.text).not.toContain(raw);
    expect(list.json.items.some((k: { name: string }) => k.name === 'demo-pipeline')).toBe(true);
    const dup = await call(s, 'POST', '/api/v1/admin/keys', { name: 'Bad Name', tenant_ids: ['x'], scopes: ['detect'], expires_in_days: 90 });
    expect(dup.json.code).toBe('invalid_name');
  });

  it('rotates with grace, disables and re-enables', async () => {
    const s = createFixtureState(NOW);
    const rot = await call(s, 'POST', '/api/v1/admin/keys/k_ledger/rotate', { grace_hours: 24 });
    expect(rot.status).toBe(201);
    expect(rot.json.old.rotated_at).toBeTruthy();
    expect(rot.json.new.record.rotated_from).toBe('k_ledger');
    expect((await call(s, 'POST', '/api/v1/admin/keys/k_kycdocs/disable')).json.status).toBe('disabled');
    expect((await call(s, 'POST', '/api/v1/admin/keys/k_kycdocs/enable')).json.status).toBe('active');
    expect((await call(s, 'POST', '/api/v1/admin/keys/nope/disable')).status).toBe(404);
  });

  it('refuses admin routes in the noadmin scenario', async () => {
    const s = createFixtureState(NOW);
    const r = await call(s, 'GET', '/api/v1/admin/audit', undefined, 'noadmin');
    expect(r.status).toBe(403);
    expect(r.json.code).toBe('forbidden');
  });

  it('filters and pages the audit log', async () => {
    const s = createFixtureState(NOW);
    const first = await call(s, 'GET', '/api/v1/admin/audit?limit=5');
    expect(first.json.items).toHaveLength(5);
    expect(first.json.next_cursor).toBeTruthy();
    const next = await call(s, 'GET', `/api/v1/admin/audit?limit=5&cursor=${first.json.next_cursor}`);
    expect(next.json.items[0].id).toBeLessThan(first.json.items[4].id);
    const failed = await call(s, 'GET', '/api/v1/admin/audit?outcome=failed&tenants=*');
    expect(failed.json.items.every((r: { outcome: string }) => ['leakage', 'failed', 'error'].includes(r.outcome))).toBe(true);
    const csv = await call(s, 'GET', '/api/v1/admin/audit?format=csv');
    expect(csv.headers.get('content-disposition')).toContain('attachment');
    expect(csv.text.split('\n')[0]).toContain('ts,tenant_id,key_name,route');
  });

  it('runs the policy store: draft, diff, publish, YAML', async () => {
    const s = createFixtureState(NOW);
    const diff = await call(s, 'GET', '/api/v1/policies/gdpr-pseudonymise/diff?from=2&to=3');
    // the backend's flatten_policy shape (M5 plan Task 15): method specs by leaf, list items by name or path
    expect(diff.json.changes.map((c: { path: string }) => c.path)).toEqual([
      'detect.declared_fields[CRM.CUSTOMERS.RISK_SCORE].entity_type', 'detect.declared_fields[CRM.CUSTOMERS.RISK_SCORE].path', 'detect.min_score',
      'treat.CVV.method', 'treat.POSTAL_CODE.method', 'treat.POSTAL_CODE.params.keep_chars',
    ]);
    const draft = await call(s, 'GET', '/api/v1/policies/gdpr-pseudonymise/diff?from=3&to=draft');
    expect(draft.json.changes.map((c: { path: string }) => c.path)).toEqual(expect.arrayContaining([
      'detect.custom_recognizers[employee-id].patterns[employee-id].regex', 'detect.declared_fields[LOANS.APPLICANTS.EMPLOYER_NAME].entity_type', 'treat.ORG_NAME.method',
    ]));
    const pub = await call(s, 'POST', '/api/v1/policies/gdpr-pseudonymise/publish', { change_ticket: 'CHG-1', note: null });
    expect(pub.status).toBe(201);
    expect(pub.json.version).toBe(4);
    const after = await call(s, 'GET', '/api/v1/policies/gdpr-pseudonymise');
    expect(after.json.active_version).toBe(4);
    expect(after.json.draft).toBe(false);
    expect((await call(s, 'GET', '/api/v1/policies/gdpr-pseudonymise/diff?from=4&to=draft')).json.code).toBe('no_draft');
    const yaml = await call(s, 'GET', '/api/v1/policies/gdpr-pseudonymise/versions/4?format=yaml');
    expect(yaml.headers.get('content-type')).toContain('yaml');
    expect(yaml.text).toContain('EMPLOYER_NAME');
    const adopted = await call(s, 'POST', '/api/v1/policies', { name: 'pci-dss', base_preset: 'pci-dss' });
    expect(adopted.status).toBe(201);
    expect(adopted.json.active_version).toBe(1);
  });
});
