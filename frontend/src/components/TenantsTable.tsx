import { quotaTone } from '../lib/admin';
import { compact, count } from '../lib/format';
import type { TenantView } from '../lib/types';

/** Tenants and quotas (mockup 11): running jobs and daily rows against their caps. */
export function TenantsTable({ tenants, onEdit, onAdd }: { tenants: TenantView[]; onEdit: (t: TenantView) => void; onAdd: () => void }): JSX.Element {
  const near = tenants.find((t) => quotaTone(t.usage.rows_today, t.effective.daily_row_quota) === 'amber');
  return (
    <section className="card" aria-labelledby="tenT">
      <div className="card-head"><div className="card-title" id="tenT">Tenants and quotas</div><button className="btn btn-sm" type="button" onClick={onAdd}>Add tenant</button></div>
      <div className="tbl-wrap"><table className="tbl stack"><thead><tr><th>Tenant</th><th>Running jobs</th><th>Daily rows</th><th>Retention</th><th>Keys</th><th /></tr></thead><tbody>
        {tenants.map((t) => {
          const e = t.effective;
          const runPct = e.max_running ? Math.min(100, Math.round((t.usage.running / e.max_running) * 100)) : 0;
          const quota = e.daily_row_quota;
          const rowPct = quota ? Math.min(100, Math.round((t.usage.rows_today / quota) * 100)) : 0;
          const tone = quotaTone(t.usage.rows_today, quota);
          const off = Object.entries(t.keys).filter(([k]) => k !== 'active').reduce((a, [, n]) => a + n, 0);
          return (
            <tr key={t.tenant_id}>
              <td data-l="Tenant"><div className="kname">{t.tenant_id}</div>{t.display_name && <div className="sub">{t.display_name}</div>}</td>
              <td data-l="Running jobs"><div className="q"><span className="v"><b>{count(t.usage.running)}</b> of {count(e.max_running)}</span><div className="bar neu"><i style={{ width: `${runPct}%` }} /></div></div></td>
              <td data-l="Daily rows"><div className="q"><span className="v"><b>{compact(t.usage.rows_today)}</b>{quota ? <> of {compact(quota)}{tone === 'amber' ? ` · ${rowPct}%` : ''}</> : ' · no quota'}</span><div className={`bar ${tone}`}><i style={{ width: `${rowPct}%` }} /></div></div></td>
              <td className="num" data-l="Retention">{count(e.retention_days)} days</td>
              <td className="num" data-l="Keys">{count(t.keys.active ?? 0)}{off > 0 && <span className="faint"> +{off} off</span>}</td>
              <td data-l=""><button className="btn btn-sm btn-ghost" type="button" onClick={() => onEdit(t)} aria-label={`Edit ${t.tenant_id}`}>Edit</button></td>
            </tr>
          );
        })}
        {!tenants.length && <tr className="empty-row"><td colSpan={6}>No tenant has settings yet; every tenant runs on the service defaults.</td></tr>}
      </tbody></table></div>
      {near && near.effective.daily_row_quota && <div className="callout amber sm" style={{ marginTop: 12 }}>{near.tenant_id} has used {Math.round((near.usage.rows_today / near.effective.daily_row_quota) * 100)}% of its daily row quota. New jobs for this tenant queue until 00:00 UTC once it is reached. Raise the quota in <b>Edit</b> or move work to tomorrow.</div>}
      <div className="faint sm" style={{ marginTop: 10 }}>Retention covers job metadata, classification snapshots and evidence packs. Raw values are never stored, so there is nothing else to purge.</div>
    </section>
  );
}
