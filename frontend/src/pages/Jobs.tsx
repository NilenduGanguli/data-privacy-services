import { Fragment, useCallback, useEffect, useMemo, useState } from 'react';
import { Link } from 'react-router-dom';
import { EngineBadge } from '../components/EngineBadge';
import { JobGroupsSection } from '../components/groups/JobGroupsSection';
import { Sparkline } from '../components/Sparkline';
import { StateChip } from '../components/StateChip';
import { usePoll } from '../hooks/usePoll';
import { useAuth } from '../hooks/useAuth';
import { api, ApiError, saveBlob, type Auth } from '../lib/api';
import {
  errorText, etaSeconds, filterJobs, fmtClock, fmtCompact, fmtCount, fmtDuration, fmtWhen, isTerminal, jobsCsv, jobTargetText,
  jobWriteMode, needsDecision, NO_FILTERS, ownerOf, progressPct, pushSample, shortId, statusBucket, tableJobs, writeModeLabel,
  writeModeTone, type JobFilters, type StatusBucket,
} from '../lib/jobs';
import type { ClassificationSnapshot, Connection, Job, JobGroup, JobStats } from '../lib/types';

const ALERT_WAIT_S = 300;
const STATUS_LABELS: Record<StatusBucket, string> = { running: 'Running', review: 'Awaiting review', completed: 'Completed', failed: 'Failed', canceled: 'Canceled' };

/** The Queue and workers card: six tiles from `GET /jobs/stats`, ring buffers kept client-side. */
function HealthStrip({ stats, error, queue, tput, updated }: {
  stats: JobStats | undefined; error: Error | undefined; queue: [string, number][]; tput: [string, number][]; updated: string | null;
}): JSX.Element {
  const s = stats;
  const idle = s ? Math.max(0, s.workers_online - s.workers_busy - s.workers_draining) : 0;
  const dots = s ? [...Array(Math.min(s.workers_busy, 24)).fill('busy'), ...Array(Math.min(s.workers_draining, 8)).fill('drain'), ...Array(Math.min(idle, 24)).fill('idle')] as string[] : [];
  const slow = !!s && (s.oldest_waiting_s ?? 0) >= ALERT_WAIT_S;
  const health = !s ? null : s.workers_online === 0 ? { t: 'no workers online', c: 'rose' } : slow ? { t: 'queue slow', c: 'amber' } : { t: 'healthy', c: 'emerald' };
  const free = s ? Math.max(0, s.tenant_max_running - s.running) : 0;
  const qMax = Math.max(0, ...queue.map(([, v]) => v));
  const tMax = Math.max(0, ...tput.map(([, v]) => v));
  return (
    <section className="card" aria-labelledby="hq">
      <div className="card-head">
        <div className="card-title" id="hq">Queue and workers</div>
        <div className="rowx">
          {health && <span className={`chip ${health.c}`}>{health.t}</span>}
          {error && <span className="chip rose">stats unavailable</span>}
          <span className="faint sm">updated {updated ?? '—'} · refreshes every 10 s</span>
        </div>
      </div>
      <div className="hgrid">
        <div className="ht">
          <div className="l">Workers</div>
          <div className="v">{s ? s.workers_online : '—'} <span className="u">online</span></div>
          <div className="wk" role="img" aria-label={s ? `${s.workers_busy} busy, ${s.workers_draining} draining, ${idle} idle` : 'workers loading'}>
            {dots.map((d, i) => <i key={i} className={d === 'busy' ? '' : d} />)}
          </div>
          <div className="s">{s ? `${s.workers_busy} busy · ${s.workers_draining} draining` : 'loading'}</div>
        </div>
        <div className="ht wide">
          <div className="l">Queue depth</div>
          <div className="v">{s ? fmtCount(s.partitions_waiting) : '—'} <span className="u">partitions waiting</span></div>
          <div className="spark"><Sparkline points={queue} width={300} height={48} muted label={`Queue depth over the last ${Math.ceil(queue.length / 6)} minutes, now ${s?.partitions_waiting ?? 0}`} /></div>
          <div className="axis"><span>−{Math.max(1, Math.ceil(queue.length / 6))} min</span><span>scale 0–{fmtCount(qMax)}</span><span>now</span></div>
        </div>
        <div className="ht">
          <div className="l">Oldest waiting</div>
          <div className={`v${slow ? ' amber' : ''}`}>{s ? (s.oldest_waiting_s === null ? 'none' : fmtDuration(s.oldest_waiting_s)) : '—'}</div>
          <div className="s">Alert at 5 min</div>
        </div>
        <div className="ht wide">
          <div className="l">Throughput</div>
          <div className="v">{s ? fmtCompact(s.rows_per_s) : '—'} <span className="u">rows protected / s</span></div>
          <div className="spark"><Sparkline points={tput} width={300} height={48} label={`Rows protected per second over the last ${Math.ceil(tput.length / 6)} minutes, now ${fmtCompact(s?.rows_per_s ?? 0)}`} /></div>
          <div className="axis"><span>−{Math.max(1, Math.ceil(tput.length / 6))} min</span><span>scale 0–{fmtCompact(tMax)}</span><span>now</span></div>
        </div>
        <div className="ht">
          <div className="l">Running jobs</div>
          <div className="v">{s ? s.running : '—'} <span className="u">of {s ? s.tenant_max_running : '—'}</span></div>
          <div className="cap" role="img" aria-label={s ? `${s.running} of ${s.tenant_max_running} slots used` : 'slots loading'}>
            {s && Array.from({ length: Math.min(s.tenant_max_running, 32) }, (_, i) => <i key={i} className={i < s.running ? '' : 'free'} />)}
          </div>
          <div className="s">Tenant cap · {free} {free === 1 ? 'slot' : 'slots'} free</div>
        </div>
        <div className="ht">
          <div className="l">Lease reclaims · 24 h</div>
          <div className="v">{s ? s.lease_reclaims_24h : '—'}</div>
          <div className="s">{!s ? '' : s.lease_reclaims_24h === 0 ? 'No worker lost a lease.' : `${s.lease_reclaims_24h === 1 ? 'It' : 'All'} resumed from the last checkpoint. No row was written twice.`}</div>
        </div>
      </div>
    </section>
  );
}

/** One job row (and its error row when expanded). */
function JobRow({ job, auth, engine, proposed, snapshot, groupName, open, onToggle, onRetried }: {
  job: Job; auth: Auth; engine: string | undefined; proposed: Set<string>; snapshot: ClassificationSnapshot | undefined; groupName: string | undefined;
  open: boolean; onToggle: () => void; onRetried: () => void;
}): JSX.Element {
  const [retry, setRetry] = useState<string | null>(null);
  const pct = progressPct(job);
  const bucket = statusBucket(job, proposed);
  const wm = jobWriteMode(job);
  const load = job.spec?.target?.table_load as string | undefined;
  const failed = job.state === 'failed' || job.state === 'dead';
  const decisions = snapshot ? snapshot.columns.filter(needsDecision).length : 0;
  const target = jobTargetText(job);
  const eta = etaSeconds(job);
  const hits = (job.progress.values_by_method['verify:hits'] ?? 0) + (job.progress.verification.hits ?? 0);
  const barTone = job.mode === 'synthesize' ? ' violet' : '';
  const errId = `err-${job.id}`;

  let line: string;
  if (job.kind === 'discover') {
    line = bucket === 'review' ? (decisions ? `${decisions} ${decisions === 1 ? 'column needs' : 'columns need'} a decision` : 'snapshot ready to accept')
      : job.state === 'succeeded' ? 'snapshot reviewed' : failed ? (job.error_code ?? 'failed') : (job.progress.stage || 'sampling');
  } else if (failed) line = job.error_code ?? 'failed';
  else if (job.state === 'succeeded') line = job.progress.outcome === 'dry_run' ? 'dry run · nothing written' : `leakage sweep ${hits} hits`;
  else if (job.state === 'canceled') line = 'canceled · written rows stay';
  else if (job.state === 'paused') line = 'paused at a committed batch';
  else if (job.state === 'queued' && job.phase !== 'finalize') line = 'waiting for a worker';
  else if (job.phase === 'partitions') line = `${job.progress.partitions_total} partitions${eta !== null ? ` · about ${fmtDuration(eta)} left` : ''}`;
  else line = job.progress.stage || job.phase;

  async function doRetry(): Promise<void> {
    setRetry('retrying');
    try { await api.jobs.retry(auth, job.id); setRetry(null); onRetried(); } catch (e) {
      setRetry(e instanceof ApiError && e.code === 'key_material_required' ? 'The job key was purged. Open the job and load the key file to retry.' : e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : String(e));
    }
  }

  return (
    <Fragment>
      <tr className="job">
        <td className="c-job">
          <Link className="mono jid" to={`/data-sources/jobs/${job.id}`} title={job.id}>{shortId(job.id)}</Link>
          <div className="faint sm">{job.purpose?.ticket ? `${job.purpose.ticket} · ` : `${job.kind} · `}{fmtWhen(job.created_at)}</div>
        </td>
        <td className="c-src">
          <div className="src"><EngineBadge engine={engine} /><div>
            <div className="obj">{job.object_ref ?? '—'}</div>
            {job.kind === 'discover' ? <div className="faint sm">discovery · classification snapshot</div>
              : wm === 'table' || (wm === 'files') ? <div className="to">{'→\u00a0'}{target}</div>
                : <div className="faint sm">{target}</div>}
            {job.group_id && (
              <Link className="grp" to={`/data-sources?group=${encodeURIComponent(job.group_id)}#group`} title="Job group: one consistency key across systems">
                <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} aria-hidden="true"><circle cx="8" cy="15" r="4" /><path d="M11 12l9-9M17 6l3 3" /></svg>{groupName ?? shortId(job.group_id)}
              </Link>
            )}
          </div></div>
        </td>
        <td data-l="Mode · write">
          <div className="mw">
            {job.mode && <span className={`chip ${job.mode === 'synthesize' ? 'violet' : 'teal'}`}>{job.mode}</span>}
            {wm && <span className={`chip ${writeModeTone(wm)}`}>{writeModeLabel(wm)}</span>}
            {job.kind === 'discover' && <span className="chip slate">discover</span>}
          </div>
          {wm === 'table' && load && <div className="faint sm">{load === 'truncate_insert' ? 'truncate + insert' : 'upsert'}</div>}
        </td>
        <td className="c-own" data-l="Owner">{ownerOf(job) || '—'}</td>
        <td className="c-prog">
          {job.kind === 'discover' ? (
            <>
              <div className="pg"><div className="bar amber"><i style={{ width: '0%' }} /></div><span className="pct">—</span></div>
              <div className="faint sm">not started · {snapshot ? `${fmtCount(snapshot.sample_rows)} rows sampled` : job.state === 'succeeded' ? 'sample complete' : 'sampling'}</div>
            </>
          ) : (
            <>
              <div className="pg"><div className={`bar${failed ? ' rose' : job.state === 'succeeded' ? ' emerald' : barTone}`}><i style={{ width: `${pct ?? 0}%` }} /></div><span className="pct">{pct === null || failed ? '—' : `${pct}%`}</span></div>
              <div className="faint sm mono">{job.state === 'succeeded' ? `${fmtCount(job.progress.rows_written)} rows` : `${fmtCount(job.progress.rows_written)} / ${fmtCount(job.progress.rows_estimated)}`}</div>
            </>
          )}
        </td>
        <td className="c-status"><StateChip job={job} proposed={proposed} /><div className="faint sm">{line}</div></td>
        <td className="c-act">
          <div className="rowx">
            {bucket === 'review' && job.progress.snapshot_ids[0]
              ? <Link className="btn btn-sm" to={`/data-sources/jobs/new?snapshot=${encodeURIComponent(job.progress.snapshot_ids[0])}`}>{decisions ? `Review ${decisions} ${decisions === 1 ? 'column' : 'columns'}` : 'Review snapshot'}</Link>
              : <Link className="btn btn-sm" to={`/data-sources/jobs/${job.id}`}>Open</Link>}
            {job.progress.evidence_id && <Link className="btn btn-sm" to={`/reports/evidence/${job.progress.evidence_id}`}>Evidence pack</Link>}
            {failed && <button className="linkbtn" type="button" aria-expanded={open} aria-controls={errId} onClick={onToggle}>{open ? 'Hide error' : 'Show error'}</button>}
          </div>
        </td>
      </tr>
      {failed && open && (
        <tr className="err" id={errId}><td colSpan={7}><div className="errbox">
          <div className="what"><b>{errorText(job.error_code)}</b>{job.error ? ` ${job.error}` : ''}</div>
          <div className="fix">{retry && retry !== 'retrying' ? retry : job.kind === 'run' ? 'Retry resumes every partition from its last committed batch; rows already written are not written again.' : 'Fix the cause, then start a new discovery.'}</div>
          {job.kind === 'run' && <button className="btn btn-sm" type="button" disabled={retry === 'retrying'} onClick={() => void doRetry()}>Retry</button>}
        </div></td></tr>
      )}
    </Fragment>
  );
}

/** Jobs (mockup 07): queue health, filters, the jobs table and the job groups (M3). */
export function Jobs(): JSX.Element {
  const auth = useAuth();
  const [filters, setFilters] = useState<JobFilters>(NO_FILTERS);
  const [openErr, setOpenErr] = useState<Set<string>>(new Set());
  const [queue, setQueue] = useState<[string, number][]>([]);
  const [tput, setTput] = useState<[string, number][]>([]);
  const [connections, setConnections] = useState<Connection[]>([]);

  const [anyActive, setAnyActive] = useState(true);
  const list = usePoll(() => api.jobs.list(auth, { limit: 200 }), anyActive ? 5000 : 30000, () => true, [auth]);
  const snaps = usePoll(() => api.snapshots.list(auth, { status: 'proposed', limit: 200 }), anyActive ? 5000 : 30000, () => true, [auth]);
  const stats = usePoll(() => api.jobs.stats(auth), 10000, () => true, [auth]);
  const [updated, setUpdated] = useState<string | null>(null);

  const [groups, setGroups] = useState<JobGroup[] | null>(null);
  const loadGroups = useCallback(() => { api.groups.list(auth).then((p) => setGroups(p.items)).catch(() => setGroups([])); }, [auth]);
  useEffect(() => { loadGroups(); }, [loadGroups]);
  useEffect(() => { api.connections.list(auth, 200).then((p) => setConnections(p.items)).catch(() => setConnections([])); }, [auth]);
  useEffect(() => { setAnyActive(tableJobs(list.data?.items ?? []).some((j) => !isTerminal(j))); }, [list.data]);
  useEffect(() => {
    if (!stats.data) return;
    const now = new Date().toISOString();
    setQueue((b) => pushSample(b, [now, stats.data!.partitions_waiting]));
    setTput((b) => pushSample(b, [now, stats.data!.rows_per_s]));
    setUpdated(fmtClock(now));
  }, [stats.data]);

  const jobs = tableJobs(list.data?.items ?? []);
  const proposedSnaps = snaps.data?.items ?? [];
  const proposed = useMemo(() => new Set(proposedSnaps.map((s) => s.id)), [proposedSnaps]);
  const snapById = useMemo(() => new Map(proposedSnaps.map((s) => [s.id, s])), [proposedSnaps]);
  const groupNames = useMemo(() => new Map((groups ?? []).map((g) => [g.id, g.name])), [groups]);
  const engines = useMemo(() => Object.fromEntries(connections.map((c) => [c.id, c.engine])) as Record<string, string>, [connections]);
  const shown = useMemo(() => filterJobs(jobs, filters, { proposed, engines }), [jobs, filters, proposed, engines]);
  const anyFilter = JSON.stringify(filters) !== JSON.stringify(NO_FILTERS);

  const count = (pred: (j: Job) => boolean) => jobs.filter(pred).length;
  const owners = [...new Set(jobs.map(ownerOf).filter(Boolean))].sort();
  const set = (k: keyof JobFilters) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setFilters((f) => ({ ...f, [k]: e.target.value }));
  const clear = () => setFilters(NO_FILTERS);

  function exportCsv(): void {
    saveBlob(new Blob([jobsCsv(shown)], { type: 'text/csv' }), `dps-jobs-${new Date().toISOString().slice(0, 10)}.csv`);
  }

  const loadError = list.error instanceof ApiError ? `${list.error.message}${list.error.code ? ` (${list.error.code})` : ''}` : list.error ? String(list.error.message) : null;

  return (
    <main className="page jobs-page">
      <div className="page-head">
        <div><h1>Jobs</h1><p>Batch jobs over PostgreSQL, Oracle and MongoDB in the last 7 days. Keys go back to the requester when a job is submitted; DPS keeps none.</p></div>
        <div className="head-actions">
          <button className="btn" type="button" onClick={exportCsv} disabled={!shown.length}>Export list (CSV)</button>
          <Link className="btn btn-primary" to="/data-sources/jobs/new">New job</Link>
        </div>
      </div>

      <HealthStrip stats={stats.data} error={stats.error} queue={queue} tput={tput} updated={updated} />

      <section className="card" aria-labelledby="hj">
        <div className="card-head"><div className="card-title" id="hj">All jobs</div><span className="faint sm">Showing {shown.length} of {jobs.length} jobs</span></div>
        {loadError && <div className="err" style={{ marginBottom: 12 }}>Could not load jobs: {loadError}</div>}
        <div className="filters" role="search" aria-label="Filter jobs">
          <div className="search">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.2} aria-hidden="true"><circle cx="11" cy="11" r="7" /><path d="M20 20l-4-4" /></svg>
            <label className="sr" htmlFor="jobs-q">Search jobs</label>
            <input className="input" id="jobs-q" type="search" placeholder="Search job, table, ticket" autoComplete="off" value={filters.q} onChange={set('q')} />
          </div>
          <div className="sel"><label className="sr" htmlFor="f-status">Status</label>
            <select className={`input${filters.status ? ' set' : ''}`} id="f-status" value={filters.status} onChange={set('status')}>
              <option value="">Status: all</option>
              {(Object.keys(STATUS_LABELS) as StatusBucket[]).map((b) => <option key={b} value={b}>{STATUS_LABELS[b]} ({count((j) => statusBucket(j, proposed) === b)})</option>)}
            </select></div>
          <div className="sel"><label className="sr" htmlFor="f-engine">Engine</label>
            <select className={`input${filters.engine ? ' set' : ''}`} id="f-engine" value={filters.engine} onChange={set('engine')}>
              <option value="">Engine: all</option>
              {[['postgres', 'PostgreSQL'], ['oracle', 'Oracle'], ['mongo', 'MongoDB']].map(([v, l]) => <option key={v} value={v}>{l} ({count((j) => engines[j.connection_id ?? ''] === v)})</option>)}
            </select></div>
          <div className="sel"><label className="sr" htmlFor="f-mode">Mode</label>
            <select className={`input${filters.mode ? ' set' : ''}`} id="f-mode" value={filters.mode} onChange={set('mode')}>
              <option value="">Mode: all</option>
              <option value="obfuscate">Obfuscate ({count((j) => j.mode === 'obfuscate')})</option>
              <option value="synthesize">Synthesize ({count((j) => j.mode === 'synthesize')})</option>
            </select></div>
          <div className="sel"><label className="sr" htmlFor="f-wm">Write mode</label>
            <select className={`input${filters.writeMode ? ' set' : ''}`} id="f-wm" value={filters.writeMode} onChange={set('writeMode')}>
              <option value="">Write mode: all</option>
              {[['in_place', 'In place'], ['append', 'Append'], ['table', 'Copy to a table'], ['files', 'Export files']].map(([v, l]) => <option key={v} value={v}>{l} ({count((j) => jobWriteMode(j) === v)})</option>)}
            </select></div>
          <div className="sel"><label className="sr" htmlFor="f-owner">Owner</label>
            <select className={`input${filters.owner ? ' set' : ''}`} id="f-owner" value={filters.owner} onChange={set('owner')}>
              <option value="">Owner: all</option>
              {owners.map((o) => <option key={o} value={o}>{o}</option>)}
            </select></div>
          {anyFilter && <button className="btn btn-ghost btn-sm" type="button" onClick={clear}>Clear filters</button>}
        </div>

        <div className="twrap"><table className="tbl jobs">
          <colgroup><col className="w-job" /><col /><col className="w-mw" /><col className="w-own" /><col className="w-prog" /><col className="w-st" /><col className="w-act" /></colgroup>
          <thead><tr><th className="w-job">Job</th><th>Source → target</th><th className="w-mw">Mode · write</th><th className="w-own c-own">Owner</th><th className="w-prog">Progress</th><th className="w-st">Status</th><th className="w-act"><span className="sr">Actions</span></th></tr></thead>
          <tbody>
            {shown.map((j) => (
              <JobRow key={j.id} job={j} auth={auth} engine={engines[j.connection_id ?? '']} proposed={proposed}
                      snapshot={snapById.get(j.progress.snapshot_ids[0] ?? '')} groupName={j.group_id ? groupNames.get(j.group_id) : undefined} open={openErr.has(j.id)}
                      onToggle={() => setOpenErr((s) => { const n = new Set(s); if (n.has(j.id)) n.delete(j.id); else n.add(j.id); return n; })}
                      onRetried={list.refresh} />
            ))}
            {!shown.length && list.data && (
              <tr className="emptyrow"><td colSpan={7} className="empty">
                {jobs.length ? <><div className="t">No jobs match these filters</div><p>Try a different status or owner, or start a job for the data you are looking for.</p></>
                  : <><div className="t">No jobs yet</div><p>Connect a PostgreSQL, Oracle or MongoDB source and protect a table in five steps.</p></>}
                <div className="rowx" style={{ justifyContent: 'center' }}>
                  {jobs.length > 0 && <button className="btn btn-sm" type="button" onClick={clear}>Clear filters</button>}
                  <Link className="btn btn-sm btn-primary" to="/data-sources/jobs/new">New job</Link>
                </div>
              </td></tr>
            )}
            {!list.data && !list.error && <tr className="emptyrow"><td colSpan={7}><div className="skeleton" /></td></tr>}
          </tbody>
        </table></div>
        <div className="tfoot"><span>Times are in your browser&apos;s time zone. Jobs and their evidence are kept for 30 days.</span><span>Paused jobs resume from their last checkpoint.</span></div>
      </section>

      <JobGroupsSection auth={auth} groups={groups} jobs={jobs} onGroupsChanged={loadGroups} />
    </main>
  );
}
