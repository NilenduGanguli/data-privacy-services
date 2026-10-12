import { useEffect, useMemo, useRef, useState } from 'react';
import { HeatLegend, HeatMap } from '../../components/HeatMap';
import { usePoll } from '../../hooks/usePoll';
import { api, ApiError, type Auth } from '../../lib/api';
import {
  decisionReason, fmtCount, fqn, heatTone, isTerminal, METHOD_NAMES, methodText, needsDecision, overridesFor, parseCount, runMethodFor,
  selectionOf, shortId, shortType, sourceObject, type ColumnEdit, type WizardDraft,
} from '../../lib/jobs';
import type { ClassificationSnapshot, ColumnClassification, MethodSpec } from '../../lib/types';
import { canLeave, type WizardAction } from './state';

const PATCH_DEBOUNCE_MS = 400;

function apiMessage(e: unknown): string {
  return e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : e instanceof Error ? e.message : String(e);
}

/** A method choice keeps the snapshot's params when the method is unchanged. */
function methodChoice(name: string, current: MethodSpec | null | undefined, suggested: MethodSpec | null | undefined): MethodSpec {
  if (current?.method === name) return current;
  if (suggested?.method === name) return suggested;
  return { method: name, params: {} };
}

/** Step 3 · Discover: run discovery, read the heat-map, decide, review, accept the snapshot. */
export function StepDiscover({ d, dispatch, auth, onNext, onBack }: {
  d: WizardDraft; dispatch: React.Dispatch<WizardAction>; auth: Auth; onNext: () => void; onBack: () => void;
}): JSX.Element {
  const [snapshot, setSnapshot] = useState<ClassificationSnapshot | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);
  const [edits, setEdits] = useState<Record<string, ColumnEdit>>({});
  const [saving, setSaving] = useState<'idle' | 'saving' | 'saved'>('idle');
  const [reviewed, setReviewed] = useState<Set<string>>(new Set());
  const [focus, setFocus] = useState<string | null>(null);
  const [ner, setNer] = useState<boolean | null>(null);
  const reviewRef = useRef<HTMLDivElement>(null);
  const decideRef = useRef<HTMLDivElement>(null);
  const mode = d.mode;

  useEffect(() => { api.readyz().then((r) => setNer(!!r.components.ner?.ok)).catch(() => setNer(null)); }, []);

  // the discover job, polled every 2 s until it ends
  const job = usePoll(
    () => (d.discoverJobId ? api.jobs.get(auth, d.discoverJobId) : Promise.resolve(undefined)),
    2000, (j) => !!j && !isTerminal(j), [auth, d.discoverJobId],
  );
  const snapId = d.snapshotId ?? (job.data?.state === 'succeeded' ? job.data.progress.snapshot_ids[0] : undefined);

  useEffect(() => {
    if (!snapId || snapshot?.id === snapId) return;
    api.snapshots.get(auth, snapId).then((s) => { setSnapshot(s); dispatch({ type: 'snapshot', snapshot: s }); })
      .catch((e) => setError(apiMessage(e)));
  }, [auth, snapId, snapshot?.id, dispatch]);

  // review edits → PATCH (debounced); the table re-renders from the answer
  const pending = useMemo(() => (snapshot && snapshot.status === 'proposed' ? overridesFor(snapshot, edits) : []), [snapshot, edits]);
  useEffect(() => {
    if (!snapshot || !pending.length) return;
    const timer = window.setTimeout(() => {
      setSaving('saving');
      api.snapshots.patch(auth, snapshot.id, pending)
        .then((s) => { setSnapshot(s); setEdits({}); setSaving('saved'); setError(null); })
        .catch((e) => { setSaving('idle'); setError(apiMessage(e)); });
    }, PATCH_DEBOUNCE_MS);
    return () => window.clearTimeout(timer);
  }, [auth, snapshot, pending]);

  async function runDiscovery(): Promise<void> {
    if (!d.connectionId) return;
    setStarting(true); setError(null); setSnapshot(null); setEdits({}); setReviewed(new Set());
    try {
      const sample = Math.min(50_000, Math.max(50, parseCount(d.sampleRows) ?? 2000));
      const out = await api.jobs.discover(auth, { connection_id: d.connectionId, objects: [sourceObject(d)], selection: selectionOf(d), sample_rows: sample, mode: d.mode });
      dispatch({ type: 'discovering', jobId: out.job.id });
    } catch (e) { setError(apiMessage(e)); } finally { setStarting(false); }
  }

  async function accept(): Promise<void> {
    if (!snapshot) return;
    setError(null);
    try {
      const s = await api.snapshots.accept(auth, snapshot.id);
      setSnapshot(s); dispatch({ type: 'snapshot', snapshot: s });
    } catch (e) { setError(apiMessage(e)); }
  }

  const edit = (path: string, patch: ColumnEdit) => setEdits((e) => ({ ...e, [path]: { ...e[path], ...patch } }));
  const value = <K extends keyof ColumnEdit>(c: ColumnClassification, k: K, fallback: ColumnEdit[K]): ColumnEdit[K] => (edits[c.path]?.[k] !== undefined ? edits[c.path][k] : fallback);
  const j = job.data;
  const running = !!d.discoverJobId && (!j || !isTerminal(j)) && !snapshot;
  const failed = j && (j.state === 'failed' || j.state === 'dead' || j.state === 'canceled');
  const cols = snapshot?.columns ?? [];
  const tally = { crit: 0, sens: 0, decl: 0, kept: 0 };
  for (const c of cols) tally[heatTone(c)] += 1;
  const decisions = cols.filter(needsDecision);
  const open = decisions.filter((c) => !reviewed.has(c.path));
  const editable = snapshot?.status === 'proposed';
  const seconds = j?.started_at && j.finished_at ? (new Date(j.finished_at).getTime() - new Date(j.started_at).getTime()) / 1000 : null;
  const entityOptions = [...new Set(cols.map((c) => c.entity_type).filter(Boolean) as string[])].sort();
  const keyNames = new Set(snapshot?.shape.key ?? []);

  return (
    <section className="panel" aria-labelledby="h3s">
      <div className="panel-head"><h2 id="h3s">3 · Discover</h2><p>DPS samples the selection, runs the detectors and suggests a method for every column. The accepted result becomes the run policy; nothing is written.</p></div>
      {error && <div className="err">{error}</div>}

      <div className="card">
        {!snapshot ? (
          <>
            <div className="card-head"><div className="card-title">{running ? `Sampling ${fqn(sourceObject(d))}…` : 'Discovery'}</div>
              {running ? <span className="chip teal">{j?.progress.stage || j?.state || 'queued'}</span> : <button className="btn btn-primary btn-sm" type="button" disabled={starting} onClick={() => void runDiscovery()}>Run discovery</button>}</div>
            <p className="muted" style={{ margin: 0, fontSize: 13 }}>
              {running ? 'A worker reads the sample in a read-only transaction and classifies each column; values stay inside the worker.'
                : failed ? `The discovery ${j!.state}${j!.error_code ? ` (${j!.error_code})` : ''}. Run it again.`
                  : `Reads a sample of ${fmtCount(parseCount(d.sampleRows) ?? 2000)} rows of ${fqn(sourceObject(d))} and proposes a classification snapshot.`}
            </p>
            {running && <div className="skeleton" style={{ marginTop: 12 }} />}
          </>
        ) : (
          <>
            <div className="card-head"><div className="card-title">Sampled {fmtCount(snapshot.sample_rows)} of {fmtCount(d.count?.rows ?? d.shape?.row_estimate ?? null)} rows{seconds !== null ? ` in ${seconds.toFixed(1)} s` : ''}</div>
              <div className="rowx"><span className="chip emerald">sample complete</span><button className="btn btn-sm" type="button" disabled={starting || saving === 'saving'} onClick={() => void runDiscovery()}>Run again</button></div></div>
            <div className="kv">Detectors: <b>structural</b> · <b>patterns and checksums</b> · NER <b>{ner === null ? 'unknown' : ner ? 'on' : 'off'}</b> · LLM labelling <b>off</b> · <b style={{ color: 'var(--emerald)' }}>0</b> raw values sent to the LLM</div>
            <div className="dist" role="img" aria-label={`${cols.length} columns: ${tally.crit} critical, ${tally.sens} sensitive, ${tally.decl} declared, ${tally.kept} kept`}>
              <i style={{ flex: tally.crit, background: 'var(--rose)' }} /><i style={{ flex: tally.sens, background: 'var(--amber)' }} /><i style={{ flex: tally.decl, background: 'var(--teal)' }} /><i style={{ flex: tally.kept, background: 'var(--surface-3)' }} />
            </div>
            <div className="counts">
              <div className="cbox"><div className="l"><i style={{ background: 'var(--rose)' }} />Critical</div><div className="v">{tally.crit}</div><div className="s">government ids, accounts, cards</div></div>
              <div className="cbox"><div className="l"><i style={{ background: 'var(--amber)' }} />Sensitive</div><div className="v">{tally.sens}</div><div className="s">contact, identity, free text</div></div>
              <div className="cbox"><div className="l"><i style={{ background: 'var(--teal)' }} />Declared</div><div className="v">{tally.decl}</div><div className="s">declared fields of the policy</div></div>
              <div className="cbox"><div className="l"><i style={{ background: 'var(--surface-3)', border: '1px solid var(--border-strong)' }} />Kept</div><div className="v">{tally.kept}</div><div className="s">not personal data</div></div>
            </div>
          </>
        )}
      </div>

      {snapshot && (
        <>
          <div className="card">
            <div className="card-head"><div className="card-title">Column heat-map · {snapshot.object_ref}</div><HeatLegend /></div>
            <HeatMap columns={cols} />
            <div className="between" style={{ marginTop: 14 }}>
              <span className="faint sm">Confidence is the combined detector score on the sample. Examples shown to reviewers are masked.</span>
              {decisions.length > 0 && <button className="btn btn-sm" type="button" onClick={() => decideRef.current?.scrollIntoView({ behavior: 'smooth' })}>Go to the {decisions.length} {decisions.length === 1 ? 'decision' : 'decisions'}</button>}
            </div>
          </div>

          <div className="card" ref={decideRef}>
            <div className="card-head"><div className="card-title">Needs a decision</div><span className={`chip ${open.length ? 'amber' : 'emerald'}`}>{open.length ? `${open.length} ${open.length === 1 ? 'column' : 'columns'}` : decisions.length ? 'all reviewed' : 'none'}</span></div>
            {decisions.length === 0 && <p className="muted" style={{ margin: 0, fontSize: 13 }}>Every classified column has a confident detector result and a method that fits its type.</p>}
            {decisions.map((c) => (
              <div className="decide" key={c.path}>
                <span className="mono">{c.name}</span>
                <div><div className="why">{decisionReason(c)}</div>
                  <div className="sug">Suggested: {mode === 'synthesize' ? methodText(c.synthesize_suggested) : c.text_column ? 'text pipeline (det_token on each detected span)' : methodText(c.suggested)}{c.entity_type ? ` as ${c.entity_type}` : ''}.</div></div>
                <span className="rowx">
                  {reviewed.has(c.path) ? <span className="chip emerald">reviewed</span> : <button className="btn btn-sm" type="button" disabled={!editable} onClick={() => setReviewed((r) => new Set(r).add(c.path))}>Accept</button>}
                  <button className="btn btn-sm btn-ghost" type="button" disabled={!editable} onClick={() => { setFocus(c.path); reviewRef.current?.scrollIntoView({ behavior: 'smooth' }); }}>Change</button>
                </span>
              </div>
            ))}
          </div>

          <div className="card" ref={reviewRef}>
            <div className="card-head"><div className="card-title">Review · every column</div>
              <span className="faint sm">{editable ? (saving === 'saving' ? 'saving…' : saving === 'saved' ? `saved · ${snapshot.overrides.length} overrides` : `${snapshot.overrides.length} overrides`) : `snapshot ${snapshot.status}`}</span></div>
            <datalist id="entity-types">{entityOptions.map((e) => <option key={e} value={e} />)}</datalist>
            <div className="tbl-scroll"><table className="tbl review">
              <thead><tr><th>Column</th><th>Type</th><th>Entity</th><th>Conf.</th><th>Examples</th><th>Method</th>{mode === 'synthesize' && <th>Synthesize</th>}<th>Anchor</th><th>Text pipeline</th></tr></thead>
              <tbody>
                {cols.map((c) => {
                  const m = value(c, 'method', c.method) as MethodSpec;
                  const sm = value(c, 'synthesize_method', c.synthesize_method ?? undefined) as MethodSpec | undefined;
                  // in place updates rows by the key and the server refuses a key it would rewrite
                  const keyRewritten = keyNames.has(c.name) && (mode === 'synthesize' ? (sm ?? runMethodFor(c, 'synthesize')).method : m.method) !== 'keep';
                  return (
                    <tr key={c.path} className={edits[c.path] || c.override || focus === c.path ? 'edited' : ''}>
                      <td className="mono">{c.name}{c.is_key && <span className="faint sm"> · key</span>}
                        {keyRewritten && <span className="faint sm" style={{ display: 'block' }} title="In place updates rows by this key and never changes it">keep it to write in place</span>}</td>
                      <td className="faint" title={c.data_type}>{shortType(c.data_type)}</td>
                      <td><input className="input mono ent" list="entity-types" aria-label={`Entity of ${c.name}`} disabled={!editable}
                                 value={(value(c, 'entity_type', c.entity_type) as string | null) ?? ''} placeholder="kept"
                                 onChange={(e) => edit(c.path, { entity_type: e.target.value.trim().toUpperCase() || null })} /></td>
                      <td className="mono">{c.entity_type ? c.confidence.toFixed(2) : '—'}</td>
                      <td className="shapes" title={c.shapes.length ? `Value shapes (letters as x/X, digits as 9): ${c.shapes.join('  ')}` : undefined}>{c.shapes[0] ?? '—'}</td>
                      <td><select className="input mono" aria-label={`Method of ${c.name}`} disabled={!editable} value={m.method}
                                  onChange={(e) => edit(c.path, { method: methodChoice(e.target.value, c.method, c.suggested) })}>
                        {METHOD_NAMES.map((n) => <option key={n} value={n}>{n}</option>)}</select></td>
                      {mode === 'synthesize' && <td><select className="input mono" aria-label={`Synthesize method of ${c.name}`} disabled={!editable} value={sm?.method ?? 'keep'}
                                  onChange={(e) => edit(c.path, { synthesize_method: methodChoice(e.target.value, c.synthesize_method, c.synthesize_suggested) })}>
                        {METHOD_NAMES.map((n) => <option key={n} value={n}>{n}</option>)}</select></td>}
                      <td><input type="radio" name="anchor" aria-label={`Anchor on ${c.name}`} disabled={!editable || !c.entity_type} checked={!!value(c, 'anchor', c.anchor)}
                                 onChange={() => edit(c.path, { anchor: true })} /></td>
                      <td><input type="checkbox" aria-label={`Text pipeline for ${c.name}`} disabled={!editable || !['text', 'lob_text', 'json'].includes(c.kind)} checked={!!value(c, 'text_column', c.text_column)}
                                 onChange={(e) => edit(c.path, { text_column: e.target.checked })} /></td>
                    </tr>
                  );
                })}
              </tbody>
            </table></div>
            <div className="help">Examples are value shapes (letters as x, digits as 9), never values. Every change is saved to the proposed snapshot; the anchor column keys date shifts and personas.</div>
          </div>

          <div className="card">
            <div className="between">
              <span className="muted" style={{ fontSize: 13 }}>Snapshot <span className="mono">{shortId(snapshot.id)}</span> · policy <b>{snapshot.policy_name}</b> · {snapshot.overrides.length} overrides</span>
              {snapshot.status === 'proposed'
                ? <button className="btn btn-primary btn-sm" type="button" disabled={saving === 'saving' || pending.length > 0} onClick={() => void accept()}>Accept snapshot</button>
                : <span className={`chip ${snapshot.status === 'accepted' ? 'emerald' : 'slate'}`}>{snapshot.status}</span>}
            </div>
            {snapshot.status === 'accepted' && (
              <div className="callout emerald" style={{ marginTop: 12, fontSize: 12.5 }}>Accepted choices are saved as classification snapshot <span className="mono">{snapshot.id}</span>. It becomes the run policy and goes into the evidence pack. An accepted snapshot does not change: to choose another method, press Run again, review the new snapshot and accept it.</div>
            )}
            {snapshot.status === 'superseded' && <div className="callout amber" style={{ marginTop: 12, fontSize: 12.5 }}>A newer snapshot of this table was accepted after this one. Run discovery again to work from the current one.</div>}
          </div>
        </>
      )}

      <div className="wfoot"><button className="btn" type="button" onClick={onBack}>Back</button><button className="btn btn-primary" type="button" disabled={!canLeave(3, d)} onClick={onNext}>Next: Target and write mode</button></div>
    </section>
  );
}
