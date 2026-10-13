import { useEffect, useMemo, useRef, useState } from 'react';
import { Link, Navigate, useParams } from 'react-router-dom';
import { EngineBadge } from '../components/EngineBadge';
import { HeatLegend } from '../components/HeatMap';
import { Sparkline } from '../components/Sparkline';
import { StateChip } from '../components/StateChip';
import { useAuth } from '../hooks/useAuth';
import { usePoll } from '../hooks/usePoll';
import { api, ApiError } from '../lib/api';
import {
  engineName, errorText, evidenceSoFar, fmtClock, fmtCompact, fmtCount, heatTone, isTerminal, jobTargetText, jobWriteMode,
  jobHref, methodFor, methodText, needsDecision, parseKeyFile, partitionPct, progressPct, shortId, shortType, stepperState, typeNoteShort,
  writeModeLabel, type DetailStep,
} from '../lib/jobs';
import type { ClassificationSnapshot, ColumnClassification, Connection, Job, PartitionView } from '../lib/types';

const TONE_CHIP: Record<string, string> = { crit: 'rose', sens: 'amber', decl: 'teal', kept: 'slate' };
const STEPS: { id: DetailStep; label: string }[] = [
  { id: 'connect', label: 'Connect' }, { id: 'discover', label: 'Discover' }, { id: 'review', label: 'Review' },
  { id: 'target', label: 'Target' }, { id: 'run', label: 'Run' }, { id: 'evidence', label: 'Evidence' },
];

function apiMessage(e: unknown): string {
  return e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : e instanceof Error ? e.message : String(e);
}

/** ✓ Connect · ✓ Discover · … · Run · Evidence, the current step by phase. */
function Stepper({ job, snapshot }: { job: Job; snapshot: ClassificationSnapshot | undefined }): JSX.Element {
  const current = stepperState(job);
  const order = STEPS.map((s) => s.id);
  const at = current === 'done' ? order.length : order.indexOf(current);
  const plan = job.plan as Record<string, any> | null;
  const prep = (plan?.prepare ?? {}) as Record<string, unknown>;
  const wm = jobWriteMode(job);
  const pct = progressPct(job);
  const target = wm === 'table' ? (prep.created ? 'created' : (job.spec?.target?.table_load ?? 'table')) : wm === 'in_place' ? 'in place' : wm ?? '';
  const sub: Partial<Record<DetailStep, string>> = {
    discover: snapshot ? `${fmtCount(snapshot.sample_rows)} sampled` : undefined,
    review: snapshot ? `${snapshot.columns.length} columns, ${snapshot.overrides.length} overrides` : undefined,
    target: job.kind === 'run' ? target : undefined,
    run: job.kind === 'run' ? `${pct === null ? '—' : `${pct}%`} · ${job.progress.partitions_total} ${job.progress.partitions_total === 1 ? 'partition' : 'partitions'}` : undefined,
  };
  return (
    <div className="card" style={{ padding: '14px 18px' }}><div className="stepper">
      {STEPS.map((s, i) => {
        const done = i < at;
        const on = i === at;
        const body = <><span className="n">{done ? '✓' : i + 1}</span>{s.label}{sub[s.id] && <span className="faint">· {sub[s.id]}</span>}</>;
        return (
          <div key={s.id} style={{ display: 'contents' }}>
            {i > 0 && <div className="step-sep" />}
            {s.id === 'evidence' && job.progress.evidence_id
              ? <Link className={`step${done ? ' done' : on ? ' on' : ''}`} to={`/reports/evidence/${job.progress.evidence_id}`}>{body}</Link>
              : <div className={`step${done ? ' done' : on ? ' on' : ''}`} aria-current={on ? 'step' : undefined}>{body}</div>}
          </div>
        );
      })}
    </div></div>
  );
}

/** The reviewed snapshot's columns (kept ones collapsed). */
function ClassificationCard({ snapshot, mode }: { snapshot: ClassificationSnapshot; mode: Job['mode'] }): JSX.Element {
  const [all, setAll] = useState(false);
  const shown = snapshot.columns.filter((c) => all || c.entity_type || c.text_column);
  const kept = snapshot.columns.length - snapshot.columns.filter((c) => c.entity_type || c.text_column).length;
  const evidence = (c: ColumnClassification) => [
    c.detectors.join(' + ') || null,
    c.entity_type && c.value_hit_rate > 0 ? `${Math.round(c.value_hit_rate * 100)}% of sample` : null,
    c.anchor ? 'anchor' : null,
    mode === 'synthesize' || !c.type_note?.startsWith('synthesize_') ? typeNoteShort(c.type_note) : null,
  ].filter(Boolean).join(' · ');
  return (
    <div className="card">
      <div className="card-head"><div className="card-title">Column classification ({snapshot.status === 'accepted' ? 'reviewed snapshot' : snapshot.status})</div><HeatLegend /></div>
      <div className="tbl-scroll"><table className="tbl" id="cls"><thead><tr><th>Column</th><th>Type</th><th>Inferred entity</th><th>Conf.</th><th>Method</th><th>Evidence</th></tr></thead><tbody>
        {shown.map((c) => (
          <tr key={c.path}>
            <td className="mono">{c.name}</td>
            <td className="faint" title={c.data_type}>{shortType(c.data_type)}</td>
            <td><span className={`chip ${TONE_CHIP[heatTone(c)]}`}>{c.entity_type ? `${c.entity_type}${c.is_key ? ' · key' : ''}${c.text_column && c.entity_type !== 'FREE_TEXT' ? ' · free text' : ''}` : 'kept'}</span></td>
            <td className="mono">{c.entity_type ? c.confidence.toFixed(2) : '—'}</td>
            <td className="mono">{c.text_column && mode !== 'synthesize' ? 'text pipeline' : methodText(methodFor(c, mode))}</td>
            <td className="faint">{c.override && <b>override</b>}{c.override && evidence(c) ? ': ' : ''}{evidence(c) || (c.override ? '' : '—')}</td>
          </tr>
        ))}
      </tbody></table></div>
      <div className="faint" style={{ fontSize: 12, marginTop: 10 }}>
        {kept > 0 && <><button type="button" className="linkbtn" onClick={() => setAll((v) => !v)}>{all ? 'Hide kept columns' : `+ ${kept} more ${kept === 1 ? 'column' : 'columns'} kept`}</button> · </>}
        snapshot <span className="mono">{shortId(snapshot.id)}</span> is part of the evidence pack · examples shown to reviewers were masked.
      </div>
    </div>
  );
}

/** One partition row: progress, rows/s, committed batch or state, checkpoint fingerprint. */
function PartitionRow({ p, job }: { p: PartitionView; job: Job }): JSX.Element {
  const pct = partitionPct(p, job);
  const tone = p.state === 'done' ? ' emerald' : p.state === 'failed' || p.state === 'dead' ? ' rose' : p.resumed > 0 ? ' amber' : '';
  const chip = p.state === 'done' ? <span className="chip emerald">done</span>
    : p.state === 'failed' || p.state === 'dead' ? <span className="chip rose" title={p.error_code ?? undefined}>{p.state}{p.error_code ? ` · ${p.error_code}` : ''}</span>
      : p.state === 'canceled' ? <span className="chip slate">canceled</span>
        : p.state === 'queued' ? <span className="chip slate">{p.resumed > 0 ? 'requeued' : 'queued'}</span>
          : <span className="mono faint">batch {p.batch_no}</span>;
  return (
    <div className="part">
      <span className="mono">p{p.partition_no}</span>
      <div className={`bar${tone}`}><i style={{ width: `${pct}%` }} /></div>
      <span className="mono">{p.state === 'running' && p.rows_per_second ? fmtCount(p.rows_per_second) : p.state === 'queued' && p.resumed > 0 ? 'retrying' : '—'}</span>
      <span className="st">
        {chip}
        {p.resumed > 0 && <span className="chip amber">lease reclaimed, resumed</span>}
        {p.checkpoint_fp && <span className="fp" title="Keyed fingerprint of the checkpoint (never a key value)">#{p.checkpoint_fp}</span>}
      </span>
    </div>
  );
}

/** Run · partitions: one row per partition and the throughput sparkline. */
function PartitionsCard({ job, parts, keyCols }: { job: Job; parts: PartitionView[] | undefined; keyCols: string[] }): JSX.Element {
  const resumed = (parts ?? []).filter((p) => p.resumed > 0);
  const rate = job.progress.rows_per_second;
  return (
    <div className="card">
      <div className="card-head"><div className="card-title">Run · partitions{keyCols.length ? ` by ${keyCols.join(', ')} range` : ''}</div><StateChip job={job} /></div>
      {!parts?.length ? (
        <p className="muted" style={{ margin: 0, fontSize: 13 }}>{job.phase === 'prepare' || job.state === 'queued' ? 'Partitions appear when the job has prepared its run plan.' : 'No partition ran.'}</p>
      ) : (
        <>
          <div className="part head"><span>Part</span><span>Progress</span><span>Rows/s</span><span>Committed</span></div>
          {parts.map((p) => <PartitionRow key={p.partition_no} p={p} job={job} />)}
        </>
      )}
      <div className="tput">
        <Sparkline points={job.progress.throughput} width={320} height={44} label={`Throughput over the last ${Math.max(1, Math.round(job.progress.throughput.length / 6))} minutes`} />
        <div className="axis"><span>throughput · {Math.max(1, Math.round(job.progress.throughput.length / 6))} min</span><span>{rate ? `~${fmtCompact(rate)} rows/s total` : isTerminal(job) ? 'finished' : 'no rate yet'}</span></div>
      </div>
      {resumed.length > 0 && (
        <div className="faint" style={{ fontSize: 12, marginTop: 8 }}>
          {resumed.map((p) => `p${p.partition_no}`).join(', ')}: the worker lost its lease; the reaper re-queued {resumed.length === 1 ? 'it' : 'them'} from the last committed batch — no row is written twice. Key positions are not shown because a key value identifies a customer.
        </div>
      )}
    </div>
  );
}

/** Evidence so far: six tiles and the output label. */
function EvidenceCard({ job }: { job: Job }): JSX.Element {
  const e = evidenceSoFar(job);
  const leaked = e.hits > 0 || job.error_code === 'leakage';
  return (
    <div className="card">
      <div className="card-head"><div className="card-title">Evidence so far</div><span className={`chip ${leaked ? 'rose' : 'emerald'}`}>{leaked ? 'leakage caught · failed closed' : 'verifier passing'}</span></div>
      <div className="ev">
        <div className="box"><div className="l">Rows protected</div><div className="v">{fmtCount(e.rows)}</div></div>
        <div className="box"><div className="l">Values transformed</div><div className="v">{fmtCompact(e.values)}</div></div>
        <div className="box"><div className="l">Leakage sweep</div><div className={`v ${leaked ? 'bad' : 'ok'}`}>{leaked && !e.hits ? 'failed closed' : `${fmtCount(e.hits)} hits`}</div></div>
        <div className="box"><div className="l">FPE fallbacks</div><div className="v">{fmtCount(e.fallbacks)}{e.dominantFallback && <span className="faint"> ({e.dominantFallback.replace(/_/g, ' ')})</span>}</div></div>
        <div className="box"><div className="l">Raw PII to LLM</div><div className="v ok">0</div></div>
        <div className="box"><div className="l">Partitions</div><div className="v">{e.done} / {e.total}{e.failed > 0 && <span className="faint" style={{ color: 'var(--rose)' }}> · {e.failed} failed</span>}</div></div>
      </div>
      {job.mode === 'synthesize' ? (
        <div className="callout violet" style={{ marginTop: 12, fontSize: 12 }}>Output is <b>synthetic — measured, not claimed anonymous</b>. The seed is held by the requesting team; DPS keeps no copy.</div>
      ) : (
        <div className="callout" style={{ marginTop: 12, fontSize: 12 }}>Output is <b>pseudonymised — still personal data</b> under GDPR/DPDP. The key is held by the requesting team{job.kid ? <> (kid <span className="mono">{job.kid}</span>)</> : null}; DPS keeps no copy.</div>
      )}
    </div>
  );
}

/** Snapshots a discover job produced, with the way into the review. */
function SnapshotsCard({ snapshots }: { snapshots: ClassificationSnapshot[] }): JSX.Element {
  return (
    <div className="card">
      <div className="card-head"><div className="card-title">Classification snapshots</div></div>
      <div className="tbl-scroll"><table className="tbl snaps"><thead><tr><th>Snapshot</th><th>Object</th><th>Columns</th><th>Status</th><th><span className="sr">Actions</span></th></tr></thead><tbody>
        {snapshots.map((s) => {
          const decide = s.columns.filter(needsDecision).length;
          return (
            <tr key={s.id}>
              <td className="mono" title={s.id}>{shortId(s.id)}</td>
              <td className="mono">{s.object_ref}</td>
              <td>{s.columns.length} · {s.columns.filter((c) => c.entity_type).length} classified{decide ? ` · ${decide} to decide` : ''}</td>
              <td><span className={`chip ${s.status === 'proposed' ? 'amber' : s.status === 'accepted' ? 'emerald' : 'slate'}`}>{s.status === 'proposed' ? 'awaiting review' : s.status}</span></td>
              <td>{s.status === 'proposed' && <Link className="btn btn-sm btn-primary" to={`/data-sources/jobs/new?snapshot=${encodeURIComponent(s.id)}`}>Review and accept</Link>}</td>
            </tr>
          );
        })}
      </tbody></table></div>
    </div>
  );
}

/** Job detail (mockup 03): stepper, reviewed snapshot, live partitions, evidence so far, controls. */
export function JobDetail(): JSX.Element {
  const { jobId = '' } = useParams();
  const auth = useAuth();
  const job = usePoll(() => api.jobs.get(auth, jobId), 2000, (j) => !isTerminal(j), [auth, jobId]);
  const j = job.data && job.data.id === jobId ? job.data : undefined;
  const terminal = isTerminal(j);
  const partitionsLive = !!j && j.kind === 'run' && (j.phase === 'partitions' || j.phase === 'finalize');
  const parts = usePoll(
    () => (j && j.kind === 'run' && j.phase !== 'prepare' ? api.jobs.partitions(auth, jobId).then((r) => r.items) : Promise.resolve(undefined)),
    2000, () => partitionsLive && !terminal, [auth, jobId, j?.phase, terminal],
  );
  const [snapshots, setSnapshots] = useState<ClassificationSnapshot[]>([]);
  const [conns, setConns] = useState<{ source?: Connection; target?: Connection }>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [retryOpen, setRetryOpen] = useState(false);
  const [keyFile, setKeyFile] = useState<{ name: string; text: string } | null>(null);
  const loadedSnaps = useRef('');

  const snapIds = useMemo(() => (j ? (j.kind === 'discover' ? j.progress.snapshot_ids : j.snapshot_id ? [j.snapshot_id] : []) : []), [j]);
  useEffect(() => {
    const key = snapIds.join(',');
    if (!key || loadedSnaps.current === key) return;
    loadedSnaps.current = key;
    Promise.all(snapIds.map((id) => api.snapshots.get(auth, id).catch(() => null)))
      .then((list) => setSnapshots(list.filter((s): s is ClassificationSnapshot => !!s)));
  }, [auth, snapIds]);
  useEffect(() => {
    if (!j) return;
    const src = j.connection_id ? api.connections.get(auth, j.connection_id).catch(() => undefined) : Promise.resolve(undefined);
    const tgt = j.target_connection_id ? api.connections.get(auth, j.target_connection_id).catch(() => undefined) : Promise.resolve(undefined);
    Promise.all([src, tgt]).then(([source, target]) => setConns({ source, target }));
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [auth, j?.connection_id, j?.target_connection_id]);

  async function control(action: 'pause' | 'resume' | 'cancel'): Promise<void> {
    setBusy(action); setActionError(null);
    try { await api.jobs[action](auth, jobId); job.refresh(); } catch (e) { setActionError(apiMessage(e)); } finally { setBusy(null); setConfirmCancel(false); }
  }

  async function retry(): Promise<void> {
    setBusy('retry'); setActionError(null);
    try {
      const file = keyFile ? parseKeyFile(keyFile.text) : null;
      const consistency = file && (file.key_material || file.synthesis_seed)
        ? { ...(file.key_material ? { key_material: file.key_material } : {}), ...(file.synthesis_seed ? { synthesis_seed: file.synthesis_seed } : {}) }
        : undefined;
      if (keyFile && !consistency) { setActionError('The file is not a DPS key or seed file (JSON with key_material or a kid).'); return; }
      await api.jobs.retry(auth, jobId, consistency);
      setRetryOpen(false); setKeyFile(null); job.refresh();
    } catch (e) {
      setActionError(e instanceof ApiError && e.code === 'key_material_required'
        ? 'The job key was purged when the job stopped, so DPS cannot resume without it. Load the key file returned when the job was submitted, then retry.'
        : apiMessage(e));
      if (e instanceof ApiError && e.code === 'key_material_required') setRetryOpen(true);
    } finally { setBusy(null); }
  }

  // a scan is a job too (GET /jobs/{id} returns it): its page is the UI scanner's scan detail
  if (j && j.kind === 'scan') return <Navigate to={jobHref(j)} replace />;

  if (!j) {
    return (
      <main className="page job-detail-page">
        <div className="page-head"><div><h1>Job <span className="mono">{shortId(jobId)}</span></h1><p><Link to="/data-sources" style={{ color: 'var(--teal)' }}>Back to Jobs</Link></p></div></div>
        {job.error ? <div className="err">Could not load the job: {apiMessage(job.error)}</div> : <div className="card"><div className="skeleton" /></div>}
      </main>
    );
  }

  const snapshot = j.kind === 'run' ? snapshots[0] : undefined;
  const wm = jobWriteMode(j);
  const target = jobTargetText(j);
  const title = `${engineName(conns.source?.engine)} · ${j.object_ref ?? '—'}${j.kind === 'discover' ? '' : wm === 'in_place' ? ' · rewritten in place' : ` → ${target}`}`;
  const policy = (j.plan as Record<string, any> | null)?.policy_name ?? (typeof j.spec?.policy === 'string' ? j.spec.policy : undefined) ?? snapshot?.policy_name;
  const failed = j.state === 'failed' || j.state === 'dead';
  const canPause = j.kind === 'run' && j.phase === 'partitions' && (j.state === 'waiting' || j.state === 'running');
  const canCancel = !terminal && j.phase !== 'finalize';
  const keyCols = ((j.plan as Record<string, any> | null)?.key as string[] | undefined) ?? snapshot?.shape.key ?? [];

  return (
    <main className="page job-detail-page">
      <div className="page-head">
        <div style={{ minWidth: 0 }}>
          <div className="rowx" style={{ alignItems: 'flex-start' }}><EngineBadge engine={conns.source?.engine} /><h1 style={{ overflowWrap: 'anywhere' }}>{title}</h1></div>
          <p>
            {j.kind === 'discover' ? <>Discovery · connection <span className="mono">{conns.source?.name ?? shortId(j.connection_id)}</span></>
              : <>Mode <b>{j.mode}</b> · {writeModeLabel(wm, j.spec?.target?.table_load)}{policy ? <> · policy <b>{policy}</b></> : null}</>}
            {j.purpose?.ticket && <> · purpose {j.purpose.ticket}{j.purpose.legal_basis ? ` (${j.purpose.legal_basis})` : ''}</>}
            {j.group_id && <> · consistency key shared with job group <span className="mono">{shortId(j.group_id)}</span></>}
          </p>
          <p className="faint" style={{ fontSize: 12 }}>Job <span className="mono">{j.id}</span> · created {fmtClock(j.created_at)}{j.finished_at ? ` · finished ${fmtClock(j.finished_at)}` : ''}</p>
        </div>
        <div className="head-actions">
          <Link className="btn btn-ghost" to="/data-sources">Jobs</Link>
          {canPause && <button className="btn" type="button" disabled={!!busy} onClick={() => void control('pause')}>Pause</button>}
          {j.state === 'paused' && <button className="btn" type="button" disabled={!!busy} onClick={() => void control('resume')}>Resume</button>}
          {canCancel && <button className="btn" type="button" disabled={!!busy} onClick={() => setConfirmCancel(true)}>Cancel</button>}
          {failed && j.kind === 'run' && <button className="btn" type="button" disabled={!!busy} onClick={() => setRetryOpen((v) => !v)}>Retry</button>}
          {j.progress.evidence_id
            ? <Link className="btn btn-primary" to={`/reports/evidence/${j.progress.evidence_id}`}>Evidence pack</Link>
            : j.kind === 'run' && <button className="btn" type="button" disabled title="The evidence pack is sealed when the job finishes">Evidence pack (when finished)</button>}
        </div>
      </div>

      {actionError && <div className="err">{actionError}</div>}

      {retryOpen && (
        <div className="card retry" aria-label="Retry the job">
          <div className="card-title">Retry from the last checkpoints</div>
          <p className="muted" style={{ margin: 0, fontSize: 13 }}>Every partition resumes from its last committed batch. If the job key was purged when the job stopped, load the key file returned at submission (<span className="mono">dps-{j.id}.key.json</span>): it stays in this page's memory and goes to DPS only inside the retry request.</p>
          <div className="rowx">
            <label className="btn btn-sm" style={{ cursor: 'pointer' }}>Load key file
              <input type="file" accept=".json,application/json" className="sr" onChange={(e) => { const f = e.target.files?.[0]; if (f) void f.text().then((text) => setKeyFile({ name: f.name, text })); e.target.value = ''; }} />
            </label>
            {keyFile && <span className="chip teal">{keyFile.name} loaded</span>}
            {keyFile && <button type="button" className="linkbtn" onClick={() => setKeyFile(null)}>Forget file</button>}
            <span className="spacer" />
            <button className="btn btn-primary btn-sm" type="button" disabled={busy === 'retry'} onClick={() => void retry()}>Retry job</button>
          </div>
        </div>
      )}

      <Stepper job={j} snapshot={snapshots[0]} />

      {failed && (
        <div className="callout rose" style={{ fontSize: 13 }}><b>{errorText(j.error_code)}</b>{j.error ? ` ${j.error}` : ''} <span className="mono faint">{j.error_code}</span></div>
      )}

      {j.kind === 'discover' ? (
        <div className="col" style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
          {snapshots.length > 0 && <SnapshotsCard snapshots={snapshots} />}
          {!snapshots.length && <div className="card"><p className="muted" style={{ margin: 0 }}>{terminal ? 'This discovery produced no snapshot.' : `Sampling · ${j.progress.stage || 'waiting for a worker'}`}</p></div>}
          {snapshots[0] && <ClassificationCard snapshot={snapshots[0]} mode={j.mode ?? 'obfuscate'} />}
        </div>
      ) : (
        <div className="two">
          <div className="col">{snapshot ? <ClassificationCard snapshot={snapshot} mode={j.mode} /> : <div className="card"><div className="card-title">Column classification</div><div className="skeleton" style={{ marginTop: 12 }} /></div>}</div>
          <div className="col">
            <PartitionsCard job={j} parts={parts.data} keyCols={keyCols} />
            <EvidenceCard job={j} />
          </div>
        </div>
      )}

      {confirmCancel && (
        <div className="modal-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget) setConfirmCancel(false); }}>
          <div className="confirm append" role="dialog" aria-modal="true" aria-labelledby="cancel-title" onKeyDown={(e) => { if (e.key === 'Escape') setConfirmCancel(false); }}>
            <h3 id="cancel-title">Cancel job?</h3>
            <p className="lead">Rows already written stay written; the evidence pack records the cancellation.</p>
            <div className="rowx" style={{ justifyContent: 'flex-end' }}>
              <button className="btn" type="button" autoFocus onClick={() => setConfirmCancel(false)}>Keep running</button>
              <button className="btn btn-danger" type="button" disabled={busy === 'cancel'} onClick={() => void control('cancel')}>Cancel job</button>
            </div>
          </div>
        </div>
      )}
    </main>
  );
}
