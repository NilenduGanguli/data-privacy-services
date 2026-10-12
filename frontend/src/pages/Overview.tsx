import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { Tone } from '../components/Chip';
import { api, metricTotal } from '../lib/api';
import type { Readiness } from '../lib/types';

export function Overview(): JSX.Element {
  const [ready, setReady] = useState<Readiness | null>(null);
  const [metrics, setMetrics] = useState<string>('');
  useEffect(() => {
    api.readyz().then(setReady).catch(() => setReady(null));
    api.metrics().then(setMetrics).catch(() => setMetrics(''));
  }, []);
  const requests = metricTotal(metrics, 'dps_requests_total');
  const findings = metricTotal(metrics, 'dps_findings_total');
  const rawToLlm = metricTotal(metrics, 'dps_llm_raw_pii_sent_total');
  return (
    <main className="page">
      <div className="page-head">
        <div><h1>Protection posture</h1><p>Milestone M1 · Layer 1 complete: JSON, text and documents (OCR, redacted PDFs), schema synthesis. Connectors and the UI scanner arrive with M2–M4.</p></div>
        <Link className="btn btn-primary" to="/playground">Open playground</Link>
      </div>
      <div className="grid cols-4">
        <div className="card kpi hero"><div className="label">Requests served</div><div className="value">{requests.toLocaleString()}</div><div className="delta">since process start</div></div>
        <div className="card kpi"><div className="label">Findings</div><div className="value">{findings.toLocaleString()}</div><div className="delta">by entity type and detector</div></div>
        <div className="card kpi"><div className="label">Service</div><div className="value">{ready ? (ready.ready ? 'Ready' : 'Degraded') : '—'}</div><div className="delta">{ready ? Object.keys(ready.components).length : 0} components</div></div>
        <div className={`card kpi ${rawToLlm === 0 ? 'zero' : ''}`}><div className="label">Raw PII sent to LLM</div><div className="value">{rawToLlm}</div><div className="delta">placeholders only · must stay 0</div></div>
      </div>
      <div className="card">
        <div className="card-head"><div className="card-title">Readiness</div>{ready && <Tone tone={ready.ready ? 'emerald' : 'rose'}>{ready.ready ? 'all required up' : 'degraded'}</Tone>}</div>
        <table className="tbl"><thead><tr><th>Component</th><th>State</th><th>Detail</th></tr></thead>
          <tbody>{ready ? Object.entries(ready.components).map(([name, c]) => (
            <tr key={name}><td className="mono">{name}</td><td><Tone tone={c.ok ? 'emerald' : 'amber'}>{c.ok ? 'ok' : 'off'}</Tone></td><td className="muted">{c.detail}</td></tr>
          )) : <tr><td colSpan={3}><div className="skeleton" /></td></tr>}</tbody></table>
      </div>
    </main>
  );
}
