import { displayPath, engineLabel, ENGINE_BADGE, formLabel } from '../../lib/groups';
import { fmtCount } from '../../lib/jobs';
import type { ConsistencyCheckResult } from '../../lib/types';

/**
 * Join coverage (in place of mockup 07's sample of masked rows, which the counts-only contract does
 * not allow — M3 open question 3): per member the join column, its form and found / sampled; per
 * compared entity the method and agreed / compared.
 */
export function CoverageTable({ result }: { result: ConsistencyCheckResult | null | undefined }): JSX.Element {
  const head = (
    <div className="rowx" style={{ justifyContent: 'space-between', marginBottom: 8 }}>
      <span className="card-title">Join coverage</span><span className="faint sm">counts only · no value is shown</span>
    </div>
  );
  if (!result) return <div>{head}<p className="muted" style={{ margin: 0, fontSize: 13 }}>Coverage per system and per compared entity appears after the first check.</p></div>;
  const prefixed = result.members.find((m) => m.form === 'prefixed_digits');
  const numeric = result.members.some((m) => m.form === 'numeric');
  return (
    <div data-testid="coverage">
      {head}
      <div className="jwrap"><table className="tbl join">
        <thead><tr><th>System</th><th>Join column</th><th>Join form</th><th>Found / sampled</th></tr></thead>
        <tbody>
          {result.members.map((m, i) => (
            <tr key={`${m.job_id}-${i}`}>
              <td><div className="stack"><span><span className="eb">{ENGINE_BADGE[m.engine] ?? m.engine}</span><span className="mono">{m.object}</span></span>{m.anchor && <span className="k">anchor · sampled here</span>}</div></td>
              <td><div className="stack"><span className="mono">{displayPath(m.path)}</span>{m.method && <span className="k">{m.entity_type ?? '—'} · {m.method}</span>}</div></td>
              <td>{formLabel(m)}</td>
              <td className="mono">{fmtCount(m.found)} / {fmtCount(result.sampled)}</td>
            </tr>
          ))}
        </tbody>
      </table></div>
      {result.attributes.length > 0 && (
        <div className="jwrap" style={{ marginTop: 12 }}><table className="tbl join">
          <thead><tr><th>Compared entity</th><th>Method</th><th>Systems</th><th>Agreed / compared</th></tr></thead>
          <tbody>
            {result.attributes.map((a) => (
              <tr key={`${a.entity_type}-${a.method}`}>
                <td><span className="chip teal">{a.entity_type}</span></td>
                <td className="mono">{a.method}</td>
                <td>{a.members.map((i) => engineLabel(result.members[i]?.engine ?? '?')).join(' · ')}</td>
                <td><div className="stack">
                  <span className={a.consistent ? 'yes' : 'yes no'}><i aria-hidden="true">{a.consistent ? '✓' : '✗'}</i>{fmtCount(a.agreed)} / {fmtCount(a.compared)}</span>
                  {!a.counts_toward_pass && <span className="chip slate">informational</span>}
                  {a.ambiguous > 0 && <span className="k">+{fmtCount(a.ambiguous)} with several values</span>}
                </div></td>
              </tr>
            ))}
          </tbody>
        </table></div>
      )}
      {prefixed && numeric && (
        <div className="callout sm" style={{ marginTop: 12, fontSize: 12.5 }}>
          {engineLabel(prefixed.engine)} stores the id with a {prefixed.prefix_len ?? '?'}-character prefix. Format-preserving encryption changes only the digits, so the token still joins to the numeric columns.
        </div>
      )}
    </div>
  );
}
