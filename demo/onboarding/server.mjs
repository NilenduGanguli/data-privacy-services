// Demo onboarding web app for the DPS UI scanner (M4). Node 22 built-ins only.
// Every value it renders is synthetic (data.json) and stands in for customer data in tests.
// GET /__received reports every non-GET request and every GET form submission it ever received,
// so tests can prove that the scanner never submitted anything.
import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join, normalize, dirname, sep } from 'node:path';
import { fileURLToPath } from 'node:url';
import * as site from './site.mjs';

const HERE = dirname(fileURLToPath(import.meta.url));
const STATIC = join(HERE, 'static');
const arg = (name) => { const i = process.argv.indexOf(name); return i > -1 ? process.argv[i + 1] : undefined; };
const PORT = Number(arg('--port') ?? process.env.DEMO_PORT ?? 8410);
const HOST = arg('--host') ?? process.env.DEMO_HOST ?? '127.0.0.1';
const SESSION = 'demo-valid';
const TYPES = { '.css': 'text/css', '.js': 'text/javascript', '.json': 'application/json' };
const received = { non_get: 0, form_get: 0, logout_hits: 0, admin_hits: 0, by_path: {} };

const hasSession = (req) => (req.headers.cookie ?? '').split(/;\s*/).includes(`demo_session=${SESSION}`);
const send = (res, status, body, type = 'text/html; charset=utf-8', extra = {}) => {
  res.writeHead(status, { 'content-type': type, 'cache-control': 'no-store', ...extra });
  res.end(body);
};
const redirect = (res, to, extra = {}) => { res.writeHead(302, { location: to, ...extra }); res.end(); };

const server = createServer(async (req, res) => {
  let url;
  try { url = new URL(req.url, 'http://demo.invalid'); } catch { return send(res, 400, 'bad request', 'text/plain'); }
  const path = url.pathname.replace(/\/+$/, '') || '/';
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    if (!path.startsWith('/__')) {
      received.non_get += 1;
      const key = `${req.method} ${path}`;
      received.by_path[key] = (received.by_path[key] ?? 0) + 1;
    }
    req.resume();
    return path.startsWith('/api/') ? send(res, 204, '') : (res.writeHead(303, { location: '/thanks' }), res.end());
  }
  if (path === '/health') return send(res, 200, JSON.stringify({ status: 'ok' }), 'application/json');
  if (path === '/__received') return send(res, 200, JSON.stringify(received), 'application/json');
  if (path.startsWith('/static/')) {
    const file = normalize(join(HERE, path));
    if (!file.startsWith(STATIC + sep)) return send(res, 404, 'not found', 'text/plain');
    try { return send(res, 200, await readFile(file), TYPES[extname(file)] ?? 'application/octet-stream'); }
    catch { return send(res, 404, 'not found', 'text/plain'); }
  }
  if (path === '/logout') { received.logout_hits += 1; return redirect(res, '/', { 'set-cookie': 'demo_session=; Path=/; Max-Age=0' }); }
  if (path.startsWith('/admin')) { received.admin_hits += 1; return send(res, 403, site.layout('Forbidden', '<p>Administrators only.</p>')); }
  if (path === '/help/track/result') { received.form_get += 1; return send(res, 200, site.layout('Tracking', '<p>Lookup is disabled in the demo.</p>')); }
  if (path.startsWith('/profile') && !hasSession(req)) return redirect(res, `/login?next=${encodeURIComponent(path)}`);
  const html = site.render(path, url);
  return html === null ? send(res, 404, site.layout('Not found', '<p>No such page.</p>')) : send(res, 200, html);
});

server.listen(PORT, HOST, () => console.log(`demo onboarding site on http://${HOST}:${PORT}`));
// PID 1 in a container has no default SIGTERM handler: exit at once (nothing to flush).
for (const signal of ['SIGTERM', 'SIGINT']) process.on(signal, () => process.exit(0));
