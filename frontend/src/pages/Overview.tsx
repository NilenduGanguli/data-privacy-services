import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { BarChart } from '../components/BarChart';
import { Kpi } from '../components/Kpi';
import { useAuth } from '../hooks/useAuth';
import { useSettings } from '../hooks/useSettings';
import { api, ApiError, errorText, metricTotal } from '../lib/api';
import { compact, count, delta, plural } from '../lib/format';
import { engineName, shortId } from '../lib/jobs';
import type { JobBrief, JobStats, PolicyList, Readiness, SummaryOut } from '../lib/types';

const DAYS = 14;

/** Why the summary is missing: `db` (404/503, an L1-only deployment) or another refusal. */
export interface Degraded { metrics: string; stats: JobStats | null; reason: 'db' | string }

/** The readiness pill of the crumb bar (mockup 01): "All layers ready" or "N degraded". */
export function readinessPill(ready: Readiness | null): { label: string; tone: 'ok' | 'warn' | 'none'; components: string } {
  if (!ready) return { label: 'Checking readiness', tone: 'none', components: '' };
  const entries = Object.entries(ready.components);
  const down = entries.filter(([, c]) => !c.ok);
  return {
    label: down.length ? `${down.length} degraded` : 'All layers ready',
    tone: down.length ? 'warn' : 'ok',
    components: entries.filter(([, c]) => c.ok).map(([n]) => n).join(' · '),
  };
}

export interface PostureBar { label: string; pct: number; tone: '' | 'emerald' | 'amber'; note?: string }

/** The four posture bars (dps/reports.py `posture`, 0–100). */
export function postureBars(p: Record<string, number>): PostureBar[] {
  const v = (k: string): number => Math.round(Math.max(0, Math.min(100, Number(p[k] ?? 0))));
  const tone = (n: number, good: '' | 'emerald'): PostureBar['tone'] => (n < 90 ? 'amber' : good);
  return [
    { label: 'Leakage sweeps passed', pct: v('sweeps_passed_pct'), tone: tone(v('sweeps_passed_pct'), 'emerald') },
    { label: 'Reversible with key held by caller', pct: v('key_held_by_caller_pct'), tone: tone(v('key_held_by_caller_pct'), ''), note: 'by design: DPS keeps no key' },
    { label: 'Columns reviewed before run', pct: v('snapshots_reviewed_pct'), tone: tone(v('snapshots_reviewed_pct'), '') },
    { label: 'Policies on latest version', pct: v('policies_latest_pct'), tone: tone(v('policies_latest_pct'), '') },
  ];
}

/** "name vN" of the policy the tenant's jobs use most (tenant policies first, then presets). */
export function mostUsedPolicy(list: PolicyList): string | null {
  const rows = [
    ...list.tenant.map((t) => ({ name: t.name, version: t.active_version ?? 1, jobs: t.jobs })),
    ...list.presets.filter((p) => !list.tenant.some((t) => t.name === p.name)).map((p) => ({ name: p.name, version: p.version, jobs: p.jobs })),
  ];
  const best = rows.reduce<(typeof rows)[number] | null>((a, r) => (!a || r.jobs > a.jobs ? r : a), null);
  return best && best.jobs > 0 ? `${best.name} v${best.version}` : null;
}

function shares(parts: Record<string, number> | undefined, name: (k: string) => string = (k) => k): { key: string; label: string }[] {
  const entries = Object.entries(parts ?? {}).filter(([, n]) => n > 0).sort((a, b) => b[1] - a[1]);
  const total = entries.reduce((a, [, n]) => a + n, 0);
  return entries.map(([k, n]) => ({ key: k, label: `${name(k)} ${Math.round((n / total) * 100)}%` }));
}

const MODE_COLOR: Record<string, string> = { obfuscate: 'var(--teal)', synthesize: 'var(--violet)', detect: 'var(--ink-3)', reveal: 'var(--amber)' };
const SENS_TONE: Record<string, string> = { CRITICAL: 'rose', HIGH: 'amber', MEDIUM: 'amber', LOW: 'slate' };

function jobTone(j: JobBrief): string {
  if (j.note === 'awaiting review') return 'amber';
  return ({ running: 'teal', waiting: 'teal', succeeded: 'emerald', failed: 'rose', dead: 'rose', canceled: 'slate', queued: 'slate', paused: 'amber' } as Record<string, string>)[j.state] ?? 'slate';
}

function jobLink(j: JobBrief): string {
  return j.kind === 'scan' ? `/scanner/scans/${encodeURIComponent(j.id)}` : `/data-sources/jobs/${encodeURIComponent(j.id)}`;
}

function jobWhat(j: JobBrief): string {
  if (j.kind === 'scan') return `UI scan of ${j.source ?? '—'}`;
  if (j.kind === 'discover') return `${j.source ?? '—'} · discovery`;
  if (!j.target) return j.source ?? '—';
  if (j.target === 'in place' || j.target === j.source) return `${j.source ?? '—'}, ${j.target === 'in place' ? 'in place' : 'append'}`;
  return `${j.source ?? '—'} → ${j.target}`;
}

function Ring({ score }: { score: number }): JSX.Element {
  const c = 2 * Math.PI * 50;
  const s = Math.max(0, Math.min(100, score));
  return (
    <svg viewBox="0 0 120 120" width="128" height="128" role="img" aria-label={`Posture score ${Math.round(s)} of 100`}>
      <circle cx="60" cy="60" r="50" fill="none" stroke="var(--surface-3)" strokeWidth="12" />
      <circle cx="60" cy="60" r="50" fill="none" stroke="var(--teal)" strokeWidth="12" strokeDasharray={c.toFixed(1)} strokeDashoffset={(c * (1 - s / 100)).toFixed(1)} strokeLinecap="round" transform="rotate(-90 60 60)" />
      <text x="60" y="58" textAnchor="middle" fontSize="26" fontWeight="800" fill="var(--ink)">{Math.round(s)}</text>
      <text x="60" y="76" textAnchor="middle" fontSize="11" fill="var(--ink-3)">of 100</text>
    </svg>
  );
}

function CrumbBar({ tenant, ready }: { tenant: string; ready: Readiness | null }): JSX.Element {
  const pill = readinessPill(ready);
  return (
    <div className="crumbbar">
      <span className="crumb">Overview</span><span className="sep">/</span><span className="muted">Tenant: {tenant || 'default'}</span>
      <span className="spacer" />
      <span className="pill"><span className={`dot${pill.tone === 'warn' ? ' amber' : ''}`} />{pill.label}</span>
      {pill.components && <span className="pill mono hide-sm">{pill.components}</span>}
    </div>
  );
}

function DegradedView({ degraded, ready }: { degraded: Degraded; ready: Readiness | null }): JSX.Element {
  const requests = metricTotal(degraded.metrics, 'dps_requests_total');
  const findings = metricTotal(degraded.metrics, 'dps_findings_total');
  const raw = metricTotal(degraded.metrics, 'dps_llm_raw_pii_sent_total');
  const s = degraded.stats;
  return (
    <>
      <div className="callout amber" role="status">{degraded.reason === 'db'
        ? <>Connect the metadata database to see 14-day trends, posture and layer totals. The tiles below count since this process started.</>
        : <>Could not load the 14-day summary ({degraded.reason}). The tiles below count since this process started.</>}</div>
      <div className="grid cols-4 degraded-tiles">
        <Kpi hero label="Requests served" value={count(requests)} delta="since process start" />
        <Kpi label="Findings" value={count(findings)} delta="by entity type and detector" />
        <Kpi label="Jobs running" value={s ? s.running : '—'} delta={s ? `of ${s.tenant_max_running} slots · ${plural(s.workers_online, 'worker')} online` : 'no job queue on this deployment'} />
        <Kpi zero={raw === 0} alert={raw > 0} label="Raw PII sent to LLM" value={count(raw)}
          delta={raw === 0 ? 'placeholders only · must stay 0' : 'raw values reached the gateway since process start · must stay 0: investigate'} />
      </div>
      <div className="card">
        <div className="card-head"><div className="card-title">Readiness</div></div>
        <div className="tbl-scroll"><table className="tbl stack"><thead><tr><th>Component</th><th>State</th><th>Detail</th></tr></thead>
          <tbody>{ready ? Object.entries(ready.components).map(([name, c]) => (
            <tr key={name}><td className="mono" data-l="Component">{name}</td><td data-l="State"><span className={`chip ${c.ok ? 'emerald' : 'amber'}`}>{c.ok ? 'ok' : 'off'}</span></td><td className="muted" data-l="Detail">{c.detail}</td></tr>
          )) : <tr><td colSpan={3}><div className="skeleton" /></td></tr>}</tbody></table></div>
      </div>
    </>
  );
}

/** The Overview (mockup 01) as a pure view: summary, readiness and the most-used policy in. */
export function OverviewView({ tenant, ready, summary, degraded, policyLabel }: {
  tenant: string; ready: Readiness | null; summary: SummaryOut | null; degraded: Degraded | null; policyLabel: string | null;
}): JSX.Element {
  const head = (
    <div className="page-head">
      <div><h1>Protection posture</h1><p>Last {summary?.days ?? DAYS} days{policyLabel ? <> · policy pack <b>{policyLabel}</b></> : null}</p></div>
      <div className="head-actions">
        <Link className="btn" to="/reports">Export evidence</Link>
        <Link className="btn btn-primary" to="/playground">Open playground</Link>
      </div>
    </div>
  );
  if (!summary) {
    return (
      <main className="page m5-page overview-page">
        <CrumbBar tenant={tenant} ready={ready} />
        {head}
        {degraded ? <DegradedView degraded={degraded} ready={ready} /> : <div className="grid cols-4">{[0, 1, 2, 3].map((i) => <div key={i} className="card"><div className="skeleton" /></div>)}</div>}
      </main>
    );
  }
  const t = summary.tiles;
  const n = (k: string): number => Number(t[k] ?? 0);
  const change = delta(n('values_protected'), t.values_protected_prev);
  const bars = postureBars(summary.posture);
  const score = Number(summary.posture.score ?? 0);
  const api = summary.layers.api ?? {};
  const conn = summary.layers.conn ?? {};
  const ui = summary.layers.ui ?? {};
  const awaiting = n('snapshots_awaiting_review');
  const maxFind = Math.max(1, ...summary.findings_by_type.map((f) => f.findings));
  const last = ui.last_scan;
  return (
    <main className="page m5-page overview-page">
      <CrumbBar tenant={tenant} ready={ready} />
      {head}

      <div className="grid cols-4">
        <Kpi hero label="Values protected" value={count(n('values_protected'))}
          delta={<>{change ? <b>{change.text}</b> : null}{change ? ' ' : ''}vs previous {summary.days} days · 3 layers</>} />
        <Kpi label="PII findings" value={count(n('findings'))} delta={`API, documents and jobs · ${count(n('entity_types'))} entity types`} />
        <Kpi label="Jobs" value={<>{count(n('jobs_running'))} <span className="faint" style={{ fontSize: 16, fontWeight: 600 }}>running</span></>}
          delta={`${count(n('jobs_completed'))} completed · ${count(n('jobs_failed'))} failed · ${count(awaiting)} awaiting review`} />
        <Kpi zero={n('raw_pii_sent_to_llm') === 0} alert={n('raw_pii_sent_to_llm') > 0} label="Raw PII sent to LLM" value={count(n('raw_pii_sent_to_llm'))}
          delta={`${count(n('llm_calls'))} gateway calls · ${count(n('llm_blocked'))} blocked at egress · this tenant · last ${summary.days} days · ${n('raw_pii_sent_to_llm') === 0 ? 'placeholders only' : 'raw values reached the gateway: investigate'}`} />
      </div>

      <div className="g-trend">
        <section className="card" aria-labelledby="trend-t">
          <div className="card-head"><div className="card-title" id="trend-t">Protected values per day</div></div>
          <BarChart data={summary.trend as unknown as Record<string, string | number>[]} xKey="day" label={`Protected values per day, last ${summary.days} days`} height={220}
            series={[{ key: 'obfuscated', label: 'Obfuscated', color: 'var(--teal)' }, { key: 'synthesized', label: 'Synthetic', color: 'var(--violet)' }]} />
          <div className="trend-note">Days in UTC; today runs to now. Layer 2 jobs count on the day they start.</div>
        </section>
        <section className="card" aria-labelledby="posture-t">
          <div className="card-head"><div className="card-title" id="posture-t">Posture score</div>{score >= 95 ? <span className="chip emerald">Verified</span> : <span className="chip amber">Review</span>}</div>
          <div className="posture">
            <Ring score={score} />
            <div className="rows">
              {bars.map((b) => (
                <div key={b.label} title={b.note}>
                  <div className="between"><span>{b.label}</span><b>{b.pct}%</b></div>
                  <div className={`bar${b.tone ? ` ${b.tone}` : ''}`}><i style={{ width: `${b.pct}%` }} /></div>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>

      <div className="grid cols-3">
        <section className="card layer">
          <div className="card-head"><div className="card-title"><Link to="/playground">Layer 1 · API</Link></div><span className="chip teal">stateless</span></div>
          <div className="kpi"><div className="value">{compact(api.requests ?? 0)}</div><div className="delta">requests · last {summary.days} days</div></div>
          <div className="legend more">{shares(api.by_mode).map((s) => <span key={s.key}><i style={{ background: MODE_COLOR[s.key] ?? 'var(--ink-3)' }} />{s.label}</span>)}</div>
        </section>
        <section className="card layer">
          <div className="card-head"><div className="card-title"><Link to="/data-sources">Layer 2 · Connectors</Link></div>
            {awaiting > 0 ? <span className="chip amber">{count(awaiting)} awaiting review</span> : <span className="chip emerald">reviewed</span>}</div>
          <div className="kpi"><div className="value">{compact(conn.rows ?? 0)}</div>
            <div className="delta">rows in {summary.days} days{shares(conn.by_engine, engineName).length ? ` · ${shares(conn.by_engine, engineName).map((s) => s.label).join(' · ')}` : ''}</div></div>
          <div className="more muted">{conn.groups ? `${plural(conn.groups, 'job group')} ${conn.groups === 1 ? 'keeps' : 'keep'} ids consistent across systems.` : 'No job group is open.'}</div>
        </section>
        <section className="card layer">
          <div className="card-head"><div className="card-title"><Link to="/scanner">Layer 3 · UI scanner</Link></div><span className="chip emerald">never submits</span></div>
          <div className="kpi"><div className="value">{count(ui.scans ?? 0)}</div>
            <div className="delta">scans · {count(ui.pages ?? 0)} pages · {count(ui.fields ?? 0)} personal data fields · {count(ui.risk_flags ?? 0)} risk flags</div></div>
          <div className="more muted">{last ? <>Last scan: {count(last.pages ?? 0)} pages, {count(last.personas ?? 0)} personas, {count(ui.submissions ?? 0)} submitted{last.id ? <> (<Link className="mono" to={`/scanner/scans/${encodeURIComponent(last.id)}`}>{shortId(last.id)}</Link>)</> : null}.</> : 'No scan in this window.'}</div>
        </section>
      </div>

      <div className="g-jobs">
        <section className="card" aria-labelledby="ft-t">
          <div className="card-head"><div className="card-title" id="ft-t">Findings by entity type</div><span className="faint sm">API, documents and jobs · {summary.days} days</span></div>
          <table className="tbl ftype"><tbody>
            {summary.findings_by_type.map((f) => {
              const tone = SENS_TONE[f.sensitivity] ?? 'slate';
              return (
                <tr key={f.entity_type}>
                  <td><span className={`chip ${tone}`}>{f.entity_type}</span></td>
                  <td style={{ width: '45%' }}><div className={`bar ${tone === 'slate' ? 'neu' : tone}`}><i style={{ width: `${Math.max(1, Math.round((f.findings / maxFind) * 100))}%` }} /></div></td>
                  <td className="mono num">{count(f.findings)}</td>
                </tr>
              );
            })}
            {!summary.findings_by_type.length && <tr className="empty-row"><td colSpan={3}>No findings in this window.</td></tr>}
          </tbody></table>
        </section>
        <section className="card" aria-labelledby="rj-t">
          <div className="card-head"><div className="card-title" id="rj-t">Recent jobs and scans</div><Link className="btn btn-sm" to="/data-sources">All jobs</Link></div>
          <div className="tbl-scroll"><table className="tbl stack" id="recent"><thead><tr><th>Job</th><th>Source → target</th><th>Mode</th><th>Progress</th><th>Status</th></tr></thead><tbody>
            {summary.recent_jobs.map((j) => (
              <tr key={j.id}>
                <td className="mono" data-l="Job"><Link to={jobLink(j)} title={j.id}>{shortId(j.id)}</Link></td>
                <td data-l="Source → target">{jobWhat(j)}</td>
                <td data-l="Mode">{j.mode ? <span className={`chip ${j.mode === 'synthesize' ? 'violet' : 'teal'}`}>{j.mode}</span> : <span className="faint">{j.kind}</span>}</td>
                <td data-l="Progress">{j.progress_pct === null ? <span className="faint">—</span> : <div className={`bar${j.mode === 'synthesize' ? ' violet' : ''}`}><i style={{ width: `${j.progress_pct}%` }} /></div>}</td>
                <td data-l="Status"><span className={`chip ${jobTone(j)}`}>{j.note === 'awaiting review' ? 'awaiting review' : j.state}</span>{j.note && j.note !== 'awaiting review' && <span className="s2">{j.note}</span>}</td>
              </tr>
            ))}
            {!summary.recent_jobs.length && <tr className="empty-row"><td colSpan={5}>No jobs yet. <Link to="/data-sources/jobs/new" style={{ color: 'var(--teal)', fontWeight: 600 }}>Start one</Link>.</td></tr>}
          </tbody></table></div>
        </section>
      </div>
    </main>
  );
}

/** Overview — protection posture (mockup 01): GET /reports/summary?days=14 and /readyz. */
export function Overview(): JSX.Element {
  const auth = useAuth();
  const { tenant } = useSettings();
  const [ready, setReady] = useState<Readiness | null>(null);
  const [summary, setSummary] = useState<SummaryOut | null>(null);
  const [degraded, setDegraded] = useState<Degraded | null>(null);
  const [policyLabel, setPolicyLabel] = useState<string | null>(null);

  useEffect(() => { api.readyz().then(setReady).catch(() => setReady(null)); }, []);
  useEffect(() => {
    let live = true;
    setSummary(null); setDegraded(null);
    api.reports.summary(auth, DAYS).then((s) => { if (live) setSummary(s); }).catch(async (e: unknown) => {
      const reason = e instanceof ApiError && (e.status === 404 || e.status === 503) ? 'db' : errorText(e);
      const metrics = await api.metrics().catch(() => '');
      const stats = await api.jobs.stats(auth).catch(() => null);
      if (live) setDegraded({ metrics, stats, reason });
    });
    api.policies.list(auth).then((l) => { if (live) setPolicyLabel(mostUsedPolicy(l)); }).catch(() => undefined);
    return () => { live = false; };
  }, [auth]);

  return <OverviewView tenant={tenant} ready={ready} summary={summary} degraded={degraded} policyLabel={policyLabel} />;
}
