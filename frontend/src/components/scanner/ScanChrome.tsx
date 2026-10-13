import { Link } from 'react-router-dom';
import { isTerminal, shortId } from '../../lib/jobs';
import type { Job, ScanSummary } from '../../lib/types';

/** The page's crumb row under the console topbar: "UI scanner / …" and the no-submission pill. */
export function ScanCrumb({ children }: { children: React.ReactNode }): JSX.Element {
  return (
    <div className="scan-crumb">
      <Link className="crumb" to="/scanner">UI scanner</Link><span className="sep">/</span><span className="muted">{children}</span>
      <span className="pill"><span className="dot" style={{ background: 'var(--teal)', boxShadow: '0 0 0 3px var(--teal-soft)' }} />No submissions · POST interception on</span>
    </div>
  );
}

export const scanLabel = (id: string): JSX.Element => <span className="mono">{shortId(id)}</span>;

type StepId = 'setup' | 'crawl' | 'classify' | 'parallel' | 'export';

/**
 * Setup · Crawl · Classify · Parallel view · Export (mockups 12 and 04). `current` is the page's own
 * step; crawl and classify are done once the scan is terminal, export once the export artefact exists.
 */
export function ScanStepper({ scan, summary, current, linkSetup }: {
  scan: Job | undefined; summary: ScanSummary | undefined; current: 'setup' | 'crawl' | 'parallel'; linkSetup?: string;
}): JSX.Element {
  const terminal = isTerminal(scan);
  const s = summary;
  const crawlSub = s ? `${!terminal && current === 'parallel' ? 'running, ' : ''}${s.pages_visited} of ${Math.max(s.pages_discovered, s.pages_visited)} pages` : undefined;
  const steps: { id: StepId; label: string; sub?: string; done: boolean }[] = [
    { id: 'setup', label: 'Setup', done: !!scan },
    { id: 'crawl', label: 'Crawl', sub: crawlSub, done: terminal },
    { id: 'classify', label: 'Classify', sub: s ? `${s.pages_classified} pages` : undefined, done: terminal },
    { id: 'parallel', label: 'Parallel view', sub: s ? `${s.pages_filled} pages ready${current === 'parallel' ? ` · persona ${s.display_persona} of ${s.personas}` : ''}` : undefined, done: false },
    { id: 'export', label: 'Export', done: !!s?.export_artefacts.json },
  ];
  return (
    <div className="card" style={{ padding: '14px 18px' }}><div className="stepper">
      {steps.map((st, i) => {
        const on = st.id === current && !(st.done && st.id !== 'parallel');
        const cls = `step${st.done && !on ? ' done' : on ? ' on' : ''}`;
        const inner = <><span className="n">{st.done && !on ? '✓' : i + 1}</span>{st.label}{st.sub && <span className="faint">· {st.sub}</span>}</>;
        return (
          <div key={st.id} style={{ display: 'contents' }}>
            {i > 0 && <div className="step-sep" />}
            {linkSetup && (st.id === 'setup' || st.id === 'crawl')
              ? <Link className={cls} to={linkSetup}>{inner}</Link>
              : <div className={cls} aria-current={on ? 'step' : undefined}>{inner}</div>}
          </div>
        );
      })}
    </div></div>
  );
}
