import { useState } from 'react';
import { EntityChip } from './Chip';
import { EMPTY_DRAFT, METHODS, REVERSIBLE, filterRows, treatmentRows, type Filter } from '../lib/policyDraft';
import { parseParam, SYNTH_HINTS } from '../lib/policyEdit';
import type { EntityInfo, MethodSpec, PolicyDoc, Sensitivity } from '../lib/types';

function Params({ spec }: { spec: MethodSpec | undefined }): JSX.Element | null {
  if (!spec) return null;
  return <>{Object.entries(spec.params ?? {}).map(([k, v]) => <span key={k} className="prm">{k} {Array.isArray(v) ? v.join(' · ') : String(v)}</span>)}</>;
}

function Editor({ spec, onSave, onCancel }: { spec: MethodSpec; onSave: (s: MethodSpec) => void; onCancel: () => void }): JSX.Element {
  const [method, setMethod] = useState(spec.method);
  const [rows, setRows] = useState<[string, string][]>(Object.entries(spec.params ?? {}).map(([k, v]) => [k, typeof v === 'string' ? v : JSON.stringify(v)]));
  const save = (): void => onSave({ method, params: Object.fromEntries(rows.filter(([k]) => k.trim()).map(([k, v]) => [k.trim(), parseParam(v)])) });
  return (
    <div className="inline-edit" role="group" aria-label="Edit treatment">
      <select className="input" value={method} onChange={(e) => setMethod(e.target.value)} aria-label="Method">{METHODS.map((m) => <option key={m}>{m}</option>)}</select>
      {rows.map(([k, v], i) => (
        <div className="kvrow" key={i}>
          <input className="input mono" value={k} placeholder="param" aria-label="Parameter name" onChange={(e) => setRows(rows.map((r, j) => (j === i ? [e.target.value, r[1]] : r)))} />
          <input className="input mono" value={v} placeholder="value" aria-label="Parameter value" onChange={(e) => setRows(rows.map((r, j) => (j === i ? [r[0], e.target.value] : r)))} />
          <button className="btn btn-sm btn-ghost" type="button" onClick={() => setRows(rows.filter((_, j) => j !== i))} aria-label="Remove parameter">×</button>
        </div>
      ))}
      <div className="row"><button className="btn btn-sm" type="button" onClick={() => setRows([...rows, ['', '']])}>Add parameter</button>
        <span className="spacer" /><button className="btn btn-sm" type="button" onClick={onCancel}>Cancel</button><button className="btn btn-sm btn-primary" type="button" onClick={save}>Save to draft</button></div>
    </div>
  );
}

/** Treatment per entity type (mockup 05): entity × obfuscate / synthesize / reversible, rows
 *  changed in the draft marked; a method opens the inline editor that writes the draft. */
export function TreatmentMatrix({ body, entities, changed, readOnly, fpe, onSet }: {
  body: PolicyDoc; entities: EntityInfo[]; changed: Set<string>; readOnly: boolean; fpe: boolean | null; onSet: (entity: string, spec: MethodSpec) => void;
}): JSX.Element {
  const [filter, setFilter] = useState<Filter>('all');
  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const rows = treatmentRows(body, EMPTY_DRAFT, entities, showAll).map((r) => ({ ...r, changed: changed.has(r.entity) }));
  for (const e of changed) if (!rows.some((r) => r.entity === e) && body.treat[e]) rows.push({ entity: e, info: entities.find((x) => x.name === e), obf: body.treat[e], syn: body.synthesize.default, changed: true, inherited: false });
  const visible = filterRows(rows, filter, query);
  const tone = (s: Sensitivity | undefined): Sensitivity => s ?? 'MEDIUM';
  return (
    <section className="card" id="matrix" aria-labelledby="matrix-t">
      <div className="card-head"><div className="card-title" id="matrix-t">Treatment per entity type {changed.size > 0 && <span className="faint">· draft</span>}</div>
        <div className="tools">
          <div className="seg sm" role="group" aria-label="Filter">{(['all', 'critical', 'draft'] as Filter[]).map((f) => <button key={f} type="button" className={filter === f ? 'on' : ''} aria-pressed={filter === f} onClick={() => setFilter(f)}>{f === 'draft' ? 'Changed in draft' : f === 'all' ? 'All' : 'Critical'}</button>)}</div>
          <input className="input" placeholder="Search entity or method" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search entity types" />
        </div></div>
      <div className="tbl-scroll"><table className="tbl stack">
        <thead><tr><th>Entity type</th><th>Family</th><th>Sensitivity</th><th>Obfuscate</th><th>Synthesize</th><th>Reversible</th></tr></thead>
        <tbody>{visible.map((r) => (
          <tr key={r.entity} className={r.changed ? 'drafted' : ''}>
            <td className="ent" data-l="Entity type"><EntityChip type={r.entity} sensitivity={tone(r.info?.sensitivity)} />{r.changed && <span className="chip amber" style={{ marginLeft: 6 }}>draft</span>}</td>
            <td className="fam" data-l="Family">{r.info?.family.replace(/_/g, ' ') ?? 'custom'}{r.info?.jurisdictions.length ? ` · ${r.info.jurisdictions.join(', ')}` : ''}</td>
            <td data-l="Sensitivity"><EntityChip type={tone(r.info?.sensitivity)} sensitivity={tone(r.info?.sensitivity)} /></td>
            <td data-l="Obfuscate">
              <button className="mbtn" type="button" disabled={readOnly} onClick={() => setEditing(editing === r.entity ? null : r.entity)} aria-label={`Edit the obfuscate method for ${r.entity}`}>{r.obf.method}</button>
              <Params spec={r.obf} />{r.inherited && <span className="syn-note">default_sensitive</span>}
              {editing === r.entity && !readOnly && <Editor spec={r.obf} onCancel={() => setEditing(null)} onSave={(s) => { setEditing(null); onSet(r.entity, s); }} />}
            </td>
            <td data-l="Synthesize"><span className="m">{r.syn.method}</span><Params spec={r.syn} />{SYNTH_HINTS[r.entity] && r.syn.method !== 'keep' && <span className="syn-note">{SYNTH_HINTS[r.entity]}</span>}</td>
            <td data-l="Reversible" className={`revcell${REVERSIBLE.has(r.obf.method) ? '' : ' no'}`}>{REVERSIBLE.has(r.obf.method) ? 'Yes' : 'No'}</td>
          </tr>))}
          {visible.length === 0 && <tr><td colSpan={6} className="muted">No entity type matches. Clear the search or the filter to see all {rows.length}.</td></tr>}
        </tbody></table></div>
      <div className="tbl-foot"><span>Showing {visible.length} of {rows.length} entity types. Others are tokenised by <span className="mono">default_sensitive → {body.treat.default_sensitive?.method ?? 'det_token'}</span>.</span>
        <button className="btn btn-sm" type="button" onClick={() => setShowAll((v) => !v)}>{showAll ? 'Show policy types only' : `Show all ${entities.length}`}</button></div>
      <div className="faint" style={{ fontSize: 12, marginTop: 8 }}><span className="mono">fpe_*</span> methods need at least 10⁶ free values; smaller domains run as <span className="mono">det_token</span> and the plan records <span className="mono">fallback:fpe_domain_too_small</span>. FF1 on this deployment: <b>{fpe === null ? 'unknown' : fpe ? 'on' : 'off'}</b>.</div>
    </section>
  );
}
