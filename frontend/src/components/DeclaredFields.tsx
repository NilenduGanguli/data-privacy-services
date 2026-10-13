import { useState } from 'react';
import { EntityChip } from './Chip';
import type { DeclaredField, EntityInfo, MethodSpec, PolicyDoc, Sensitivity } from '../lib/types';

/** "OWNER.TABLE.COLUMN" → system · owner; "$.a.b" → API. */
function systemOf(path: string): string {
  if (path.startsWith('$')) return 'API';
  const parts = path.split('.');
  return parts.length >= 3 ? `${parts[0]} · ${parts[1]}` : parts.length === 2 ? parts[0] : 'any system';
}

/** Declared fields (mockup 05): paths owners declare as an entity type, applied before detection. */
export function DeclaredFields({ body, entities, since, publisher, readOnly, onDeclare }: {
  body: PolicyDoc; entities: EntityInfo[]; since: Record<string, string>; publisher: (since: string) => string; readOnly: boolean; onDeclare: (f: DeclaredField) => void;
}): JSX.Element {
  const [path, setPath] = useState('');
  const [entity, setEntity] = useState('ORG_NAME');
  const byName = new Map(entities.map((e) => [e.name, e]));
  const tone = (n: string): Sensitivity => byName.get(n)?.sensitivity ?? 'MEDIUM';
  const treat = (n: string): MethodSpec | undefined => body.treat[n] ?? body.treat.default_sensitive;
  return (
    <section className="card" id="declared" aria-labelledby="declared-t">
      <div className="card-head"><div className="card-title" id="declared-t">Declared fields <span className="faint">· more than PII</span></div></div>
      <p className="card-sub">Owners name the paths and columns detectors cannot know about. Declarations apply before detection and win over inferred types; the plan records <span className="mono">declared:&lt;path&gt;</span>.</p>
      <div className="tbl-scroll"><table className="tbl stack"><thead><tr><th>Path or column</th><th>System · owner</th><th>Declared as</th><th>Treatment</th><th>Declared by</th><th>Since</th></tr></thead>
        <tbody>{body.detect.declared_fields.map((d) => {
          const s = since[d.path] ?? '—';
          return (
            <tr key={d.path} className={s.startsWith('draft') ? 'drafted' : ''}>
              <td className="mono" data-l="Path">{d.path}</td>
              <td data-l="System · owner">{systemOf(d.path)}</td>
              <td data-l="Declared as"><EntityChip type={d.entity_type} sensitivity={tone(d.entity_type)} /></td>
              <td data-l="Treatment"><span className="m">{treat(d.entity_type)?.method ?? '—'}</span></td>
              <td data-l="Declared by">{publisher(s)}</td>
              <td data-l="Since">{s.startsWith('draft') ? <span className="chip amber">{s}</span> : s}</td>
            </tr>
          );
        })}
          {!body.detect.declared_fields.length && <tr><td colSpan={6} className="muted">No field is declared in this policy.</td></tr>}
        </tbody></table></div>
      {!readOnly && <form className="row" style={{ marginTop: 10, flexWrap: 'wrap' }} onSubmit={(e) => { e.preventDefault(); if (path.trim()) { onDeclare({ path: path.trim(), entity_type: entity }); setPath(''); } }}>
        <input className="input mono" style={{ flex: '1 1 260px' }} placeholder="$.loan.internal_score or OWNER.TABLE.COLUMN" value={path} onChange={(e) => setPath(e.target.value)} aria-label="Declared path" />
        <input className="input mono" style={{ width: 200 }} list="entity-names" value={entity} onChange={(e) => setEntity(e.target.value.toUpperCase())} aria-label="Declared entity type" />
        <button className="btn btn-sm" type="submit" disabled={!path.trim()}>Declare a field</button>
        <datalist id="entity-names">{entities.map((e) => <option key={e.name} value={e.name} />)}</datalist>
      </form>}
    </section>
  );
}
