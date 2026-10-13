import { relative } from '../lib/format';
import type { PostureReport } from '../lib/types';

/** Posture checks (mockup 11): failing checks first; a failing key-rotation check offers to
 *  rotate each key it names. */
export function PostureList({ posture, now, dueKeys, onRotate }: {
  posture: PostureReport; now: Date; dueKeys: string[]; onRotate: (name: string) => void;
}): JSX.Element {
  const checks = [...posture.checks].sort((a, b) => Number(a.ok) - Number(b.ok));
  const failing = checks.filter((c) => !c.ok).length;
  return (
    <section className="card" aria-labelledby="postT">
      <div className="card-head"><div className="card-title" id="postT">Posture checks</div><span className={`chip ${failing ? 'amber' : 'emerald'}`}>{failing ? `${failing} to fix` : 'all pass'}</span></div>
      {checks.map((c) => (
        <div key={c.id} className={`chk${c.ok ? '' : ' fail'}`}>
          <span className={`ic ${c.ok ? 'ok' : 'no'}`} aria-label={c.ok ? 'pass' : 'fail'}>{c.ok ? '✓' : '✕'}</span>
          <span className="nm">{c.name}</span>
          <span className={`chip ${c.ok ? 'emerald' : 'rose'}`}>{c.ok ? 'pass' : 'fail'}</span>
          <span className="d">{c.detail}{c.enforced_at_boot ? ' · enforced at boot' : ''}
            {!c.ok && c.id === 'keys_rotated' && dueKeys.map((name) => (
              <span key={name} style={{ display: 'block' }}><button className="btn btn-sm" type="button" style={{ marginTop: 8 }} onClick={() => onRotate(name)}>Rotate {name}</button></span>
            ))}</span>
        </div>
      ))}
      <div className="faint sm" style={{ marginTop: 8 }}>Staging and production refuse to boot when a check marked "enforced at boot" fails ({posture.env}: {posture.enforced ? 'enforced' : 'reported only'}). Checked {relative(posture.checked_at, now)}.</div>
    </section>
  );
}
