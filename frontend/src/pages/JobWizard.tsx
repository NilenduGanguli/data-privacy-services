import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { useAuth } from '../hooks/useAuth';
import { api, ApiError } from '../lib/api';
import {
  buildRunSpec, draftForStorage, emptyDraft, engineName, fmtClock, fmtCount, fqn, isWriteConfirmed, newDraftId, sourceObject, specPreview, writeModeLabel,
  type Step, type WizardDraft,
} from '../lib/jobs';
import type { ClassificationSnapshot, Connection, RunOut } from '../lib/types';
import { RunResult } from './wizard/RunResult';
import { StepConnect } from './wizard/StepConnect';
import { StepDiscover } from './wizard/StepDiscover';
import { StepRun, type RunSubmission } from './wizard/StepRun';
import { StepSelect } from './wizard/StepSelect';
import { StepTarget } from './wizard/StepTarget';
import { clearSavedDraft, DRAFT_KEY, isSubmitted, loadSavedDraft, reachable, submissionKey, type SubmissionKey, useWizard, wizardReducer } from './wizard/state';

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
    case 1: return d.connectionId ? `${engineName(d.engine)} · ${d.connectionName}` : engineName(d.engine);
    case 2: return d.shape ? `${d.count ? fmtCount(d.count.rows) : `~${fmtCount(d.shape.row_estimate)}`} ${d.engine === 'mongo' ? 'documents' : 'rows'} · ${d.columns?.length ?? new Set(d.shape.columns.map((c) => c.name)).size} ${d.engine === 'mongo' ? 'fields' : 'columns'}` : '';
    case 3: return d.snapshotId ? (d.snapshotAccepted ? 'snapshot accepted' : 'review pending') : '';
    case 4: return d.writeMode ? `${writeModeLabel(d.writeMode, d.writeMode === 'table' ? d.tableLoad : null)}${isWriteConfirmed(d) ? ' · confirmed' : ''}` : '';
    case 5: return d.ticket ? `${d.mode} · ${d.ticket}` : '';
    default: return '';
  }
}

function apiError(e: unknown): { message: string; code?: string } {
  return e instanceof ApiError ? { message: e.message, code: e.code } : { message: e instanceof Error ? e.message : String(e) };
}

/**
 * New job (mockup 06): five steps on the left, guarantees and the job specification on the right.
 * The wizard owns the submission: the Idempotency-Key (one per submission, kept across retries of the
 * same kind), the request in flight (the stepper is locked meanwhile) and the answer of Run job — the
 * job and its once-only key, in memory only. Once a run is submitted the draft is closed (`submitted`)
 * and `RunResult` replaces the steps until Start a new job.
 */
export function JobWizard(): JSX.Element {
  const auth = useAuth();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const deepSnapshot = params.get('snapshot');
  const deepGroup = params.get('group');   // Jobs → "Add a job to this group"
  const [groupName, setGroupName] = useState<string | null>(null);
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
  const [snapshot, setSnapshot] = useState<ClassificationSnapshot | null>(null);
  const [busy, setBusy] = useState<'run' | 'dry' | null>(null);
  const [runError, setRunError] = useState<{ message: string; code?: string } | null>(null);
  /** The answer of Run job and the specification it was given (without key material). Memory only. */
  const [submission, setSubmission] = useState<{ out: RunOut; spec: Record<string, unknown> } | null>(null);
  const idem = useRef<SubmissionKey | null>(null);
  const inFlight = useRef(false);   // set synchronously: a second click before the re-render sends nothing
  const reach = reachable(d);
  const submitted = isSubmitted(d);
  const frozen = submitted || busy !== null;   // no step changes while a run is in flight or after it

  // steps 4 and 5 read the accepted snapshot (key, one-way columns, plan); the draft takes its key
  // methods from it (never from storage), so In place opens only when the server's snapshot keeps the key
  useEffect(() => {
    if (!d.snapshotId || !d.snapshotAccepted) { setSnapshot(null); return; }
    if (snapshot?.id === d.snapshotId && snapshot.status === 'accepted') return;
    api.snapshots.get(auth, d.snapshotId).then((s) => { setSnapshot(s); dispatch({ type: 'snapshot', snapshot: s }); }).catch(() => setSnapshot(null));
  }, [auth, d.snapshotId, d.snapshotAccepted, snapshot, dispatch]);

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
          engine: conn.engine === 'oracle' || conn.engine === 'mongo' ? conn.engine : 'postgres', selMode: conn.engine === 'mongo' ? 'mongo' : 'where',
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

  // ?group=<id>: step 5's consistency control starts on that group (the caller still loads its key)
  useEffect(() => {
    if (!deepGroup) return;
    dispatch({ type: 'set', patch: { keyChoice: 'group', groupId: deepGroup } });
    let live = true;
    api.groups.get(auth, deepGroup).then((g) => { if (live) setGroupName(g.group.name); }).catch(() => { if (live) setGroupName(null); });
    return () => { live = false; };
  }, [auth, deepGroup, dispatch]);

  // a restored draft opens on the farthest step it can still show
  useEffect(() => { if (!frozen && d.step > reach) dispatch({ type: 'step', step: reach }); }, [d.step, reach, frozen, dispatch]);
  useEffect(() => { window.scrollTo({ top: 0 }); }, [d.step, submitted]);

  const go = (step: Step) => { if (!frozen && step <= reach) dispatch({ type: 'step', step }); };
  const preview = useMemo(() => submission?.spec ?? specPreview(d), [d, submission]);

  async function submit(dry: boolean): Promise<void> {
    if (inFlight.current || submitted) return;
    inFlight.current = true;
    setBusy(dry ? 'dry' : 'run'); setRunError(null);
    try {
      const spec = buildRunSpec(d);
      spec.options = { ...spec.options, dry_run: dry };
      idem.current = submissionKey(idem.current, dry, JSON.stringify(spec));
      const shown = { ...specPreview(d), options: spec.options };   // the rail keeps what was sent
      const out = await api.jobs.run(auth, spec, idem.current.key);
      idem.current = null;
      clearSavedDraft();
      if (dry) { navigate(`/data-sources/jobs/${out.job.id}`); return; }
      restoredFrom.current = null;
      setSubmission((held) => held ?? { out, spec: shown });   // never replace an answer that holds the key
      dispatch({ type: 'submitted', jobId: out.job.id });   // the confirmation is used; the draft is closed
    } catch (e) { setRunError(apiError(e)); } finally { inFlight.current = false; setBusy(null); }
  }
  const run: RunSubmission = { busy, error: runError, submit: (dry) => { void submit(dry); } };

  /** Start a new job: forget the answer (and its key) and open an empty draft. */
  function startOver(): void {
    setSubmission(null); setRunError(null); setSavedAt(null);
    idem.current = null; restoredFrom.current = null;
    dispatch({ type: 'reset' });
  }

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
  const shownReach: Step = submitted ? 5 : reach;   // a submitted draft shows every step done and none open
  return (
    <main className={`page wizard-page${d.mode === 'synthesize' ? ' mode-syn' : ''}`}>
      <div className="faint sm rowx">
        <Link to="/data-sources" style={{ color: 'var(--ink-2)', fontWeight: 600 }}>Data sources &amp; jobs</Link><span>/</span>
        <span>New job · draft <span className="mono">{d.draftId}</span>{savedAt ? ` · Draft saved ${fmtClock(savedAt, false)}` : ''}</span>
      </div>
      <div className="page-head">
        <div><h1>New job{d.table ? ` · ${fqn(sourceObject(d))}` : ''}</h1><p>Protect a table in five steps. DPS reads only the discovery sample until you press Run job, and writes nothing before then.</p></div>
        {!submitted && <div className="head-actions"><Link className="btn btn-ghost" to="/data-sources">Cancel</Link><button className="btn" type="button" disabled={busy !== null} onClick={saveDraft}>Save draft</button></div>}
      </div>
      {linkError && <div className="err">Could not open the snapshot: {linkError}</div>}
      {deepGroup && !submitted && d.keyChoice === 'group' && d.groupId === deepGroup && (
        <div className="callout" style={{ fontSize: 12.5 }} data-testid="group-preset">Adding a job to job group <span className="mono">{groupName ?? deepGroup}</span>. Step 5 reuses the group's {d.mode === 'synthesize' ? 'seed' : 'key'}: load the <span className="mono">{d.mode === 'synthesize' ? '.dpsseed' : '.dpskey'}</span> file saved when the group was created. DPS keeps no copy.</div>
      )}
      {restoredFrom.current && !submitted && (
        <div className="callout amber" style={{ fontSize: 12.5 }}>Restored the saved draft <span className="mono">{d.draftId}</span>. Credentials, keys and filters are not saved: re-enter them.{' '}
          <button type="button" className="linkbtn" onClick={() => { restoredFrom.current = null; try { localStorage.removeItem(DRAFT_KEY); } catch { /* private mode */ } dispatch({ type: 'reset' }); }}>Start a new draft</button></div>
      )}

      <div className="card" style={{ padding: '12px 14px' }}><nav className="wsteps" aria-label="Job steps">
        {steps.map((s, i) => (
          <div key={s} style={{ display: 'contents' }}>
            {i > 0 && <div className="step-sep" />}
            <button type="button" className={`step${d.step === s ? ' on' : s < shownReach ? ' done' : ''}`} disabled={frozen || s > reach} aria-current={d.step === s ? 'step' : undefined} onClick={() => go(s)}>
              <span className="n">{s < shownReach && d.step !== s ? '✓' : s}</span>
              <span className="lbl"><span className="t">{STEP_TITLES[s]}</span>{stepSub(s, d) && <span className="sub">{stepSub(s, d)}</span>}</span>
            </button>
          </div>
        ))}
      </nav></div>

      <div className="wiz">
        <div className="wiz-main">
          {submitted && d.submittedJobId && <RunResult jobId={d.submittedJobId} out={submission?.out ?? null} writeMode={d.writeMode} onNewJob={startOver} />}
          {!submitted && d.step === 1 && <StepConnect d={d} dispatch={dispatch} auth={auth} connections={connections} onConnectionsChanged={loadConnections} onNext={() => go(2)} />}
          {!submitted && d.step === 2 && <StepSelect d={d} dispatch={dispatch} auth={auth} connection={connections.find((c) => c.id === d.connectionId) ?? null} onNext={() => go(3)} onBack={() => go(1)} />}
          {!submitted && d.step === 3 && <StepDiscover d={d} dispatch={dispatch} auth={auth} onNext={() => go(4)} onBack={() => go(2)} />}
          {!submitted && d.step === 4 && <StepTarget d={d} dispatch={dispatch} auth={auth} connections={connections} snapshot={snapshot} onConnectionsChanged={loadConnections} onNext={() => go(5)} onBack={() => go(3)} />}
          {!submitted && d.step === 5 && <StepRun d={d} dispatch={dispatch} auth={auth} snapshot={snapshot} go={go} onBack={() => go(4)} run={run} />}
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
            <div className="faint" style={{ fontSize: 11.5, marginTop: 8 }}>{submitted ? `As submitted for job ${d.submittedJobId}. ` : ''}Filters appear as entered, bound parameters as &lt;bound&gt;. Key material is never part of the specification.</div>
          </div>
        </aside>
      </div>
    </main>
  );
}
