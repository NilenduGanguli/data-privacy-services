/** Shield with a cross (mockup 12): the no-submission statement's icon. */
export function NoSubmitIcon(): JSX.Element {
  return (
    <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} aria-hidden="true">
      <path d="M12 3l8 3v6c0 5-3.5 8-8 9-4.5-1-8-4-8-9V6l8-3z" /><path d="M9 9l6 6M15 9l-6 6" />
    </svg>
  );
}

const D4 = 'There is no submission path in DPS (decision D4); every scan\'s evidence pack records submissions: 0 and the measured non-GET count.';

/**
 * "DPS never submits forms." with the scan's blocked-POST count (from GET /scans/{id}) and a
 * constant 0: there is no submit option anywhere, so 0 is a fact, not a measurement of this page.
 */
export function NoSubmitBanner({ blocked }: { blocked: number }): JSX.Element {
  return (
    <section className="nosubmit" aria-labelledby="nsT">
      <div className="ns-icon"><NoSubmitIcon /></div>
      <div className="ns-body"><div className="ns-t" id="nsT">DPS never submits forms.</div>
        <p>There is no submit option in the API or in this console. The browser worker fills fields in the real page and captures them; it never presses a button. Every POST and every non-GET navigation from a filled page is blocked by request interception.</p></div>
      <div className="ns-stats">
        <div className="ns-stat"><div className="v" style={{ color: 'var(--teal)' }}>{blocked}</div><div className="l">POST requests blocked in this scan</div></div>
        <div className="ns-stat" title={D4}><div className="v" style={{ color: 'var(--emerald)' }}>0</div><div className="l">forms submitted, in any scan</div></div>
      </div>
    </section>
  );
}
