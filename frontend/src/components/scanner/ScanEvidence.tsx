/**
 * The evidence pack page's body for a UI scan (M4). A scan pack (dps/layers/scanner/inventory.py::
 * build_scan_evidence) has a `scan` block and no columns, partitions or row counts, so the table-run
 * sections of mockup 10 would read "0 rows from —"; these sections mirror the HTML renderer's "UI scan"
 * section (dps/layers/connectors/evidence.py::_scan_section) instead. Value-free: counts, codes, entity
 * types, the redacted start URL and paths only.
 */
import type { ReactNode } from 'react';
import { fmtCount, fmtDuration, fmtUtc } from '../../lib/jobs';
import type { EvidencePack, EvidenceSummary, NumberedRiskFlag } from '../../lib/types';
import { entityName, flagSentence, flagTone, hostOf, pathOf } from './format';

export function Sec({ n, title, right, keep, children }: { n: number; title: string; right?: ReactNode; keep?: boolean; children: ReactNode }): JSX.Element {
  return (
    <section className={`sec${keep ? ' keep' : ''}`} aria-labelledby={`s${n}`}>
      <h2 id={`s${n}`}><span className="n">{n}</span> {title}{right && <span className="r">{right}</span>}</h2>
      {children}
    </section>
  );
}

const text = (v: unknown): string => (v === null || v === undefined || v === '' ? '—' : typeof v === 'object' ? JSON.stringify(v) : String(v));
const num = (v: unknown): number => (typeof v === 'number' && !Number.isNaN(v) ? v : Number(v ?? 0) || 0);

/** A pack written by a scan's finalize (job kind `scan` with its `scan` block). */
export function isScanPack(p: Pick<EvidencePack, 'job_kind' | 'scan'>): boolean {
  return p.job_kind === 'scan' && !!p.scan;
}

/** The pack's heading: the scanned origin and what the scan produced. */
export function scanPackTitle(p: EvidencePack): string {
  return `UI scan ${hostOf((p.source?.origin as string | undefined) ?? (p.source?.url as string | undefined)) || '—'} → captures and exports`;
}

/**
 * The pack as dps/layers/connectors/evidence.py::canonical_json writes it (sorted keys, compact
 * separators, UTF-8 kept), so a pack without a stored JSON artefact still downloads as the bytes its
 * `pack_sha256` names. (Whole-number floats would differ from Python's `1.0`; packs hold counts.)
 */
export function canonicalJson(value: unknown): string {
  if (value === null || typeof value !== 'object') return JSON.stringify(value) ?? 'null';
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  const entries = Object.entries(value as Record<string, unknown>).filter(([, v]) => v !== undefined).sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
  return `{${entries.map(([k, v]) => `${JSON.stringify(k)}:${canonicalJson(v)}`).join(',')}}`;
}

/** A scan's job id (dps/ids.py PREFIX_SCAN = "s"; runs are "j_…"). The evidence list carries no job kind. */
export const isScanJobId = (jobId: string): boolean => jobId.startsWith('s_');

export interface EvidenceRowView { href: string; what: string; sub: string; layer: string; verification: string }

/** How a scan's row of the evidence list reads (Reports, mockup 09): a scan has no source table, target or rows. */
export function scanEvidenceRow(s: EvidenceSummary): EvidenceRowView {
  return {
    href: `/scanner/scans/${encodeURIComponent(s.job_id)}`,
    what: 'UI scan → captures and exports',
    sub: [s.ticket, 'nothing written to the scanned site', s.outcome !== 'succeeded' ? s.outcome.replace('_', ' ') : null].filter(Boolean).join(' · '),
    layer: 'UI scanner',
    verification: s.passed ? 'passed' : 'failed',
  };
}

function scopeText(scope: Record<string, any>): string {
  const parts = [`max ${fmtCount(num(scope.max_pages))} pages`, `depth ≤ ${text(scope.max_depth)}`, scope.same_origin === false ? 'other origins allowed' : 'same origin'];
  if (Array.isArray(scope.allow) && scope.allow.length) parts.push(`only: ${scope.allow.join(', ')}`);
  if (Array.isArray(scope.deny) && scope.deny.length) parts.push(`never: ${scope.deny.join(', ')}`);
  const off = [scope.iframes === false && 'iframes', scope.shadow_dom === false && 'shadow DOM', scope.spa_clicks === false && 'SPA clicks'].filter(Boolean);
  if (off.length) parts.push(`${off.join(', ')} off`);
  return parts.join(' · ');
}

const AUTH: Record<string, string> = { storage_state_ref: 'stored session (storage state reference)', inline: 'inline session (dev only)', none: 'none · public pages only' };

/** The opening paragraph and the stat strip of a scan pack (section 1). */
export function ScanPackSummary({ p }: { p: EvidencePack }): JSX.Element {
  const s = p.scan ?? {};
  const v = p.verification ?? {};
  const hits = num(v.hits);
  const passed = !!v.passed;
  const day = new Date(p.created_at).toLocaleDateString('en-GB', { day: 'numeric', month: 'long', year: 'numeric', timeZone: 'UTC' });
  const captures = (s.captures ?? {}) as Record<string, number>;
  const nonGet = num(s.non_get_allowed);
  const synth = p.mode === 'synthesize';
  return (
    <Sec n={1} title="Summary">
      <p className="lead">
        On {day} DPS crawled {fmtCount(num(s.pages))} pages of <b>{hostOf(p.source?.origin as string | undefined) || '—'}</b>, inventoried {fmtCount(num(s.fields))} fields in {fmtCount(num(s.forms))} forms and filled them with {synth ? 'synthetic personas' : 'tokens'} under policy <b>{p.policy?.name || '—'}</b> without submitting anything: {fmtCount(num(s.submissions))} submissions, {fmtCount(nonGet)} non-GET requests allowed.{' '}
        Every stored fill value is a synthetic persona value or a token of one, never a value read from a page{passed ? '' : `; the scan's verification failed (${fmtCount(hits)} ${hits === 1 ? 'hit' : 'hits'})`}.{' '}
        {fmtCount(num(captures.captured))} pages that rendered personal data were captured masked{num(captures.withheld) ? `, ${fmtCount(num(captures.withheld))} withheld (fail closed)` : ''}. The scan key was returned once and purged from DPS{p.outcome === 'succeeded' ? '' : `; the scan ended ${p.outcome}`}.
      </p>
      <div className="stats">
        <div><div className="l">Pages</div><div className="v">{fmtCount(num(s.pages))}</div><div className="s">{fmtCount(num(s.spa_routes))} SPA routes</div></div>
        <div><div className="l">Forms · fields</div><div className="v">{fmtCount(num(s.forms))} · {fmtCount(num(s.fields))}</div><div className="s">{fmtCount(num(p.counts?.personal_fields))} personal</div></div>
        <div><div className="l">Submissions</div><div className="v ok">{fmtCount(num(s.submissions))}</div><div className="s">no submit path exists</div></div>
        <div><div className="l">Non-GET allowed</div><div className={`v ${nonGet === 0 ? 'ok' : 'bad'}`}>{fmtCount(nonGet)}</div><div className="s">measured from the request log</div></div>
        <div><div className="l">Stored fill values</div><div className={`v ${passed ? 'ok' : 'bad'}`}>synthetic</div><div className="s">{fmtCount(num(v.values_checked))} tokens of persona values</div></div>
        <div><div className="l">Duration</div><div className="v">{fmtDuration(num(p.timings?.duration_s))}</div><div className="s">{fmtCount(num(s.navigations))} navigations</div></div>
      </div>
    </Sec>
  );
}

/** Sections 2–5 of a scan pack: configuration, the scan's findings, execution and verification. */
export function ScanPackSections({ p, sha }: { p: EvidencePack; sha: string }): JSX.Element {
  const s = p.scan ?? {};
  const v = p.verification ?? {};
  const hits = num(v.hits);
  const passed = !!v.passed;
  const src = p.source ?? {};
  const tgt = p.target ?? {};
  const cons = p.consistency ?? {};
  const custody = (cons.key_custody ?? {}) as Record<string, any>;
  const exports = (tgt.exports ?? {}) as Record<string, string>;
  const byEntity = Object.entries((s.by_entity ?? {}) as Record<string, number>).sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
  const flags = (s.risk_flags ?? []) as NumberedRiskFlag[];
  const validation = Object.entries((s.validation ?? {}) as Record<string, Record<string, number>>).sort(([a], [b]) => a.localeCompare(b));
  const blocked = Object.entries((s.blocked_requests ?? {}) as Record<string, number>).sort(([a], [b]) => a.localeCompare(b));
  const blockedTotal = blocked.reduce((a, [, n]) => a + num(n), 0);
  const captures = (s.captures ?? {}) as Record<string, number>;
  const nonGet = num(s.non_get_allowed);
  const reached = num(s.scope_paths_reached);
  const routesRun = num(s.scope_routes_reached);
  const neutral = [
    [num(s.submit_attempts_neutralised), 'submit attempts neutralised'], [num(s.dialogs_dismissed), 'dialogs dismissed'],
    [num(s.popups_closed), 'pop-ups closed'], [num(s.websockets_closed), 'web sockets closed'], [num(s.fills_skipped), 'fills skipped'],
    [num(s.events_dropped), 'log events dropped'],
  ].filter(([n]) => (n as number) > 0).map(([n, what]) => `${fmtCount(n as number)} ${what}`);
  const synth = p.mode === 'synthesize';
  return (
    <>
      <Sec n={2} title="Configuration hashes" right="hashes identify exactly what ran">
        <dl className="kv">
          <dt>Policy</dt><dd><b>{p.policy?.name || '—'}</b><span className="s2">Policy hash <span className="mono">sha256:{p.policy?.hash || '—'}</span></span></dd>
          <dt>Configuration hash</dt><dd><span className="mono">sha256:{p.config_hash}</span><span className="s2">policy + scan specification, canonical JSON</span></dd>
          <dt>Engine</dt><dd>DPS engine {p.engine_version} · image <span className="mono">{p.image_digest || 'unset (local build)'}</span></dd>
          <dt>Key and seed</dt><dd>kid <span className="mono">{text(cons.kid)}</span> · seed fingerprint <span className="mono">{text(cons.seed_fingerprint)}</span> · consistency scope <b>{text(cons.scope)}</b>
            <span className="s2">{custody.returned ? 'Returned once at scan creation' : 'Supplied by the caller'}{custody.wrapped ? ', wrapped with the caller\'s public key' : ''}; {custody.purged_at ? `purged from the sealed job at ${fmtUtc(custody.purged_at as string)}` : 'DPS stored no key'}. This pack holds the key id only.</span></dd>
          <dt>Source</dt><dd>UI · <span className="mono">{text(src.url)}</span>
            <span className="s2">{scopeText((src.scope ?? {}) as Record<string, any>)} · authentication: {AUTH[String(src.auth)] ?? text(src.auth)}</span></dd>
          <dt>Target</dt><dd>Artefact store <span className="mono">{text(tgt.store)}</span> · {synth ? 'synthetic' : 'obfuscated'} captures, field inventory and persona export
            <span className="s2">exports {Object.keys(exports).length ? Object.entries(exports).map(([k, id]) => `${k.toUpperCase()} ${id}`).join(' · ') : 'not written'} · {fmtCount(num(s.personas))} personas, shown: persona {fmtCount(num(s.display_persona))}</span></dd>
        </dl>
      </Sec>

      <Sec n={3} title="UI scan" right={`${fmtCount(num(s.pages))} pages · ${fmtCount(flags.length)} risk flags`}>
        <div className="tbl-scroll"><table className="tbl doc stack c3"><thead><tr><th>Entity</th><th className="num">Fields</th></tr></thead><tbody>
          {byEntity.map(([type, n]) => <tr key={type}><td data-l="Entity">{entityName(type)} <span className="prm">{type}</span></td><td className="num m" data-l="Fields">{fmtCount(n)}</td></tr>)}
          {!byEntity.length && <tr><td colSpan={2} className="faint">No field was classified as personal data.</td></tr>}
        </tbody></table></div>
        <div className="tbl-scroll"><table className="tbl doc stack" style={{ marginTop: 12 }}><thead><tr><th>Code</th><th>Risk flag</th><th>Severity</th><th>Page</th></tr></thead><tbody>
          {flags.map((f) => (
            <tr key={f.code}><td data-l="Code"><span className={`chip ${flagTone(f)}`}>{f.code}</span></td><td className="wide" data-l="Flag">{flagSentence(f)}</td>
              <td data-l="Severity">{f.severity}</td><td className="m" data-l="Page">{pathOf(f.path)}</td></tr>
          ))}
          {!flags.length && <tr><td colSpan={4} className="faint">No risk flag.</td></tr>}
        </tbody></table></div>
        <div className="tbl-scroll"><table className="tbl doc stack c3" style={{ marginTop: 12 }}><thead><tr><th>The page&apos;s own validation · fill</th><th className="num">Fields checked</th><th className="num">Accepted</th><th className="num">Rejected</th></tr></thead><tbody>
          {validation.map(([mode, c]) => (
            <tr key={mode}><td data-l="Fill">{mode}</td><td className="num m" data-l="Checked">{fmtCount(num(c.checked))}</td><td className="num m" data-l="Accepted">{fmtCount(num(c.accepted))}</td><td className="num m" data-l="Rejected">{fmtCount(num(c.rejected))}</td></tr>
          ))}
          {!validation.length && <tr><td colSpan={4} className="faint">No field was filled.</td></tr>}
        </tbody></table></div>
        <p className="note">Entity types, field counts, risk codes and paths only; field values, page text and the start URL&apos;s query values are never stored.</p>
      </Sec>

      <Sec n={4} title="Execution" right={`${fmtCount(num(s.navigations))} navigations`}>
        <div className="timeline">
          <div><div className="t">{fmtUtc(p.timings?.started_at as string | undefined, true).replace(/^.*, /, '')}</div>Scan started</div>
          <div><div className="t">{fmtCount(num(s.pages))} pages</div>{fmtCount(num(s.spa_routes))} SPA routes{s.truncated ? ' · budget reached' : ''}</div>
          <div className={num(captures.withheld) ? 'warn' : ''}><div className="t">{fmtCount(num(captures.captured))} captures</div>{num(captures.withheld) ? `${fmtCount(num(captures.withheld))} withheld` : 'masked before storage'}</div>
          <div className={passed ? 'ok' : 'bad'}><div className="t">{fmtCount(num(v.values_checked))} values</div>Verification {passed ? 'passed' : 'failed closed'}</div>
          <div className="ok"><div className="t">{fmtUtc(p.created_at, true).replace(/^.*, /, '')}</div>Pack sealed and hashed</div>
        </div>
        <p className="note">{neutral.length ? `${neutral.join(' · ')}. ` : ''}Wall clock {fmtDuration(num(p.timings?.duration_s))}. Pack sha256 <span className="mono">{sha.slice(0, 12)}…</span></p>
      </Sec>

      <Sec n={5} title="Verification" right={`mode ${text(v.mode)}`} keep>
        <div className="tbl-scroll"><table className="tbl doc stack"><thead><tr><th>Check</th><th>Result</th><th>What was checked</th><th>Status</th></tr></thead><tbody>
          <tr><td data-l="Check"><b>Submissions</b></td><td className="m" data-l="Result">{fmtCount(num(s.submissions))}</td><td className="wide" data-l="What">The scan request has no submit field and the worker never presses a button</td><td data-l="Status"><span className="chip emerald">pass</span></td></tr>
          <tr><td data-l="Check"><b>Non-GET requests allowed</b></td><td className="m" data-l="Result">{fmtCount(nonGet)}</td><td className="wide" data-l="What">Every request of the scan&apos;s browser counted by the request guard; {blocked.length ? blocked.map(([m, n]) => `${fmtCount(n)} ${m}`).join(' · ') : 'none'} blocked ({fmtCount(blockedTotal)})</td><td data-l="Status"><span className={`chip ${nonGet === 0 ? 'emerald' : 'rose'}`}>{nonGet === 0 ? 'pass' : 'review'}</span></td></tr>
          <tr><td data-l="Check"><b>Never-visit paths</b></td><td className="m" data-l="Result">{fmtCount(reached)} reached</td><td className="wide" data-l="What">Requests to denied paths that left the browser</td><td data-l="Status"><span className={`chip ${reached === 0 ? 'emerald' : 'rose'}`}>{reached === 0 ? 'pass' : 'review'}</span></td></tr>
          <tr><td data-l="Check"><b>Never-visit routes</b></td><td className="m" data-l="Result">{fmtCount(routesRun)} run</td><td className="wide" data-l="What">Refused routes a page&apos;s own click handler ran in the same document (hash route or pushState: no request to refuse)</td><td data-l="Status"><span className={`chip ${routesRun === 0 ? 'emerald' : 'rose'}`}>{routesRun === 0 ? 'pass' : 'review'}</span></td></tr>
          <tr><td data-l="Check"><b>Stored fill values</b></td><td className="m" data-l="Result">{fmtCount(num(v.values_checked))} tokens · {fmtCount(hits)} hits</td><td className="wide" data-l="What">Fields are filled with synthetic persona values or tokens of them, never with a value read from a page ({fmtCount(num(v.batches))} pages filled); captures that render personal data are masked before storage or withheld</td><td data-l="Status"><span className={`chip ${passed ? 'emerald' : 'rose'}`}>{passed ? 'pass' : 'fail'}</span></td></tr>
        </tbody></table></div>
      </Sec>
    </>
  );
}
