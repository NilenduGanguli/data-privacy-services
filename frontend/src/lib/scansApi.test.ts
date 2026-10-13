// api.scans (contract §9 / §14): paths, methods, headers, blobs and coded refusals. fetch is stubbed.
import { afterEach, describe, expect, it } from 'vitest';
import { api, ApiError } from './api';
import type { ScanSpecIn } from './types';

interface Call { path: string; init: RequestInit | undefined }
const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

function stub(response: () => Response): Call[] {
  const calls: Call[] = [];
  globalThis.fetch = (async (path: string, init?: RequestInit) => { calls.push({ path, init }); return response(); }) as typeof fetch;
  return calls;
}
const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const auth = { apiKey: 'test-key', tenant: 'acme' };
const headersOf = (c: Call): Record<string, string> => c.init?.headers as Record<string, string>;

const spec: ScanSpecIn = {
  url: 'http://demo-site:8410/apply',
  scope: { same_origin: true, max_pages: 50, max_depth: 3, allow: [], deny: ['/logout'], iframes: true, shadow_dom: true, spa_clicks: true },
  auth: { storage_state_ref: 'dps/ui/demo-onboarding' }, mode: 'synthesize',
  fill: { personas: 20, locale: 'en_IN', display_persona: 1 }, parallel_view: true, capture: { screenshots: true, har: false },
};

describe('api.scans', () => {
  it('creates a scan with POST /api/v1/scans, the spec as JSON and the Idempotency-Key header', async () => {
    const calls = stub(() => json({ scan: { id: 's_1' }, key_material: null, synthesis_seed: null, replay: false }, 202));
    const out = await api.scans.create(auth, spec, 'idem-1');
    expect(out.scan.id).toBe('s_1');
    expect(calls[0].path).toBe('/api/v1/scans');
    expect(calls[0].init?.method).toBe('POST');
    expect(headersOf(calls[0])).toMatchObject({ 'X-API-KEY': 'test-key', 'X-DPS-Tenant': 'acme', 'Idempotency-Key': 'idem-1', 'Content-Type': 'application/json' });
    const body = JSON.parse(calls[0].init?.body as string) as Record<string, unknown>;
    expect(body).toEqual(spec);
    expect(JSON.stringify(body)).not.toMatch(/submit/i);
  });

  it('reads the scan, its pages, one page, events and the inventory on their routes', async () => {
    const calls = stub(() => json({ items: [], next_cursor: null }));
    await api.scans.list(auth, { state: 'running', limit: 5 });
    await api.scans.get(auth, 's_1');
    await api.scans.pages(auth, 's_1');
    await api.scans.pages(auth, 's_1', 200, 50);
    await api.scans.page(auth, 's_1', 7);
    await api.scans.events(auth, 's_1', 42);
    await api.scans.inventory(auth, 's_1');
    expect(calls.map((c) => c.path)).toEqual([
      '/api/v1/scans?state=running&limit=5',
      '/api/v1/scans/s_1',
      '/api/v1/scans/s_1/pages?after=0&limit=200',
      '/api/v1/scans/s_1/pages?after=200&limit=50',
      '/api/v1/scans/s_1/pages/7',
      '/api/v1/scans/s_1/events?after=42&limit=200',
      '/api/v1/scans/s_1/inventory?format=json',
    ]);
    expect(calls.every((c) => !c.init?.method || c.init.method === 'GET')).toBe(true);
  });

  it('cancels and previews personas with POST', async () => {
    const calls = stub(() => json({}));
    await api.scans.cancel(auth, 's_1');
    await api.scans.personaPreview(auth, { locale: 'en_IN', count: 1, synthesis_seed: null });
    expect(calls.map((c) => [c.path, c.init?.method])).toEqual([
      ['/api/v1/scans/s_1/cancel', 'POST'], ['/api/v1/scans/personas/preview', 'POST'],
    ]);
    expect(JSON.parse(calls[1].init?.body as string)).toEqual({ locale: 'en_IN', count: 1, synthesis_seed: null });
  });

  it('escapes ids in paths', async () => {
    const calls = stub(() => json({}));
    await api.scans.get(auth, 's/../x');
    expect(calls[0].path).toBe('/api/v1/scans/s%2F..%2Fx');
  });

  it('downloads exports and artefacts as blobs with the API key', async () => {
    const calls = stub(() => new Response(new Uint8Array([137, 80, 78, 71]), { status: 200, headers: { 'content-type': 'image/png' } }));
    const png = await api.scans.artefact(auth, 'a_1');
    const csv = await api.scans.download(auth, 's_1', 'inventory', 'csv');
    await api.scans.download(auth, 's_1', 'export', 'json');
    expect(png).toBeInstanceOf(Blob);
    expect(png.size).toBe(4);
    expect(csv).toBeInstanceOf(Blob);
    expect(calls.map((c) => c.path)).toEqual(['/api/v1/artefacts/a_1', '/api/v1/scans/s_1/inventory?format=csv', '/api/v1/scans/s_1/export?format=json']);
    expect(headersOf(calls[0])).toMatchObject({ 'X-API-KEY': 'test-key' });
  });

  it('turns a refusal into an ApiError with its code', async () => {
    stub(() => json({ detail: 'the host is not registered for this tenant', code: 'host_not_registered' }, 422));
    const err = await api.scans.create(auth, spec).catch((e: unknown) => e);
    expect(err).toBeInstanceOf(ApiError);
    expect((err as ApiError).status).toBe(422);
    expect((err as ApiError).code).toBe('host_not_registered');
  });

  it('a failed artefact fetch rejects instead of returning an error body as an image', async () => {
    stub(() => json({ detail: 'not found', code: 'not_found' }, 404));
    await expect(api.scans.artefact(auth, 'a_missing')).rejects.toBeInstanceOf(ApiError);
  });
});
