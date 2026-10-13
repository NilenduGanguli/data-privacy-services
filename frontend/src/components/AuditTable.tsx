import { count, utcDay, utcTime } from '../lib/format';
import { auditOutcomeTone, methodsSummary, policyLabel } from '../lib/reports';
import type { AuditRow } from '../lib/types';

/** The append-only audit log (mockup 09; admin keys only): one row per call, job event or admin
 *  action — key names, routes, counts and method names, never a value. */
export function AuditTable({ rows }: { rows: AuditRow[] }): JSX.Element {
  return (
    <div className="tbl-scroll"><table className="tbl stack" id="audit-tbl"><thead><tr><th>Time (UTC)</th><th>Tenant</th><th>Key name</th><th>Route</th><th>Mode</th><th>Policy</th><th className="num">Findings</th><th>Methods</th><th>Outcome</th></tr></thead><tbody>
      {rows.map((r) => {
        const m = methodsSummary(r.methods);
        const pol = policyLabel(r.policy);
        return (
          <tr key={r.id}>
            <td className="t" data-l="Time (UTC)">{utcTime(r.ts)}<span className="s2">{utcDay(r.ts)}</span></td>
            <td className="nw" data-l="Tenant">{r.tenant_id && r.tenant_id !== '—' ? r.tenant_id : <span className="faint">—</span>}</td>
            <td className="mono nw" data-l="Key name">{r.key_name ?? <span className="faint">unknown key</span>}</td>
            <td className="mono rt" data-l="Route">{r.route}</td>
            <td data-l="Mode">{r.mode ?? r.action.split('.')[0]}</td>
            <td className={pol ? 'mono nw' : 'faint'} data-l="Policy">{pol ?? '—'}</td>
            <td className="num mono nw" data-l="Findings">{r.findings === null ? '—' : count(r.findings)}</td>
            <td className="nw" data-l="Methods">{m ? <><span className="meth">{m.first}</span>{m.more > 0 && <span className="more">+{m.more}</span>}</> : <span className="faint">—</span>}</td>
            <td data-l="Outcome"><span className={`chip ${auditOutcomeTone(r.outcome)}`}>{r.outcome.replace(/_/g, ' ')}</span>{r.status !== null && <span className="s2">{r.status}{r.job_id ? ` · ${r.action}` : ''}</span>}</td>
          </tr>
        );
      })}
      {!rows.length && <tr className="empty-row"><td colSpan={9}>No audit rows match these filters. Clear the search or pick another layer.</td></tr>}
    </tbody></table></div>
  );
}
