import { useState, type FormEvent } from 'react';
import type { HostRow } from '../lib/admin';
import { ApiError } from '../lib/api';
import { utcDate } from '../lib/format';

/** UI-scanner hosts (mockup 11): a scan whose URL is outside this list is refused. */
export function HostsCard({ rows, tenants, defaultTenant, onAdd, onRemove }: {
  rows: HostRow[]; tenants: string[]; defaultTenant: string; onAdd: (tenant: string, host: string) => Promise<void>; onRemove: (row: HostRow) => void;
}): JSX.Element {
  const [host, setHost] = useState('');
  const [tenant, setTenant] = useState('');
  // the tenant list arrives after the first render: fall back to the default, then the first tenant
  const chosen = tenants.includes(tenant) ? tenant : tenants.includes(defaultTenant) ? defaultTenant : tenants[0] ?? defaultTenant;
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent): Promise<void> {
    e.preventDefault();
    setErr(null);
    if (!host.trim()) { setErr('Enter a host such as https://host.bank.internal.'); return; }
    setBusy(true);
    try { await onAdd(chosen, host.trim()); setHost(''); }
    catch (x) { setErr(x instanceof ApiError ? `${x.message}${x.code ? ` (${x.code})` : ''}` : 'The host could not be added.'); }
    finally { setBusy(false); }
  }
  return (
    <section className="card" id="hosts" aria-labelledby="hostT">
      <div className="card-head"><div className="card-title" id="hostT">UI-scanner hosts</div><span className="chip slate">{rows.length} registered</span></div>
      <div className="tbl-wrap"><table className="tbl stack"><thead><tr><th>Host</th><th>Tenant</th><th>Storage state</th><th>Added</th><th /></tr></thead><tbody>
        {rows.map((r) => (
          <tr key={`${r.tenant}-${r.host}`}>
            <td className="mono host" data-l="Host">https://{r.host}</td>
            <td className="mono nw" data-l="Tenant">{r.tenant}</td>
            <td className={r.storageRef ? 'mono nw' : 'faint'} data-l="Storage state">{r.storageRef ?? 'none · public pages only'}</td>
            <td className="dt" data-l="Added">{utcDate(r.added)}</td>
            <td className="acts-td" data-l=""><div className="acts"><button className="btn btn-sm btn-ghost danger" type="button" onClick={() => onRemove(r)} aria-label={`Remove ${r.host}`}>Remove</button></div></td>
          </tr>
        ))}
        {!rows.length && <tr className="empty-row"><td colSpan={5}>No host is registered. Scans are refused until one is.</td></tr>}
      </tbody></table></div>
      <div className="hostfoot">
        <form className="hostform" onSubmit={(e) => void submit(e)} noValidate>
          <input className="input" placeholder="https://host.bank.internal" aria-label="Host to register" value={host} onChange={(e) => setHost(e.target.value)} autoComplete="off" spellCheck={false} />
          <select className="input" aria-label="Tenant for the host" value={chosen} onChange={(e) => setTenant(e.target.value)}>{tenants.map((t) => <option key={t}>{t}</option>)}</select>
          <button className="btn" type="submit" disabled={busy}>Add host</button>
        </form>
        <div className="faint sm">A scan whose URL is outside this list is refused before the browser starts. Scans stay on the same origin, and POST requests from filled pages are always blocked.</div>
      </div>
      {err && <div className="ferr-line" role="alert">{err}</div>}
    </section>
  );
}
