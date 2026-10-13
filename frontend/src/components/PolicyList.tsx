import { Link } from 'react-router-dom';
import { shortHash } from '../lib/format';
import type { PresetView, TenantPolicyView } from '../lib/types';

export interface TocCounts { matrix: number; declared: number; recognizers: number; plan: number; versions: number }

const modeWord = (mode: string, reversible: boolean): string => (mode === 'synthesize' ? 'one-way' : reversible ? 'reversible' : 'irreversible');

/** Left column of the Policies page (mockup 05): tenant policies and shipped presets, then the
 *  "On this page" anchors with counts. */
export function PolicyList({ presets, tenant, selected, counts, canCreate, onNew }: {
  presets: PresetView[]; tenant: TenantPolicyView[]; selected: string; counts: TocCounts; canCreate: boolean; onNew: () => void;
}): JSX.Element {
  const byName = new Map(presets.map((p) => [p.name, p]));
  const adopted = new Set(tenant.map((t) => t.name));
  const items = [
    ...tenant.map((t) => {
      const base = byName.get(t.base_preset ?? t.name);
      return { name: t.name, ver: `v${t.active_version ?? 1}`, hash: t.active_hash ?? base?.hash ?? '', mode: base?.mode ?? 'obfuscate', meta: `${modeWord(base?.mode ?? 'obfuscate', base?.reversible ?? true)} · ${t.jobs} ${t.jobs === 1 ? 'job' : 'jobs'}`, draft: t.draft };
    }),
    ...presets.filter((p) => !adopted.has(p.name)).map((p) => ({ name: p.name, ver: `v${p.version} · shipped`, hash: p.hash, mode: p.mode, meta: `${modeWord(p.mode, p.reversible)} · ${p.jobs} ${p.jobs === 1 ? 'job' : 'jobs'}`, draft: false })),
  ];
  return (
    <aside className="pol-side" aria-label="Policies">
      <div className="card">
        <div className="card-head"><div className="card-title">Presets</div><span className="chip slate">{items.length}</span></div>
        <div className="plist">
          {items.map((p) => (
            <Link key={p.name} to={`/policies/${encodeURIComponent(p.name)}`} className={`preset${p.name === selected ? ' on' : ''}`} aria-current={p.name === selected ? 'page' : undefined}>
              <div className="p-top"><span className="p-name">{p.name}</span><span className="p-ver">{p.ver}</span></div>
              <div className="p-hash" title={p.hash ? `sha256:${p.hash}` : undefined}>{shortHash(p.hash)}</div>
              <div className="p-meta"><span className={`chip ${p.mode === 'synthesize' ? 'violet' : 'teal'}`}>{p.mode}</span>{p.meta}{p.draft && <span className="chip amber">draft</span>}</div>
            </Link>
          ))}
        </div>
        {canCreate && <button className="btn btn-sm" type="button" style={{ marginTop: 12, width: '100%', justifyContent: 'center' }} onClick={onNew}>New policy from a preset</button>}
      </div>
      <div className="card toc-card"><div className="nav-label">On this page</div>
        <nav className="toc" aria-label="On this page">
          <a href="#matrix">Treatment per entity <span>{counts.matrix}</span></a>
          <a href="#declared">Declared fields <span>{counts.declared}</span></a>
          <a href="#recognizers">Custom recognizers <span>{counts.recognizers}</span></a>
          <a href="#plan">Plan preview <span>{counts.plan}</span></a>
          <a href="#versions">Versions <span>{counts.versions}</span></a>
        </nav></div>
    </aside>
  );
}
