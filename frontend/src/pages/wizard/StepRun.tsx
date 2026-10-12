import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate } from 'react-router-dom';
import { KeyMaterialCard } from '../../components/KeyMaterialCard';
import { api, ApiError, type Auth } from '../../lib/api';
import {
  buildRunSpec, fmtCount, fqn, heatTone, isWriteConfirmed, LEGAL_BASES, legalBasisText, needsConfirmation, parseKeyFile, planGroups,
  seedFingerprint, shortId, sourceObject, writeModeLabel, writtenObject, type Step, type WizardDraft,
} from '../../lib/jobs';
import type { ClassificationSnapshot, JobGroup, Preset, RunOut } from '../../lib/types';
import { clearSavedDraft, type WizardAction } from './state';

function apiError(e: unknown): { message: string; code?: string } {
  return e instanceof ApiError ? { message: e.message, code: e.code } : { message: e instanceof Error ? e.message : String(e) };
}

/** Step 5 · Review and run: mode, policy, key or seed, purpose, summary, Dry run / Run job. */
export function StepRun({ d, dispatch, auth, snapshot, go, onBack }: {
  d: WizardDraft; dispatch: React.Dispatch<WizardAction>; auth: Auth; snapshot: ClassificationSnapshot | null; go: (s: Step) => void; onBack: () => void;
}): JSX.Element {
  const navigate = useNavigate();
  const [presets, setPresets] = useState<Preset[]>([]);
  const [groups, setGroups] = useState<JobGroup[]>([]);
  const [fileName, setFileName] = useState<string | null>(null);
  const [match, setMatch] = useState<'none' | 'matched' | 'mismatch' | 'invalid'>('none');
  const [busy, setBusy] = useState<'run' | 'dry' | null>(null);
  const [error, setError] = useState<{ message: string; code?: string } | null>(null);
  const [result, setResult] = useState<RunOut | null>(null);
  const idem = useRef<{ key: string; dry: boolean } | null>(null);
  const set = (patch: Partial<WizardDraft>) => dispatch({ type: 'set', patch });
  const syn = d.mode === 'synthesize';
  const group = groups.find((g) => g.id === d.groupId) ?? null;
  const confirmed = isWriteConfirmed(d);

  useEffect(() => { api.presets(auth).then(setPresets).catch(() => setPresets([])); }, [auth]);
  useEffect(() => { api.groups.list(auth).then((p) => setGroups(p.items)).catch(() => setGroups([])); }, [auth]);

  // the loaded file must belong to the chosen group: kid (key) or seed fingerprint (seed)
  useEffect(() => {
    let live = true;
    if (d.keyChoice !== 'group' || !d.keyMaterialJson) { setMatch('none'); return; }
    const file = parseKeyFile(d.keyMaterialJson);
    if (!group) { setMatch('none'); return; }
    if (!syn) { setMatch(!file.key_material ? 'invalid' : file.key_material.kid === group.kid ? 'matched' : 'mismatch'); return; }
    const seed = file.synthesis_seed?.seed;
    if (typeof seed !== 'string') { setMatch('invalid'); return; }
    seedFingerprint(seed).then((fp) => { if (live) setMatch(fp === null ? 'matched' : fp === group.seed_fingerprint ? 'matched' : 'mismatch'); });
    return () => { live = false; };
  }, [d.keyChoice, d.keyMaterialJson, group, syn]);

  const plan = snapshot ? planGroups(snapshot, d.mode) : null;
  const overrides = snapshot?.overrides.length ?? 0;
  const purposeOk = d.ticket.trim().length >= 3 && legalBasisText(d).length >= 3;
  const blocked: string[] = [];
  if (!d.snapshotAccepted) blocked.push('The classification snapshot is not accepted (step 3).');
  if (needsConfirmation(d.writeMode) && !confirmed) blocked.push(`${d.writeMode === 'in_place' ? 'In place' : 'Append'} changes ${fqn(writtenObject(d))}: confirm the write in step 4.`);
  if (d.keyChoice === 'group' && (!d.groupId || match !== 'matched')) blocked.push(`Load the ${syn ? 'seed' : 'key'} file of the chosen job group.`);
  if (!purposeOk) blocked.push('Enter the ticket (3 characters or more) for the purpose.');
  const counts = { crit: 0, sens: 0, decl: 0, kept: 0 };
  for (const c of snapshot?.columns ?? []) counts[heatTone(c)] += 1;

  async function submit(dry: boolean): Promise<void> {
    setBusy(dry ? 'dry' : 'run'); setError(null);
    if (!idem.current || idem.current.dry !== dry) idem.current = { key: globalThis.crypto.randomUUID(), dry };
    try {
      const spec = buildRunSpec(d);
      spec.options = { ...spec.options, dry_run: dry };
      const out = await api.jobs.run(auth, spec, idem.current.key);
      idem.current = null;
      clearSavedDraft();
      if (dry) { navigate(`/data-sources/jobs/${out.job.id}`); return; }
      setResult(out);
      set({ keyMaterialJson: '' });
    } catch (e) { setError(apiError(e)); } finally { setBusy(null); }
  }

  async function loadFile(f: File): Promise<void> {
    const text = await f.text();
    setFileName(f.name);
    set({ keyMaterialJson: text });   // React state only; never stored
  }

  if (result) {
    const id = result.job.id;
    return (
      <section className="panel" aria-labelledby="h5s">
        <div className="panel-head"><h2 id="h5s">5 · Review and run</h2></div>
        <div className="callout emerald" style={{ fontSize: 13 }}><b>Job {shortId(id)} queued.</b> {result.job.progress.partitions_total ? `${result.job.progress.partitions_total} partitions are waiting.` : 'A worker prepares the run plan and its partitions.'}{' '}
          <Link to="/data-sources" style={{ color: 'var(--teal)', fontWeight: 700 }}>Open in Jobs</Link> · <Link to={`/data-sources/jobs/${id}`} style={{ color: 'var(--teal)', fontWeight: 700 }}>Open job</Link></div>
        {(result.key_material || result.synthesis_seed)
          ? <KeyMaterialCard keyMaterial={result.key_material} seed={result.synthesis_seed} jobId={id} />
          : <div className="card"><p className="muted" style={{ margin: 0 }}>The job runs with the {syn ? 'seed' : 'key'} you loaded; DPS returned none and keeps none.</p></div>}
      </section>
    );
  }

  return (
    <section className="panel" aria-labelledby="h5s">
      <div className="panel-head"><h2 id="h5s">5 · Review and run</h2><p>Choose the mode, the policy, the key and the purpose. Everything here is recorded in the evidence pack.</p></div>

      <div className="card">
        <div className="card-head"><div className="card-title">Mode</div></div>
        <div className="choices c2" role="radiogroup" aria-label="Mode">
          <label className="choice"><input type="radio" name="mode" value="obfuscate" checked={!syn} onChange={() => set({ mode: 'obfuscate' })} /><span className="radio" />
            <span className="ct">Obfuscate <span className="chip teal">reversible with the key</span></span>
            <span className="cd">Replaces values with format-preserving tokens. Whoever holds the key can restore them; the same key gives the same tokens in every system.</span></label>
          <label className="choice syn"><input type="radio" name="mode" value="synthesize" checked={syn} onChange={() => set({ mode: 'synthesize', ...(d.pkKind === 'token' ? { pkKind: 'offset' as const } : {}) })} /><span className="radio" />
            <span className="ct">Synthesize <span className="chip violet">one-way</span></span>
            <span className="cd">Replaces values with realistic fakes that pass each value's own rules. There is no path back.</span></label>
        </div>
        {confirmed && <div className="help">Your {d.writeMode === 'in_place' ? 'In place' : 'Append'} confirmation is for {syn ? 'Synthesize' : 'Obfuscate'}. Choosing the other mode withdraws it and takes you back to step 4 to confirm again.</div>}
      </div>

      <div className="card">
        <div className="card-head"><div className="card-title">Policy</div>{overrides > 0 && <span className="chip amber">{overrides} {overrides === 1 ? 'override' : 'overrides'} from discovery</span>}</div>
        <div className="fgrid">
          <div><label className="lbl" htmlFor="run-policy">Preset</label>
            <select className="input" id="run-policy" value={d.policy ?? ''} onChange={(e) => set({ policy: e.target.value || null })}>
              <option value="">{d.policyName ? `${d.policyName} (the snapshot's)` : 'The snapshot\'s policy'}</option>
              {presets.filter((p) => p.name !== d.policyName).map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
            </select></div>
          <div className="kv" style={{ alignSelf: 'end' }}>The reviewed snapshot decides each column's method; the preset fills what the snapshot leaves open.</div>
        </div>
        {plan && (
          <>
            <div className="lbl" style={{ marginTop: 14 }}>Plan · {plan.groups.reduce((a, [, cols]) => a + cols.length, 0)} columns protected, {plan.kept.length} kept</div>
            <div className="plan">
              {plan.groups.map(([method, cols]) => <div key={method}><span className="mono">{method}</span><span className="faint">{cols.join(' · ')}</span></div>)}
              <div><span className="mono">keep</span><span className="faint">{plan.kept.length} {plan.kept.length === 1 ? 'column' : 'columns'}{plan.kept.some((k) => snapshot?.shape.key.includes(k)) ? `, ${plan.kept.filter((k) => snapshot?.shape.key.includes(k)).join(', ')} included` : ''}</span></div>
            </div>
          </>
        )}
      </div>

      <div className="card">
        <div className="card-head"><div className="card-title">{syn ? 'Synthesis seed' : 'Consistency key'}</div></div>
        <div className="choices c2" role="radiogroup" aria-label="Consistency">
          <label className="choice"><input type="radio" name="key" value="new" checked={d.keyChoice === 'new'} onChange={() => set({ keyChoice: 'new', keyMaterialJson: '' })} /><span className="radio" />
            <span className="ct">{syn ? 'New seed for this job' : 'New key for this job'}</span>
            <span className="cd">{syn ? 'DPS generates a seed and returns it with the job, once. Fakes will not match earlier synthetic copies.' : 'DPS generates a 256-bit key and returns it with the job, once. Tokens will not match earlier jobs.'}</span></label>
          <label className="choice"><input type="radio" name="key" value="group" checked={d.keyChoice === 'group'} disabled={!groups.length} onChange={() => set({ keyChoice: 'group' })} /><span className="radio" />
            <span className="ct">Reuse the {syn ? 'seed' : 'key'} of a job group</span>
            <span className="cd">{groups.length ? `The same customer gets the same ${syn ? 'fakes' : 'tokens'} as in the group's other copies, so tables join.` : 'No job group exists for this tenant yet.'}</span></label>
        </div>
        {d.keyChoice === 'group' && (
          <div style={{ marginTop: 12 }}>
            <div className="fgrid">
              <div><label className="lbl" htmlFor="run-group">Job group</label>
                <select className="input" id="run-group" value={d.groupId ?? ''} onChange={(e) => set({ groupId: e.target.value || null })}>
                  <option value="">Choose a job group…</option>
                  {groups.map((g) => <option key={g.id} value={g.id}>{g.name}</option>)}
                </select></div>
            </div>
            <div className="drop">
              <span style={{ flex: 1, minWidth: 200 }}>{fileName ? <><b className="mono">{fileName}</b><br /></> : null}
                <span className="faint">{match === 'matched' ? `${syn ? 'Seed' : `Key id ${group?.kid}`} matches the group. DPS uses it for this run and does not store it.`
                  : match === 'mismatch' ? (syn ? 'seed does not match the group' : 'kid does not match the group')
                    : match === 'invalid' ? `The file holds no ${syn ? 'synthesis seed' : 'key material'}.`
                      : `Load the ${syn ? 'seed' : 'key'} file saved when the group was created. It stays in this page's memory.`}</span></span>
              {match === 'matched' && <span className="chip emerald">matched</span>}
              {match === 'mismatch' && <span className="chip rose">no match</span>}
              <label className="btn btn-sm" style={{ cursor: 'pointer' }}>{fileName ? 'Use another file' : `Load ${syn ? 'seed' : 'key'} file`}
                <input type="file" accept=".json,application/json" className="sr" onChange={(e) => { const f = e.target.files?.[0]; if (f) void loadFile(f); e.target.value = ''; }} /></label>
            </div>
          </div>
        )}
      </div>

      <div className="card">
        <div className="card-head"><div className="card-title">Purpose</div><span className="faint sm">required for every run</span></div>
        <div className="fgrid">
          <div><label className="lbl" htmlFor="run-ticket">Ticket</label><input className="input mono" id="run-ticket" value={d.ticket} onChange={(e) => set({ ticket: e.target.value.slice(0, 64) })} placeholder="CHG-48190" spellCheck={false} /></div>
          <div><label className="lbl" htmlFor="run-basis">Legal basis</label>
            <select className="input" id="run-basis" value={d.legalBasis} onChange={(e) => set({ legalBasis: e.target.value })}>
              {LEGAL_BASES.map((b) => <option key={b} value={b}>{b}</option>)}
            </select></div>
          <div className="full"><label className="lbl" htmlFor="run-why">What the data is for</label>
            <input className="input" id="run-why" value={d.purposeNote} onChange={(e) => set({ purposeNote: e.target.value })} placeholder="Load UAT for release 26.4; masked copy only" spellCheck={false} />
            <div className="help">Appended to the legal basis in the evidence pack ({legalBasisText(d).length} of 256 characters). Do not paste customer data here.</div></div>
        </div>
      </div>

      <div className="card">
        <div className="card-head"><div className="card-title">Ready to run</div><span className={`chip ${blocked.length ? 'amber' : 'emerald'}`}>{blocked.length ? `${blocked.length} to resolve` : 'all checks passed'}</span></div>
        <div className="summ"><span className="k">Source</span><span>PostgreSQL · <span className="mono">{fqn(sourceObject(d))}</span> via <span className="mono">{d.connectionName}</span>{d.production && <span className="chip amber" style={{ marginLeft: 6 }}>production</span>}</span><button className="linkbtn" type="button" onClick={() => go(1)}>Edit</button></div>
        <div className="summ"><span className="k">Rows</span><span>{d.rows !== null ? `${fmtCount(d.rows)} rows` : `about ${fmtCount(d.shape?.row_estimate ?? null)} rows`} · {d.columns?.length ?? d.shape?.columns.length ?? '—'} columns · {d.parallelism} partitions</span><button className="linkbtn" type="button" onClick={() => go(2)}>Edit</button></div>
        <div className="summ"><span className="k">Discovery</span><span>{counts.crit} critical · {counts.sens} sensitive · {counts.decl} declared · {counts.kept} kept · {overrides} {overrides === 1 ? 'override' : 'overrides'}</span><button className="linkbtn" type="button" onClick={() => go(3)}>Edit</button></div>
        <div className="summ"><span className="k">Write</span><span>{writeModeLabel(d.writeMode, d.writeMode === 'table' ? d.tableLoad : null)} · <span className="mono">{d.writeMode === 'files' ? d.filesPrefix : fqn(d.writeMode === 'table' ? { owner: d.targetOwner || null, name: d.targetTable } : writtenObject(d))}</span>{confirmed ? ` · confirmed for ${fmtCount(d.rows)} rows` : ''}</span><button className="linkbtn" type="button" onClick={() => go(4)}>Edit</button></div>
        <div className="summ"><span className="k">Key</span><span>{d.keyChoice === 'group' ? `Reuse the ${syn ? 'seed' : 'key'} of ${group?.name ?? 'a job group'}${!syn && group ? ` · ${group.kid}` : ''}` : `New ${syn ? 'seed' : 'key'}, returned once with the job`}</span><span /></div>
        <div className="summ"><span className="k">Purpose</span><span><span className="mono">{d.ticket || '—'}</span> · {legalBasisText(d)}</span><span /></div>
        {blocked.length > 0 && <div className="callout rose" style={{ marginTop: 12, fontSize: 12.5 }}><b>Run job is off until these are resolved.</b> {blocked.join(' ')}</div>}
      </div>

      <div className="runbar">
        <div style={{ flex: '1 1 340px' }}><div style={{ fontWeight: 700 }}>{confirmed ? `Writes to ${fqn(writtenObject(d))} after you press Run job` : 'Nothing is written to the source'}</div>
          <div className="faint sm">A dry run reads and transforms a sample, runs the leakage sweep and writes nothing. You can pause from Jobs; a paused job resumes from its last checkpoint.</div></div>
        <div className="rowx"><button className="btn" type="button" disabled={!!busy || blocked.length > 0} onClick={() => void submit(true)}>Dry run (no writes)</button>
          <button className="btn btn-primary" type="button" disabled={!!busy || blocked.length > 0} onClick={() => void submit(false)}>{busy === 'run' ? 'Submitting…' : 'Run job'}</button></div>
      </div>
      {error && <div className="err">{error.message}{error.code && <span className="mono"> · {error.code}</span>}</div>}

      <div className="wfoot"><button className="btn" type="button" onClick={onBack}>Back</button><span className="hint">Step 5 of 5</span></div>
    </section>
  );
}
