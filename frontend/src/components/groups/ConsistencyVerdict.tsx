import { displayPath, pairLabel, problemText } from '../../lib/groups';
import { fmtCount } from '../../lib/jobs';
import type { ConsistencyCheckResult } from '../../lib/types';

/** "12 s ago", "4 min ago", else the local date and time. */
export function ago(iso: string, now: number = Date.now()): string {
  const s = Math.max(0, Math.round((now - Date.parse(iso)) / 1000));
  return s < 60 ? `${s} s ago` : s < 3600 ? `${Math.round(s / 60)} min ago` : new Date(iso).toLocaleString();
}

/**
 * The verdict panel of mockup 07: passed / failed, consistent pairs, the anchor's sample and one row
 * per pair with matched / checked. Counts only: the check never returns a value.
 */
export function ConsistencyVerdict({ result }: { result: ConsistencyCheckResult | null | undefined }): JSX.Element {
  if (!result) {
    return (
      <div className="verdict muted-verdict" aria-live="polite">
        <div className="faint sm vlabel">Cross-system consistency</div>
        <div className="big">—<span className="muted">not checked yet</span></div>
        <div className="faint sm">Run the check once every member has written rows.</div>
        <div className="faint sm" style={{ marginTop: 8 }}>The check reads the masked copies only. It needs no key and sees no original value.</div>
      </div>
    );
  }
  const anchor = result.members[result.anchor];
  return (
    <div className={`verdict${result.passed ? '' : ' failed'}`} aria-live="polite" data-testid="verdict">
      <div className="rowx" style={{ justifyContent: 'space-between' }}>
        <span className="faint sm vlabel">Cross-system consistency</span>
        <span className={`chip ${result.passed ? 'emerald' : 'rose'}`}>{result.passed ? 'passed' : 'failed'}</span>
      </div>
      <div className="big">{result.consistent_pairs} / {result.total_pairs}<span>consistent</span></div>
      <div className="faint sm" style={{ margin: '2px 0 8px' }}>{fmtCount(result.sampled)} values sampled from <span className="mono">{anchor?.object ?? '—'}</span> · checked {ago(result.checked_at)}</div>
      {result.pairs.map((p) => {
        const a = result.members[p.left];
        const b = result.members[p.right];
        return (
          <div className="pair" key={`${p.left}-${p.right}`}>
            <span>{pairLabel(a, b)}</span>
            <span className="on">{displayPath(a?.path ?? '')} = {displayPath(b?.path ?? '')}</span>
            <span className={p.consistent ? 'ok' : 'ok bad'}><i aria-hidden="true">{p.consistent ? '✓' : '✗'}</i>{fmtCount(p.matched)} / {fmtCount(p.checked)}</span>
          </div>
        );
      })}
      {result.problems.map((p, i) => <div key={i} className="callout amber sm" style={{ marginTop: 8 }}>{problemText(p, result.members)}</div>)}
      <div className="faint sm" style={{ marginTop: 8 }}>The check reads the masked copies only. It needs no key and sees no original value.</div>
    </div>
  );
}
