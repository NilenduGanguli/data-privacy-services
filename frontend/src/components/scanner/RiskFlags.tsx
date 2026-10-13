import type { NumberedRiskFlag } from '../../lib/types';
import { flagSentence, flagTone } from './format';

/** Numbered risk flags (mockup 04): code chip by severity, the sentence and the page path. */
export function RiskFlags({ flags }: { flags: NumberedRiskFlag[] }): JSX.Element {
  if (!flags.length) return <div className="hint" style={{ padding: '6px 0' }}>No risk flags so far.</div>;
  return (
    <div>{flags.map((r) => (
      <div key={r.code} className="flag"><span className={`chip ${flagTone(r)}`}>{r.code}</span>
        <span>{flagSentence(r)} <span className="mono">{r.path}</span></span></div>
    ))}</div>
  );
}
