import { useState } from 'react';
import { Link, useParams, useSearchParams } from 'react-router-dom';
import { CapturesTab } from '../components/scanner/CapturesTab';
import { FieldInventory, ScanInventory } from '../components/scanner/FieldInventory';
import { flagTone, hostOf, pathOf, plural } from '../components/scanner/format';
import { NoSubmitIcon } from '../components/scanner/NoSubmitBanner';
import { ParallelView, type DetailView } from '../components/scanner/ParallelView';
import { RiskFlags } from '../components/scanner/RiskFlags';
import { ScanCrumb, scanLabel, ScanStepper } from '../components/scanner/ScanChrome';
import { useAuth } from '../hooks/useAuth';
import { usePoll } from '../hooks/usePoll';
import { api, ApiError, errorText, saveBlob } from '../lib/api';
import { isTerminal } from '../lib/jobs';
import { fetchAllPages } from '../lib/scans';
import type { InventoryRow, NumberedRiskFlag, ScanDetail as Detail, ScanMode, ScanPage, ScanPageDetail } from '../lib/types';

export type DetailTab = 'parallel' | 'inventory' | 'captures';
export interface DetailState {
  scanId: string; detail: Detail | undefined; pages: ScanPage[] | undefined; pageNo: number | null; pageDetail: ScanPageDetail | undefined;
  inventory: { items: InventoryRow[]; risk_flags: NumberedRiskFlag[] } | undefined; tab: DetailTab; side: ScanMode; view: DetailView;
  notice?: string | null; error?: Error;
}

const TABS: [DetailTab, string][] = [['parallel', 'Parallel view'], ['inventory', 'Inventory'], ['captures', 'Captures']];
const parseTab = (v: string | null): DetailTab => (v === 'inventory' || v === 'captures' ? v : 'parallel');
const visited = (p: ScanPage): boolean => p.status === 'done' || p.status === 'redirected';
const rendersPii = (p: ScanPage): boolean =>
  Object.values(p.rendered_findings).some((n) => n > 0) || p.capture_status === 'captured' || p.capture_status === 'withheld';
const withForms = (pages: ScanPage[]): ScanPage[] => pages.filter((p) => visited(p) && p.form_count > 0);

/** The page the parallel view opens on: the first visited page with forms. */
export function defaultPage(pages: ScanPage[] | undefined): number | null {
  return withForms(pages ?? [])[0]?.page_no ?? null;
}

function sentence(d: Detail): string {
  const s = d.summary;
  const flags = plural(s.risk_flags.length, 'risk flag');
  const ref = d.scan.spec?.auth?.storage_state_ref as string | null | undefined;
  const auth = ref ? `auth: storage state ${ref}` : 'auth: none (public pages)';
  const head: Record<string, string> = {
    queued: 'Queued', running: `Crawl running · ${s.pages_visited} of ${Math.max(s.pages_discovered, s.pages_visited)} pages`,
    succeeded: `Crawl finished · ${s.pages_visited} pages crawled`, canceled: `Crawl stopped · ${s.pages_visited} pages crawled`,
  };
  const first = head[d.scan.state] ?? `Crawl ${d.scan.state} · ${s.pages_visited} pages crawled`;
  return `${first} · ${plural(s.forms, 'form')} · ${plural(s.fields, 'field')} · ${s.personal_fields} classified as personal data · ${flags} · ${auth}`;
}

function PagesCard({ s, pages, flags }: { s: DetailState; pages: ScanPage[]; flags: NumberedRiskFlag[] }): JSX.Element {
  const forms = withForms(pages);
  const rendered = pages.filter((p) => visited(p) && rendersPii(p));
  const queued = pages.filter((p) => p.status === 'queued' || p.status === 'visiting').map((p) => pathOf(p.url_redacted));
  const origin = (s.detail?.scan.spec?.url_origin as string | undefined) ?? '';
  const q = (tab: DetailTab, n: number): string => `?${new URLSearchParams({ tab, page: String(n), fill: s.side })}`;
  const codes = (n: number): NumberedRiskFlag[] => flags.filter((r) => r.page_no === n);
  return (
    <div className="card crawl">
      <div className="card-head"><div className="card-title">Pages with forms</div><span className="chip slate">{forms.length}{isTerminal(s.detail?.scan) ? '' : ' so far'}</span></div>
      {forms.map((p) => (
        <Link key={p.page_no} to={q('parallel', p.page_no)} className={p.page_no === s.pageNo ? 'on' : undefined} aria-current={p.page_no === s.pageNo ? 'true' : undefined}>
          <span className="pth">{pathOf(p.url_redacted, origin)}</span>
          <span className="cnt">{p.personal_field_count > 0 && <span className="chip amber">PII {p.personal_field_count}</span>}
            {p.critical_field_count > 0 && <span className="chip rose">{p.critical_field_count} crit.</span>}
            {p.personal_field_count === 0 && <span className="chip slate">{plural(p.field_count, 'field')}</span>}</span>
        </Link>
      ))}
      {!forms.length && <div className="more">No page with forms has been filled yet.</div>}
      {queued.length > 0 && <div className="more">Queued: {queued.slice(0, 5).join(', ')}{queued.length > 5 ? ` and ${queued.length - 5} more` : ''}</div>}
      <div className="card-head sub-head"><div className="card-title">Rendered personal data</div><span className="chip slate">{plural(rendered.length, 'page')}</span></div>
      {rendered.map((p) => (
        <Link key={p.page_no} to={q('captures', p.page_no)}>
          <span className="pth">{pathOf(p.url_redacted, origin)}</span>
          <span className="cnt">{codes(p.page_no).map((r) => <span key={r.code} className={`chip ${flagTone(r)}`}>{r.code}</span>)}
            <span className={`chip ${p.capture_status === 'withheld' ? 'amber' : 'slate'}`}>{p.capture_status === 'withheld' ? 'withheld' : 'capture'}</span></span>
        </Link>
      ))}
      {!rendered.length && <div className="more">None so far.</div>}
      <div className="card-head sub-head"><div className="card-title">Risk flags</div><span className={`chip ${flags.length ? 'rose' : 'slate'}`}>{flags.length}</span></div>
      <RiskFlags flags={flags} />
    </div>
  );
}

/** Scan detail body (mockup 04), pure: everything it shows comes in `s` (the container polls). */
export function ScanDetailBody({ s, onTab, onSide, onView, onExport }: {
  s: DetailState; onTab: (t: DetailTab) => void; onSide: (m: ScanMode) => void; onView: (v: DetailView) => void;
  onExport: (what: 'inventory' | 'export', format: 'csv' | 'json') => void;
}): JSX.Element {
  const d = s.detail;
  const crumbOrigin = (d?.scan.spec?.url_origin as string | undefined) ?? '';
  if (!d) {
    const notFound = s.error instanceof ApiError && s.error.status === 404;
    return (
      <main className="page scanner-page">
        <ScanCrumb>Scan {scanLabel(s.scanId)}</ScanCrumb>
        {notFound ? <div className="callout rose">This scan was not found for the tenant.</div>
          : s.error ? <div className="callout amber" role="alert">The scan could not be loaded: {errorText(s.error)}. Retrying.</div>
            : <section className="card"><div className="card-title">Loading the scan…</div><div className="skeleton" style={{ height: 120, marginTop: 12 }} aria-hidden="true" /></section>}
      </main>
    );
  }
  const sum = d.summary;
  const running = !isTerminal(d.scan);
  const pages = s.pages ?? [];
  const exportReady = !!sum.export_artefacts.json;
  const page = s.pageDetail && s.pageDetail.page.page_no === s.pageNo ? s.pageDetail : undefined;
  return (
    <main className="page scanner-page">
      <ScanCrumb><span className="mono">{crumbOrigin || '—'}</span></ScanCrumb>
      <div className="page-head">
        <div><h1>Scan {scanLabel(s.scanId)} · {hostOf(crumbOrigin) || '—'}</h1>
          <p>{sentence(d)}</p>
          <p className="nosub-line"><NoSubmitIcon /><span><b>DPS never submits forms.</b> The browser worker fills and captures each form; it never presses a button, and every POST from a filled page is blocked.</span></p></div>
        <div className="head-actions">
          <Link className="btn" to={`/scanner/new?scan=${encodeURIComponent(s.scanId)}`}>Live crawl</Link>
          <button type="button" className="btn" onClick={() => onExport('inventory', 'csv')}>{running ? 'Export inventory so far' : 'Export inventory'}</button>
          <button type="button" className="btn" disabled={!exportReady} title={exportReady ? undefined : 'The persona export is ready when the scan has finished'} onClick={() => onExport('export', 'json')}>Export personas</button>
          {sum.evidence_id
            ? <Link className="btn btn-primary" to={`/reports/evidence/${encodeURIComponent(sum.evidence_id)}`}>Evidence pack</Link>
            : <span className="btn btn-primary" aria-disabled="true" title="The evidence pack is written when the scan finishes" style={{ opacity: 0.55, cursor: 'not-allowed' }}>Evidence pack</span>}
        </div>
      </div>
      {s.notice && <div className="callout amber" role="alert">{s.notice}</div>}
      <ScanStepper scan={d.scan} summary={sum} current="parallel" linkSetup={`/scanner/new?scan=${encodeURIComponent(s.scanId)}`} />
      <div className="seg tabs" role="tablist" aria-label="Scan views">
        {TABS.map(([id, label]) => (
          <button key={id} type="button" role="tab" aria-selected={s.tab === id} className={s.tab === id ? 'on' : undefined} onClick={() => onTab(id)}>{label}</button>
        ))}
      </div>

      {s.tab === 'parallel' && (
        <div className="scan">
          <PagesCard s={s} pages={pages} flags={sum.risk_flags} />
          <div className="right">
            {page && page.page.form_count > 0 ? (
              <>
                <ParallelView page={page.page} fields={page.fields} side={s.side} onSide={onSide} view={s.view} onView={onView} origin={crumbOrigin} persona={sum.display_persona} />
                <FieldInventory path={pathOf(page.page.url_redacted, crumbOrigin)} fields={page.fields} flags={sum.risk_flags} side={s.side} />
              </>
            ) : page ? (
              <section className="card"><div className="card-title">{pathOf(page.page.url_redacted, crumbOrigin)} has no form</div>
                <p className="hint">{rendersPii(page.page) ? 'It renders personal data: open its masked capture in the Captures tab.' : 'Nothing on this page needed filling.'}</p></section>
            ) : s.pageNo ? (
              <section className="card"><div className="card-title">Loading the page…</div><div className="skeleton" style={{ height: 240, marginTop: 12 }} aria-hidden="true" /></section>
            ) : (
              <section className="card"><div className="card-title">Parallel view</div>
                <p className="hint">{running ? 'Pages appear here as soon as the browser worker has filled and captured them.' : 'This scan found no page with a form.'}</p></section>
            )}
          </div>
        </div>
      )}
      {s.tab === 'inventory' && (s.inventory
        ? <ScanInventory rows={s.inventory.items} flags={s.inventory.risk_flags} onDownload={(f) => onExport('inventory', f)} />
        : <section className="card"><div className="card-title">Inventory · whole scan</div><div className="skeleton" style={{ height: 200, marginTop: 12 }} aria-hidden="true" /></section>)}
      {s.tab === 'captures' && <CapturesTab scanId={s.scanId} pages={pages} selected={s.pageNo} mode={sum.mode} origin={crumbOrigin} />}
    </main>
  );
}

/**
 * UI scanner · scan detail (mockup 04; `/scanner/scans/:scanId?tab=parallel|inventory|captures&page=&fill=`).
 * Polls the scan, its pages, the selected page and (on the Inventory tab) the inventory every 2 s while
 * the scan is not terminal, with one final fetch after it ends.
 */
export function ScanDetail(): JSX.Element {
  const { scanId = '' } = useParams();
  const [params, setParams] = useSearchParams();
  const auth = useAuth();
  const [view, setView] = useState<DetailView>('screens');
  const [notice, setNotice] = useState<string | null>(null);
  const tab = parseTab(params.get('tab'));

  const detail = usePoll(() => api.scans.get(auth, scanId), 2000, (d) => !!d && !isTerminal(d.scan), [scanId, auth]);
  const running = !!detail.data && !isTerminal(detail.data.scan);
  const pages = usePoll(() => fetchAllPages(auth, scanId), 2000, () => running, [scanId, running, auth]);
  const pageNo = Number(params.get('page')) || defaultPage(pages.data);
  const pageDetail = usePoll(() => (pageNo ? api.scans.page(auth, scanId, pageNo) : Promise.resolve(undefined)), 2000, () => running, [scanId, pageNo, running, auth]);
  const inventory = usePoll(() => (tab === 'inventory' ? api.scans.inventory(auth, scanId) : Promise.resolve(undefined)), 2000,
    () => running && tab === 'inventory', [scanId, tab, running, auth]);
  const fill = params.get('fill');
  const side: ScanMode = fill === 'obfuscate' || fill === 'synthesize' ? fill : detail.data?.summary.mode ?? 'synthesize';

  const set = (patch: Record<string, string>): void => setParams((p) => {
    const next = new URLSearchParams(p);
    for (const [k, v] of Object.entries(patch)) next.set(k, v);
    return next;
  }, { replace: true });

  async function onExport(what: 'inventory' | 'export', format: 'csv' | 'json'): Promise<void> {
    setNotice(null);
    try {
      saveBlob(await api.scans.download(auth, scanId, what, format), `dps-scan-${scanId}-${what === 'export' ? 'personas' : 'inventory'}.${format}`);
    } catch (e) { setNotice(`The download failed: ${errorText(e)}`); }
  }

  const s: DetailState = {
    scanId, detail: detail.data, pages: pages.data, pageNo, pageDetail: pageDetail.data, inventory: inventory.data, tab, side, view,
    notice, error: detail.error,
  };
  return <ScanDetailBody s={s} onTab={(t) => set({ tab: t })} onSide={(m) => set({ fill: m })} onView={setView} onExport={(w, f) => void onExport(w, f)} />;
}
