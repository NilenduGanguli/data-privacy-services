// The dev-only fixture server: answers the scans routes from typed fixtures and plays a crawl over time.
import { describe, expect, it } from 'vitest';
import { demoScan, FIXTURE_SCAN_ID, summarize } from '../lib/scanFixtures';
import type { Page, ScanCreated, ScanDetail, ScanEvent, ScanPage, ScanPageDetail, ScanSpecIn } from '../lib/types';
import { ScanFixtureServer } from './fixtureServer';

const spec = (over: Partial<ScanSpecIn> = {}): ScanSpecIn => ({
  url: 'http://demo-site:8410/apply',
  scope: { same_origin: true, max_pages: 50, max_depth: 3, allow: [], deny: ['/logout', '/admin'], iframes: true, shadow_dom: true, spa_clicks: true },
  auth: { storage_state_ref: 'dps/ui/demo-onboarding' }, mode: 'synthesize', fill: { personas: 20, locale: 'en_IN', display_persona: 1 },
  parallel_view: true, capture: { screenshots: true, har: false }, ...over,
});

function server(): { s: ScanFixtureServer; advance: (ms: number) => void } {
  let now = 1_000_000;
  const s = new ScanFixtureServer({ now: () => now, durationMs: 40_000 });
  return { s, advance: (ms) => { now += ms; } };
}

async function call<T>(s: ScanFixtureServer, method: string, url: string, body?: unknown): Promise<{ status: number; body: T }> {
  const res = await s.handle(method, url, body === undefined ? undefined : JSON.stringify(body));
  if (!res) throw new Error(`not a fixture route: ${url}`);
  const type = res.headers.get('content-type') ?? '';
  return { status: res.status, body: (type.includes('json') ? await res.json() : await res.text()) as T };
}

describe('fixture data', () => {
  it('is internally consistent: the summary is computed from the pages, fields and events', () => {
    const d = demoScan();
    const again = summarize(d.pages, d.fields, d.events, { export_artefacts: d.summary.export_artefacts, evidence_id: d.summary.evidence_id, elapsed_s: d.summary.elapsed_s });
    expect(d.summary).toEqual(again);
    expect(d.summary.pages_visited).toBeGreaterThan(10);
    expect(d.summary.blocked_requests.POST).toBeGreaterThan(0);
    expect(d.summary.risk_flags.map((r) => r.code)).toEqual(['A1', 'A2', 'A3', 'A4', 'A5']);
    for (const p of d.pages) for (const f of d.fields[p.page_no]) expect(f.page_no).toBe(p.page_no);
  });
});

describe('ScanFixtureServer', () => {
  it('passes through anything that is not a scans or artefacts route', async () => {
    const { s } = server();
    expect(await s.handle('GET', '/api/v1/jobs')).toBeNull();
    expect(await s.handle('GET', '/readyz')).toBeNull();
  });

  it('serves the finished demo scan', async () => {
    const { s } = server();
    const d = await call<ScanDetail>(s, 'GET', `/api/v1/scans/${FIXTURE_SCAN_ID}`);
    expect(d.status).toBe(200);
    expect(d.body.scan.state).toBe('succeeded');
    expect(d.body.summary.evidence_id).toBeTruthy();
    const pages = await call<Page<ScanPage>>(s, 'GET', `/api/v1/scans/${FIXTURE_SCAN_ID}/pages?after=0&limit=200`);
    expect(pages.body.items.length).toBe(demoScan().pages.length);
    const one = await call<ScanPageDetail>(s, 'GET', `/api/v1/scans/${FIXTURE_SCAN_ID}/pages/2`);
    expect(one.body.page.page_no).toBe(2);
    expect(one.body.fields.length).toBe(9);
    expect(one.body.requests.some((r) => r.decision === 'blocked')).toBe(true);
    const list = await call<Page<{ id: string }>>(s, 'GET', '/api/v1/scans?limit=10');
    expect(list.body.items.map((j) => j.id)).toContain(FIXTURE_SCAN_ID);
  });

  it('pages events incrementally with after=', async () => {
    const { s } = server();
    const all = await call<Page<ScanEvent>>(s, 'GET', `/api/v1/scans/${FIXTURE_SCAN_ID}/events?after=0&limit=200`);
    const last = all.body.items[all.body.items.length - 1].seq;
    const tail = await call<Page<ScanEvent>>(s, 'GET', `/api/v1/scans/${FIXTURE_SCAN_ID}/events?after=${last - 2}&limit=200`);
    expect(tail.body.items.map((e) => e.seq)).toEqual([last - 1, last]);
    const limited = await call<Page<ScanEvent>>(s, 'GET', `/api/v1/scans/${FIXTURE_SCAN_ID}/events?after=0&limit=5`);
    expect(limited.body.items.length).toBe(5);
    expect(limited.body.next_cursor).toBe('5');
  });

  it('creates a scan that crawls over time and finishes with exports and evidence', async () => {
    const { s, advance } = server();
    const created = await call<ScanCreated>(s, 'POST', '/api/v1/scans', spec());
    expect(created.status).toBe(202);
    expect(created.body.scan.state).toBe('queued');
    expect(created.body.key_material?.kid).toBeTruthy();
    const id = created.body.scan.id;
    advance(16_000);
    const mid = await call<ScanDetail>(s, 'GET', `/api/v1/scans/${id}`);
    expect(mid.body.scan.state).toBe('running');
    expect(mid.body.summary.pages_visited).toBeGreaterThan(0);
    expect(mid.body.summary.pages_visited).toBeLessThan(demoScan().summary.pages_visited);
    expect(mid.body.summary.pages_queued).toBeGreaterThan(0);
    const midPages = await call<Page<ScanPage>>(s, 'GET', `/api/v1/scans/${id}/pages`);
    expect(midPages.body.items.some((p) => p.status === 'visiting')).toBe(true);
    advance(40_000);
    const end = await call<ScanDetail>(s, 'GET', `/api/v1/scans/${id}`);
    expect(end.body.scan.state).toBe('succeeded');
    expect(end.body.summary.export_artefacts.json).toBeTruthy();
    expect(end.body.summary.evidence_id).toBeTruthy();
  });

  it('stores the start URL value-free and the wrap as a flag, the way dps_job.spec does', async () => {
    const { s } = server();
    const pem = '-----BEGIN PUBLIC KEY-----\nAAA\n-----END PUBLIC KEY-----';
    const { body } = await call<ScanCreated>(s, 'POST', '/api/v1/scans', spec({
      url: 'http://demo-site:8410/apply/12345?ref=ABC#top', mode: 'obfuscate', consistency: { wrap_key: { type: 'rsa-oaep-256', public_key_pem: pem } },
    }));
    const got = await call<ScanDetail>(s, 'GET', `/api/v1/scans/${body.scan.id}`);
    expect(got.body.scan.spec.url).toBe('http://demo-site:8410/apply/{id}?ref=<v>');
    expect(got.body.scan.spec.consistency).toMatchObject({ key_wrapped: true });
    expect(JSON.stringify(got.body.scan.spec)).not.toMatch(/12345|ABC|BEGIN PUBLIC KEY/);
  });

  it('cancels a running scan and keeps what it found', async () => {
    const { s, advance } = server();
    const { body } = await call<ScanCreated>(s, 'POST', '/api/v1/scans', spec());
    advance(12_000);
    const canceled = await call<{ state: string }>(s, 'POST', `/api/v1/scans/${body.scan.id}/cancel`);
    expect(canceled.body.state).toBe('canceled');
    const before = await call<ScanDetail>(s, 'GET', `/api/v1/scans/${body.scan.id}`);
    advance(60_000);
    const after = await call<ScanDetail>(s, 'GET', `/api/v1/scans/${body.scan.id}`);
    expect(after.body.scan.state).toBe('canceled');
    expect(after.body.summary.pages_visited).toBe(before.body.summary.pages_visited);
  });

  it('refuses what the API refuses: unregistered hosts, HAR, unknown keys such as a submit field', async () => {
    const { s } = server();
    const host = await call<{ code: string }>(s, 'POST', '/api/v1/scans', spec({ url: 'https://elsewhere.example/apply' }));
    expect([host.status, host.body.code]).toEqual([422, 'host_not_registered']);
    const har = await call<{ code: string }>(s, 'POST', '/api/v1/scans', spec({ capture: { screenshots: true, har: true as false } }));
    expect([har.status, har.body.code]).toEqual([422, 'har_not_supported']);
    const submit = await call<{ code: string }>(s, 'POST', '/api/v1/scans', { ...spec(), submit: true });
    expect(submit.status).toBe(422);
    const depth = await call<{ code: string }>(s, 'POST', '/api/v1/scans', spec({ scope: { ...spec().scope, max_depth: 9 } }));
    expect([depth.status, depth.body.code]).toEqual([422, 'scope_invalid']);
  });

  it('answers a one-page session probe: valid with a stored session, redirected to /login without one', async () => {
    const { s, advance } = server();
    const probe = (ref: string | null) => spec({
      url: 'http://demo-site:8410/profile', auth: { storage_state_ref: ref }, parallel_view: false, capture: { screenshots: false, har: false },
      scope: { ...spec().scope, max_pages: 1, max_depth: 0, spa_clicks: false },
    });
    const ok = await call<ScanCreated>(s, 'POST', '/api/v1/scans', probe('dps/ui/demo-onboarding'));
    const bad = await call<ScanCreated>(s, 'POST', '/api/v1/scans', probe(null));
    advance(5_000);
    const okDetail = await call<ScanDetail>(s, 'GET', `/api/v1/scans/${ok.body.scan.id}`);
    expect(okDetail.body.scan.state).toBe('succeeded');
    const okPage = await call<Page<ScanPage>>(s, 'GET', `/api/v1/scans/${ok.body.scan.id}/pages`);
    expect(okPage.body.items).toHaveLength(1);
    expect(okPage.body.items[0]).toMatchObject({ status: 'done', http_status: 200 });
    const badPage = await call<Page<ScanPage>>(s, 'GET', `/api/v1/scans/${bad.body.scan.id}/pages`);
    expect(badPage.body.items[0].status).toBe('redirected');
    expect(badPage.body.items[0].final_url_redacted).toContain('/login');
  });

  it('previews persona 1 per locale and keeps a given seed', async () => {
    const { s } = server();
    const seed = { alg: 'hmac-sha256', seed: 'c2VlZA==', created_at: '2026-10-13T00:00:00Z' };
    const p = await call<{ synthesis_seed: unknown; personas: { values: Record<string, string> }[] }>(s, 'POST', '/api/v1/scans/personas/preview', { locale: 'en_US', count: 1, synthesis_seed: seed });
    expect(p.body.synthesis_seed).toEqual(seed);
    expect(Object.keys(p.body.personas[0].values)).toContain('ssn');
  });

  it('serves artefacts as images and exports as files', async () => {
    const { s } = server();
    const png = await s.handle('GET', '/api/v1/artefacts/art_2_annotated');
    expect(png?.status).toBe(200);
    expect(png?.headers.get('content-type')).toBe('image/png');
    const csv = await call<string>(s, 'GET', `/api/v1/scans/${FIXTURE_SCAN_ID}/inventory?format=csv`);
    expect(csv.body.split('\n')[0]).toContain('entity_type');
    const inv = await call<{ items: unknown[]; risk_flags: unknown[] }>(s, 'GET', `/api/v1/scans/${FIXTURE_SCAN_ID}/inventory?format=json`);
    expect(inv.body.items.length).toBe(demoScan().summary.fields);
    const missing = await s.handle('GET', '/api/v1/artefacts/art_unknown');
    expect(missing?.status).toBe(404);
    const unknownScan = await call<{ code: string }>(s, 'GET', '/api/v1/scans/s_nope');
    expect(unknownScan.status).toBe(404);
  });
});
