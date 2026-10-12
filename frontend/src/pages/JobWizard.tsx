import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useSearchParams } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { api, ApiError } from '../lib/api';
import { draftForStorage, emptyDraft, fmtClock, fmtCount, fqn, newDraftId, sourceObject, specPreview, writeModeLabel, type Step, type WizardDraft } from '../lib/jobs';
import type { Connection } from '../lib/types';
import { StepConnect } from './wizard/StepConnect';
import { StepDiscover } from './wizard/StepDiscover';
import { StepSelect } from './wizard/StepSelect';
import { DRAFT_KEY, loadSavedDraft, reachable, useWizard, wizardReducer } from './wizard/state';

const STEP_TITLES: Record<Step, string> = { 1: 'Connect', 2: 'Select data', 3: 'Discover', 4: 'Target and write mode', 5: 'Review and run' };

function Check(): JSX.Element {
  return <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.4} aria-hidden="true"><path d="M5 12l5 5L20 7" /></svg>;
}

/** The job specification as highlighted JSON (keys muted, values bright). */
function JsonPane({ value }: { value: unknown }): JSX.Element {
  const lines = JSON.stringify(value, null, 2).split('\n');
  return (
    <div className="pane spec" tabIndex={0} aria-label="Job specification JSON">
      {lines.map((line, i) => {
        const m = /^(\s*)("(?:[^"\\]|\\.)*")(: )(.*)$/.exec(line);
        return <span key={i} style={{ display: 'block' }}>{m ? <>{m[1]}<span className="k">{m[2]}</span>{m[3]}<span className="s">{m[4]}</span></> : <span className="s">{line}</span>}</span>;
      })}
    </div>
  );
}

/** The one-line summary a finished step shows in the stepper. */
function stepSub(step: Step, d: WizardDraft): string {
  switch (step) {
    case 1: return d.connectionId ? `PostgreSQL · ${d.connectionName}` : 'PostgreSQL';
    case 2: return d.shape ? `${d.count ? fmtCount(d.count.rows) : `~${fmtCount(d.shape.row_estimate)}`} rows · ${d.columns?.length ?? d.shape.columns.length} columns` : '';
    case 3: return d.snapshotId ? (d.snapshotAccepted ? 'snapshot accepted' : 'review pending') : '';
    case 4: return d.writeMode ? `${writeModeLabel(d.writeMode, d.writeMode === 'table' ? d.tableLoad : null)}${d.typed ? ' · confirmed' : ''}` : '';
    case 5: return d.ticket ? `${d.mode} · ${d.ticket}` : '';
    default: return '';
  }
}

/** Steps that arrive with the next tasks of the plan render a note in the meantime. */
function Pending({ step, onBack }: { step: Step; onBack: () => void }): JSX.Element {
  return (
    <section className="panel">
      <div className="panel-head"><h2>{step} · {STEP_TITLES[step]}</h2></div>
      <div className="card"><p className="muted" style={{ margin: 0 }}>This step is not available yet.</p></div>
      <div className="wfoot"><button className="btn" type="button" onClick={onBack}>Back</button></div>
    </section>
  );
}

/** New job (mockup 06): five steps on the left, guarantees and the job specification on the right. */
export function JobWizard(): JSX.Element {
  const auth = useAuth();
  const [params] = useSearchParams();
  const deepSnapshot = params.get('snapshot');
  const restoredFrom = useRef<Partial<WizardDraft> | null>(null);
  const [linkError, setLinkError] = useState<string | null>(null);
  const [d, dispatch] = useWizard(() => {
    const saved = deepSnapshot ? null : loadSavedDraft();
    if (!saved) return emptyDraft();
    restoredFrom.current = saved;
    return wizardReducer(emptyDraft(typeof saved.draftId === 'string' ? saved.draftId : newDraftId()), { type: 'restore', draft: saved });
  });
  const [savedAt, setSavedAt] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const [connections, setConnections] = useState<Connection[]>([]);
  const reach = reachable(d);

  const loadConnections = useCallback(() => {
    api.connections.list(auth, 200).then((p) => setConnections(p.items)).catch(() => setConnections([]));
  }, [auth]);
  useEffect(() => { loadConnections(); }, [loadConnections]);

  // ?snapshot=<id> (Jobs "Review N columns"): open step 3 on that snapshot, its connection and object restored
  useEffect(() => {
    if (!deepSnapshot) return;
    let live = true;
    (async () => {
      try {
        const snap = await api.snapshots.get(auth, deepSnapshot);
        const conn = await api.connections.get(auth, snap.connection_id);
        const test = conn.last_test?.ok ? conn.last_test : await api.connections.test(auth, conn.id, { role: 'source' });
        if (!live) return;
        const dot = snap.object_ref.indexOf('.');
        dispatch({ type: 'set', patch: {
          connMode: 'saved', connectionId: conn.id, connectionName: conn.name, connectionTags: conn.tags, connectionRoles: conn.roles,
          production: conn.tags.includes('production'), test, testedAt: new Date().toISOString(),
          owner: dot > 0 ? snap.object_ref.slice(0, dot) : '', table: dot > 0 ? snap.object_ref.slice(dot + 1) : snap.object_ref,
          shape: snap.shape, discoverJobId: snap.job_id, snapshotId: snap.id, snapshotAccepted: snap.status === 'accepted',
          policyName: snap.policy_name, sampleRows: String(snap.sample_rows),
        } });
        dispatch({ type: 'step', step: 3 });
      } catch (e) {
        if (live) setLinkError(e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : String(e));
      }
    })();
    return () => { live = false; };
  }, [auth, deepSnapshot, dispatch]);

  // a restored draft opens on the farthest step it can still show
  useEffect(() => { if (d.step > reach) dispatch({ type: 'step', step: reach }); }, [d.step, reach, dispatch]);
  useEffect(() => { window.scrollTo({ top: 0 }); }, [d.step]);

  const go = (step: Step) => { if (step <= reach) dispatch({ type: 'step', step }); };
  const preview = useMemo(() => specPreview(d), [d]);

  function saveDraft(): void {
    try {
      localStorage.setItem(DRAFT_KEY, JSON.stringify(draftForStorage(d)));
      setSavedAt(new Date().toISOString());
    } catch { setSavedAt(null); }
  }

  async function copySpec(): Promise<void> {
    try { await navigator.clipboard.writeText(JSON.stringify(preview, null, 2)); setCopied(true); window.setTimeout(() => setCopied(false), 2000); } catch { setCopied(false); }
  }

  const steps: Step[] = [1, 2, 3, 4, 5];
  return (
    <main className={`page wizard-page${d.mode === 'synthesize' ? ' mode-syn' : ''}`}>
      <div className="faint sm rowx">
        <Link to="/data-sources" style={{ color: 'var(--ink-2)', fontWeight: 600 }}>Data sources &amp; jobs</Link><span>/</span>
        <span>New job · draft <span className="mono">{d.draftId}</span>{savedAt ? ` · Draft saved ${fmtClock(savedAt, false)}` : ''}</span>
      </div>
      <div className="page-head">
        <div><h1>New job{d.table ? ` · ${fqn(sourceObject(d))}` : ''}</h1><p>Protect a table in five steps. DPS reads only the discovery sample until you press Run job, and writes nothing before then.</p></div>
        <div className="head-actions"><Link className="btn btn-ghost" to="/data-sources">Cancel</Link><button className="btn" type="button" onClick={saveDraft}>Save draft</button></div>
      </div>
      {linkError && <div className="err">Could not open the snapshot: {linkError}</div>}
      {restoredFrom.current && (
        <div className="callout amber" style={{ fontSize: 12.5 }}>Restored the saved draft <span className="mono">{d.draftId}</span>. Credentials, keys and filters are not saved: re-enter them.{' '}
          <button type="button" className="linkbtn" onClick={() => { restoredFrom.current = null; try { localStorage.removeItem(DRAFT_KEY); } catch { /* private mode */ } dispatch({ type: 'reset' }); }}>Start a new draft</button></div>
      )}

      <div className="card" style={{ padding: '12px 14px' }}><nav className="wsteps" aria-label="Job steps">
        {steps.map((s, i) => (
          <div key={s} style={{ display: 'contents' }}>
            {i > 0 && <div className="step-sep" />}
            <button type="button" className={`step${d.step === s ? ' on' : s < reach ? ' done' : ''}`} disabled={s > reach} aria-current={d.step === s ? 'step' : undefined} onClick={() => go(s)}>
              <span className="n">{s < reach && d.step !== s ? '✓' : s}</span>
              <span className="lbl"><span className="t">{STEP_TITLES[s]}</span>{stepSub(s, d) && <span className="sub">{stepSub(s, d)}</span>}</span>
            </button>
          </div>
        ))}
      </nav></div>

      <div className="wiz">
        <div className="wiz-main">
          {d.step === 1 && <StepConnect d={d} dispatch={dispatch} auth={auth} connections={connections} onConnectionsChanged={loadConnections} onNext={() => go(2)} />}
          {d.step === 2 && <StepSelect d={d} dispatch={dispatch} auth={auth} onNext={() => go(3)} onBack={() => go(1)} />}
          {d.step === 3 && <StepDiscover d={d} dispatch={dispatch} auth={auth} onNext={() => go(4)} onBack={() => go(2)} />}
          {d.step >= 4 && <Pending step={d.step} onBack={() => go((d.step - 1) as Step)} />}
        </div>

        <aside className="rail" aria-label="Job specification">
          <div className="card">
            <div className="card-head"><div className="card-title">What DPS guarantees</div></div>
            <div className="guard">
              <div><Check /><span>The source is read in read-only transactions, never locked{d.writeMode === 'in_place' || d.writeMode === 'append' ? ', and written only after your typed confirmation' : ' and never written'}.</span></div>
              <div><Check /><span>The job stores secret names and sealed credentials, never a password in clear.</span></div>
              <div><Check /><span>Values are transformed inside the worker. Raw PII sent to an LLM: 0.</span></div>
              <div><Check /><span>A leakage sweep checks the output before the job is marked complete, and every run ends with an evidence pack.</span></div>
            </div>
          </div>
          <div className="card spec-card">
            <div className="card-head"><div className="card-title">Job specification</div>
              <div className="rowx"><span className="pill mono" style={{ fontSize: 11 }}>POST /api/v1/jobs/run</span><button className="btn btn-sm" type="button" onClick={() => void copySpec()}>{copied ? 'Copied' : 'Copy JSON'}</button></div></div>
            <JsonPane value={preview} />
            <div className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>Filters appear as entered, bound parameters as &lt;bound&gt;. Key material is never part of the specification.</div>
          </div>
        </aside>
      </div>
    </main>
  );
}
