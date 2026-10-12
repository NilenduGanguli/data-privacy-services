import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Tone } from '../components/Chip';
import { StateChip } from '../components/StateChip';
import { useAuth } from '../hooks/useAuth';
import { api, metricTotal } from '../lib/api';
import { fmtWhen, shortId } from '../lib/jobs';
import type { Job, JobStats, Readiness } from '../lib/types';

/** Recent jobs (mockup 01, the M2 part): the five newest jobs of the tenant. */
function RecentJobs({ jobs }: { jobs: Job[] }): JSX.Element {
  return (
    <div className="card overview-jobs">
      <div className="card-head"><div className="card-title">Recent jobs</div><Link className="btn btn-sm" to="/data-sources">Open Jobs</Link></div>
      <div className="tbl-scroll"><table className="tbl">
        <thead><tr><th>Job</th><th>Object</th><th>Mode</th><th>Status</th><th>Created</th></tr></thead>
        <tbody>
          {jobs.map((j) => (
            <tr key={j.id}>
              <td><Link className="mono" to={`/data-sources/jobs/${j.id}`} title={j.id}>{shortId(j.id)}</Link></td>
              <td className="mono">{j.object_ref ?? '—'}</td>
              <td>{j.mode ? <Tone tone={j.mode === 'synthesize' ? 'violet' : 'teal'}>{j.mode}</Tone> : <span className="faint">{j.kind}</span>}</td>
              <td><StateChip job={j} /></td>
              <td className="faint">{fmtWhen(j.created_at)}</td>
            </tr>
          ))}
          {!jobs.length && <tr><td colSpan={5} className="muted">No jobs yet. <Link to="/data-sources/jobs/new" style={{ color: 'var(--teal)', fontWeight: 600 }}>Start one</Link>.</td></tr>}
        </tbody>
      </table></div>
    </div>
  );
}

export function Overview(): JSX.Element {
  const auth = useAuth();
  const [ready, setReady] = useState<Readiness | null>(null);
  const [metrics, setMetrics] = useState<string>('');
  const [stats, setStats] = useState<JobStats | null>(null);
  const [recent, setRecent] = useState<Job[] | null>(null);
  useEffect(() => {
    api.readyz().then(setReady).catch(() => setReady(null));
    api.metrics().then(setMetrics).catch(() => setMetrics(''));
  }, []);
  const jobsUp = !!ready?.components.jobs?.ok;
  useEffect(() => {
    if (!jobsUp) return;
    api.jobs.stats(auth).then(setStats).catch(() => setStats(null));
    api.jobs.list(auth, { limit: 5 }).then((p) => setRecent(p.items)).catch(() => setRecent(null));
  }, [auth, jobsUp]);
  const requests = metricTotal(metrics, 'dps_requests_total');
  const findings = metricTotal(metrics, 'dps_findings_total');
  const rawToLlm = metricTotal(metrics, 'dps_llm_raw_pii_sent_total');
  return (
    <main className="page">
      <div className="page-head">
        <div><h1>Protection posture</h1><p>Layer 1 serves the engine: JSON, text and documents (OCR, redacted PDFs), schema synthesis. Layer 2 runs batch jobs over PostgreSQL. The UI scanner arrives with M4.</p></div>
        <div className="head-actions">
          {jobsUp && <Link className="btn" to="/data-sources/jobs/new">New job</Link>}
          <Link className="btn btn-primary" to="/playground">Open playground</Link>
        </div>
      </div>
      <div className={`grid ${jobsUp ? 'cols-5' : 'cols-4'}`}>
        <div className="card kpi hero"><div className="label">Requests served</div><div className="value">{requests.toLocaleString()}</div><div className="delta">since process start</div></div>
        <div className="card kpi"><div className="label">Findings</div><div className="value">{findings.toLocaleString()}</div><div className="delta">by entity type and detector</div></div>
        {jobsUp && <div className="card kpi"><div className="label">Jobs running</div><div className="value">{stats ? stats.running : '—'}</div><div className="delta">{stats ? `of ${stats.tenant_max_running} slots · ${stats.workers_online} ${stats.workers_online === 1 ? 'worker' : 'workers'} online` : 'loading'}</div></div>}
        <div className="card kpi"><div className="label">Service</div><div className="value">{ready ? (ready.ready ? 'Ready' : 'Degraded') : '—'}</div><div className="delta">{ready ? Object.keys(ready.components).length : 0} components</div></div>
        <div className={`card kpi ${rawToLlm === 0 ? 'zero' : ''}`}><div className="label">Raw PII sent to LLM</div><div className="value">{rawToLlm}</div><div className="delta">placeholders only · must stay 0</div></div>
      </div>
      {jobsUp && recent && <RecentJobs jobs={recent} />}
      <div className="card">
        <div className="card-head"><div className="card-title">Readiness</div>{ready && <Tone tone={ready.ready ? 'emerald' : 'rose'}>{ready.ready ? 'all required up' : 'degraded'}</Tone>}</div>
        <div className="tbl-scroll"><table className="tbl"><thead><tr><th>Component</th><th>State</th><th>Detail</th></tr></thead>
          <tbody>{ready ? Object.entries(ready.components).map(([name, c]) => (
            <tr key={name}><td className="mono">{name}</td><td><Tone tone={c.ok ? 'emerald' : 'amber'}>{c.ok ? 'ok' : 'off'}</Tone></td><td className="muted">{c.detail}</td></tr>
          )) : <tr><td colSpan={3}><div className="skeleton" /></td></tr>}</tbody></table></div>
      </div>
    </main>
  );
}
