import { useEffect, useMemo, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { AuditTable } from '../components/AuditTable';
import { BarChart } from '../components/BarChart';
import { DownloadButton } from '../components/DownloadButton';
import { EvidenceTable, type EvidenceDownloads } from '../components/EvidenceTable';
import { Kpi } from '../components/Kpi';
import { useAuth } from '../hooks/useAuth';
import { useSettings } from '../hooks/useSettings';
import { api, ApiError, errorText, type Download } from '../lib/api';
import { compact, count, plural, utc } from '../lib/format';
import {
  auditFilters, DEFAULT_FILTERS, evidenceRows, failedSweepLayer, failedSweepText, filtersToParams, fromSummary, parseFilters, sweepSeries, type EvidenceRow, type Layer,
  type Outcome, type ReportFilters,
} from '../lib/reports';
import type { ApiRollup, AuditRow, EvidenceItem, Readiness, ReportsJobsOut } from '../lib/types';

export interface AuditState { rows: AuditRow[]; cursor: string | null; forbidden: boolean }

export interface ReportsViewProps {
  tenant: string; readableTenants: number | null; ready: Readiness | null; filters: ReportFilters; now: Date;
  jobs: ReportsJobsOut | null; rows: EvidenceRow[] | null; packsCursor: string | null; audit: AuditState | null;
  error: string | null; busy: string | null;
  onFilters: (f: ReportFilters) => void; onOlderPacks: () => void; onOlderAudit: () => void; onScope: (scope: 'tenant' | 'all') => void;
  download: EvidenceDownloads & { auditCsv: () => Promise<Download> };
}

function Seg<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: [T, string][]; onChange: (v: T) => void }): JSX.Element {
  return (
    <div className="seg" role="group" aria-label={label}>
      {options.map(([v, text]) => <button key={v} type="button" className={value === v ? 'on' : ''} aria-pressed={value === v} onClick={() => onChange(v)}>{text}</button>)}
    </div>
  );
}

const LockIcon = (): JSX.Element => (
  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" aria-hidden="true"><rect x="4" y="10" width="16" height="11" rx="2" /><path d="M8 10V7a4 4 0 0 1 8 0v3" /></svg>
);

/** Reports & audit (mockup 09) as a pure view. */
export function ReportsView(p: ReportsViewProps): JSX.Element {
  const f = p.filters;
  const totals = p.jobs?.totals ?? {};
  const series = useMemo(() => (p.jobs ? sweepSeries(p.jobs.sweeps, f.layer, f.outcome) : []), [p.jobs, f.layer, f.outcome]);
  const passed = series.reduce((a, d) => a + d.passed, 0);
  const failed = series.reduce((a, d) => a + d.failed, 0);
  // a failed UI sweep is a scan whose own verification failed: not "failed closed" (S2 console r2 B1)
  const uiFailed = useMemo(() => (p.jobs && (f.layer === 'all' || f.layer === 'ui') ? sweepSeries(p.jobs.sweeps, 'ui', f.outcome).reduce((a, d) => a + d.failed, 0) : 0),
    [p.jobs, f.layer, f.outcome]);
  const closed = failed - uiFailed;
  const failedNote = !failed ? '' : !uiFailed ? ', all failed closed'
    : `${closed ? `, ${count(closed)} failed closed` : ''}, ${plural(uiFailed, 'scan')} to review`;
  const fails = (p.jobs?.failed_sweeps ?? []).filter((s) => (f.layer === 'all' || failedSweepLayer(s) === f.layer)
    && (f.outcome === 'all' || f.outcome === 'failed'));
  const isAdmin = !!p.audit && !p.audit.forbidden;
  const dbOk = p.ready?.components.db?.ok;
  const set = (patch: Partial<ReportFilters>): void => p.onFilters({ ...f, ...patch });
  const auditRows = totals.audit_rows;
  return (
    <main className="page m5-page reports-page">
      <div className="crumbbar">
        <span className="crumb">Reports &amp; audit</span><span className="sep">/</span>
        <span className="muted">Tenant: {p.tenant || 'default'}{p.readableTenants && p.readableTenants > 1 ? ` + ${plural(p.readableTenants - 1, 'tenant')} you can read` : ''}</span>
        <span className="spacer" />
        {p.ready && <span className="pill"><span className={`dot${dbOk ? '' : ' amber'}`} />{dbOk ? 'Audit writer healthy · append-only' : 'Audit writer unavailable'}</span>}
      </div>
      <div className="page-head">
        <div><h1>Evidence and audit</h1><p>Every job, scan and API call leaves an evidence pack or an audit row. Rows hold counts, entity types, method names, hashes and key names. They never hold values.</p></div>
        {isAdmin && <div className="head-actions">
          <DownloadButton className="btn btn-primary" label={auditRows !== undefined ? `Export ${compact(auditRows)} audit rows as CSV` : 'Export audit rows as CSV'}
            ariaLabel="Export the audit rows of these filters as CSV" disabled={auditRows === 0} fetcher={p.download.auditCsv} />
        </div>}
      </div>

      <div className="filters" role="search" aria-label="Filter reports and audit">
        <select className="input" aria-label="Date range" value={f.days} onChange={(e) => set({ days: Number(e.target.value) as ReportFilters['days'] })}>
          <option value={7}>Last 7 days</option><option value={14}>Last 14 days</option><option value={30}>Last 30 days</option>
        </select>
        <span className="flabel">Layer</span>
        <Seg<Layer> label="Layer" value={f.layer} onChange={(v) => set({ layer: v })} options={[['all', 'All'], ['api', 'API'], ['conn', 'Connectors'], ['ui', 'UI scanner']]} />
        <span className="flabel">Outcome</span>
        <Seg<Outcome> label="Outcome" value={f.outcome} onChange={(v) => set({ outcome: v })} options={[['all', 'All'], ['ok', 'OK'], ['failed', 'Failed'], ['blocked', 'Blocked']]} />
        <input className="input search" type="search" placeholder="Job, ticket or key name" aria-label="Search by job, ticket or key name" value={f.q} onChange={(e) => set({ q: e.target.value })} />
        <button className="btn btn-ghost btn-sm" type="button" onClick={() => p.onFilters({ ...DEFAULT_FILTERS, scope: f.scope })}>Clear</button>
      </div>
      {p.error && <div className="err" role="alert">{p.error}</div>}

      <div className="grid cols-4">
        <Kpi label="Evidence packs" value={p.jobs ? count(totals.packs) : '—'} delta={p.jobs ? `${count(totals.run_packs)} jobs · ${count(totals.scan_packs)} scans · ${count(totals.api_rollups)} API roll-ups` : 'loading'} />
        <Kpi label="Verification sweeps" value={p.jobs ? count(passed + failed) : '—'} delta={p.jobs ? `${count(passed)} passed · ${count(failed)} failed${failedNote}` : 'loading'} />
        <Kpi label="Audit rows" value={auditRows !== undefined ? compact(auditRows) : '—'} delta="append-only · monthly partitions" />
        <Kpi zero label="Customer values stored" value={0} delta="counts, hashes and key names only" />
      </div>

      <div className="split">
        <section className="card" aria-labelledby="vh-t">
          <div className="card-head"><div><div className="card-title" id="vh-t">Verification sweeps per day</div><div className="faint sm" style={{ marginTop: 2 }}>One sweep per job partition, completed scan and hourly API batch</div></div></div>
          {f.outcome === 'blocked'
            ? <div className="muted sm" style={{ padding: '40px 8px', textAlign: 'center' }}>Sweeps pass or fail; none are blocked. Blocked requests are listed in the audit log below.</div>
            : <BarChart data={series as unknown as Record<string, string | number>[]} xKey="day" height={220} label={`Verification sweeps per day, last ${f.days} days, passed and failed`}
              series={[{ key: 'passed', label: 'Passed', color: 'var(--emerald)' }, { key: 'failed', label: 'Failed', color: 'var(--rose)' }]} />}
        </section>
        <section className="card" aria-labelledby="fs-t">
          <div className="card-head"><div className="card-title" id="fs-t">Failed sweeps</div><span className={`chip ${fails.length ? 'rose' : 'emerald'}`}>{fails.length} in {f.days} days</span></div>
          {fails.map((s) => {
            const words = failedSweepText(s);
            const to = s.job_id ? (s.where === 'scan' ? `/scanner/scans/${encodeURIComponent(s.job_id)}` : `/data-sources/jobs/${encodeURIComponent(s.job_id)}`) : null;
            return (
              <div className="fail" key={`${s.where}-${s.job_id ?? s.route}-${s.day}`}>
                <div className="top"><span className="mono">{to ? <Link to={to}>{words.where}</Link> : words.where}</span><span className="faint sm">{utc(`${s.day}T00:00:00Z`).replace(' 00:00', '')} · UTC day</span></div>
                <div className="what">{words.what}</div>
                <div className="fix">{s.where === 'job' ? <><span className="chip amber">failed closed</span> Review the columns named above, then re-run the job.</>
                  : s.where === 'scan' ? <><span className="chip rose">review</span> Open the scan and check the target system for that request.</>
                    : <><span className="chip slate">contained automatically</span> No action needed. The model saw placeholders only.</>}</div>
              </div>
            );
          })}
          {!fails.length && <p className="muted sm" style={{ margin: 0 }}>No sweep failed in this window. A failed sweep stops the batch before anything is written.</p>}
        </section>
      </div>

      <section className="card" aria-labelledby="ep-t">
        <div className="card-head"><div className="card-title" id="ep-t">Evidence packs</div><span className="faint sm">Kept under the tenant retention policy. Download a pack to keep it longer.</span></div>
        {p.rows ? <EvidenceTable rows={p.rows} downloads={p.download} hasOlder={!!p.packsCursor} busy={p.busy === 'packs'} onOlder={p.onOlderPacks} total={null} /> : <div className="skeleton" />}
      </section>

      <section className="card" id="audit" aria-labelledby="al-t">
        <div className="card-head"><div className="card-title" id="al-t">Audit log</div>
          <span className="lock"><LockIcon />Append-only. Rows cannot be edited or deleted; partitions expire under the retention policy.</span></div>
        {p.audit?.forbidden ? <div className="callout">The audit log needs a key with the admin scope. Evidence packs above are readable with the jobs scope.</div> : p.audit ? (
          <>
            <div className="scope-seg" style={{ marginBottom: 10 }}>
              <Seg<'tenant' | 'all'> label="Audit scope" value={f.scope} onChange={p.onScope} options={[['tenant', 'This tenant'], ['all', 'All tenants I can read']]} />
            </div>
            <AuditTable rows={p.audit.rows} />
            <div className="card-foot"><span>Showing {count(p.audit.rows.length)} rows{auditRows !== undefined && f.scope === 'tenant' && f.layer === 'all' && f.outcome === 'all' && !f.q ? ` of ${count(auditRows)}` : ''}, newest first</span>
              {p.audit.cursor && <button className="btn btn-sm" type="button" disabled={p.busy === 'audit'} onClick={p.onOlderAudit}>Load 50 older rows</button>}</div>
          </>
        ) : <div className="skeleton" />}
      </section>
    </main>
  );
}

/** Reports & audit (mockup 09): GET /reports/jobs, /reports/evidence, /reports/api-rollups and,
 *  for admin keys, /admin/audit; filters live in the URL query so a view can be shared. */
export function Reports(): JSX.Element {
  const auth = useAuth();
  const { tenant } = useSettings();
  const [params, setParams] = useSearchParams();
  const filters = useMemo(() => parseFilters(params), [params]);
  const [q, setQ] = useState(filters.q);
  const [ready, setReady] = useState<Readiness | null>(null);
  const [readable, setReadable] = useState<number | null>(null);
  const [jobs, setJobs] = useState<ReportsJobsOut | null>(null);
  const [items, setItems] = useState<EvidenceItem[] | null>(null);
  const [packsCursor, setPacksCursor] = useState<string | null>(null);
  const [rollups, setRollups] = useState<ApiRollup[]>([]);
  const [audit, setAudit] = useState<AuditState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => { const t = window.setTimeout(() => setQ(filters.q), 300); return () => window.clearTimeout(t); }, [filters.q]);
  useEffect(() => { api.readyz().then(setReady).catch(() => setReady(null)); }, []);
  useEffect(() => { api.admin.tenants(auth).then((r) => setReadable(r.items.length)).catch(() => setReadable(null)); }, [auth]);
  useEffect(() => {
    let live = true;
    setJobs(null);
    api.reports.jobs(auth, filters.days).then((r) => { if (live) setJobs(r); }).catch((e: unknown) => { if (live) setError(errorText(e)); });
    api.reports.apiRollups(auth, filters.days).then((r) => { if (live) setRollups(r.items); }).catch(() => { if (live) setRollups([]); });
    return () => { live = false; };
  }, [auth, filters.days]);
  const evidenceQuery = useMemo(() => ({
    days: filters.days, layer: filters.layer === 'conn' || filters.layer === 'ui' ? filters.layer : undefined,
    outcome: filters.outcome === 'ok' || filters.outcome === 'failed' ? filters.outcome : undefined, q: q.trim() || undefined, limit: 50,
  }), [filters.days, filters.layer, filters.outcome, q]);
  useEffect(() => {
    let live = true;
    setItems(null);
    if (filters.layer === 'api' || filters.outcome === 'blocked') { setItems([]); setPacksCursor(null); return () => { live = false; }; }
    api.reports.evidence(auth, evidenceQuery).then((r) => { if (live) { setItems(r.items); setPacksCursor(r.next_cursor); } })
      .catch(async (e: unknown) => {
        // a backend before M5 has no /reports/evidence: fall back to M2's evidence list
        if (e instanceof ApiError && e.status === 404) {
          const r = await api.evidence.list(auth, { limit: 50 }).catch(() => null);
          if (live && r) { setItems(r.items.map(fromSummary)); setPacksCursor(null); return; }
        }
        if (live) { setItems([]); setError(errorText(e)); }
      });
    return () => { live = false; };
  }, [auth, evidenceQuery, filters.layer, filters.outcome]);
  const auditQuery = useMemo(() => auditFilters({ ...filters, q }), [filters, q]);
  useEffect(() => {
    let live = true;
    api.admin.audit(auth, auditQuery).then((r) => { if (live) setAudit({ rows: r.items, cursor: r.next_cursor, forbidden: false }); })
      .catch((e: unknown) => { if (live) setAudit(e instanceof ApiError && (e.status === 403 || e.status === 401) ? { rows: [], cursor: null, forbidden: true } : { rows: [], cursor: null, forbidden: false }); });
    return () => { live = false; };
  }, [auth, auditQuery]);

  const rows = useMemo(() => (items ? evidenceRows(items, rollups, { ...filters, q }) : null), [items, rollups, filters, q]);

  function onFilters(next: ReportFilters): void { setParams(filtersToParams(next), { replace: true }); }
  async function olderPacks(): Promise<void> {
    if (!packsCursor) return;
    setBusy('packs');
    try { const r = await api.reports.evidence(auth, { ...evidenceQuery, cursor: packsCursor }); setItems((xs) => [...(xs ?? []), ...r.items]); setPacksCursor(r.next_cursor); }
    catch (e) { setError(errorText(e)); } finally { setBusy(null); }
  }
  async function olderAudit(): Promise<void> {
    if (!audit?.cursor) return;
    setBusy('audit');
    try { const r = await api.admin.audit(auth, { ...auditQuery, cursor: audit.cursor }); setAudit((a) => ({ rows: [...(a?.rows ?? []), ...r.items], cursor: r.next_cursor, forbidden: false })); }
    catch (e) { setError(errorText(e)); } finally { setBusy(null); }
  }

  return (
    <ReportsView tenant={tenant} readableTenants={readable} ready={ready} filters={filters} now={new Date()} jobs={jobs} rows={rows} packsCursor={packsCursor}
      audit={audit} error={error} busy={busy} onFilters={onFilters} onOlderPacks={() => void olderPacks()} onOlderAudit={() => void olderAudit()}
      onScope={(scope) => onFilters({ ...filters, scope })}
      download={{
        packJson: (item) => api.evidence.json(auth, item.id), packPdf: (item) => api.evidence.pdf(auth, item.id),
        rollup: (r, format) => api.reports.apiRollupFile(auth, r.day, r.action, format), auditCsv: () => api.admin.auditCsv(auth, auditQuery),
      }} />
  );
}
