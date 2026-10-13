/**
 * DEV ONLY — a fixture server for the UI-scanner routes (contract §9), so the console pages can be
 * built and checked before the scans API exists (`VITE_DPS_FIXTURES=1 npm run dev`). It is reached
 * only through src/dev/fixtureMode.ts, which main.tsx imports behind `import.meta.env.DEV`; the
 * production build drops it and `npm run build` fails if it ever lands in dist (scripts/check-bundle.mjs).
 *
 * It answers like the real API — the same shapes, the same refusal codes for a few accept-time checks
 * — and plays a created scan over time: pages appear queued, are visited, filled and captured; the
 * scan succeeds with exports and an evidence id, or stops when canceled. Every value is a fixture.
 */
import {
  DEMO_ORIGIN, demoScan, FIXTURE_KEY, FIXTURE_SCAN_ID, FIXTURE_SEED, inventoryRows, personaPreview, scanJob, summarize,
  type DemoScan,
} from '../lib/scanFixtures';
import type {
  Job, JobState, KeyMaterial, ScanEvent, ScanField, ScanPage, ScanSpecIn, ScanSummary, SynthesisSeed,
} from '../lib/types';

export const FIXTURE_MARKER = 'dps-fixture-mode';

export interface ArtefactInfo { id: string; kind: string; page: ScanPage | null; fields: ScanField[] }
export interface FixtureServerOptions {
  now?: () => number;
  /** How long a created scan takes to crawl the whole demo site. */
  durationMs?: number;
  /** Paints a screenshot artefact (the browser fixture mode draws one on a canvas). */
  png?: (info: ArtefactInfo) => Promise<Blob>;
}

interface ScanRecord {
  id: string; spec: ScanSpecIn; createdAt: number; canceledAt: number | null; probe: boolean; full: DemoScan; origin: string;
}

const REGISTERED = new Set(['localhost', '127.0.0.1', 'demo-site']);
const LOCALES = new Set(['en_US', 'en_IN', 'en_GB', 'en_CA', 'es_MX']);
const SPEC_KEYS = new Set(['url', 'scope', 'auth', 'mode', 'fill', 'parallel_view', 'capture', 'consistency', 'policy', 'purpose']);
const QUEUED_MS = 1200;
// A 1×1 transparent PNG: the default screenshot when no painter is given (tests).
const PNG_1X1 = Uint8Array.from(atob('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNkYAAAAAYAAjCB0C8AAAAASUVORK5CYII='), (c) => c.charCodeAt(0));

const json = (body: unknown, status = 200): Response =>
  new Response(JSON.stringify(body), { status, headers: { 'content-type': 'application/json' } });
const refuse = (status: number, code: string, detail: string): Response => json({ detail, code }, status);

function params(url: string): URLSearchParams { return new URLSearchParams(url.split('?')[1] ?? ''); }

/** The stored start URL as `redact_url` writes it (roughly): numeric path segments `{id}`, query values `<v>`, no fragment. */
function redactedStart(url: string): string {
  const [base, query] = url.split('#')[0].split('?');
  const path = base.replace(/\/\d+(?=\/|$)/g, '/{id}');
  return query ? `${path}?${query.split('&').map((kv) => `${kv.split('=')[0]}=<v>`).sort().join('&')}` : path;
}

export class ScanFixtureServer {
  private readonly now: () => number;
  private readonly durationMs: number;
  private readonly png?: (info: ArtefactInfo) => Promise<Blob>;
  private readonly scans = new Map<string, ScanRecord>();
  private readonly demo = demoScan();
  private counter = 0;

  constructor(opts: FixtureServerOptions = {}) {
    this.now = opts.now ?? (() => Date.now());
    this.durationMs = opts.durationMs ?? 45_000;
    this.png = opts.png;
  }

  /** The fixture answer for a request, or null when the route is not a scanner route (pass through). */
  async handle(method: string, url: string, body?: string): Promise<Response | null> {
    const path = url.replace(/^https?:\/\/[^/]+/, '').split('?')[0];
    const m = method.toUpperCase();
    if (path === '/api/v1/scans') return m === 'POST' ? this.create(body) : this.list(url);
    if (path === '/api/v1/scans/personas/preview' && m === 'POST') {
      const req = JSON.parse(body || '{}') as { locale?: string; synthesis_seed?: SynthesisSeed | null };
      if (req.locale && !LOCALES.has(req.locale)) return refuse(422, 'locale_unsupported', 'locale is not supported');
      return json(personaPreview(req.locale ?? 'en_US', req.synthesis_seed ?? null));
    }
    const art = /^\/api\/v1\/artefacts\/([^/]+)$/.exec(path);
    if (art) return this.artefact(decodeURIComponent(art[1]));
    const sm = /^\/api\/v1\/scans\/([^/]+)(\/.*)?$/.exec(path);
    if (!sm) return null;
    const id = decodeURIComponent(sm[1]);
    const rest = sm[2] ?? '';
    const view = this.view(id);
    if (!view) return refuse(404, 'not_found', 'scan not found');
    if (rest === '' && m === 'GET') return json({ scan: view.job, summary: view.summary });
    if (rest === '/cancel' && m === 'POST') return this.cancel(id);
    if (rest === '/pages') {
      const q = params(url);
      const after = Number(q.get('after') ?? 0);
      const limit = Number(q.get('limit') ?? 200);
      const items = view.pages.filter((p) => p.page_no > after).slice(0, limit);
      const more = view.pages.some((p) => p.page_no > (items[items.length - 1]?.page_no ?? Infinity));
      return json({ items, next_cursor: more ? String(items[items.length - 1].page_no) : null });
    }
    const pm = /^\/pages\/(\d+)$/.exec(rest);
    if (pm) {
      const pg = view.pages.find((p) => p.page_no === Number(pm[1]));
      if (!pg) return refuse(404, 'not_found', 'page not found');
      const done = pg.status === 'done' || pg.status === 'redirected';
      return json({ page: pg, fields: done ? view.fields[pg.page_no] ?? [] : [], requests: done ? view.full.requests[pg.page_no] ?? [] : [] });
    }
    if (rest === '/events') {
      const q = params(url);
      const after = Number(q.get('after') ?? 0);
      const limit = Number(q.get('limit') ?? 200);
      const items = view.events.filter((e) => e.seq > after).slice(0, limit);
      const more = view.events.some((e) => e.seq > (items[items.length - 1]?.seq ?? Infinity));
      return json({ items, next_cursor: more ? String(items[items.length - 1].seq) : null });
    }
    if (rest === '/inventory' || rest === '/export') {
      const format = params(url).get('format') ?? 'json';
      const items = inventoryRows(view.pages, view.fields);
      if (rest === '/inventory') {
        if (format === 'csv') return this.csv(['page_no', 'path', 'form_index', 'field_no', 'label', 'tag', 'input_type', 'entity_type', 'sensitivity', 'confidence', 'detectors', 'risk_codes', 'fill_status'], items as unknown as Record<string, unknown>[]);
        return json({ items, risk_flags: view.summary.risk_flags });
      }
      if (!view.summary.export_artefacts.json) return refuse(409, 'scan_not_finished', 'the export is ready when the scan has finished');
      const mapping = view.pages.flatMap((p) => (view.fields[p.page_no] ?? []).filter((f) => f.entity_type).map((f) => ({
        page_no: p.page_no, path: p.template, field_no: f.field_no, entity_type: f.entity_type, persona_attr: f.persona_attr,
        synthetic_value: f.fill_synthesize, token: f.fill_obfuscate,
      })));
      if (format === 'csv') return this.csv(['page_no', 'path', 'field_no', 'entity_type', 'persona_attr', 'synthetic_value', 'token'], mapping);
      return json({ scan_id: id, personas: [personaPreview(view.summary.locale).personas[0]], mapping });
    }
    return refuse(404, 'not_found', 'route not found');
  }

  private list(url: string): Response {
    const q = params(url);
    const state = q.get('state');
    const limit = Number(q.get('limit') ?? 50);
    const jobs = [...this.scans.keys()].reverse().map((id) => this.view(id)?.job).filter((j): j is Job => !!j);
    const items = [...jobs, this.demo.job].filter((j) => !state || j.state === state).slice(0, limit);
    return json({ items, next_cursor: null });
  }

  private create(body: string | undefined): Response {
    let spec: ScanSpecIn;
    try { spec = JSON.parse(body || '{}') as ScanSpecIn; } catch { return refuse(422, 'body_invalid', 'the body is not JSON'); }
    const extra = Object.keys(spec).filter((k) => !SPEC_KEYS.has(k));
    if (extra.length) return json({ detail: extra.map((k) => ({ loc: ['body', k], msg: 'Extra inputs are not permitted' })) }, 422);
    const um = /^(https?):\/\/([^/:?#@]+)(:\d+)?(\/[^?#]*)?/i.exec(spec.url ?? '');
    if (!um || spec.url.includes('@')) return refuse(422, 'url_invalid', 'the start URL must be http(s) without user information');
    if (!REGISTERED.has(um[2].toLowerCase())) return refuse(422, 'host_not_registered', 'the host is not registered for this tenant');
    if (spec.capture?.har) return refuse(422, 'har_not_supported', 'HAR capture is not supported: a HAR holds response bodies');
    const sc = spec.scope;
    if (!sc || (sc.max_pages !== null && (sc.max_pages < 1 || sc.max_pages > 500)) || (sc.max_depth !== null && (sc.max_depth < 0 || sc.max_depth > 5))) {
      return refuse(422, 'scope_invalid', 'max_pages must be 1–500 and max_depth 0–5');
    }
    if (!LOCALES.has(spec.fill?.locale) || spec.fill.personas < 1 || spec.fill.personas > 50 || spec.fill.display_persona > spec.fill.personas) {
      return json({ detail: [{ loc: ['body', 'fill'], msg: 'personas must be 1–50 and the locale supported' }] }, 422);
    }
    this.counter += 1;
    const id = `s_01K7ZRFX${String(this.counter).padStart(18, '0')}`;
    const origin = `${um[1].toLowerCase()}://${um[2].toLowerCase()}${um[3] ?? ''}`;
    const rec: ScanRecord = { id, spec, createdAt: this.now(), canceledAt: null, probe: sc.max_pages === 1, full: demoScan(id), origin };
    this.scans.set(id, rec);
    const wrap = spec.consistency?.wrap_key;
    const key: KeyMaterial = wrap
      ? { ...FIXTURE_KEY, kid: `k_fx${this.counter}`, dek: null, wrapped_dek: 'V1JBUFBFRC1GSVhUVVJFLU9OTFk=', wrap: { type: wrap.type, kid: 'caller-key' } }
      : { ...FIXTURE_KEY, kid: `k_fx${this.counter}` };
    return json({ scan: this.view(id)?.job, key_material: key, synthesis_seed: spec.consistency?.synthesis_seed ?? FIXTURE_SEED, replay: false }, 202);
  }

  private cancel(id: string): Response {
    const rec = this.scans.get(id);
    const view = this.view(id);
    if (!rec || !view) return refuse(409, 'job_terminal', 'the scan has finished');
    if (view.job.state === 'succeeded' || view.job.state === 'canceled') return refuse(409, 'job_terminal', 'the scan has finished');
    rec.canceledAt = this.now();
    return json(this.view(id)?.job);
  }

  private csv(cols: string[], rows: Record<string, unknown>[]): Response {
    const cell = (v: unknown): string => {
      const s = Array.isArray(v) ? v.join('|') : v === null || v === undefined ? '' : String(v);
      return /[",\n]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
    };
    const text = [cols.join(','), ...rows.map((r) => cols.map((c) => cell(r[c])).join(','))].join('\n');
    return new Response(text, { status: 200, headers: { 'content-type': 'text/csv' } });
  }

  private async artefact(id: string): Promise<Response> {
    const m = /^art_(\d+)_(original|annotated|filled_synthesize|filled_obfuscate|masked_capture|capture_json)$/.exec(id);
    const ex = /^art_(.+)_export_(json|csv)$/.exec(id);
    if (ex) return this.handle('GET', `/api/v1/scans/${ex[1]}/export?format=${ex[2]}`, undefined) as Promise<Response>;
    if (!m) return refuse(404, 'not_found', 'artefact not found');
    const page = this.demo.pages.find((p) => p.page_no === Number(m[1])) ?? null;
    if (!page || !Object.values(page.artefacts).includes(id)) return refuse(404, 'not_found', 'artefact not found');
    const fields = this.demo.fields[page.page_no] ?? [];
    if (m[2] === 'capture_json') {
      const entries = Object.entries(page.rendered_findings).map(([entity, n], i) => ({ n: i + 1, entity_type: entity, count: n, token: `[[PII:${entity}:fx${i}…]]` }));
      return json({ page_no: page.page_no, kind: 'reveal_map', entries, note: 'reveal with POST /api/v1/reveal and this scan’s key material' });
    }
    const blob = this.png ? await this.png({ id, kind: m[2], page, fields }) : new Blob([PNG_1X1], { type: 'image/png' });
    return new Response(blob, { status: 200, headers: { 'content-type': 'image/png' } });
  }

  /** The scan as the API would show it now. */
  private view(id: string): { job: Job; pages: ScanPage[]; fields: Record<number, ScanField[]>; events: ScanEvent[]; summary: ScanSummary; full: DemoScan } | null {
    if (id === FIXTURE_SCAN_ID) return { job: this.demo.job, pages: this.demo.pages, fields: this.demo.fields, events: this.demo.events, summary: this.demo.summary, full: this.demo };
    const rec = this.scans.get(id);
    if (!rec) return null;
    return rec.probe ? this.probeView(rec) : this.crawlView(rec);
  }

  private jobFor(rec: ScanRecord, state: JobState, s: ScanSummary, endMs: number): Job {
    const { spec } = rec;
    const started = state === 'queued' ? null : new Date(rec.createdAt + QUEUED_MS).toISOString();
    const terminal = state === 'succeeded' || state === 'canceled';
    return scanJob({
      id: rec.id, state, mode: spec.mode, created_at: new Date(rec.createdAt).toISOString(), updated_at: new Date(endMs).toISOString(),
      started_at: started, finished_at: terminal ? new Date(endMs).toISOString() : null,
      spec: {
        url: redactedStart(spec.url), url_origin: rec.origin, scope: spec.scope, auth: { storage_state_ref: spec.auth.storage_state_ref },
        mode: spec.mode, fill: spec.fill, parallel_view: spec.parallel_view, capture: spec.capture,
        consistency: { scope: spec.consistency?.scope ?? 'request', key_wrapped: !!spec.consistency?.wrap_key }, policy: spec.policy ?? null,
      },
      progress: { ...scanJob().progress, outcome: state === 'succeeded' ? 'succeeded' : state === 'canceled' ? 'canceled' : '', evidence_id: s.evidence_id, pages: s.pages_visited, fields: s.fields, blocked_requests: Object.values(s.blocked_requests).reduce((a, b) => a + b, 0) },
    });
  }

  private crawlView(rec: ScanRecord): ReturnType<ScanFixtureServer['view']> {
    const end = rec.canceledAt ?? this.now();
    const elapsed = end - rec.createdAt;
    const all = rec.full.pages.map((p) => this.reorigin(p, rec.origin));
    const n = all.length;
    const p = Math.max(0, Math.min(1, (elapsed - QUEUED_MS) / this.durationMs));
    const k = elapsed < QUEUED_MS ? -1 : Math.floor(p * n);
    const pages: ScanPage[] = [];
    all.forEach((pg, i) => {
      if (k < 0) return;
      if (i < k || (p >= 1)) pages.push(pg);
      else if (i === k && !rec.canceledAt) pages.push(this.blank(pg, pg.status === 'skipped' ? 'skipped' : 'visiting'));
      else if (i <= k + 3 && !rec.canceledAt) pages.push(pg.status === 'skipped' ? pg : this.blank(pg, 'queued'));
      else if (i <= k + 3 && rec.canceledAt) pages.push(this.blank(pg, 'skipped', 'budget'));
    });
    const doneNos = new Set(pages.filter((pg) => pg.status === 'done' || pg.status === 'skipped').map((pg) => pg.page_no));
    const fields: Record<number, ScanField[]> = {};
    for (const no of doneNos) fields[no] = rec.full.fields[no] ?? [];
    const visiting = pages.find((pg) => pg.status === 'visiting');
    const events = rec.full.events
      .filter((e) => (e.page_no !== null && doneNos.has(e.page_no)) || (visiting && e.page_no === visiting.page_no && e.kind === 'navigate'))
      .map((e, i) => ({ ...e, seq: i + 1, target: e.target.replace(DEMO_ORIGIN, rec.origin), ts: new Date(rec.createdAt + QUEUED_MS + (e.page_no ?? 1) * (this.durationMs / n)).toISOString() }));
    const state: JobState = rec.canceledAt ? 'canceled' : k < 0 ? 'queued' : p >= 1 ? 'succeeded' : 'running';
    const finished = state === 'succeeded';
    const summary = summarize(pages, fields, events, {
      mode: rec.spec.mode, locale: rec.spec.fill.locale, personas: rec.spec.fill.personas, display_persona: rec.spec.fill.display_persona,
      export_artefacts: finished || rec.canceledAt ? rec.full.summary.export_artefacts : {}, evidence_id: finished || rec.canceledAt ? `ev_fx_${rec.id.slice(-6)}` : null,
      elapsed_s: k < 0 ? null : Math.round((end - rec.createdAt - QUEUED_MS) / 1000),
    });
    return { job: this.jobFor(rec, state, summary, end), pages, fields, events, summary, full: rec.full };
  }

  private probeView(rec: ScanRecord): ReturnType<ScanFixtureServer['view']> {
    const end = rec.canceledAt ?? this.now();
    const elapsed = end - rec.createdAt;
    const path = /^[a-z]+:\/\/[^/]+(\/[^?#]*)?/i.exec(rec.spec.url)?.[1] || '/';
    const done = elapsed >= QUEUED_MS + 1500;
    const valid = !!rec.spec.auth.storage_state_ref;
    const pg = this.blank(this.reorigin(this.demo.pages[0], rec.origin), done ? (valid ? 'done' : 'redirected') : elapsed >= QUEUED_MS ? 'visiting' : 'queued');
    pg.url_redacted = `${rec.origin}${path}`;
    pg.template = path;
    if (done) {
      pg.http_status = valid ? 200 : 302;
      pg.final_url_redacted = valid ? null : `${rec.origin}/login`;
    }
    const pages = elapsed >= QUEUED_MS ? [pg] : [];
    const events: ScanEvent[] = done ? [{ seq: 1, ts: new Date(end).toISOString(), kind: 'navigate', method: 'GET', result: String(pg.http_status), page_no: 1, target: pg.url_redacted, note_code: 'start', counts: {} }] : [];
    const state: JobState = rec.canceledAt ? 'canceled' : done ? 'succeeded' : elapsed >= QUEUED_MS ? 'running' : 'queued';
    const summary = summarize(pages, {}, events, { mode: rec.spec.mode, locale: rec.spec.fill.locale, personas: 1, display_persona: 1 });
    return { job: this.jobFor(rec, state, summary, end), pages, fields: {}, events, summary, full: rec.full };
  }

  private reorigin(p: ScanPage, origin: string): ScanPage {
    return { ...p, url_redacted: p.url_redacted.replace(DEMO_ORIGIN, origin), final_url_redacted: p.final_url_redacted?.replace(DEMO_ORIGIN, origin) ?? null };
  }

  /** A page not yet (fully) processed: no counts, no artefacts. */
  private blank(p: ScanPage, status: ScanPage['status'], reason: string | null = null): ScanPage {
    return {
      ...p, status, status_reason: reason ?? (status === 'skipped' ? p.status_reason : null), http_status: null, form_count: 0, field_count: 0,
      personal_field_count: 0, critical_field_count: 0, forms: [], artefacts: {}, rendered_findings: {}, risk_flags: [], validation: {},
      capture_status: null, timings: {},
    };
  }
}
