import { useEffect, useMemo, useState } from 'react';
import { EntityChip, Tone } from '../components/Chip';
import { useSettings } from '../hooks/useSettings';
import { api, ApiError, type Policy } from '../lib/api';
import type { DetectOut, Finding, Preset, Report, TransformOut } from '../lib/types';

const SAMPLE_JSON = JSON.stringify({
  customer_id: 'CUST-884120', full_name: 'Ananya Krishnan', dob: '1988-03-14', aadhaar: '2341 2341 2346',
  pan: 'ACUPA7085R', email: 'ananya.k@example-mail.in', phone: '+91 98451 20377',
  card: { pan: '4111 1111 1111 1111', expiry: '09/28' }, address: '14 Lavelle Road, Bengaluru 560001',
  risk_score: 712, notes: 'Called on 2026-03-14 about card ending 1111; mail ananya.k@example-mail.in.',
}, null, 2);
const SAMPLE_TEXT = 'Ananya Krishnan (ananya.k@example-mail.in, +91 98451 20377) asked to update her Aadhaar 2341 2341 2346 before 2026-03-14.';

type Mode = 'detect' | 'obfuscate' | 'synthesize';
type Kind = 'json' | 'text';

function pretty(v: unknown): string { return typeof v === 'string' ? v : JSON.stringify(v, null, 2); }

function ReportLine({ r }: { r: Report }): JSX.Element {
  return (
    <div className="kv" style={{ marginTop: 10 }}>
      <span className="k">findings</span><span>{r.findings_total} · {Object.entries(r.findings_by_type).map(([k, v]) => `${k} ${v}`).join(' · ')}</span>
      <span className="k">methods</span><span>{Object.entries(r.methods_used).map(([k, v]) => `${k} ×${v}`).join(' · ') || '—'}</span>
      {Object.keys(r.fallbacks).length > 0 && <><span className="k">fallbacks</span><span>{Object.entries(r.fallbacks).map(([k, v]) => `${k} ×${v}`).join(' · ')}</span></>}
      <span className="k">verification</span><span>{r.verification ? (r.verification.passed ? <Tone tone="emerald">leakage sweep passed · 0 originals in output</Tone> : <Tone tone="rose">failed</Tone>) : '—'}</span>
      <span className="k">LLM</span><span>{r.llm.calls} calls · raw PII sent: <b style={{ color: 'var(--emerald)' }}>{r.llm.raw_pii_sent}</b></span>
      <span className="k">timing</span><span>{r.duration_ms} ms · policy {r.policy} · {r.policy_hash.slice(0, 12)}</span>
    </div>
  );
}

function Findings({ items }: { items: Finding[] }): JSX.Element {
  if (items.length === 0) return <p className="muted">No sensitive values found above the policy threshold.</p>;
  return (
    <>
      {items.map((f, i) => (
        <div className="finding" key={i}>
          <EntityChip type={f.entity_type} sensitivity={f.sensitivity} />
          <span className="mono muted">{f.score.toFixed(2)}</span>
          <span className="path">{f.locator.path}{f.locator.start !== null ? `[${f.locator.start}:${f.locator.end}]` : ''}</span>
          <span className="why">{f.evidence.join(' · ')}</span>
        </div>
      ))}
    </>
  );
}

export function Playground(): JSX.Element {
  const { apiKey, tenant } = useSettings();
  const auth = useMemo(() => ({ apiKey, tenant }), [apiKey, tenant]);
  const [kind, setKind] = useState<Kind>('json');
  const [input, setInput] = useState(SAMPLE_JSON);
  const [presets, setPresets] = useState<Preset[]>([]);
  const [policy, setPolicy] = useState<string>('gdpr-pseudonymise');
  const [busy, setBusy] = useState<Mode | 'reveal' | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [detect, setDetect] = useState<DetectOut | null>(null);
  const [out, setOut] = useState<{ mode: Mode; res: TransformOut } | null>(null);
  const [revealed, setRevealed] = useState<unknown>(null);

  useEffect(() => { api.presets(auth).then(setPresets).catch(() => setPresets([])); }, [auth]);
  useEffect(() => { setInput(kind === 'json' ? SAMPLE_JSON : SAMPLE_TEXT); setOut(null); setDetect(null); setRevealed(null); }, [kind]);

  function body(): { data?: unknown; text?: string; policy: Policy } {
    if (kind === 'text') return { text: input, policy };
    return { data: JSON.parse(input) as unknown, policy };
  }

  async function run(mode: Mode): Promise<void> {
    setBusy(mode); setError(null); setRevealed(null);
    try {
      const b = body();
      if (mode === 'detect') { setDetect(await api.detect(auth, b)); setOut(null); }
      else {
        const res = mode === 'obfuscate' ? await api.obfuscate(auth, b) : await api.synthesize(auth, b);
        setOut({ mode, res });
        setDetect({ findings: res.findings, record_sensitivity: res.report.record_sensitivity as DetectOut['record_sensitivity'], report: res.report, trace_id: res.trace_id });
      }
    } catch (e) {
      setError(e instanceof ApiError ? `${e.status}: ${e.message}` : e instanceof SyntaxError ? `Input is not valid JSON: ${e.message}` : String(e));
    } finally { setBusy(null); }
  }

  async function doReveal(): Promise<void> {
    if (!out || !out.res.key_material) return;
    setBusy('reveal'); setError(null);
    try {
      const payload = kind === 'text' ? { text: out.res.result as string } : { data: out.res.result };
      const r = await api.reveal(auth, { ...payload, key_material: out.res.key_material, reveal_hints: out.res.reveal_hints });
      setRevealed(r.result);
    } catch (e) { setError(e instanceof ApiError ? `${e.status}: ${e.message}` : String(e)); }
    finally { setBusy(null); }
  }

  const findings = detect?.findings ?? [];
  return (
    <main className="page">
      <div className="page-head">
        <div><h1>Try the engine</h1><p>Paste JSON or text. Detection and transformation run locally; the LLM only ever sees placeholders.</p></div>
        <div className="row"><span className="muted" style={{ fontSize: 12.5 }}>Policy</span>
          <select className="input" style={{ width: 240 }} value={policy} onChange={(e) => setPolicy(e.target.value)}>
            {(presets.length ? presets : [{ name: policy, description: '' }]).map((p) => <option key={p.name} value={p.name}>{p.name}</option>)}
          </select></div>
      </div>
      {error && <div className="err">{error}</div>}
      <div className="play">
        <div className="card">
          <div className="card-head"><div className="card-title">Input</div>
            <div className="seg"><button className={kind === 'json' ? 'on' : ''} onClick={() => setKind('json')}>JSON</button><button className={kind === 'text' ? 'on' : ''} onClick={() => setKind('text')}>Text</button></div></div>
          <textarea className="input" value={input} onChange={(e) => setInput(e.target.value)} spellCheck={false} />
          <div className="row" style={{ marginTop: 12 }}>
            <span className="faint" style={{ fontSize: 12 }}>{input.length.toLocaleString()} chars</span><span className="spacer" />
            <button className="btn" disabled={busy !== null} onClick={() => run('detect')}>Detect only</button>
            <button className="btn btn-primary" disabled={busy !== null} onClick={() => run('obfuscate')}>{busy === 'obfuscate' ? 'Working…' : 'Obfuscate'}</button>
            <button className="btn" disabled={busy !== null} onClick={() => run('synthesize')}>{busy === 'synthesize' ? 'Working…' : 'Synthesize'}</button>
          </div>
        </div>
        <div className="card">
          <div className="card-head"><div className="card-title">Findings <span className="faint">· {findings.length}</span></div>
            {detect && <EntityChip type={detect.record_sensitivity} sensitivity={detect.record_sensitivity} />}</div>
          {detect ? <Findings items={findings} /> : <p className="muted">Run Detect, Obfuscate or Synthesize to see what the engine found and why.</p>}
        </div>
        <div className="card">
          <div className="card-head">
            <div className="card-title">Output{out && <> · {out.mode === 'obfuscate' ? <Tone tone="teal">reversible</Tone> : <Tone tone="violet">synthetic · no path back</Tone>}</>}</div>
            {out && out.mode === 'obfuscate' && out.res.key_material && (
              <button className="btn btn-sm btn-primary" disabled={busy !== null} onClick={doReveal}>{busy === 'reveal' ? 'Revealing…' : 'Reveal with key'}</button>)}
          </div>
          {out ? (
            <>
              <pre className="pane" style={{ minHeight: 260 }}>{pretty(revealed ?? out.res.result)}</pre>
              {revealed !== null && <div className="callout" style={{ marginTop: 10, fontSize: 12 }}>Original restored via <span className="mono">POST /api/v1/reveal</span> with the key you hold. DPS kept no copy.</div>}
              {out.res.key_material && (
                <div className="card keycard" style={{ marginTop: 12, padding: '12px 14px' }}>
                  <div className="card-head" style={{ marginBottom: 6 }}><div className="card-title">Key material · returned once, never stored</div><Tone tone="teal">{out.res.key_material.alg}</Tone></div>
                  <div className="mono" style={{ fontSize: 12, wordBreak: 'break-all' }}>kid {out.res.key_material.kid} · dek {out.res.key_material.dek ? `${out.res.key_material.dek.slice(0, 6)}…${out.res.key_material.dek.slice(-4)}` : 'wrapped'} · scope {out.res.key_material.consistency_scope}</div>
                  <div className="row" style={{ marginTop: 8 }}>
                    <button className="btn btn-sm" onClick={() => navigator.clipboard?.writeText(JSON.stringify(out.res.key_material))}>Copy key material</button>
                    <button className="btn btn-sm" onClick={() => navigator.clipboard?.writeText(JSON.stringify(out.res.reveal_hints))}>Copy reveal hints</button>
                  </div>
                </div>)}
              {out.res.synthesis_seed && <div className="callout amber" style={{ marginTop: 12, fontSize: 12 }}>Synthesis seed echoed for cross-call consistency. It is not a reveal key — generation is one-way.</div>}
              <ReportLine r={out.res.report} />
            </>
          ) : detect ? <ReportLine r={detect.report} /> : <p className="muted">The transformed document, the plan and the evidence appear here.</p>}
        </div>
      </div>
    </main>
  );
}
