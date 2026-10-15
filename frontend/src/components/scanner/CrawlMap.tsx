import { useState } from 'react';
import { Link } from 'react-router-dom';
import { crawlTree, type CrawlNode } from '../../lib/scans';
import type { NumberedRiskFlag, ScanEvent, ScanPage } from '../../lib/types';
import { Refused } from './Refused';
import { flagSentence, flagTone, pathOf, plural, skipLabel, statusIcon } from './format';

const rendersPii = (p: ScanPage): boolean =>
  Object.values(p.rendered_findings).some((n) => n > 0) || p.capture_status === 'captured' || p.capture_status === 'withheld';

/** Where a crawl-map path links: the parallel view for a page with forms, the capture for rendered PII. */
export function pageLink(scanId: string, p: ScanPage): string | null {
  if (p.status !== 'done' && p.status !== 'redirected') return null;
  if (p.form_count > 0) return `/scanner/scans/${encodeURIComponent(scanId)}?tab=parallel&page=${p.page_no}`;
  if (rendersPii(p)) return `/scanner/scans/${encodeURIComponent(scanId)}?tab=captures&page=${p.page_no}`;
  return null;
}

function Meta({ p, codes, posts }: { p: ScanPage; codes: NumberedRiskFlag[]; posts: number }): JSX.Element {
  if (p.status === 'visiting') return <span className="m">in progress</span>;
  if (p.status === 'queued') return <span className="m">queued</span>;
  if (p.status === 'skipped') return <span className="m"><span className="chip slate">{skipLabel(p.status_reason)}</span></span>;
  if (p.status === 'failed') return <span className="m"><span className="chip rose">failed · {skipLabel(p.status_reason)}</span></span>;
  const what = p.status === 'redirected' ? `redirected to ${pathOf(p.final_url_redacted)}`
    : p.form_count ? `${plural(p.form_count, 'form')} · ${plural(p.field_count, 'field')}` : rendersPii(p) ? 'no form · rendered PII' : 'no form';
  return (
    <span className="m">{what}
      {p.personal_field_count > 0 && <span className="chip amber">PII {p.personal_field_count}</span>}
      {p.critical_field_count > 0 && <span className="chip rose">{p.critical_field_count} crit.</span>}
      {codes.map((r) => <span key={r.code} className={`chip ${flagTone(r)}`} title={flagSentence(r)}>{r.code}</span>)}
      {posts > 0 && <span className="chip teal">{posts} POST blocked</span>}
    </span>
  );
}

/**
 * Crawl map (mockup 12): the tree of discovered pages by `parent_page_no` with status, depth, form and
 * field counts, PII chips, risk codes and the POSTs blocked on each page (from the event feed).
 */
export function CrawlMap({ scanId, pages, events, flags, origin, loaded = true, error }: {
  scanId: string; pages: ScanPage[]; events: ScanEvent[]; flags: NumberedRiskFlag[]; origin: string;
  /** false while the pages list has not loaded yet: a skeleton, never "No pages discovered yet". */
  loaded?: boolean;
  /** the last refusal of the pages list (usePoll retries): shown above the pages already known. */
  error?: Error;
}): JSX.Element {
  const [closed, setClosed] = useState<ReadonlySet<number>>(new Set());
  const posts = new Map<number, number>();
  for (const e of events) if (e.kind === 'request' && e.result === 'blocked' && e.method === 'POST' && e.page_no !== null) posts.set(e.page_no, (posts.get(e.page_no) ?? 0) + 1);
  const toggle = (no: number): void => setClosed((s) => { const n = new Set(s); if (n.has(no)) n.delete(no); else n.add(no); return n; });

  const node = (n: CrawlNode): JSX.Element => {
    const p = n.page;
    const st = statusIcon(p);
    const path = pathOf(p.url_redacted, origin);
    const to = pageLink(scanId, p);
    const open = !closed.has(p.page_no);
    const skipped = p.status === 'skipped' || p.status === 'failed';
    return (
      <div key={p.page_no} style={{ display: 'contents' }}>
        <div className={`node${skipped ? ' skipped' : ''}`}>
          {n.children.length
            ? <button className="tg" type="button" aria-expanded={open} aria-label={`${open ? 'Collapse' : 'Expand'} ${path}`} onClick={() => toggle(p.page_no)}>▶</button>
            : <span />}
          <span className={`st ${st.cls}`} title={st.label}>{st.cls === 'run' ? <i /> : st.glyph}</span>
          <span className="p" title={path}>{to ? <Link to={to}>{path}</Link> : path}
            {!skipped && <span className="d">{p.depth === 0 ? 'start' : `d${p.depth}`}</span>}</span>
          <Meta p={p} codes={flags.filter((r) => r.page_no === p.page_no)} posts={posts.get(p.page_no) ?? 0} />
        </div>
        {n.children.length > 0 && <div className={`kids${open ? '' : ' closed'}`}>{n.children.map(node)}</div>}
      </div>
    );
  };

  return (
    <section className="card" aria-labelledby="mapT">
      <div className="card-head"><div className="card-title" id="mapT">Crawl map</div>
        <div className="legend"><span><i style={{ background: 'var(--emerald)' }} />visited</span><span><i style={{ background: 'var(--teal)' }} />in progress</span>
          <span><i style={{ background: 'transparent', border: '1.5px dashed var(--border-strong)' }} />queued</span>
          <span><i style={{ background: 'var(--surface-3)', border: '1px solid var(--border-strong)' }} />skipped</span></div></div>
      {error && <Refused what="pages" error={error} />}
      <div className="tree">{pages.length ? crawlTree(pages).map(node)
        : error ? null
          : !loaded ? <div className="skeleton" style={{ height: 120 }} aria-hidden="true" />
            : <div className="empty-live">No pages discovered yet.</div>}</div>
      <div className="tree-foot"><span>Paths with forms open in the parallel view; pages that render personal data open their masked capture.</span>
        {flags.length > 0 && <span>Risk flags: {flags.map((r, i) => (
          <span key={r.code}>{i > 0 && ' · '}<b style={{ color: `var(--${flagTone(r)})` }}>{r.code}</b> {flagSentence(r)}</span>))}</span>}
      </div>
    </section>
  );
}
