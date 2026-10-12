import { useEffect, useMemo, useRef, useState, type DragEvent } from 'react';
import { useLocation, useNavigate } from 'react-router-dom';
import { EntityChip, Tone } from '../components/Chip';
import { OcrStatus } from '../components/OcrStatus';
import { PageViewer } from '../components/PageViewer';
import { useSettings } from '../hooks/useSettings';
import { api, base64Blob, errorText, type Policy } from '../lib/api';
import {
  curlCommand, documentOptions, extensionLabel, extractionHead, isPaged, ocrWordCount, redactedName, sameValueAs, secondsText,
  sizeText, timingLine, tokenSegments, uniqueFindings, verifyState, type OutMode,
} from '../lib/documents';
import type { DocumentTransformOut, OcrMode, PageFinding, PresetSummary, Sensitivity } from '../lib/types';

const ACCEPT = '.pdf,.docx,.png,.jpg,.jpeg,.txt,.csv';
const DEMO = '/demo/kyc-scanned.pdf';
const MODE_LABEL: Record<OutMode, string> = { red: 'Redact', obf: 'Obfuscate text', syn: 'Synthesize text' };
const STEP_LABEL: Record<OutMode, string> = { red: 'Redact', obf: 'Obfuscate', syn: 'Synthesize' };

function plural(n: number, word: string, many = `${word}s`): string { return `${n} ${n === 1 ? word : many}`; }

/** The result text with each replacement wrapped as a token chip (by its output span). */
function TokenText({ text, findings, synthetic }: { text: string; findings: PageFinding[]; synthetic: boolean }): JSX.Element {
  return (
    <>{tokenSegments(text, findings).map((s, i) => (s.n === undefined
      ? <span key={i}>{s.text}</span>
      : <span key={i} className={`tok${synthetic ? ' syn' : ''}`} title={`${s.n} · ${s.entity_type}`}>{s.text}</span>))}</>
  );
}

export function PlaygroundDocuments(): JSX.Element {
  const { apiKey, tenant } = useSettings();
  const auth = useMemo(() => ({ apiKey, tenant }), [apiKey, tenant]);
  const navigate = useNavigate();
  const location = useLocation();
  // Hand-over: a preset name from the Playground, or an inline draft (object) from the Policies page.
  const handed = (location.state as { policy?: Policy } | null)?.policy ?? null;
  const inline = handed !== null && typeof handed === 'object' ? handed : null;
  const input = useRef<HTMLInputElement>(null);
  const [presets, setPresets] = useState<PresetSummary[]>([]);
  const [policy, setPolicy] = useState<Policy>(handed ?? 'gdpr-pseudonymise');
  const [file, setFile] = useState<File | null>(null);
  const [mode, setMode] = useState<OutMode>('red');
  const [ocr, setOcr] = useState<OcrMode>('auto');
  const [busy, setBusy] = useState<'run' | 'reveal' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [out, setOut] = useState<{ mode: OutMode; name: string; res: DocumentTransformOut } | null>(null);
  const [page, setPage] = useState(1);
  const [hot, setHot] = useState<number | null>(null);
  const [revealed, setRevealed] = useState<string | null>(null);
  const [copied, setCopied] = useState<string | null>(null);

  useEffect(() => { api.policies.presets(auth).then(setPresets).catch(() => setPresets([])); }, [auth]);

  const res = out?.res ?? null;
  const findings = useMemo(() => (res ? uniqueFindings(res.pages) : []), [res]);
  const sameAs = useMemo(() => sameValueAs(findings), [findings]);
  const current = res?.pages.find((p) => p.page === page) ?? res?.pages[0] ?? null;
  const ocrWords = res ? ocrWordCount(res.pages) : 0;
  const verdict = res ? verifyState(res) : null;
  const pdfOut = out?.mode === 'red' && !!res?.redacted_pdf_b64;
  const syn = out?.mode === 'syn';
  const head = extractionHead(syn, revealed !== null);

  function choose(f: File | null): void { setFile(f); setOut(null); setError(null); setRevealed(null); setPage(1); setHot(null); }
  function onDrop(e: DragEvent<HTMLElement>): void { e.preventDefault(); choose(e.dataTransfer.files?.[0] ?? null); }
  async function loadDemo(): Promise<void> {
    const blob = await fetch(DEMO).then((r) => { if (!r.ok) throw new Error('The demo scan is missing from this build.'); return r.blob(); });
    choose(new File([blob], 'kyc-scanned.pdf', { type: 'application/pdf' }));
  }

  async function run(): Promise<void> {
    if (!file) return;
    setBusy('run'); setError(null); setRevealed(null); setHot(null);
    try {
      const call = mode === 'syn' ? api.documents.synthesize : api.documents.obfuscate;
      setOut({ mode, name: file.name, res: await call(auth, file, policy, documentOptions(mode, ocr, file.name)) });
      setPage(1);
    } catch (e) { setError(errorText(e)); } finally { setBusy(null); }
  }

  async function reveal(): Promise<void> {
    if (!res || !res.key_material) return;
    setBusy('reveal'); setError(null);
    try {
      const payload = typeof res.result === 'string' ? { text: res.result } : { data: res.result };
      const r = await api.reveal(auth, { ...payload, key_material: res.key_material, reveal_hints: res.reveal_hints });
      setRevealed(typeof r.result === 'string' ? r.result : JSON.stringify(r.result, null, 2));
    } catch (e) { setError(errorText(e)); } finally { setBusy(null); }
  }

  function copy(label: string, text: string): void {
    navigator.clipboard?.writeText(text).catch(() => undefined);
    setCopied(label); window.setTimeout(() => setCopied(null), 1400);
  }

  function download(): void {
    if (!res?.redacted_pdf_b64 || !out) return;
    const url = URL.createObjectURL(base64Blob(res.redacted_pdf_b64, 'application/pdf'));
    const a = document.createElement('a');
    a.href = url; a.download = redactedName(out.name); a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  const curl = curlCommand({ origin: window.location.origin, mode, tenant, fileName: file?.name ?? null, policy, ocr });
  const methods = res ? Object.entries(res.report.methods_used).map(([m, n]) => `${m} ×${n}`).join(' · ') : '';
  const repeats = Object.keys(sameAs).length;
  const pageCount = (n: number): string => plural(n, 'page');
  const ocrStep = !res ? '—' : res.document.ocr_pages.length ? `${ocrWords} words`
    : res.document.unreadable_pages.length ? `${pageCount(res.document.unreadable_pages.length)} unread`
      : res.document.format === 'pdf' ? 'text layer' : 'not needed';
  const valueStep = !res ? '—' : pdfOut && res.document.redaction ? plural(res.document.redaction.boxes, 'box', 'boxes')
    : plural(findings.filter((f) => f.redacted).length, 'value');
  const presetNames = (presets.length ? presets.map((p) => p.name) : ['gdpr-pseudonymise']).filter((n) => !n.startsWith('synthetic'));

  return (
    <main className="page">
      <div className="page-head">
        <div><h1>Try the engine</h1><p>Drop a document. OCR reads scanned pages; detection, redaction and tokens run inside DPS. The LLM never sees a value.</p></div>
        <div className="head-actions">
          <span className="muted" style={{ fontSize: 12.5 }}>Policy</span>
          <div className="seg sm" role="group" aria-label="Policy">
            {presetNames.map((n) => (
              <button key={n} className={policy === n ? 'on' : ''} aria-pressed={policy === n} onClick={() => setPolicy(n)}>{n}</button>))}
            {inline !== null && <button className={policy === inline ? 'on' : ''} aria-pressed={policy === inline} onClick={() => setPolicy(inline)}>draft from Policies</button>}
          </div>
          <button className="btn btn-sm" onClick={() => copy('curl', curl)} title="The command uses $DPS_API_KEY, never the key in the header">{copied === 'curl' ? 'Copied' : 'Copy as curl'}</button>
        </div>
      </div>
      {error && <div className="err" role="alert">{error}</div>}

      <section className="card" onDragOver={(e) => e.preventDefault()} onDrop={onDrop} aria-label="Input">
        <div className="card-head"><div className="card-title">Input</div>
          <div className="head-actions">
            <button className="btn btn-sm" onClick={() => input.current?.click()}>{file ? 'Replace file' : 'Choose file'}</button>
            <button className="btn btn-sm btn-ghost" onClick={() => { void loadDemo().catch((e: unknown) => setError(errorText(e))); }}>Load demo KYC scan</button>
            <div className="seg sm" role="group" aria-label="Input kind">
              <button onClick={() => navigate('/playground', { state: { kind: 'json', policy } })}>JSON</button>
              <button onClick={() => navigate('/playground', { state: { kind: 'text', policy } })}>Text</button>
              <button className="on" aria-pressed="true">Document</button>
            </div>
          </div></div>
        <input ref={input} type="file" accept={ACCEPT} hidden data-testid="file-input" onChange={(e) => { choose(e.target.files?.[0] ?? null); e.target.value = ''; }} />
        <div className="inrow">
          <div className="file">
            <div className="fileicon">{file ? extensionLabel(file.name) : 'DOC'}</div>
            <div>{file ? <><div className="name">{file.name}</div><div className="sub">{sizeText(file.size)}{res ? ` · ${pageCount(res.document.pages)} · ${res.document.format}` : ''}</div></>
              : <><div className="name">Drop a PDF, DOCX, PNG, JPEG, TXT or CSV here</div><div className="sub">Up to the server's document limit · nothing is stored</div></>}</div>
          </div>
          <div className="ctl"><span className="lbl">OCR</span>
            <div className="row"><OcrStatus compact />
              <div className="seg sm" role="group" aria-label="OCR mode">
                {(['auto', 'always', 'never'] as OcrMode[]).map((m) => <button key={m} className={ocr === m ? 'on' : ''} aria-pressed={ocr === m} onClick={() => setOcr(m)}>{m}</button>)}
              </div></div></div>
          <div className="ctl"><span className="lbl">Output</span>
            <div className="runrow">
              <div className="seg sm" role="group" aria-label="Output">
                {(['red', 'obf', 'syn'] as OutMode[]).map((m) => <button key={m} data-testid={`mode-${m}`} className={mode === m ? 'on' : ''} aria-pressed={mode === m} onClick={() => setMode(m)}>{MODE_LABEL[m]}</button>)}
              </div>
              <button className="btn btn-primary" data-testid="run" disabled={!file || busy !== null} onClick={() => { void run(); }}>
                {busy === 'run' ? 'Working…' : mode === 'red' && file && !isPaged(file.name) ? 'Obfuscate as text'
                  : mode === 'red' ? `Redact${res ? ` ${pageCount(res.document.pages)}` : ''}` : MODE_LABEL[mode]}
              </button>
            </div></div>
        </div>
        {file && mode === 'red' && !isPaged(file.name) && <div className="faint" style={{ fontSize: 12, marginTop: 10 }}>A redacted PDF is produced for PDFs and images. This file is obfuscated as text.</div>}
        <div className="stepwrap">
          <div className="stepper" aria-label="Progress">
            <div className={`step ${file ? 'done' : 'on'}`}><span className="n">{file ? '✓' : '1'}</span>Upload</div><div className="step-sep" />
            <div className={`step ${res ? 'done' : ''}`}><span className="n">{res ? '✓' : '2'}</span>OCR <span className="faint">· {ocrStep}</span></div><div className="step-sep" />
            <div className={`step ${res ? 'done' : ''}`}><span className="n">{res ? '✓' : '3'}</span>Detect <span className="faint">· {res ? plural(findings.length, 'finding') : '—'}</span></div><div className="step-sep" />
            <div className={`step ${res ? 'done' : ''}`}><span className="n">{res ? '✓' : '4'}</span>{STEP_LABEL[out?.mode ?? mode]} <span className="faint">· {valueStep}</span></div><div className="step-sep" />
            <div className={`step ${verdict?.verified ? 'done' : ''}`} data-testid={verdict?.verified ? 'verify-ok' : undefined}><span className="n">{verdict?.verified ? '✓' : '5'}</span>Verify <span className="faint">· {verdict ? plural(verdict.leaks, 'leak') : '—'}</span></div>
          </div>
          {res && <span className="timing">{timingLine(res.document.timings_ms)}</span>}
        </div>
      </section>

      {res && current && out && (
        <div className="docgrid">
          <section className="card pcard">
            <div className="card-head"><div className="card-title">Source · page {current.page} of {res.pages.length}</div>
              <div className="legend"><span><i style={{ background: 'var(--rose)' }} />critical</span><span><i style={{ background: 'var(--amber)' }} />sensitive</span><span><i style={{ background: 'var(--ink-3)' }} />kept</span></div></div>
            <PageViewer page={current} mode="source" hot={hot} onHot={setHot} />
            <div className="strip">
              {res.pages.map((p) => (
                <button key={p.page} type="button" className={`thumb${p.page === current.page ? ' on' : ''}`} aria-label={`Show page ${p.page}`} aria-pressed={p.page === current.page} onClick={() => setPage(p.page)}>
                  <span className={`thumb-paper${p.preview_png_b64 ? '' : ' blank'}`}>{p.preview_png_b64 ? <img src={`data:image/png;base64,${p.preview_png_b64}`} alt="" /> : null}</span>
                  <span>p{p.page} · <b>{new Set(p.findings.map((f) => f.n)).size}</b></span>
                </button>))}
              <div className="strip-meta">{res.document.redaction ? `${res.document.redaction.dpi} dpi` : res.document.format}<br /><b>{res.document.boxes} boxes on {pageCount(res.pages.length)}</b></div>
            </div>
          </section>

          <section className="card" data-testid="findings">
            <div className="card-head"><div className="card-title">Findings <span className="faint">· {findings.length}</span></div>
              <EntityChip type={res.report.record_sensitivity} sensitivity={res.report.record_sensitivity as Sensitivity} /></div>
            <div className="fhead-sub">Grouped by page. Numbers match the boxes on the page.</div>
            {res.pages.map((p) => (
              <div key={p.page} className={`grp${p.page === current.page ? ' open' : ''}`}>
                <button className="grp-h" aria-expanded={p.page === current.page} onClick={() => setPage(p.page)}>
                  <span className="t"><span className="chev">▶</span> Page {p.page}</span><span className="faint">{new Set(p.findings.map((f) => f.n)).size}</span>
                  <span className="faint grp-src">{p.source === 'ocr' || p.source === 'hybrid' ? `${res.document.ocr_engine ?? 'OCR'}${p.source === 'hybrid' ? ' + text layer' : ''}${p.ocr_confidence !== null ? ` · conf ${p.ocr_confidence.toFixed(2)}` : ''}` : p.source.replace('_', ' ')}</span>
                </button>
                {p.page === current.page && <div className="grp-b">
                  {p.findings.map((f) => (
                    <div key={`${f.n}-${p.page}`} className={`fd${hot === f.n ? ' hot' : ''}`} tabIndex={0} onMouseEnter={() => setHot(f.n)} onMouseLeave={() => setHot(null)} onFocus={() => setHot(f.n)} onBlur={() => setHot(null)}>
                      <span className="fd-n">{f.n}</span><EntityChip type={f.entity_type} sensitivity={f.sensitivity} />
                      <span className="mono muted">{f.score.toFixed(2)}</span>
                      <span className="why">{f.line !== null ? <span className="mono">l.{f.line} · </span> : null}{f.evidence.join(' · ')}{sameAs[f.n] ? ` · same value as ${sameAs[f.n]}` : ''}{f.method === 'keep' ? ' · kept by policy' : ''}{f.review ? <> · <b>review</b> (OCR confidence {f.ocr_confidence?.toFixed(2)})</> : null}</span>
                    </div>))}
                  {p.findings.length === 0 && <p className="muted">Nothing found on this page.</p>}
                </div>}
              </div>))}
            {res.document.unreadable_pages.length > 0 && <div className="callout amber">Page{res.document.unreadable_pages.length === 1 ? '' : 's'} {res.document.unreadable_pages.join(', ')} could not be read (no OCR). Their content was not scanned.</div>}
          </section>

          <section className="card pcard">
            <div className="card-head"><div className="card-title">{pdfOut ? 'Output · redacted PDF' : syn ? 'Output · synthetic text, page view' : 'Output · obfuscated text, page view'}</div>
              {pdfOut ? <Tone tone="teal">flattened</Tone> : syn ? <Tone tone="violet">no path back</Tone> : <Tone tone="teal">reversible</Tone>}</div>
            <PageViewer page={current} mode={pdfOut ? 'redacted' : 'tokens'} hot={hot} onHot={setHot} synthetic={syn} />
            <div className="out-foot">
              <div className="meta">{pdfOut && res.document.redaction
                ? <><span className="mono">{redactedName(out.name)}</span><br />{pageCount(res.document.redaction.pages)} · {sizeText(res.document.redaction.bytes)} · page {current.page} shown</>
                : <>Text laid over page {current.page} · no PDF is produced in this mode</>}</div>
              {pdfOut
                ? <button className="btn btn-primary btn-sm" onClick={download}>Download redacted PDF</button>
                : <button className="btn btn-sm" onClick={() => copy('page-text', typeof res.result === 'string' ? res.result : res.result_csv ?? JSON.stringify(res.result))}>{copied === 'page-text' ? 'Copied' : syn ? 'Copy synthetic text' : 'Copy obfuscated text'}</button>}
            </div>
            <p className="out-note">{pdfOut ? `${plural(res.document.redaction?.boxes ?? 0, 'box', 'boxes')} burned into the page images; the output has no text layer. No key can bring them back.`
              : syn ? 'Every value is replaced by a rule-valid fake from one persona per document.' : 'Tokens keep the format of each value. Reveal restores the originals with the key below.'}</p>
          </section>
        </div>)}

      {res && out && verdict && (
        <div className="two">
          <section className="card">
            <div className="card-head"><div className="card-title" data-testid="result-head">{head.title} <Tone tone={head.tone}>{head.note}</Tone></div>
              <div className="btnrow"><span className="faint">{pageCount(res.document.pages)}</span>
                <button className="btn btn-sm" onClick={() => copy('text', revealed ?? (typeof res.result === 'string' ? res.result : res.result_csv ?? JSON.stringify(res.result)))}>{copied === 'text' ? 'Copied' : 'Copy text'}</button></div></div>
            <div className="pane textpane" data-testid="result-text">{revealed !== null ? revealed
              : typeof res.result === 'string' ? <TokenText text={res.result} findings={findings} synthetic={syn} /> : (res.result_csv ?? JSON.stringify(res.result, null, 2))}</div>
          </section>
          <section className="card">
            {res.key_material && (
              <div className="keycard card" style={{ padding: '12px 14px' }}>
                <div className="card-head" style={{ marginBottom: 6 }}><div className="card-title">Key material · returned once, never stored</div><Tone tone="teal">{res.key_material.alg}</Tone></div>
                <div className="mono" style={{ fontSize: 12, wordBreak: 'break-all' }}>kid {res.key_material.kid} · dek {res.key_material.dek ? `${res.key_material.dek.slice(0, 6)}…${res.key_material.dek.slice(-4)}` : 'wrapped'}<br /><span className="faint">reveal hints: {plural(res.reveal_hints.length, 'span')} · locators, methods and types, no originals</span></div>
                <div className="btnrow" style={{ marginTop: 8 }}>
                  <button className="btn btn-sm" onClick={() => copy('key', JSON.stringify(res.key_material))}>{copied === 'key' ? 'Key copied' : 'Copy key'}</button>
                  {revealed === null ? <button className="btn btn-sm btn-primary" disabled={busy !== null} onClick={() => { void reveal(); }}>{busy === 'reveal' ? 'Revealing…' : 'Reveal text with key'}</button>
                    : <span className="faint">original text restored · POST /api/v1/reveal</span>}
                </div>
                <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>{pdfOut ? 'Applies to the text extraction only. The redacted PDF has no key.' : 'Pass the same key back on the next call to get the same tokens.'}</div>
              </div>)}
            {res.synthesis_seed && <div className="callout amber" style={{ fontSize: 12 }}>Synthesis seed echoed for cross-call consistency. It is not a reveal key: generation is one-way.</div>}
            <div className="diffrow"><span className="k">methods</span><span>{methods || '—'}</span></div>
            <div className="diffrow"><span className="k">consistency</span><span>{repeats ? `${plural(repeats, 'repeated value')} share one token each` : 'no repeated values'}</span></div>
            {res.document.synthetic_validity && <div className="diffrow"><span className="k">validity</span><span>{res.document.synthetic_validity.valid} of {res.document.synthetic_validity.checked} rule-checked surrogates pass their checksums</span></div>}
            <div className="diffrow"><span className="k">ocr</span><span>{res.document.ocr_engine ? `${res.document.ocr_engine} · ${pageCount(res.document.ocr_pages.length)} · ${ocrWords} words · ${secondsText(res.document.timings_ms.ocr)}` : 'text layer only'}</span></div>
            <div className="diffrow"><span className="k">llm</span><span>{res.report.llm.calls ? plural(res.report.llm.calls, 'call') : 'not called for this document'} · raw values sent: <b>{res.report.llm.raw_pii_sent}</b></span></div>
            {res.document.warnings.length > 0 && <div className="diffrow"><span className="k">warnings</span><span className="mono">{res.document.warnings.join(' · ')}</span></div>}
            <div className={`verify ${verdict.state}`} data-testid="verify-line">
              {verdict.state === 'failed' ? <span><b>Not verified.</b> {plural(verdict.leaks, 'original')} found in the output. Nothing should leave DPS from this run.</span>
                : verdict.state === 'unverified' ? <span><b>Not verified.</b> {res.document.redaction && !res.document.redaction.ocr_checked
                  ? 'The redacted PDF was not re-read by OCR (OCR unavailable or the re-read was skipped), so what its pages show is unchecked.'
                  : 'No leakage sweep ran for this output.'}</span>
                  : <span><b>Verified{verdict.state === 'partial' ? ', with partial coverage' : ''}.</b> Leakage sweep: 0 of {verdict.checked} detected values in the text output.{res.document.redaction
                    ? ` The produced PDF has ${res.document.redaction.text_layer_chars} extractable characters and an OCR re-read of ${pageCount(res.document.redaction.ocr_pages)} found ${res.document.redaction.ocr_hits} originals (${res.document.redaction.chars_under_boxes} characters read under ${res.document.redaction.boxes} boxes).` : ''}
                    {verdict.gaps.length > 0 && <> Not covered: {verdict.gaps.join('; ')}.</>}</span>}
            </div>
          </section>
        </div>)}
    </main>
  );
}
