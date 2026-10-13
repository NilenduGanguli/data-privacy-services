import { useEffect, useMemo, useRef } from 'react';
import { saveText } from '../../lib/download';
import { fmtClock } from '../../lib/jobs';
import { eventsJsonl } from '../../lib/scans';
import type { ScanEvent, ScanPage } from '../../lib/types';
import { eventNote, eventResultTone, eventVerb, pathOf } from './format';

const SHOWN = 13;

/**
 * Navigation log (mockup 12): the 13 newest events, newest first, value-free (redacted targets, codes,
 * counts). New rows flash once unless the user prefers reduced motion (CSS). "Download log as JSONL"
 * writes the events already fetched; nothing is re-requested.
 */
export function NavLog({ scanId, events, pages, origin }: { scanId: string; events: ScanEvent[]; pages: ScanPage[]; origin: string }): JSX.Element {
  const byNo = useMemo(() => new Map(pages.map((p) => [p.page_no, p])), [pages]);
  const seen = useRef(0);
  const before = seen.current;
  const latest = events.length ? events[events.length - 1].seq : 0;
  useEffect(() => { seen.current = Math.max(seen.current, latest); }, [latest]);
  const rows = events.slice(-SHOWN).reverse();
  return (
    <section className="card" aria-labelledby="logT">
      <div className="card-head"><div className="card-title" id="logT">Navigation log</div>
        <span className="faint" style={{ fontSize: 12 }}>newest first · local time · query values masked · every navigation is audited</span></div>
      <div className="log" aria-live="polite">
        <div className="lr head"><span>Time</span><span>Event</span><span className="stc">Result</span><span>Target</span><span>Note</span></div>
        {rows.length === 0 && <div className="empty-live">No navigation yet.</div>}
        {rows.map((e) => {
          const tone = eventResultTone(e.result);
          const target = e.target ? pathOf(e.target, origin) : e.page_no !== null ? pathOf(byNo.get(e.page_no)?.url_redacted, origin) : '';
          const note = eventNote(e, byNo);
          return (
            <div key={e.seq} data-seq={e.seq} className={`lr${before > 0 && e.seq > before ? ' fresh' : ''}`}>
              <span className="t">{fmtClock(e.ts)}</span>
              <span className={`chip ${e.kind === 'fill' ? 'violet' : 'slate'}`}>{eventVerb(e)}</span>
              <span className="stc">{tone ? <span className={`chip ${tone}`}>{e.result}</span> : <span>{e.result}</span>}</span>
              <span className="u" title={target}>{target}</span>
              <span className="n" title={note}>{note}</span>
            </div>
          );
        })}
      </div>
      <div className="log-foot">
        <span className="faint" style={{ fontSize: 12 }}>Showing {rows.length} of {events.length} entries</span>
        <button className="btn btn-sm" type="button" disabled={!events.length}
          onClick={() => saveText(eventsJsonl(events), `dps-scan-${scanId}-navigation.jsonl`, 'application/x-ndjson')}>Download log as JSONL</button>
      </div>
    </section>
  );
}
