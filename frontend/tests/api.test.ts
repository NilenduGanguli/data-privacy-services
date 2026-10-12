// The typed API client: multipart uploads, coded errors, helpers. fetch is stubbed per test.
import assert from 'node:assert/strict';
import { afterEach, test } from 'node:test';
import { api, ApiError, base64Blob, errorText, metricTotal } from '../src/lib/api.ts';

interface Call { path: string; init: RequestInit | undefined }
const realFetch = globalThis.fetch;
afterEach(() => { globalThis.fetch = realFetch; });

function stub(response: () => Response): Call[] {
  const calls: Call[] = [];
  globalThis.fetch = (async (path: string, init?: RequestInit) => { calls.push({ path, init }); return response(); }) as typeof fetch;
  return calls;
}

const auth = { apiKey: 'test-key', tenant: 'acme' };
const json = (body: unknown, status = 200): Response => new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });

test('document uploads are multipart: the browser writes the boundary, so no Content-Type is set', async () => {
  const calls = stub(() => json({ pages: [] }));
  const file = new File([new Uint8Array([37, 80, 68, 70])], 'kyc-scanned.pdf', { type: 'application/pdf' });
  await api.documents.obfuscate(auth, file, 'gdpr-pseudonymise', { ocr: 'auto', previews: true, redacted_pdf: true });
  assert.equal(calls.length, 1);
  assert.equal(calls[0].path, '/api/v1/documents/obfuscate');
  assert.equal(calls[0].init?.method, 'POST');
  const headers = calls[0].init?.headers as Record<string, string>;
  assert.deepEqual(headers, { 'X-API-KEY': 'test-key', 'X-DPS-Tenant': 'acme' });
  const form = calls[0].init?.body as FormData;
  assert.ok(form instanceof FormData);
  assert.equal((form.get('file') as File).name, 'kyc-scanned.pdf');
  assert.deepEqual(JSON.parse(form.get('request') as string), {
    policy: 'gdpr-pseudonymise', options: { ocr: 'auto', previews: true, redacted_pdf: true },
  });
});

test('each document route posts to its own path; an inline policy travels inside the request field', async () => {
  const calls = stub(() => json({}));
  const file = new File(['a,b\n'], 'customers.csv', { type: 'text/csv' });
  await api.documents.detect(auth, file, null, {});
  await api.documents.synthesize(auth, file, { preset: 'pci-dss', treat: {} }, { previews: true });
  assert.deepEqual(calls.map((c) => c.path), ['/api/v1/documents/detect', '/api/v1/documents/synthesize']);
  assert.deepEqual(JSON.parse((calls[1].init?.body as FormData).get('request') as string).policy, { preset: 'pci-dss', treat: {} });
});

test('JSON calls send Content-Type and no empty credentials', async () => {
  const calls = stub(() => json({ items: [], families: [] }));
  await api.entities({ apiKey: '', tenant: '' });
  assert.equal(calls[0].path, '/api/v1/entities');
  assert.deepEqual(calls[0].init?.headers, { 'Content-Type': 'application/json' });
  stub(() => json({}));
  const plan = stub(() => json({ plan: [] }));
  await api.policies.plan(auth, { data: { a: 1 }, policy: 'pci-dss', mode: 'synthesize' });
  assert.equal(plan[0].path, '/api/v1/policies/plan');
  assert.deepEqual(JSON.parse(plan[0].init?.body as string), { data: { a: 1 }, policy: 'pci-dss', mode: 'synthesize' });
});

test('policy tooling routes', async () => {
  const calls = stub(() => json({}));
  await api.policies.presets(auth);
  await api.policies.validate(auth, 'pci-dss');
  await api.policies.testRecognizer(auth, { name: 'r', supported_entity: 'EMPLOYEE_ID', patterns: [] }, 'text');
  assert.deepEqual(calls.map((c) => [c.path, c.init?.method ?? 'GET']), [
    ['/api/v1/policies/presets', 'GET'], ['/api/v1/policies/validate', 'POST'], ['/api/v1/policies/test-recognizer', 'POST'],
  ]);
  assert.deepEqual(JSON.parse(calls[1].init?.body as string), { policy: 'pci-dss' });
  assert.deepEqual(JSON.parse(calls[2].init?.body as string).text, 'text');
});

test('errors carry the status and the API code; errorText never adds request data', async () => {
  stub(() => json({ detail: 'the redacted PDF still shows a value', code: 'leakage' }, 422));
  const file = new File(['x'], 'a.txt');
  await assert.rejects(api.documents.obfuscate(auth, file, null, {}), (e: unknown) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.status, 422);
    assert.equal(e.code, 'leakage');
    assert.equal(errorText(e), '422 leakage: the redacted PDF still shows a value');
    return true;
  });
});

test('an error without a JSON body or code keeps the status text', async () => {
  stub(() => new Response('<html>bad gateway</html>', { status: 502, statusText: 'Bad Gateway' }));
  await assert.rejects(api.entities(auth), (e: unknown) => {
    assert.ok(e instanceof ApiError);
    assert.equal(e.code, null);
    assert.equal(errorText(e), '502: Bad Gateway');
    return true;
  });
  stub(() => json({ detail: [{ loc: ['body'], msg: 'field required' }] }, 422));
  await assert.rejects(api.policies.validate(auth, {}), (e: unknown) => (e as ApiError).message.includes('field required'));
  assert.equal(errorText(new Error('demo scan missing')), 'demo scan missing');
});

test('base64Blob decodes the redacted PDF bytes', async () => {
  const blob = base64Blob('JVBERi0=', 'application/pdf');
  assert.equal(blob.type, 'application/pdf');
  assert.deepEqual([...new Uint8Array(await blob.arrayBuffer())], [37, 80, 68, 70, 45]);
});

test('metricTotal sums a counter family and ignores comments', () => {
  const text = '# HELP dps_requests_total x\n# TYPE dps_requests_total counter\ndps_requests_total{route="a"} 3\ndps_requests_total{route="b"} 4.0\nother 9\n';
  assert.equal(metricTotal(text, 'dps_requests_total'), 7);
});
