/** Pure logic of the Reports & audit page (mockup 09): URL filters, sweep series, the merged
 *  evidence list (packs + daily API roll-ups), verification and outcome chips. Value-free: ids,
 *  counts, entity types, method names and paths only. Tested in reports.test.ts. */
import { shortId } from './jobs';
import type { ApiRollup, AuditFilters, EvidenceItem, EvidenceSummary, FailedSweep, SweepDay } from './types';

export type Layer = 'all' | 'api' | 'conn' | 'ui';
export type Outcome = 'all' | 'ok' | 'failed' | 'blocked';
export interface ReportFilters { days: 7 | 14 | 30; layer: Layer; outcome: Outcome; q: string; scope: 'tenant' | 'all' }
export const DEFAULT_FILTERS: ReportFilters = { days: 14, layer: 'all', outcome: 'all', q: '', scope: 'tenant' };

const LAYERS: Layer[] = ['all', 'api', 'conn', 'ui'];
const OUTCOMES: Outcome[] = ['all', 'ok', 'failed', 'blocked'];

/** Filters from the URL query (a shared link reproduces the view); unknown values fall back. */
export function parseFilters(p: URLSearchParams): ReportFilters {
  const days = Number(p.get('days'));
  const layer = p.get('layer') as Layer;
  const outcome = p.get('outcome') as Outcome;
  return {
    days: days === 7 || days === 30 ? days : 14,
    layer: LAYERS.includes(layer) ? layer : 'all',
    outcome: OUTCOMES.includes(outcome) ? outcome : 'all',
    q: (p.get('q') ?? '').slice(0, 120),
    scope: p.get('scope') === 'all' ? 'all' : 'tenant',
  };
}

/** The non-default filters as a query (defaults are left out). */
export function filtersToParams(f: ReportFilters): URLSearchParams {
  const p = new URLSearchParams();
  if (f.days !== 14) p.set('days', String(f.days));
  if (f.layer !== 'all') p.set('layer', f.layer);
  if (f.outcome !== 'all') p.set('outcome', f.outcome);
  if (f.q.trim()) p.set('q', f.q.trim());
  if (f.scope === 'all') p.set('scope', 'all');
  return p;
}

/** `GET /admin/audit` parameters for the filters (window from `now − days`). */
export function auditFilters(f: ReportFilters, now: Date = new Date()): AuditFilters {
  const out: AuditFilters = { from: new Date(now.getTime() - f.days * 86_400_000).toISOString() };
  if (f.layer !== 'all') out.layer = f.layer;
  if (f.outcome !== 'all') out.outcome = f.outcome;
  if (f.q.trim()) out.q = f.q.trim();
  if (f.scope === 'all') out.tenants = '*';
  out.limit = 50;
  return out;
}

export interface SweepPoint { day: string; passed: number; failed: number }

/** Sweeps per day for the chosen layer(s); `ok` hides failures, `failed` hides passes and
 *  `blocked` empties the chart (sweeps pass or fail; blocked requests are audit rows). */
export function sweepSeries(sweeps: SweepDay[], layer: Layer, outcome: Outcome): SweepPoint[] {
  const days = new Map<string, SweepPoint>();
  for (const s of sweeps) {
    const point = days.get(s.day) ?? { day: s.day, passed: 0, failed: 0 };
    if (layer === 'all' || s.layer === layer) {
      if (outcome === 'all' || outcome === 'ok') point.passed += s.passed;
      if (outcome === 'all' || outcome === 'failed') point.failed += s.failed;
    }
    days.set(s.day, point);
  }
  return [...days.values()].sort((a, b) => a.day.localeCompare(b.day));
}

const column = (path: string): string => path.replace(/^\$\./, '').replace(/^\$/, '');
/** A pack error's `where` that names a place, not a column (dps/layers/connectors/jobs_run.py). */
const PLACE = /^(partition \d+|prepare|finalize)$/;

/** The columns a failed sweep names: its `paths` without the places a pack error names
 *  ("partition 0", "prepare") — the API sends those for a run, never a column (S2 console r3 B1). */
export function sweepColumns(f: FailedSweep): string[] {
  return f.paths.filter((p) => !PLACE.test(p)).map(column).filter(Boolean);
}

/** "3", "3 and 4", "1, 2 and 5". */
function andList(items: (string | number)[]): string {
  return items.length < 2 ? items.join('') : `${items.slice(0, -1).join(', ')} and ${items[items.length - 1]}`;
}

/** The report layer a failed sweep belongs to: a job's partitions are Connectors, a scan is the
 *  UI scanner, an API call is API (S2 console r2 B1). */
export function failedSweepLayer(f: FailedSweep): Exclude<Layer, 'all'> {
  return f.where === 'job' ? 'conn' : f.where === 'scan' ? 'ui' : 'api';
}

/** Where a sweep failed and what that means, in words (no value is ever available to show). */
export function failedSweepText(f: FailedSweep): { where: string; what: string } {
  if (f.where === 'scan') {
    // a UI sweep is the scan's own verification; one that failed is not "failed closed"
    const what = f.error_code === 'submission_detected'
      ? 'The scan\'s request log holds a request that is not a GET and that the guard allowed. The scan was marked failed and its key purged.'
      : `The scan's verification failed (${f.error_code}). The scan was marked failed and its key purged.`;
    return { where: `UI scan ${shortId(f.job_id)}`, what };
  }
  const where = f.where === 'job'
    ? `${shortId(f.job_id)}${f.partitions.length ? ` · ${f.partitions.map((n) => `p${n}`).join(', ')}` : ''}`
    : f.route ?? 'API request';
  let what: string;
  if (f.error_code === 'leakage' && f.where === 'job') {
    // the unit that fails closed is the batch (contract §0 rule 2, §6.4): the batch the sweep
    // refused was not written, but batches committed before it may already be in the target
    // (a table, in-place or append run commits per batch) — never "nothing reached the target"
    const cols = sweepColumns(f);
    const many = f.partitions.length > 1;
    const of = f.partitions.length ? ` of ${many ? 'partitions' : 'partition'} ${andList(f.partitions)}` : '';
    what = `The leakage sweep found ${cols.length ? `source values in ${cols.join(', ')}, in` : 'a source value in'} ${many ? 'batches' : 'a batch'}${of}. `
      + `${many ? 'Those batches were' : 'That batch was'} not written (fail closed); batches committed before ${many ? 'them' : 'it'} may already be in the target. Open the job to see what was written.`;
  } else if (f.error_code === 'leakage') {
    const cols = sweepColumns(f);
    what = `The leakage sweep found ${cols.length ? `source values in ${cols.join(', ')}` : 'a source value in the output'}. The response was not sent.`;
  } else if (f.error_code === 'verification_failed') {
    what = 'The output verifier failed closed. The response carried no original value; the request fell back or was refused.';
  } else {
    what = `The sweep failed closed (${f.error_code}). Nothing that failed it was written.`;
  }
  return { where, what };
}

export type EvidenceRow =
  | { kind: 'pack'; item: EvidenceItem; created_at: string }
  | { kind: 'rollup'; rollup: ApiRollup; created_at: string; open: boolean };

/** Packs and daily API roll-ups in one list, newest first. A roll-up is sealed at 00:05 UTC the
 *  day after; until then (today's, which the API refreshes live) it is dated `now` and `open`. It
 *  belongs to the API layer and fails when a sweep of that day failed. */
export function evidenceRows(items: EvidenceItem[], rollups: ApiRollup[], f: ReportFilters, now: Date = new Date()): EvidenceRow[] {
  const q = f.q.trim().toLowerCase();
  const packs: EvidenceRow[] = f.layer === 'api' ? [] : items.map((item) => ({ kind: 'pack', item, created_at: item.created_at }));
  const rolls: EvidenceRow[] = f.layer === 'all' || f.layer === 'api'
    ? rollups
      .filter((r) => f.outcome === 'all' || (f.outcome === 'ok' ? r.verify_failed === 0 : f.outcome === 'failed' ? r.verify_failed > 0 : false))
      .filter((r) => !q || r.action.toLowerCase().includes(q) || r.day.includes(q))
      .map((rollup) => {
        const sealed = Date.parse(`${rollup.day}T00:05:00Z`) + 86_400_000;
        const open = sealed > now.getTime();
        return { kind: 'rollup', rollup, open, created_at: new Date(open ? now.getTime() : sealed).toISOString() };
      })
    : [];
  return [...packs, ...rolls].sort((a, b) => Date.parse(b.created_at) - Date.parse(a.created_at));
}

/** The Layer 1 route behind a roll-up's audit action (`document.detect` → `/documents/detect`,
 *  `schema.synthesize` → `/schema/synthesize`, `plan[:mode]` → `/policies/plan`). */
export function rollupRoute(action: string): string {
  if (action === 'artefact.read') return 'GET /api/v1/artefacts/{id}';
  if (action === 'external.detokenize') return 'POST /api/v1/reveal · bank detokenizer';
  const [head, ...rest] = action.split(/[.:]/);
  if (head === 'plan') return 'POST /api/v1/policies/plan';
  if (head === 'document' && rest.length) return `POST /api/v1/documents/${rest.join('/')}`;
  if (head === 'schema' && rest.length) return `POST /api/v1/schema/${rest.join('/')}`;
  return `POST /api/v1/${action}`;
}

export interface Chip { tone: 'emerald' | 'rose' | 'amber' | 'slate' | 'teal' | 'violet'; text: string }

/** A pack whose verification can pass: a failed, canceled or dead run's output is incomplete, so
 *  it never reads as passed (main's N3, 6ed65be). Scans have no dry run. */
export function finishedPack(item: EvidenceItem): boolean {
  return item.outcome === 'succeeded' || (item.layer !== 'ui' && item.outcome === 'dry_run');
}

/** The verification chip of an evidence row (mockup 09). Passed only for a finished pack whose
 *  `verification_passed` is exactly true; hits read as caught; an unfinished run without a hit is
 *  "not verified · job <outcome>" (slate when canceled, rose otherwise — main's verificationTone);
 *  a finished run without a hit that needs a reviewer is "review required" (amber). */
export function verificationChip(row: EvidenceRow): Chip {
  if (row.kind === 'rollup') {
    const n = row.rollup.verify_failed;
    return n > 0 ? { tone: 'rose', text: `${n} failed · fell back` } : { tone: 'emerald', text: 'passed · 0 leaks' };
  }
  const { item } = row;
  const s = item.summary;
  const hits = Number(s.hits ?? 0);
  const finished = finishedPack(item);
  if (finished && s.verification_passed === true) {
    if (item.layer === 'ui') return { tone: 'emerald', text: `passed · ${Number(s.submissions ?? 0)} POSTs left` };
    if (s.mode === 'synthesize' && s.fidelity_score !== undefined && s.fidelity_score !== null) return { tone: 'emerald', text: `passed · ${Number(s.exact_row_matches ?? 0)} exact rows` };
    return { tone: 'emerald', text: `passed · ${hits} leaks` };
  }
  if (hits > 0) return { tone: 'rose', text: `failed · ${hits} ${hits === 1 ? 'leak' : 'leaks'} caught` };
  // a run the leakage sweep stopped: a refused batch leaves no hit in the counters (it was not
  // written), and the pack seals its verification failed closed — say so, as the pack does
  if (!finished && item.outcome === 'failed' && s.verification_status === 'failed') return { tone: 'rose', text: 'failed closed' };
  if (!finished) return { tone: item.outcome === 'canceled' ? 'slate' : 'rose', text: `not verified · ${item.layer === 'ui' ? 'scan' : 'job'} ${item.outcome}` };
  // main's post-write gate (B1) and M5's D-A review: personal data in a column kept as is
  if (s.review_required === true) return { tone: 'amber', text: 'review required' };
  if (s.verification_passed === false) return { tone: 'rose', text: 'failed closed' };
  return { tone: 'slate', text: 'not verified · no verification recorded' };
}

const FAILED = new Set(['error', 'failed', 'verification_failed', 'leakage', 'dead', 'upstream_error']);
const AMBER = new Set(['rejected', 'bad_request']);

/** Outcome chip tone: emerald ok, rose failed, amber refused input, slate blocked by design. */
export function auditOutcomeTone(outcome: string): Chip['tone'] {
  if (outcome === 'ok' || outcome === 'replayed' || outcome === 'succeeded' || outcome === 'dry_run') return 'emerald';
  if (FAILED.has(outcome)) return 'rose';
  if (outcome === 'denied' || outcome === 'forbidden') return 'rose';
  return AMBER.has(outcome) ? 'amber' : 'slate';
}

/** The most-used method and how many others ran ("fpe_digits +4"). */
export function methodsSummary(methods: Record<string, number>): { first: string; more: number } | null {
  const entries = Object.entries(methods).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  return entries.length ? { first: entries[0][0], more: entries.length - 1 } : null;
}

/** "gdpr-pseudonymise v3" from an audit label "gdpr-pseudonymise@v3". */
export function policyLabel(policy: string | null | undefined): string | null {
  if (!policy) return null;
  return policy.replace(/@v(\d+)$/, ' v$1');
}

export const LAYER_LABEL: Record<string, string> = { api: 'API', conn: 'Connectors', ui: 'UI scanner', admin: 'Admin' };

/** An M2 evidence summary (`GET /evidence`) in the M5 `EvidenceItem` shape — the fallback while a
 *  deployment has no `/reports/evidence` (a backend before M5). */
export function fromSummary(s: EvidenceSummary): EvidenceItem {
  const [policy, version] = (s.policy ?? '').split(/@v| v/);
  const isScan = s.job_id.startsWith('s_');
  return {
    // a UI scan's job id is `s_…` (dps/ids.py PREFIX_SCAN, M4); the M2 list carries no job kind
    id: s.id, job_id: s.job_id, job_kind: isScan ? 'scan' : 'run', outcome: s.outcome, created_at: s.created_at, pack_sha256: '', layer: isScan ? 'ui' : 'conn',
    summary: {
      mode: s.mode, policy: policy || null, policy_version: version ?? null, ticket: s.ticket, source: s.source_object, target: s.target,
      write_mode: s.write_mode, rows_written: s.rows_written, verification_passed: s.passed, hits: s.hits,
      review_required: s.review_required ?? false,
    },
  };
}
