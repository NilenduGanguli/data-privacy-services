import { useState } from 'react';
import { useAuth } from '../../hooks/useAuth';
import { api, errorText, saveBlob } from '../../lib/api';
import type { ScanMode, ScanPage } from '../../lib/types';
import { ArtefactImage } from './ArtefactImage';
import { entityList, hostOf, pathOf } from './format';

function MapButton({ scanId, page }: { scanId: string; page: ScanPage }): JSX.Element | null {
  const auth = useAuth();
  const [err, setErr] = useState<string | null>(null);
  const id = page.artefacts.capture_json;
  if (!id) return null;
  const download = async (): Promise<void> => {
    setErr(null);
    try { saveBlob(await api.scans.artefact(auth, id), `dps-scan-${scanId}-page-${page.page_no}-reveal-map.json`); } catch (e) { setErr(errorText(e)); }
  };
  return (
    <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap', marginTop: 12 }}>
      <button type="button" className="btn btn-sm" onClick={() => void download()}>Download reversible map</button>
      <span className="hint">Reveal with POST /api/v1/reveal and this scan’s key material.</span>
      {err && <span className="err" role="alert">{err}</span>}
    </div>
  );
}

/**
 * Captures tab (mockup 04): one card per page that renders personal data. `captured` shows the masked
 * capture the worker stored (originals never leave worker memory); `withheld` says nothing was stored
 * because a value could not be masked (the capture fails closed).
 */
export function CapturesTab({ scanId, pages, selected, mode, origin }: { scanId: string; pages: ScanPage[]; selected: number | null; mode: ScanMode; origin: string }): JSX.Element {
  const shown = pages.filter((p) => p.capture_status === 'captured' || p.capture_status === 'withheld')
    .sort((a, b) => (a.page_no === selected ? -1 : b.page_no === selected ? 1 : a.page_no - b.page_no));
  if (!shown.length) {
    return <section className="card"><div className="card-title">Captures</div><p className="hint">No page of this scan rendered personal data that needed a masked capture so far.</p></section>;
  }
  return (
    <>{shown.map((p) => {
      const path = pathOf(p.url_redacted, origin);
      const entities = Object.keys(p.rendered_findings);
      return (
        <section key={p.page_no} className="card" id={`capture-${p.page_no}`} aria-labelledby={`capT-${p.page_no}`} style={p.page_no === selected ? { boxShadow: 'var(--ring)' } : undefined}>
          <div className="card-head"><div className="card-title" id={`capT-${p.page_no}`}>Capture · <span className="mono">{path}</span> · rendered personal data</div>
            {p.capture_status === 'captured'
              ? <span className={`chip ${mode === 'obfuscate' ? 'teal' : 'violet'}`}>{mode === 'obfuscate' ? 'obfuscated · reversible map' : 'synthetic replacement'}</span>
              : <span className="chip amber">withheld</span>}</div>
          {p.capture_status === 'captured' ? (
            <>
              <p className="cap-sub">{entities.length ? `${entityList(entities)} rendered on this page were replaced in the screenshot and the DOM text inside the worker, before anything was stored.` : 'Personal data rendered on this page was replaced inside the worker before anything was stored.'} DPS keeps no copy of the original capture.</p>
              <div className="frame cap-frame">
                <div className="bar-url"><i /><i /><i /><span className="mono">{hostOf(p.url_redacted)}…{path}</span><span className="chip teal">masked capture · exported</span></div>
                <ArtefactImage id={p.artefacts.masked_capture} alt={`Masked capture of ${path}`} />
              </div>
              <MapButton scanId={scanId} page={p} />
            </>
          ) : (
            <div className="callout amber">Withheld — a value could not be masked, so nothing was stored.</div>
          )}
        </section>
      );
    })}</>
  );
}
