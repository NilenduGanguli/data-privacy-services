import { useEffect, useState } from 'react';
import { Link } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { api, ApiError } from '../lib/api';
import { fmtCount, fmtUtc, shortId, verificationText, verificationTone, writeModeLabel } from '../lib/jobs';
import type { EvidenceSummary } from '../lib/types';

function apiMessage(e: unknown): string {
  return e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : e instanceof Error ? e.message : String(e);
}

/** Reports (mockup 09, the M2 part): the evidence pack list. Audit trail, trends and exports are M5. */
export function Reports(): JSX.Element {
  const auth = useAuth();
  const [items, setItems] = useState<EvidenceSummary[] | null>(null);
  const [cursor, setCursor] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  useEffect(() => {
    setItems(null); setError(null);
    api.evidence.list(auth, { limit: 50 }).then((p) => { setItems(p.items); setCursor(p.next_cursor); }).catch((e) => setError(apiMessage(e)));
  }, [auth]);

  async function older(): Promise<void> {
    if (!cursor) return;
    setBusy('older');
    try { const p = await api.evidence.list(auth, { limit: 50, cursor }); setItems((xs) => [...(xs ?? []), ...p.items]); setCursor(p.next_cursor); }
    catch (e) { setError(apiMessage(e)); } finally { setBusy(null); }
  }

  async function downloadJson(s: EvidenceSummary): Promise<void> {
    setBusy(s.id); setError(null);
    try {
      const ev = await api.evidence.get(auth, s.id);
      const art = ev.artefacts.find((a) => a.kind === 'evidence_json');
      if (!art) throw new Error('the pack has no JSON artefact');
      await api.artefacts.download(auth, art.id, `dps-evidence-${s.id}.json`);
    } catch (e) { setError(apiMessage(e)); } finally { setBusy(null); }
  }

  const list = items ?? [];
  const review = list.filter((s) => !s.passed && s.review_required && !s.hits).length;
  const failed = list.filter((s) => !s.passed).length - review;
  return (
    <main className="page reports-page">
      <div className="page-head">
        <div><h1>Evidence and audit</h1><p>Every job leaves an evidence pack. Packs hold counts, entity types, method names, hashes and key ids. They never hold values.</p></div>
      </div>
      <div className="grid cols-3">
        <div className="card kpi"><div className="label">Evidence packs</div><div className="value">{items ? fmtCount(list.length) : '—'}{cursor ? '+' : ''}</div><div className="delta">{cursor ? 'loaded so far · older packs below' : 'kept under the tenant retention policy'}</div></div>
        <div className="card kpi"><div className="label">Verification</div><div className="value">{items ? `${fmtCount(list.length - failed - review)} / ${fmtCount(review)} / ${fmtCount(failed)}` : '—'}</div><div className="delta">passed / review required / failed{failed ? ' · failures failed closed' : ''}</div></div>
        <div className="card kpi zero"><div className="label">Customer values stored</div><div className="value">0</div><div className="delta">counts, hashes and key ids only</div></div>
      </div>

      <section className="card" aria-labelledby="ep-t">
        <div className="card-head"><div className="card-title" id="ep-t">Evidence packs</div><span className="faint sm">Kept under the tenant retention policy. Download a pack to keep it longer.</span></div>
        {error && <div className="err" style={{ marginBottom: 10 }}>{error}</div>}
        <div className="tbl-scroll"><table className="tbl stack" id="packs"><thead><tr><th style={{ width: '34%' }}>Job or request</th><th>Layer</th><th>Mode</th><th>Policy</th><th>Verification</th><th>Created (UTC)</th><th>Download</th></tr></thead><tbody>
          {list.map((s) => (
            <tr key={s.id}>
              <td className="sub"><Link className="mono" to={`/data-sources/jobs/${s.job_id}`} title={s.job_id}>{shortId(s.job_id)}</Link> · {s.source_object ?? '—'} → {s.target ?? '—'}
                <span className="s2">{[s.ticket, writeModeLabel(s.write_mode), `${fmtCount(s.rows_written)} rows`, s.outcome !== 'succeeded' ? s.outcome.replace('_', ' ') : null].filter(Boolean).join(' · ')}</span></td>
              <td data-l="Layer">Connectors</td>
              <td data-l="Mode">{s.mode ? <span className={`chip ${s.mode === 'synthesize' ? 'violet' : 'teal'}`}>{s.mode}</span> : '—'}</td>
              <td className="mono" data-l="Policy">{s.policy ?? '—'}</td>
              <td data-l="Verification"><span className={`chip ${verificationTone(s.passed, s.hits, s.review_required)}`} title={s.review_required && !s.passed && !s.hits ? 'The post-write sample found personal data in a column kept as not personal' : undefined}>{verificationText(s.passed, s.hits, s.review_required)}</span></td>
              <td className="t" data-l="Created (UTC)">{fmtUtc(s.created_at, false).replace(' UTC', '')}</td>
              <td data-l="Download"><div className="dl">
                <button className="btn btn-sm" type="button" disabled={busy === s.id} aria-label={`Download evidence pack ${shortId(s.job_id)} as JSON`} onClick={() => void downloadJson(s)}>JSON</button>
                <Link className="btn btn-sm" to={`/reports/evidence/${s.id}`} aria-label={`Open evidence pack ${shortId(s.job_id)}`}>Open</Link>
              </div></td>
            </tr>
          ))}
          {items && !list.length && <tr className="empty-row"><td colSpan={7}>No evidence pack yet. Every finished run leaves one. <Link to="/data-sources/jobs/new" style={{ color: 'var(--teal)', fontWeight: 600 }}>Start a job</Link>.</td></tr>}
          {!items && !error && <tr><td colSpan={7}><div className="skeleton" /></td></tr>}
        </tbody></table></div>
        <div className="card-foot"><span>Times are UTC. Packs are sealed with their sha256 when the job finishes.</span>{cursor && <button className="btn btn-sm" type="button" disabled={busy === 'older'} onClick={() => void older()}>Show older</button>}</div>
      </section>

      <section className="card">
        <div className="card-title">Audit log, verification trends and exports</div>
        <p className="muted" style={{ margin: '8px 0 0', fontSize: 13 }}>The audit log, verification trends and exports arrive with M5.</p>
      </section>
    </main>
  );
}
