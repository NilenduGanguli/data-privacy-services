import type { NumberedRiskFlag } from '../../lib/types';
import { flagSentence, flagTone, pathOf } from './format';

/** Numbered risk flags (mockup 04): code chip by severity, the sentence and the page path (`path` is the
 * page's redacted URL as the API returns it; the origin is the scan's, so only the path is shown). */
export function RiskFlags({ flags }: { flags: NumberedRiskFlag[] }): JSX.Element {
  if (!flags.length) return <div className="hint" style={{ padding: '6px 0' }}>No risk flags so far.</div>;
  return (
    <div>{flags.map((r) => (
      <div key={r.code} className="flag"><span className={`chip ${flagTone(r)}`}>{r.code}</span>
        <span>{flagSentence(r)} <span className="mono">{pathOf(r.path)}</span></span></div>
    ))}</div>
  );
}
