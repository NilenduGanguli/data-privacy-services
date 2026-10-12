import { checkLabel, fmtCount } from '../lib/jobs';
import type { Check } from '../lib/types';

/** One check: ✓ passed, ✕ a required check failed, ! an advisory check failed. */
function CheckRow({ check }: { check: Check }): JSX.Element {
  const state = check.ok ? 'ok' : check.severity === 'advisory' ? 'warn' : 'bad';
  const icon = check.ok ? '✓' : state === 'warn' ? '!' : '✕';
  const meta = check.count !== undefined && check.count !== null ? fmtCount(check.count) : check.ok ? 'ok' : (check.code ?? (state === 'warn' ? 'check' : 'failed'));
  const shown = check.name === 'connect' && check.detail === 'tls' ? 'over TLS' : check.name === 'connect' && check.detail === 'plain' ? 'without TLS' : check.detail;
  const detail = [shown, !check.ok && check.code && check.count !== undefined ? check.code : null].filter(Boolean).join(' · ');
  return (
    <div className={`ck ${state}`}>
      <span className="ic" aria-hidden="true">{icon}</span>
      <span><b>{checkLabel(check.name)}</b>{detail && <span className="d">{detail}</span>}</span>
      <span className="meta">{meta}</span>
    </div>
  );
}

/** The mockup's check list with "N of M checks passed" (value-free names, details and counts). */
export function CheckList({ checks, title, aside }: { checks: Check[]; title?: string; aside?: React.ReactNode }): JSX.Element {
  const passed = checks.filter((c) => c.ok).length;
  const failedRequired = checks.some((c) => !c.ok && c.severity !== 'advisory');
  return (
    <div className="checks">
      <div className="checks-head">
        <span className="rowx">{title && <span className="sub-title">{title}</span>}{aside}</span>
        <span className={`chip ${failedRequired ? 'rose' : passed === checks.length ? 'emerald' : 'amber'}`}>{passed} of {checks.length} checks passed</span>
      </div>
      {checks.map((c, i) => <CheckRow key={`${c.name}-${i}`} check={c} />)}
    </div>
  );
}
