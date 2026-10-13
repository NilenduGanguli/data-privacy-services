// Pure logic of the New scan page (mockup 12): spec building, request preview, deny list, session
// check, crawl tree, event log. fetch is stubbed where the API is called.
import { afterEach, describe, expect, it } from 'vitest';
import { demoScan, event, FIXTURE_SEED, page, scanJob } from './scanFixtures';
import {
  blockedByPage, buildSpec, checkSession, configError, configFromSpec, crawlTree, createErrorText, DEFAULT_CONFIG, denyPathError,
  eventsJsonl, fetchAllPages, fetchNewEvents, mergeEvents, previewBody, probeSpec, requestJson, sessionVerdict,
} from './scans';
import { ApiError } from './api';

const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });
const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const auth = { apiKey: 'k', tenant: '' };

describe('the scan spec', () => {
  it('mirrors the setup form, field for field (ScanSpec has no submit field)', () => {
    const spec = buildSpec({ ...DEFAULT_CONFIG, url: ' http://demo-site:8410/apply ', deny: ['/logout'] }, FIXTURE_SEED);
    expect(spec).toEqual({
      url: 'http://demo-site:8410/apply',
      scope: { same_origin: true, max_pages: 50, max_depth: 3, allow: [], deny: ['/logout'], iframes: true, shadow_dom: true, spa_clicks: true },
      auth: { storage_state_ref: 'dps/ui/demo-onboarding' }, mode: 'synthesize',
      fill: { personas: 20, locale: 'en_IN', display_persona: 1 }, parallel_view: true, capture: { screenshots: true, har: false },
      consistency: { synthesis_seed: FIXTURE_SEED },
    });
    expect(JSON.stringify(spec)).not.toMatch(/submit/i);
  });
  it('sends no session for public pages and the wrap key only when chosen', () => {
    const spec = buildSpec({ ...DEFAULT_CONFIG, session: '  ', mode: 'obfuscate', wrap: 'rsa', pem: '-----BEGIN PUBLIC KEY-----\nAAA\n-----END PUBLIC KEY-----' }, null);
    expect(spec.auth).toEqual({ storage_state_ref: null });
    expect(spec.mode).toBe('obfuscate');
    expect(spec.consistency).toEqual({ wrap_key: { type: 'rsa-oaep-256', public_key_pem: '-----BEGIN PUBLIC KEY-----\nAAA\n-----END PUBLIC KEY-----' } });
    expect(buildSpec({ ...DEFAULT_CONFIG, wrap: 'clear' }, null).consistency).toBeUndefined();
  });
  it('previews and copies the request without consistency: the seed and the public key are never shown', () => {
    const cfg = { ...DEFAULT_CONFIG, wrap: 'rsa' as const, pem: 'PEM' };
    expect(previewBody(cfg)).not.toHaveProperty('consistency');
    const text = requestJson(cfg);
    expect(JSON.parse(text)).toEqual(previewBody(cfg));
    expect(text).not.toContain('seed');
    expect(text).not.toContain('PEM');
    expect(text).toContain('"deny": ["/logout", "/admin"]');
    expect(text).toContain('"capture": { "screenshots": true, "har": false }');
  });
  it('is a one-page probe for the session check', () => {
    const spec = probeSpec({ ...DEFAULT_CONFIG, url: 'http://demo-site:8410/profile' });
    expect(spec.scope).toMatchObject({ max_pages: 1, max_depth: 0, spa_clicks: false });
    expect(spec.parallel_view).toBe(false);
    expect(spec.capture).toEqual({ screenshots: false, har: false });
    expect(spec.fill).toEqual({ personas: 1, locale: 'en_IN', display_persona: 1 });
  });
  it('round-trips from a scan job’s value-free spec', () => {
    const cfg = configFromSpec(scanJob().spec);
    expect(cfg).toMatchObject({ url: 'http://demo-site:8410/apply', maxPages: 50, maxDepth: 3, deny: ['/logout', '/admin'], session: 'dps/ui/demo-onboarding', locale: 'en_IN', personas: 20 });
  });
  it('keeps the RSA key wrap of a scan opened by link: the operator pastes the public key again', () => {
    const spec = { ...scanJob().spec, mode: 'obfuscate', consistency: { scope: 'request', key_wrapped: true, wrapped: true, caller_key: false, caller_seed: false } };
    const cfg = configFromSpec(spec);
    expect(cfg).toMatchObject({ mode: 'obfuscate', wrap: 'rsa', pem: '' });
    expect(configError(cfg)).toBe('Paste the RSA public key (PEM) to wrap the key with, or return it in clear.');
    expect(buildSpec(cfg, null).consistency).toBeUndefined();
    expect(configFromSpec({ ...spec, consistency: { key_wrapped: false } }).wrap).toBe('clear');
    expect(configFromSpec(scanJob().spec).wrap).toBe('clear');
  });
  it('refuses a redacted start URL (a scan opened by link) until the full URL is entered again', () => {
    const msg = 'The start URL of a saved scan is stored redacted; enter the full start URL again.';
    for (const url of ['http://demo-site:8410/apply/{id}', 'http://demo-site:8410/apply?ref=<v>', 'http://demo-site:8410/track?email=<EMAIL>',
      'http://demo-site:8410/a?<k>=<v>', 'http://demo-site:8410/accounts/%3CACCOUNT_NUMBER%3E', 'http://demo-site:8410/apply/%7Bid%7D/x']) {
      expect(configError({ ...DEFAULT_CONFIG, url })).toBe(msg);
    }
    const cfg = configFromSpec({ ...scanJob().spec, url: 'http://demo-site:8410/apply/{id}?ref=<v>' });
    expect(configError(cfg)).toBe(msg);
    expect(configError({ ...cfg, url: 'http://demo-site:8410/apply/12345?ref=ABC' })).toBeNull();
  });
});

describe('form checks', () => {
  it('accepts paths on the deny list the way the mockup does', () => {
    expect(denyPathError('/payments', [])).toBeNull();
    expect(denyPathError('/help/faq/*', [])).toBeNull();
    expect(denyPathError('payments', [])).toBe('Paths start with / and contain no spaces or query strings, for example /payments.');
    expect(denyPathError('/a b', [])).not.toBeNull();
    expect(denyPathError('/a?x=1', [])).not.toBeNull();
    expect(denyPathError('/logout', ['/logout'])).toBe('/logout is already on the list.');
  });
  it('names the first input out of range', () => {
    expect(configError(DEFAULT_CONFIG)).toBeNull();
    expect(configError({ ...DEFAULT_CONFIG, url: 'ftp://x' })).toMatch(/http/);
    expect(configError({ ...DEFAULT_CONFIG, maxPages: 0 })).toMatch(/Max pages/);
    expect(configError({ ...DEFAULT_CONFIG, maxPages: 501 })).toMatch(/Max pages/);
    expect(configError({ ...DEFAULT_CONFIG, maxDepth: 6 })).toMatch(/Max depth/);
    expect(configError({ ...DEFAULT_CONFIG, personas: 51 })).toMatch(/Personas/);
    expect(configError({ ...DEFAULT_CONFIG, mode: 'obfuscate', wrap: 'rsa', pem: '' })).toMatch(/public key/);
  });
  it('explains refusals of POST /scans in words', () => {
    expect(createErrorText(new ApiError(422, 'x', 'host_not_registered'))).toBe(
      'This host is not registered for the tenant. Register it in Admin › UI-scanner hosts (M5); locally the registered hosts are localhost, 127.0.0.1 and demo-site.');
    expect(createErrorText(new ApiError(422, 'x', 'har_not_supported'))).toMatch(/HAR/);
    expect(createErrorText(new ApiError(422, 'max_pages: too big'))).toBe('422: max_pages: too big');
  });
});

describe('session check', () => {
  it('reads page 1 of the probe', () => {
    expect(sessionVerdict(page({ status: 'done', http_status: 200, url_redacted: 'http://demo-site:8410/profile' })))
      .toEqual({ ok: true, text: '✓ Session valid · /profile returned 200 without a redirect' });
    expect(sessionVerdict(page({ status: 'redirected', http_status: 302, final_url_redacted: 'http://demo-site:8410/login?next=<v>' })))
      .toEqual({ ok: false, text: '✗ Redirected to /login?next=••• — the stored session is not valid' });
    expect(sessionVerdict(page({ status: 'failed', status_reason: 'timeout' })).ok).toBe(false);
    expect(sessionVerdict(undefined).ok).toBe(false);
  });
  it('creates a probe scan, waits for it and answers from its first page', async () => {
    const calls: string[] = [];
    let polls = 0;
    globalThis.fetch = (async (path: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${path}`);
      if (path === '/api/v1/scans') return json({ scan: scanJob({ id: 's_probe', state: 'queued' }), key_material: null, synthesis_seed: null, replay: false }, 202);
      if (path === '/api/v1/scans/s_probe') { polls += 1; return json({ scan: scanJob({ id: 's_probe', state: polls < 2 ? 'running' : 'succeeded' }), summary: demoScan().summary }); }
      if (path.startsWith('/api/v1/scans/s_probe/pages')) return json({ items: [page({ status: 'done', http_status: 200, url_redacted: 'http://demo-site:8410/profile' })], next_cursor: null });
      return json({}, 404);
    }) as typeof fetch;
    const verdict = await checkSession(auth, { ...DEFAULT_CONFIG, url: 'http://demo-site:8410/profile' }, { sleep: async () => undefined });
    expect(verdict.ok).toBe(true);
    expect(calls[0]).toBe('POST /api/v1/scans');
    expect(calls.filter((c) => c === 'GET /api/v1/scans/s_probe')).toHaveLength(2);
  });
  it('never probes a redacted start URL', async () => {
    const calls: string[] = [];
    globalThis.fetch = (async (path: string) => { calls.push(path); return json({}, 500); }) as typeof fetch;
    const verdict = await checkSession(auth, { ...DEFAULT_CONFIG, url: 'http://demo-site:8410/apply/{id}?ref=<v>' }, { sleep: async () => undefined });
    expect(verdict).toEqual({ ok: false, text: '✗ The start URL of a saved scan is stored redacted; enter the full start URL again.' });
    expect(calls).toEqual([]);
  });
  it('gives up after the timeout and cancels the probe', async () => {
    const calls: string[] = [];
    globalThis.fetch = (async (path: string, init?: RequestInit) => {
      calls.push(`${init?.method ?? 'GET'} ${path}`);
      if (path === '/api/v1/scans') return json({ scan: scanJob({ id: 's_slow', state: 'queued' }), key_material: null, synthesis_seed: null, replay: false }, 202);
      if (path === '/api/v1/scans/s_slow') return json({ scan: scanJob({ id: 's_slow', state: 'running' }), summary: demoScan().summary });
      return json(scanJob({ id: 's_slow', state: 'canceled' }));
    }) as typeof fetch;
    let t = 0;
    const verdict = await checkSession(auth, DEFAULT_CONFIG, { sleep: async (ms) => { t += ms; }, now: () => t, timeoutMs: 5000 });
    expect(verdict).toEqual({ ok: false, text: '✗ The check did not finish in time; try again' });
    expect(calls).toContain('POST /api/v1/scans/s_slow/cancel');
  });
});

describe('crawl map and navigation log', () => {
  const d = demoScan();
  it('builds the tree from parent_page_no, children in page order', () => {
    const roots = crawlTree(d.pages);
    expect(roots.map((n) => n.page.page_no)).toEqual([1]);
    expect(roots[0].children.map((n) => n.page.page_no)).toEqual([2, 3, 4, 5, 6, 8, 9, 11, 16, 17, 18, 19]);
    const help = roots[0].children.find((n) => n.page.page_no === 11);
    expect(help?.children.map((n) => n.page.page_no)).toEqual([12, 13, 14, 15]);
    // a page whose parent is not (yet) listed is shown as a root rather than lost
    expect(crawlTree([page({ page_no: 5, parent_page_no: 3 })]).map((n) => n.page.page_no)).toEqual([5]);
  });
  it('counts blocked requests per page from the events', () => {
    const m = blockedByPage(d.events);
    expect(m.get(2)).toBe(2);
    expect(m.get(3)).toBe(2);
    expect(m.get(4)).toBeUndefined();
  });
  it('merges incremental event pages by seq', () => {
    const a = [event({ seq: 1 }), event({ seq: 2 })];
    expect(mergeEvents(a, [event({ seq: 2 }), event({ seq: 3 })]).map((e) => e.seq)).toEqual([1, 2, 3]);
  });
  it('downloads the log as JSONL, one event per line', () => {
    const text = eventsJsonl([event({ seq: 1 }), event({ seq: 2, kind: 'request', result: 'blocked', method: 'POST' })]);
    const lines = text.trim().split('\n');
    expect(lines).toHaveLength(2);
    expect(JSON.parse(lines[1])).toMatchObject({ seq: 2, kind: 'request', result: 'blocked' });
  });
  it('follows the pages and events cursors', async () => {
    const paths: string[] = [];
    globalThis.fetch = (async (path: string) => {
      paths.push(path);
      if (path.includes('/pages?after=0')) return json({ items: [page({ page_no: 1 }), page({ page_no: 2 })], next_cursor: '2' });
      if (path.includes('/pages?after=2')) return json({ items: [page({ page_no: 3 })], next_cursor: null });
      if (path.includes('/events?after=5')) return json({ items: [event({ seq: 6 }), event({ seq: 7 })], next_cursor: '7' });
      if (path.includes('/events?after=7')) return json({ items: [], next_cursor: null });
      return json({ items: [], next_cursor: null });
    }) as typeof fetch;
    expect((await fetchAllPages(auth, 's_1')).map((p) => p.page_no)).toEqual([1, 2, 3]);
    expect((await fetchNewEvents(auth, 's_1', 5)).map((e) => e.seq)).toEqual([6, 7]);
    expect(paths).toEqual([
      '/api/v1/scans/s_1/pages?after=0&limit=200', '/api/v1/scans/s_1/pages?after=2&limit=200',
      '/api/v1/scans/s_1/events?after=5&limit=200', '/api/v1/scans/s_1/events?after=7&limit=200',
    ]);
  });
});
