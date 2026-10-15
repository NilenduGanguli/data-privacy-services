import { useEffect, useState } from 'react';
import { isTerminal } from '../../lib/jobs';
import type { Job, ScanSummary } from '../../lib/types';
import { fmtElapsed, plural } from './format';

/** Date.now(), refreshed every second while `active` (the live clock of a running crawl). */
export function useClock(active: boolean): number {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return undefined;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}

function elapsedS(scan: Job, summary: ScanSummary, now: number): number {
  if (!isTerminal(scan) && scan.started_at) return (now - Date.parse(scan.started_at)) / 1000;
  if (summary.elapsed_s !== null) return summary.elapsed_s;
  if (scan.started_at && scan.finished_at) return (Date.parse(scan.finished_at) - Date.parse(scan.started_at)) / 1000;
  return 0;
}

/** The run chip: crawling · m:ss / finished · m:ss / stopped · N pages kept / failed. */
export function RunChip({ scan, summary, now }: { scan: Job; summary: ScanSummary; now: number }): JSX.Element {
  const clock = fmtElapsed(elapsedS(scan, summary, now));
  switch (scan.state) {
    case 'queued': return <span className="chip slate">queued</span>;
    case 'succeeded': return <span className="chip emerald">finished · {clock}</span>;
    case 'canceled': return <span className="chip slate">stopped · {plural(summary.pages_visited, 'page')} kept</span>;
    case 'failed': case 'dead': return <span className="chip rose">failed{scan.error_code ? ` · ${scan.error_code}` : ''}</span>;
    case 'paused': return <span className="chip amber">paused · {clock}</span>;
    default: return <span className="chip teal"><span className="live-dot" aria-hidden="true" />crawling · {clock}</span>;
  }
}

/** "Crawl · live" (mockup 12): progress of visited over discovered pages, the pipeline and the counts. */
export function CrawlProgress({ scan, summary, now }: { scan: Job; summary: ScanSummary; now?: number }): JSX.Element {
  const clock = useClock(!isTerminal(scan) && now === undefined);
  const at = now ?? clock;
  const scope = (scan.spec?.scope ?? {}) as { max_pages?: number | null; max_depth?: number | null };
  const found = Math.max(summary.pages_discovered, summary.pages_visited);
  const pct = found ? Math.round((summary.pages_visited / found) * 100) : 0;
  const flags = summary.risk_flags.length;
  return (
    <section className="card" aria-labelledby="liveT">
      <div className="card-head"><div className="card-title" id="liveT">Crawl · live</div><RunChip scan={scan} summary={summary} now={at} /></div>
      <div className="prog-top">
        <span><b>{summary.pages_visited}</b> of <b>{found}</b> discovered pages visited · <b>{summary.pages_queued}</b> queued</span>
        <span className="faint">budget {scope.max_pages ?? '—'} pages · depth ≤ {scope.max_depth ?? '—'}</span>
      </div>
      <div className="bar big" role="progressbar" aria-label="Pages visited" aria-valuemin={0} aria-valuemax={found} aria-valuenow={summary.pages_visited}><i style={{ width: `${pct}%` }} /></div>
      <div className="pipe">
        <div className="box"><div className="v">{summary.pages_visited}</div><div className="l">pages crawled</div></div><span className="arrow" aria-hidden="true">→</span>
        <div className="box"><div className="v">{summary.pages_classified}</div><div className="l">pages classified</div></div><span className="arrow" aria-hidden="true">→</span>
        <div className="box"><div className="v">{summary.pages_filled}</div><div className="l">filled and captured</div></div>
      </div>
      <div className="stats">
        <div className="box"><div className="v">{summary.forms}</div><div className="l">forms found</div></div>
        <div className="box"><div className="v">{summary.fields}</div><div className="l">fields inventoried</div></div>
        <div className="box"><div className="v">{summary.personal_fields}</div><div className="l">personal data fields · {summary.critical_fields} critical</div></div>
        <div className="box"><div className="v" style={{ color: flags ? 'var(--rose)' : undefined }}>{flags}</div><div className="l">risk {flags === 1 ? 'flag' : 'flags'} so far</div></div>
      </div>
    </section>
  );
}
