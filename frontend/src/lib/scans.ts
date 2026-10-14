/**
 * UI-scanner page logic (mockup 12), kept pure so it can be tested without a DOM: the setup form's
 * state and the `ScanSpecIn` it becomes, the request preview, input checks, the one-page session
 * probe, the crawl tree, and the incremental event log. Value-free: the only page data it reads are
 * redacted URLs, codes and counts.
 */
import { pathOf } from '../components/scanner/format';
import { api, ApiError, errorText, type Auth } from './api';
import { isTerminal } from './jobs';
import type { ScanEvent, ScanMode, ScanPage, ScanSpecIn, SynthesisSeed } from './types';

/** The setup form (one object, mirrored into the request preview). */
export interface ScanConfig {
  url: string; maxPages: number; maxDepth: number; deny: string[]; sameOrigin: boolean; iframes: boolean; shadowDom: boolean;
  /** Stored-session secret ref; '' = none (public pages only). */
  session: string;
  mode: ScanMode; personas: number; locale: string; screenshots: boolean;
  /** Obfuscate mode: return the key in clear (TLS only) or wrapped with the caller's RSA public key. */
  wrap: 'clear' | 'rsa'; pem: string;
}

export const DEMO_SESSION = 'dps/ui/demo-onboarding';
export const DEFAULT_CONFIG: ScanConfig = {
  url: 'http://demo-site:8410/apply', maxPages: 50, maxDepth: 3, deny: ['/logout', '/admin'], sameOrigin: true, iframes: true,
  shadowDom: true, session: DEMO_SESSION, mode: 'synthesize', personas: 20, locale: 'en_IN', screenshots: true, wrap: 'clear', pem: '',
};

/** The locales the persona engine supports (dps/engine/synth/personas.py SUPPORTED_LOCALES). */
export const LOCALES: [string, string][] = [
  ['en_IN', 'en_IN · India'], ['en_US', 'en_US · United States'], ['en_GB', 'en_GB · United Kingdom'], ['en_CA', 'en_CA · Canada'],
  ['es_MX', 'es_MX · Mexico'],
];

export const MAX_PAGES = 500;
export const MAX_DEPTH = 5;
export const MAX_PERSONAS = 50;

function body(cfg: ScanConfig, probe: boolean): ScanSpecIn {
  const session = cfg.session.trim();
  return {
    url: cfg.url.trim(),
    scope: {
      same_origin: cfg.sameOrigin, max_pages: probe ? 1 : cfg.maxPages, max_depth: probe ? 0 : cfg.maxDepth, allow: [], deny: [...cfg.deny],
      iframes: cfg.iframes, shadow_dom: cfg.shadowDom, spa_clicks: !probe,
    },
    auth: { storage_state_ref: session || null },
    mode: cfg.mode,
    fill: { personas: probe ? 1 : cfg.personas, locale: cfg.locale, display_persona: 1 },
    parallel_view: !probe,
    capture: { screenshots: probe ? false : cfg.screenshots, har: false },
  };
}

/**
 * `POST /api/v1/scans` body. `consistency` carries the persona-preview seed (so persona 1 of the scan
 * is the one previewed) and, in obfuscate mode with an RSA key chosen, the wrap key.
 */
export function buildSpec(cfg: ScanConfig, seed: SynthesisSeed | null): ScanSpecIn {
  const spec = body(cfg, false);
  const consistency: NonNullable<ScanSpecIn['consistency']> = {};
  if (seed) consistency.synthesis_seed = seed;
  if (cfg.mode === 'obfuscate' && cfg.wrap === 'rsa' && cfg.pem.trim()) consistency.wrap_key = { type: 'rsa-oaep-256', public_key_pem: cfg.pem.trim() };
  if (Object.keys(consistency).length) spec.consistency = consistency;
  return spec;
}

/** What the Request card shows and copies: the body without `consistency` (no seed, no key). */
export function previewBody(cfg: ScanConfig): ScanSpecIn { return body(cfg, false); }
export function requestJson(cfg: ScanConfig): string { return compactJson(previewBody(cfg)); }

/** JSON with mockup 12's density: arrays of scalars and short flat objects stay on one line. */
export function compactJson(value: unknown, indent = ''): string {
  const scalar = (v: unknown): boolean => v === null || typeof v !== 'object';
  if (scalar(value)) return JSON.stringify(value);
  const inner = `${indent}  `;
  if (Array.isArray(value)) {
    if (value.every(scalar)) return `[${value.map((v) => JSON.stringify(v)).join(', ')}]`;
    return `[\n${value.map((v) => inner + compactJson(v, inner)).join(',\n')}\n${indent}]`;
  }
  const entries = Object.entries(value as Record<string, unknown>);
  if (!entries.length) return '{}';
  const flat = entries.every(([, v]) => scalar(v)) ? `{ ${entries.map(([k, v]) => `${JSON.stringify(k)}: ${JSON.stringify(v)}`).join(', ')} }` : null;
  if (flat && flat.length + indent.length <= 56) return flat;
  return `{\n${entries.map(([k, v]) => `${inner}${JSON.stringify(k)}: ${compactJson(v, inner)}`).join(',\n')}\n${indent}}`;
}

/** The one-page probe the Check session button runs (no fill, no screenshots, no SPA clicks). */
export function probeSpec(cfg: ScanConfig): ScanSpecIn { return body(cfg, true); }

/**
 * The form state from a scan's value-free `dps_job.spec` (to restart with the same settings). The
 * stored `url` is redacted and the wrap key is not stored, so `configError` makes the operator enter
 * the full start URL (when it holds a placeholder) and the RSA public key (when it was wrapped) again.
 */
export function configFromSpec(spec: Record<string, any>): ScanConfig {
  const scope = spec.scope ?? {};
  return {
    ...DEFAULT_CONFIG,
    url: typeof spec.url === 'string' ? spec.url : DEFAULT_CONFIG.url,
    maxPages: scope.max_pages ?? DEFAULT_CONFIG.maxPages, maxDepth: scope.max_depth ?? DEFAULT_CONFIG.maxDepth,
    deny: Array.isArray(scope.deny) ? scope.deny : [], sameOrigin: scope.same_origin ?? true, iframes: scope.iframes ?? true,
    shadowDom: scope.shadow_dom ?? true, session: spec.auth?.storage_state_ref ?? '', mode: spec.mode === 'obfuscate' ? 'obfuscate' : 'synthesize',
    personas: spec.fill?.personas ?? DEFAULT_CONFIG.personas, locale: spec.fill?.locale ?? DEFAULT_CONFIG.locale,
    screenshots: spec.capture?.screenshots ?? true,
    // the stored spec keeps only the flag, never the key: ask for the public key again rather than
    // silently returning the restarted scan's key in clear
    wrap: spec.consistency?.key_wrapped === true ? 'rsa' : 'clear', pem: '',
  };
}

/**
 * The placeholders `redact_url` writes into a stored start URL (`<v>`, `<ENTITY>`, `<k>`, `{id}`,
 * also percent-encoded). A URL holding one is the value-free copy of a saved scan, not a target.
 */
const REDACTION_PLACEHOLDER = /<[A-Za-z_]+>|%3C[A-Za-z_]+%3E|\{id\}|%7Bid%7D/i;

/** Why the start URL cannot be crawled or probed, or null. */
export function startUrlError(url: string): string | null {
  if (!/^https?:\/\/[^/\s@]+/i.test(url.trim())) return 'The start URL must be an http(s) address without a user name or password.';
  if (REDACTION_PLACEHOLDER.test(url)) return 'The start URL of a saved scan is stored redacted; enter the full start URL again.';
  return null;
}

const DENY_PATH = /^\/[A-Za-z0-9._~\-/*{}]*$/;

/** Why a path cannot join the deny list (mockup 12's messages), or null. */
export function denyPathError(path: string, existing: string[]): string | null {
  if (!DENY_PATH.test(path)) return 'Paths start with / and contain no spaces or query strings, for example /payments.';
  if (existing.includes(path)) return `${path} is already on the list.`;
  return null;
}

/** The first input the API would refuse, in words, or null. */
export function configError(cfg: ScanConfig): string | null {
  const url = startUrlError(cfg.url);
  if (url) return url;
  if (!Number.isInteger(cfg.maxPages) || cfg.maxPages < 1 || cfg.maxPages > MAX_PAGES) return `Max pages must be between 1 and ${MAX_PAGES}.`;
  if (!Number.isInteger(cfg.maxDepth) || cfg.maxDepth < 0 || cfg.maxDepth > MAX_DEPTH) return `Max depth must be between 0 and ${MAX_DEPTH}.`;
  if (!Number.isInteger(cfg.personas) || cfg.personas < 1 || cfg.personas > MAX_PERSONAS) return `Personas must be between 1 and ${MAX_PERSONAS}.`;
  if (cfg.mode === 'obfuscate' && cfg.wrap === 'rsa' && !cfg.pem.includes('BEGIN PUBLIC KEY')) return 'Paste the RSA public key (PEM) to wrap the key with, or return it in clear.';
  return null;
}

const CREATE_ERRORS: Record<string, string> = {
  host_not_registered: 'This host is not registered for the tenant. Register it in Admin › UI-scanner hosts (M5); locally the registered hosts are localhost, 127.0.0.1 and demo-site.',
  url_invalid: 'The start URL must be an http(s) address without a user name or password.',
  start_url_denied: 'The start URL is on the never-visit list (or outside the allowed paths); choose another start page or change the scope.',
  scope_invalid: `The scope is not valid: max pages from 1 up to the service's per-scan cap (DPS_SCAN_MAX_PAGES, 50 by default), max depth 0–${MAX_DEPTH}, never-visit paths start with /.`,
  har_not_supported: 'HAR files are not supported: a HAR holds response bodies with personal data.',
  auth_invalid: 'The stored session reference is not valid for this tenant.',
  secret_ref_foreign_tenant: 'The stored session reference names another tenant\'s secret folder; a reference is relative to this tenant\'s own folder.',
  secret_ref_not_shared: 'The shared session folder does not list this tenant.',
  secret_ref_invalid: 'The stored session reference is not a valid relative path (lower-case segments, none starting with a dot or an underscore).',
  inline_auth_forbidden: 'Inline sessions are refused here; use a stored session reference.',
  fill_invalid: `The fill settings are not valid: personas 1–${MAX_PERSONAS}, a supported locale, and the displayed persona among them.`,
  policy_invalid: 'The policy of this scan does not resolve; choose a preset or a valid policy.',
  key_material_invalid: 'The supplied key material or synthesis seed is not valid; start without it to get fresh ones.',
  wrap_key_invalid: 'The RSA public key cannot be read; paste a PEM "BEGIN PUBLIC KEY" block, or return the key in clear.',
  kek_unconfigured: 'This service cannot seal a scan (DPS_JOB_KEK is not configured); ask the operator of the service.',
};

/** Refusals about the start URL itself: shown under the Start URL field, not under the Start button. */
export const START_URL_CODES = ['url_invalid', 'host_not_registered', 'start_url_denied'];

/** A refusal of POST /scans in words (codes only; the URL is never echoed). */
export function createErrorText(e: unknown): string {
  if (e instanceof ApiError && e.code && CREATE_ERRORS[e.code]) return CREATE_ERRORS[e.code];
  return errorText(e);
}

export interface SessionVerdict { ok: boolean; text: string }

/** Page 1 of the probe scan: valid when it loaded without a redirect. */
export function sessionVerdict(page: ScanPage | undefined): SessionVerdict {
  if (!page) return { ok: false, text: '✗ The check found no page; is the start URL reachable?' };
  if (page.status === 'redirected' || (page.final_url_redacted && page.final_url_redacted !== page.url_redacted)) {
    return { ok: false, text: `✗ Redirected to ${pathOf(page.final_url_redacted)} — the stored session is not valid` };
  }
  if (page.status === 'done') return { ok: true, text: `✓ Session valid · ${pathOf(page.url_redacted)} returned ${page.http_status ?? 200} without a redirect` };
  return { ok: false, text: `✗ The page could not be checked (${(page.status_reason ?? page.status).replace(/_/g, ' ')})` };
}

/**
 * Check session: run a one-page probe scan, wait for it, read page 1. A probe that does not finish in
 * `timeoutMs` is canceled.
 */
export async function checkSession(auth: Auth, cfg: ScanConfig, opts: { sleep?: (ms: number) => Promise<void>; now?: () => number; timeoutMs?: number; intervalMs?: number } = {}): Promise<SessionVerdict> {
  const sleep = opts.sleep ?? ((ms: number) => new Promise<void>((r) => { window.setTimeout(r, ms); }));
  const now = opts.now ?? (() => Date.now());
  const until = now() + (opts.timeoutMs ?? 60_000);
  const bad = startUrlError(cfg.url);
  if (bad) return { ok: false, text: `✗ ${bad}` };
  const created = await api.scans.create(auth, probeSpec(cfg));
  const id = created.scan.id;
  for (;;) {
    const d = await api.scans.get(auth, id);
    if (isTerminal(d.scan)) {
      if (d.scan.state !== 'succeeded') return { ok: false, text: `✗ The check ${d.scan.state === 'canceled' ? 'was canceled' : 'failed'}${d.scan.error_code ? ` (${d.scan.error_code})` : ''}` };
      const pages = await api.scans.pages(auth, id, 0, 1);
      return sessionVerdict(pages.items[0]);
    }
    if (now() >= until) {
      await api.scans.cancel(auth, id).catch(() => undefined);
      return { ok: false, text: '✗ The check did not finish in time; try again' };
    }
    await sleep(opts.intervalMs ?? 1000);
  }
}

export interface CrawlNode { page: ScanPage; children: CrawlNode[] }

/** The crawl map: pages under their parent (page order); a page whose parent is not listed is a root. */
export function crawlTree(pages: ScanPage[]): CrawlNode[] {
  const nodes = new Map<number, CrawlNode>();
  const sorted = [...pages].sort((a, b) => a.page_no - b.page_no);
  for (const p of sorted) nodes.set(p.page_no, { page: p, children: [] });
  const roots: CrawlNode[] = [];
  for (const p of sorted) {
    const node = nodes.get(p.page_no) as CrawlNode;
    const parent = p.parent_page_no !== null && p.parent_page_no !== p.page_no ? nodes.get(p.parent_page_no) : undefined;
    (parent ? parent.children : roots).push(node);
  }
  return roots;
}

/** Blocked requests per page, from the event feed (`kind=request, result=blocked`). */
export function blockedByPage(events: ScanEvent[]): Map<number, number> {
  const m = new Map<number, number>();
  for (const e of events) if (e.kind === 'request' && e.result === 'blocked' && e.page_no !== null) m.set(e.page_no, (m.get(e.page_no) ?? 0) + 1);
  return m;
}

export function mergeEvents(prev: ScanEvent[], next: ScanEvent[]): ScanEvent[] {
  if (!next.length) return prev;
  const bySeq = new Map(prev.map((e) => [e.seq, e]));
  for (const e of next) bySeq.set(e.seq, e);
  return [...bySeq.values()].sort((a, b) => a.seq - b.seq);
}

/** The navigation log as JSONL (one value-free event per line), built from the events already fetched. */
export function eventsJsonl(events: ScanEvent[]): string {
  return `${events.map((e) => JSON.stringify(e)).join('\n')}\n`;
}

/** Every page of a scan (the pages route pages by `after` page number). */
export async function fetchAllPages(auth: Auth, id: string, maxRounds = 20): Promise<ScanPage[]> {
  const out: ScanPage[] = [];
  let after = 0;
  for (let i = 0; i < maxRounds; i += 1) {
    const page = await api.scans.pages(auth, id, after, 200);
    out.push(...page.items);
    if (!page.next_cursor || !page.items.length) break;
    after = page.items[page.items.length - 1].page_no;
  }
  return out;
}

/** Events after `after` (seq), following the cursor. */
export async function fetchNewEvents(auth: Auth, id: string, after: number, maxRounds = 10): Promise<ScanEvent[]> {
  const out: ScanEvent[] = [];
  let cursor = after;
  for (let i = 0; i < maxRounds; i += 1) {
    const page = await api.scans.events(auth, id, cursor, 200);
    out.push(...page.items);
    if (!page.next_cursor || !page.items.length) break;
    cursor = page.items[page.items.length - 1].seq;
  }
  return out;
}
