import { useState } from 'react';
import { PRIVILEGED, SCOPES, keyMeta, rotationDue, tenantsLabel } from '../lib/admin';
import { relative, utcDate } from '../lib/format';
import type { ApiKeyView } from '../lib/types';

/** API keys (mockup 11): prefixes only, scopes, last use, expiry and rotation due, actions. */
export function KeysTable({ keys, now, maxAge, onRotate, onDisable, onEnable }: {
  keys: ApiKeyView[]; now: Date; maxAge: number; onRotate: (k: ApiKeyView) => void; onDisable: (k: ApiKeyView) => void; onEnable: (k: ApiKeyView) => void;
}): JSX.Element {
  const [filter, setFilter] = useState('');
  const q = filter.trim().toLowerCase();
  const shown = keys.filter((k) => !q || k.name.toLowerCase().includes(q) || k.tenant_ids.some((t) => t.toLowerCase().includes(q)));
  return (
    <section className="card" aria-labelledby="keysT">
      <div className="card-head"><div className="card-title" id="keysT">API keys</div>
        <div className="head-actions"><span className="legend"><span><i style={{ background: 'var(--amber)' }} />privileged scope</span></span>
          <input className="input search" type="search" placeholder="Filter by name or tenant" aria-label="Filter keys" value={filter} onChange={(e) => setFilter(e.target.value)} /></div></div>
      <div className="tbl-wrap"><table className="tbl stack" id="keysTbl"><thead><tr><th>Name</th><th>Tenants</th><th>Scopes</th><th>Created</th><th>Last used</th><th>Expires</th><th style={{ textAlign: 'right' }}>Actions</th></tr></thead><tbody>
        {shown.map((k) => {
          const t = tenantsLabel(k.tenant_ids);
          const due = rotationDue(k, maxAge);
          return (
            <tr key={k.id} className={k.status === 'active' ? '' : 'off'}>
              <td data-l="Name"><div className="kname">{k.name}</div><div className="kpref">{keyMeta(k, now)}</div></td>
              <td className="mono nw" data-l="Tenants">{t.main}{t.more && <div className="sub">{t.more}</div>}</td>
              <td data-l="Scopes"><div className="scopes">{k.scopes.map((s) => <span key={s} className={`chip ${PRIVILEGED.has(s) ? 'amber' : 'slate'}`}>{s}</span>)}</div></td>
              <td className="dt" data-l="Created">{k.source === 'static' ? 'env' : utcDate(k.created_at)}</td>
              <td className="dt" data-l="Last used">{relative(k.last_used_at, now)}</td>
              <td data-l="Expires"><span className="dt">{k.expires_at ? utcDate(k.expires_at) : 'never'}</span>{due && <div style={{ marginTop: 4 }}><span className="chip amber">rotate · {k.age_days} days</span></div>}</td>
              <td className="acts-td" data-l=""><div className="acts">
                {k.source === 'static' ? <span className="faint sm" title="Environment keys live in DPS_API_KEYS_FILE; rotate them there.">env</span>
                  : k.status === 'disabled' ? <button className="btn btn-sm" type="button" onClick={() => onEnable(k)}>Re-enable</button>
                    : <>
                      <button className="btn btn-sm" type="button" onClick={() => onRotate(k)} aria-label={`Rotate ${k.name}`}>Rotate</button>
                      {k.status === 'active' && <button className="btn btn-sm btn-ghost danger" type="button" onClick={() => onDisable(k)} aria-label={`Disable ${k.name}`}>Disable</button>}
                    </>}
              </div></td>
            </tr>
          );
        })}
        {!shown.length && <tr className="empty-row"><td colSpan={7}>{keys.length ? 'No key matches this filter.' : 'No API key yet. Create the first one below.'}</td></tr>}
      </tbody></table></div>
      <div className="faint sm" style={{ marginTop: 10 }}>{keys.length} keys · prefixes only; DPS cannot show a full key after creation · scopes: {SCOPES.join(', ')}</div>
    </section>
  );
}
