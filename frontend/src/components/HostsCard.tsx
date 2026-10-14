import { useState, type FormEvent } from 'react';
import { hostInputError, type HostRow } from '../lib/admin';
import { ApiError } from '../lib/api';
import { utcDate } from '../lib/format';

/** Where a tenant with no registered host may still scan: a local or dev deployment falls back to
 *  DPS_SCAN_REGISTERED_HOSTS (dps/config.py Settings.scan_fallback_hosts, which POST /scans applies and
 *  GET /admin/posture reports); staging and production refuse such scans (S2 console r1 B3). */
export function fallbackApplies(env: string | null | undefined): boolean {
  return env === 'local' || env === 'dev';
}

const DEFAULT_FALLBACK = 'localhost, 127.0.0.1 and demo-site';

function listed(hosts: string[]): string {
  return hosts.length > 1 ? `${hosts.slice(0, -1).join(', ')} and ${hosts[hosts.length - 1]}` : hosts.join('');
}

function emptyText(env: string | null | undefined, fallback: string[] | null | undefined): string {
  if (fallbackApplies(env)) {
    if (fallback && fallback.length) return `No tenant has a registered host. This ${env} deployment lets every tenant without one scan the service default below (DPS_SCAN_REGISTERED_HOSTS); staging and production refuse scans until a host is registered.`;
    if (fallback) return `No host is registered, and DPS_SCAN_REGISTERED_HOSTS is empty: this ${env} deployment refuses scans until a host is registered.`;
    return `No tenant has a registered host. A ${env} deployment falls back to DPS_SCAN_REGISTERED_HOSTS (by default ${DEFAULT_FALLBACK}); staging and production refuse scans until a host is registered.`;
  }
  if (env) return 'No host is registered. Scans are refused until one is.';
  return `No host is registered. Staging and production refuse scans until one is; a local or dev deployment falls back to DPS_SCAN_REGISTERED_HOSTS (by default ${DEFAULT_FALLBACK}).`;
}

/** UI-scanner hosts (mockup 11): a scan whose URL is outside this list is refused — on a local or dev
 *  deployment a tenant with no host of its own may scan the service default (`fallbackHosts`). */
export function HostsCard({ rows, tenants, defaultTenant, onAdd, onRemove, env = null, fallbackHosts = null }: {
  rows: HostRow[]; tenants: string[]; defaultTenant: string; onAdd: (tenant: string, host: string) => Promise<void>; onRemove: (row: HostRow) => void;
  /** GET /admin/posture `env`; null while unknown. */ env?: string | null;
  /** GET /admin/posture `scan_fallback_hosts`; null from a server that does not report them. */ fallbackHosts?: string[] | null;
}): JSX.Element {
  const fallback = fallbackApplies(env) ? (fallbackHosts ?? null) : null;
  const [host, setHost] = useState('');
  const [tenant, setTenant] = useState('');
  // the tenant list arrives after the first render: fall back to the default, then the first tenant
  const chosen = tenants.includes(tenant) ? tenant : tenants.includes(defaultTenant) ? defaultTenant : tenants[0] ?? defaultTenant;
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function submit(e: FormEvent): Promise<void> {
    e.preventDefault();
    setErr(null);
    const invalid = hostInputError(host);
    if (invalid) { setErr(invalid); return; }
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
        {!rows.length && <tr className="empty-row"><td colSpan={5}>{emptyText(env, fallbackHosts)}</td></tr>}
        {!rows.length && fallback?.map((h) => (
          <tr key={`fallback-${h}`} className="fallback">
            <td className="mono host" data-l="Host">{h}</td>
            <td className="faint" data-l="Tenant">every tenant without a host</td>
            <td className="faint" data-l="Storage state">—</td>
            <td data-l="Added"><span className="chip slate">service default</span></td>
            <td className="acts-td" data-l="" />
          </tr>
        ))}
      </tbody></table></div>
      <div className="hostfoot">
        <form className="hostform" onSubmit={(e) => void submit(e)} noValidate>
          <input className="input" placeholder="https://host.bank.internal" aria-label="Host to register" value={host} onChange={(e) => setHost(e.target.value)} autoComplete="off" spellCheck={false} />
          <select className="input" aria-label="Tenant for the host" value={chosen} onChange={(e) => setTenant(e.target.value)}>{tenants.map((t) => <option key={t}>{t}</option>)}</select>
          <button className="btn" type="submit" disabled={busy}>Add host</button>
        </form>
        <div className="faint sm">A scan whose URL is outside this list is refused before the browser starts{fallback && fallback.length && rows.length ? <> (on this {env} deployment a tenant without a host of its own may scan the service default: {listed(fallback)})</> : null}. Scans stay on the same origin, and POST requests from filled pages are always blocked.</div>
      </div>
      {err && <div className="ferr-line" role="alert">{err}</div>}
    </section>
  );
}
