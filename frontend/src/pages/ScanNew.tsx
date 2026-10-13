import { useEffect, useRef, useState } from 'react';
import { Link, useNavigate, useSearchParams } from 'react-router-dom';
import { CrawlMap } from '../components/scanner/CrawlMap';
import { CrawlProgress } from '../components/scanner/CrawlProgress';
import { hostOf, originOf } from '../components/scanner/format';
import { NavLog } from '../components/scanner/NavLog';
import { NoSubmitBanner } from '../components/scanner/NoSubmitBanner';
import { PersonaPreview } from '../components/scanner/PersonaPreview';
import { ScanCrumb, scanLabel, ScanStepper } from '../components/scanner/ScanChrome';
import { useAuth } from '../hooks/useAuth';
import { usePoll } from '../hooks/usePoll';
import { useSettings } from '../hooks/useSettings';
import { api, ApiError, errorText } from '../lib/api';
import { saveText } from '../lib/download';
import { fmtWhen, isTerminal, shortId } from '../lib/jobs';
import {
  buildSpec, checkSession, configError, configFromSpec, createErrorText, DEFAULT_CONFIG, DEMO_SESSION, denyPathError, fetchAllPages,
  fetchNewEvents, LOCALES, MAX_DEPTH, MAX_PAGES, MAX_PERSONAS, mergeEvents, requestJson, START_URL_CODES, type ScanConfig, type SessionVerdict,
} from '../lib/scans';
import type { ScanCreated, ScanDetail, ScanEvent, ScanPage, SynthesisSeed } from '../lib/types';

function newIdempotencyKey(): string {
  const c = globalThis.crypto as Crypto | undefined;
  if (c?.randomUUID) return c.randomUUID();
  return `scan-${Date.now().toString(36)}-${Math.random().toString(36).slice(2)}`;
}

function Switch({ label, sub, checked, onChange, disabled }: { label: string; sub: string; checked: boolean; onChange?: (v: boolean) => void; disabled?: boolean }): JSX.Element {
  return (
    <div className="sw"><div><div>{label}</div><div className="s">{sub}</div></div>
      <button className="switch" type="button" role="switch" aria-checked={checked} aria-label={label} aria-disabled={disabled || undefined}
        onClick={() => { if (!disabled && onChange) onChange(!checked); }} /></div>
  );
}

/** The request body with its keys and values coloured as in mockup 12 (rendered as text, never HTML). */
function JsonView({ text }: { text: string }): JSX.Element {
  const parts: JSX.Element[] = [];
  const re = /("(?:[^"\\]|\\.)*")(\s*:)?/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) parts.push(<span key={i++}>{text.slice(last, m.index)}</span>);
    parts.push(<span key={i++} className={m[2] ? 'k' : 's'}>{m[1]}</span>);
    if (m[2]) parts.push(<span key={i++}>{m[2]}</span>);
    last = m.index + m[0].length;
  }
  if (last < text.length) parts.push(<span key={i++} className="s">{text.slice(last)}</span>);
  return <>{parts}</>;
}

/**
 * The live crawl column (mockup 12): progress, crawl map and navigation log of a scan, pure. A refused
 * pages list or event feed is shown as the value-free refusal in its card, a list still loading as a
 * skeleton; the empty sentences are kept for a list that loaded empty.
 */
export function LiveCrawl({ scanId, scan, summary, pages, events, origin }: {
  scanId: string; scan: ScanDetail['scan']; summary: ScanDetail['summary'];
  pages: { data?: ScanPage[]; error?: Error }; events: { data?: ScanEvent[]; error?: Error }; origin: string;
}): JSX.Element {
  return (
    <>
      <CrawlProgress scan={scan} summary={summary} />
      <CrawlMap scanId={scanId} pages={pages.data ?? []} events={events.data ?? []} flags={summary.risk_flags} origin={origin}
        loaded={!!pages.data} error={pages.error} />
      <NavLog scanId={scanId} events={events.data ?? []} pages={pages.data ?? []} origin={origin} loaded={!!events.data} error={events.error} />
    </>
  );
}

/** The once-only key material and seed of a created scan: React state only, gone on reload (contract §14). */
function ScanKeyCard({ created }: { created: ScanCreated }): JSX.Element | null {
  const [stored, setStored] = useState(false);
  const [copied, setCopied] = useState(false);
  const [fingerprint, setFingerprint] = useState<string | null>(null);
  const { key_material: key, synthesis_seed: seed } = created;
  useEffect(() => {
    let alive = true;
    const subtle = globalThis.crypto?.subtle;
    if (!seed || !subtle) return undefined;
    void subtle.digest('SHA-256', new TextEncoder().encode(seed.seed)).then((d) => {
      if (alive) setFingerprint([...new Uint8Array(d).slice(0, 4)].map((b) => b.toString(16).padStart(2, '0')).join(''));
    });
    return () => { alive = false; };
  }, [seed]);
  if (!key && !seed) return null;
  const text = JSON.stringify({ scan_id: created.scan.id, key_material: key, synthesis_seed: seed }, null, 2);
  const copy = async (): Promise<void> => {
    try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 1500); } catch { setCopied(false); }
  };
  return (
    <section className="card keycard scan-key" aria-label="Scan key material">
      <div className="kc-head">
        <div>
          <div className="kc-title">Key material{key && <> · <span className="mono">{key.kid}</span></>}</div>
          <div className="kc-sub">Reverses this scan's tokens and repeats its personas. Shown once — DPS keeps no copy, and this page forgets it on reload.</div>
        </div>
        <div className="rowx">
          <button type="button" className="btn btn-sm" onClick={() => void copy()} disabled={stored}>{copied ? 'Copied' : 'Copy key material'}</button>
          <button type="button" className="btn btn-sm btn-primary" disabled={stored} onClick={() => saveText(text, `dps-scan-${created.scan.id}.key.json`)}>Download</button>
        </div>
      </div>
      {!stored && (
        <div className="kv-row" style={{ marginTop: 10 }}>
          {key && <span className="chip teal">{key.wrapped_dek ? 'key wrapped with your RSA public key' : 'key returned in clear (TLS only)'}</span>}
          {seed && <span>synthesis seed <span className="mono">{fingerprint ? `sha256:${fingerprint}…` : seed.alg}</span></span>}
        </div>
      )}
      <label className="stored" style={{ marginTop: 10 }}>
        <input type="checkbox" checked={stored} onChange={(e) => setStored(e.target.checked)} /> I have stored the key material
      </label>
    </section>
  );
}

/**
 * UI scanner · New scan and live crawl (mockup 12; routes /scanner and /scanner/new?scan=<id>).
 * The setup column is one `ScanConfig` mirrored into the request preview; Start creates the scan with
 * the persona-preview seed, then the live column polls the scan, its pages and its events every 2 s
 * while it is not terminal, with one final fetch after it ends.
 */
export function ScanNew(): JSX.Element {
  const auth = useAuth();
  const { tenant } = useSettings();
  const navigate = useNavigate();
  const [params] = useSearchParams();
  const scanId = params.get('scan') ?? '';

  const [cfg, setCfg] = useState<ScanConfig>(DEFAULT_CONFIG);
  const dirty = useRef(false);
  const hydrated = useRef('');
  const [seed, setSeed] = useState<SynthesisSeed | null>(null);
  const [created, setCreated] = useState<ScanCreated | null>(null);
  const [busy, setBusy] = useState(false);
  const [createErr, setCreateErr] = useState<{ code?: string; text: string } | null>(null);
  const [registeredHost, setRegisteredHost] = useState('');
  const [session, setSession] = useState<SessionVerdict | 'checking' | null>(null);
  const [denyIn, setDenyIn] = useState('');
  const [denyErr, setDenyErr] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const update = (patch: Partial<ScanConfig>): void => { dirty.current = true; setCfg((c) => ({ ...c, ...patch })); };

  // --- polling: the scan, its pages and its events (contract §14: M2's usePoll) ---
  const detail = usePoll(() => (scanId ? api.scans.get(auth, scanId) : Promise.resolve(undefined)), 2000,
    (d) => !!d && !isTerminal(d.scan), [scanId, auth]);
  const running = !!detail.data && !isTerminal(detail.data.scan);
  const pages = usePoll(() => (scanId ? fetchAllPages(auth, scanId) : Promise.resolve(undefined)), 2000, () => running, [scanId, running, auth]);
  const eventsRef = useRef<{ id: string; items: ScanEvent[] }>({ id: '', items: [] });
  const events = usePoll(async () => {
    if (!scanId) return undefined;
    const prev = eventsRef.current.id === scanId ? eventsRef.current.items : [];
    const next = await fetchNewEvents(auth, scanId, prev.length ? prev[prev.length - 1].seq : 0);
    eventsRef.current = { id: scanId, items: mergeEvents(prev, next) };
    return eventsRef.current.items;
  }, 2000, () => running, [scanId, running, auth]);
  const recent = usePoll(() => (scanId ? Promise.resolve(undefined) : api.scans.list(auth, { limit: 6 })), 2000, () => false, [scanId, auth]);

  // a scan opened by link: show its settings (once, unless the operator already edited the form)
  useEffect(() => {
    const d = detail.data;
    if (!d || dirty.current || hydrated.current === scanId) return;
    hydrated.current = scanId;
    setCfg(configFromSpec(d.scan.spec));
  }, [detail.data, scanId]);

  const scan = detail.data?.scan;
  const summary = detail.data?.summary;
  const origin = (scan?.spec?.url_origin as string | undefined) ?? originOf(cfg.url);
  const host = hostOf(scan?.spec?.url_origin as string | undefined) || hostOf(cfg.url) || 'new target';
  const ready = summary?.pages_filled ?? 0;
  const tenantName = tenant || 'default';

  async function start(): Promise<void> {
    const problem = configError(cfg);
    if (problem) { setCreateErr({ text: problem }); return; }
    setBusy(true);
    setCreateErr(null);
    try {
      if (scanId && scan && !isTerminal(scan)) await api.scans.cancel(auth, scanId).catch(() => undefined);
      const out = await api.scans.create(auth, buildSpec(cfg, seed), newIdempotencyKey());
      setCreated(out);
      setRegisteredHost(hostOf(cfg.url));
      dirty.current = false;
      hydrated.current = out.scan.id;
      navigate(`/scanner/new?scan=${encodeURIComponent(out.scan.id)}`);
    } catch (e) {
      setCreateErr({ code: e instanceof ApiError ? e.code : undefined, text: createErrorText(e) });
      setRegisteredHost('');
    } finally {
      setBusy(false);
    }
  }

  async function stop(): Promise<void> {
    if (!scanId) return;
    try { await api.scans.cancel(auth, scanId); } catch (e) { setCreateErr({ text: errorText(e) }); }
    detail.refresh();
  }

  async function check(): Promise<void> {
    setSession('checking');
    try { setSession(await checkSession(auth, cfg)); } catch (e) { setSession({ ok: false, text: `✗ ${createErrorText(e)}` }); }
  }

  function addDeny(e: React.FormEvent): void {
    e.preventDefault();
    const v = denyIn.trim();
    const err = denyPathError(v, cfg.deny);
    setDenyErr(err);
    if (err) return;
    update({ deny: [...cfg.deny, v] });
    setDenyIn('');
  }

  async function copyRequest(): Promise<void> {
    try { await navigator.clipboard.writeText(requestJson(cfg)); setCopied(true); window.setTimeout(() => setCopied(false), 1400); } catch { setCopied(false); }
  }

  const hostErr = createErr?.code && START_URL_CODES.includes(createErr.code) ? createErr.text : null;
  const otherErr = createErr && !hostErr ? createErr.text : null;
  const notFound = detail.error instanceof ApiError && detail.error.status === 404;

  return (
    <main className="page scanner-page">
      <ScanCrumb>New scan{scanId && <> · {scanLabel(scanId)}</>}</ScanCrumb>
      <div className="page-head">
        <div><h1>New scan · {host}</h1>
          <p>Set the target, scope and fill mode, then crawl. Pages move through crawl, classify and fill as they are found; open the parallel view at any time.</p></div>
        <div className="head-actions">
          <button className="btn" type="button" disabled={!running} onClick={() => void stop()}>{scan?.state === 'canceled' ? 'Crawl stopped' : 'Stop crawl'}</button>
          {scanId && ready > 0
            ? <Link className="btn btn-primary" to={`/scanner/scans/${encodeURIComponent(scanId)}`}>Open parallel view <span style={{ fontWeight: 600 }}>· {ready} {ready === 1 ? 'page' : 'pages'} ready</span></Link>
            : <span className="btn btn-primary" aria-disabled="true" style={{ opacity: 0.55, cursor: 'not-allowed' }}>Open parallel view</span>}
        </div>
      </div>

      <NoSubmitBanner blocked={summary?.blocked_requests.POST ?? 0} />
      <ScanStepper scan={scan} summary={summary} current={scanId ? 'crawl' : 'setup'} />

      <div className="setup">
        <div className="col">
          <section className="card" aria-labelledby="tgtT">
            <div className="card-head"><div className="card-title" id="tgtT">Target</div><span className="chip slate">{tenantName}</span></div>
            <div className="field"><label htmlFor="scan-url">Start URL</label>
              <input className="input mono" id="scan-url" value={cfg.url} spellCheck={false} autoComplete="off" aria-describedby="scan-url-err"
                onChange={(e) => { update({ url: e.target.value }); if (createErr?.code && START_URL_CODES.includes(createErr.code)) setCreateErr(null); }} />
              <div className="urlrow">
                {registeredHost && registeredHost === hostOf(cfg.url)
                  ? <><span className="chip emerald">registered host</span><span className="hint">{registeredHost} is registered for {tenantName}.</span></>
                  : <span className="hint">Only hosts registered for the tenant can be scanned (Admin › UI-scanner hosts); locally: localhost, 127.0.0.1 and demo-site.</span>}
              </div>
              <div className="err" id="scan-url-err" role="alert">{hostErr}</div></div>
          </section>

          <section className="card" aria-labelledby="scopeT">
            <div className="card-head"><div className="card-title" id="scopeT">Scope</div></div>
            <div className="two-in">
              <div className="field"><label htmlFor="scan-max-pages">Max pages</label>
                <input className="input" id="scan-max-pages" type="number" min={1} max={MAX_PAGES} value={cfg.maxPages} onChange={(e) => update({ maxPages: Number(e.target.value) || 0 })} /></div>
              <div className="field"><label htmlFor="scan-max-depth">Max depth</label>
                <input className="input" id="scan-max-depth" type="number" min={0} max={MAX_DEPTH} value={cfg.maxDepth} onChange={(e) => update({ maxDepth: Number(e.target.value) || 0 })} /></div>
            </div>
            <div className="field"><span className="lbl" id="denyLbl">Never visit</span>
              <div className="deny" aria-labelledby="denyLbl">
                {cfg.deny.map((p) => (
                  <span key={p} className="chip slate">{p}<button type="button" aria-label={`Remove ${p} from the deny list`} onClick={() => update({ deny: cfg.deny.filter((x) => x !== p) })}>×</button></span>
                ))}
                {!cfg.deny.length && <span className="hint">No paths excluded. Add /logout so the crawl keeps its session.</span>}
              </div>
              <form className="addrow" noValidate onSubmit={addDeny}>
                <input className="input" placeholder="/payments" aria-label="Path to add to the deny list" aria-describedby="denyErr" autoComplete="off" spellCheck={false}
                  value={denyIn} onChange={(e) => setDenyIn(e.target.value)} />
                <button className="btn btn-sm" type="submit">Add path</button>
              </form>
              <div className="err" id="denyErr" role="alert">{denyErr}</div></div>
            <Switch label="Same origin only" sub="Links to other hosts are listed, never opened" checked={cfg.sameOrigin} onChange={(v) => update({ sameOrigin: v })} />
            {!cfg.sameOrigin && <div className="callout amber" style={{ fontSize: 12, margin: '2px 0 8px' }}>The crawl may now open other hosts, but only those registered for {tenantName}. Anything else is still refused.</div>}
            <Switch label="Same-origin iframes" sub="Forms inside embedded frames" checked={cfg.iframes} onChange={(v) => update({ iframes: v })} />
            <Switch label="Open shadow DOM" sub="Web components with open roots" checked={cfg.shadowDom} onChange={(v) => update({ shadowDom: v })} />
          </section>

          <section className="card" aria-labelledby="authT">
            <div className="card-head"><div className="card-title" id="authT">Authentication</div></div>
            <div className="field"><label htmlFor="scan-session">Stored session</label>
              <input className="input mono" id="scan-session" list="scan-sessions" value={cfg.session} placeholder="none · public pages only" spellCheck={false} autoComplete="off"
                onChange={(e) => { update({ session: e.target.value }); setSession(null); }} />
              <datalist id="scan-sessions"><option value={DEMO_SESSION} /></datalist>
              <div className="hint">{cfg.session.trim()
                ? `Storage state ${cfg.session.trim()}. DPS loads it into worker memory for this scan only and never sees a password.`
                : 'Without a session the crawl sees only public pages; signed-in pages redirect to the sign-in page.'}</div></div>
            <div style={{ display: 'flex', gap: 10, alignItems: 'center', flexWrap: 'wrap' }}>
              <button className="btn btn-sm" type="button" disabled={session === 'checking' || !cfg.url.trim()} onClick={() => void check()}>Check session</button>
              {cfg.session.trim() && <button className="btn btn-sm btn-ghost" type="button" onClick={() => { update({ session: '' }); setSession(null); }}>none · public pages only</button>}
              <span className={`ok-line${session === 'checking' ? ' wait' : session && !session.ok ? ' bad' : ''}`} role="status">
                {session === 'checking' ? 'Checking with a one-page probe scan…' : session?.text}</span>
            </div>
          </section>

          <section className="card" aria-labelledby="modeT">
            <div className="card-head"><div className="card-title" id="modeT">Fill mode</div>
              <span className={`chip ${cfg.mode === 'synthesize' ? 'violet' : 'teal'}`}>{cfg.mode === 'synthesize' ? 'synthetic' : 'reversible tokens'}</span></div>
            <div className="seg full" role="group" aria-label="Fill mode">
              <button type="button" className={cfg.mode === 'synthesize' ? 'on' : undefined} aria-pressed={cfg.mode === 'synthesize'} onClick={() => update({ mode: 'synthesize' })}>Synthesize persona fill</button>
              <button type="button" className={cfg.mode === 'obfuscate' ? 'on' : undefined} aria-pressed={cfg.mode === 'obfuscate'} onClick={() => update({ mode: 'obfuscate' })}>Obfuscate captures</button>
            </div>
            {cfg.mode === 'synthesize' ? (
              <div>
                <div className="two-in">
                  <div className="field"><label htmlFor="scan-personas">Personas</label>
                    <input className="input" id="scan-personas" type="number" min={1} max={MAX_PERSONAS} value={cfg.personas} onChange={(e) => update({ personas: Number(e.target.value) || 0 })} /></div>
                  <div className="field"><label htmlFor="scan-locale">Locale</label>
                    <select className="input" id="scan-locale" value={cfg.locale} onChange={(e) => update({ locale: e.target.value })}>
                      {LOCALES.map(([v, l]) => <option key={v} value={v}>{l}</option>)}</select></div>
                </div>
                <PersonaPreview locale={cfg.locale} seed={seed} onSeed={setSeed} />
              </div>
            ) : (
              <div>
                <p className="hint" style={{ fontSize: 12.5, margin: '0 0 10px', color: 'var(--ink-2)' }}>Fields are filled with reversible tokens, and personal data rendered on pages is replaced in the screenshots and DOM text. Key material comes back once, when the scan is created.</p>
                <div className="field"><label htmlFor="scan-wrap">Wrap key with</label>
                  <select className="input" id="scan-wrap" value={cfg.wrap} onChange={(e) => update({ wrap: e.target.value as ScanConfig['wrap'] })}>
                    <option value="clear">Return the key in clear (TLS only)</option><option value="rsa">RSA public key (PEM) · rsa-oaep-256</option></select></div>
                {cfg.wrap === 'rsa' && (
                  <div className="field"><label htmlFor="scan-pem">Public key (PEM)</label>
                    <textarea className="input pem" id="scan-pem" spellCheck={false} placeholder={'-----BEGIN PUBLIC KEY-----\n…\n-----END PUBLIC KEY-----'} value={cfg.pem} onChange={(e) => update({ pem: e.target.value })} /></div>
                )}
              </div>
            )}
            <Switch label="Screenshots with field overlays" sub="Numbered boxes, as in the parallel view" checked={cfg.screenshots} onChange={(v) => update({ screenshots: v })} />
            <Switch label="HAR file" sub="Not supported: a HAR holds response bodies with personal data" checked={false} disabled />
          </section>

          <section className="card" aria-labelledby="reqT">
            <div className="card-head"><div className="card-title" id="reqT">Request</div><span className="pill mono" style={{ fontSize: 11.5 }}>POST /api/v1/scans</span></div>
            <pre className="pane reqpane" aria-live="polite"><JsonView text={requestJson(cfg)} /></pre>
            <div className="hint" style={{ marginTop: 8 }}>The request schema has no submit field. Copy this to run the same scan from a pipeline with a key that has the scan scope; the persona seed and any wrap key are added when the scan starts and are never shown here.</div>
            <div className="btns">
              <button className="btn btn-primary" type="button" disabled={busy} onClick={() => void start()}>{busy ? 'Starting…' : scanId ? 'Restart crawl with these settings' : 'Start crawl'}</button>
              <button className="btn btn-ghost" type="button" onClick={() => void copyRequest()}>{copied ? 'Copied' : 'Copy request'}</button>
            </div>
            {otherErr && <div className="err" role="alert" style={{ marginTop: 10 }}>{otherErr}</div>}
          </section>

          {created && created.scan.id === scanId && <ScanKeyCard created={created} />}
        </div>

        <div className="col">
          {!scanId && (
            <section className="card" aria-labelledby="liveT">
              <div className="card-head"><div className="card-title" id="liveT">Crawl · live</div><span className="chip slate">not started</span></div>
              <div className="empty-live">Start a crawl to see pages appear here.</div>
              {recent.data && recent.data.items.length > 0 && (
                <>
                  <div className="card-title" style={{ fontSize: 13, marginTop: 6 }}>Recent scans</div>
                  <div className="recent">{recent.data.items.map((j) => (
                    <Link key={j.id} to={`/scanner/scans/${encodeURIComponent(j.id)}`}>
                      <span className="mono">{shortId(j.id)} · {hostOf(j.spec?.url_origin as string | undefined) || '—'}</span>
                      <span className="faint">{fmtWhen(j.created_at)} · {j.state}</span>
                    </Link>))}</div>
                </>
              )}
            </section>
          )}
          {scanId && notFound && <div className="callout rose">This scan was not found for the tenant {tenantName}.</div>}
          {scanId && detail.error && !notFound && <div className="callout amber" role="alert">The scan could not be loaded: {errorText(detail.error)}. Retrying.</div>}
          {scanId && !scan && !detail.error && (
            <section className="card" aria-labelledby="liveT"><div className="card-head"><div className="card-title" id="liveT">Crawl · live</div></div>
              <div className="skeleton" style={{ height: 120 }} aria-hidden="true" /></section>
          )}
          {scan && summary && (
            <LiveCrawl scanId={scanId} scan={scan} summary={summary} pages={pages} events={events} origin={origin} />
          )}
        </div>
      </div>
    </main>
  );
}
