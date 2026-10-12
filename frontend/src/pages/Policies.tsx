import { useEffect, useMemo, useState } from 'react';
import { Link, useNavigate, useParams } from 'react-router-dom';
import { EntityChip, Tone } from '../components/Chip';
import { useSettings } from '../hooks/useSettings';
import { api, errorText } from '../lib/api';
import {
  DEFAULT_RECOGNIZER, EMPTY_DRAFT, METHODS, PLAN_SAMPLE, REVERSIBLE, TEST_TEXT, declareField, draftChanges, filterRows, inlinePolicy,
  listOf, livePreview, matchCount, planSummary, recognizerSpec, treatmentRows, withTreatment, type Draft, type Filter,
} from '../lib/policyDraft';
import type { EntityInfo, MethodSpec, PlanOut, PolicyDoc, PresetSummary, RecognizerTestOut, Sensitivity } from '../lib/types';
import { toYaml } from '../lib/yaml';

function Params({ spec }: { spec: MethodSpec | undefined }): JSX.Element | null {
  if (!spec) return null;
  return <>{Object.entries(spec.params).map(([k, v]) => <span key={k} className="prm">{k} {Array.isArray(v) ? v.join(' · ') : String(v)}</span>)}</>;
}

function plural(n: number, word: string, many = `${word}s`): string { return `${n} ${n === 1 ? word : many}`; }

const toneFor = (s: Sensitivity | undefined): Sensitivity => s ?? 'MEDIUM';
const shortHash = (hash: string): string => `sha256:${hash.slice(0, 8)}…${hash.slice(-4)}`;

export function Policies(): JSX.Element {
  const { apiKey, tenant } = useSettings();
  const auth = useMemo(() => ({ apiKey, tenant }), [apiKey, tenant]);
  const navigate = useNavigate();
  const { name = 'gdpr-pseudonymise' } = useParams();
  const [presets, setPresets] = useState<PresetSummary[]>([]);
  const [entities, setEntities] = useState<EntityInfo[]>([]);
  const [doc, setDoc] = useState<PolicyDoc | null>(null);
  const [draft, setDraft] = useState<Draft>(EMPTY_DRAFT);
  const [filter, setFilter] = useState<Filter>('all');
  const [query, setQuery] = useState('');
  const [showAll, setShowAll] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [fpe, setFpe] = useState<boolean | null>(null);
  const [copied, setCopied] = useState<string | null>(null);
  const [plan, setPlan] = useState<PlanOut | null>(null);

  useEffect(() => {
    api.policies.presets(auth).then(setPresets).catch((e: unknown) => setError(errorText(e)));
    api.entities(auth).then((r) => setEntities(r.items)).catch((e: unknown) => setError(errorText(e)));
    api.readyz().then((r) => setFpe(r.components.fpe?.ok ?? null)).catch(() => setFpe(null));
  }, [auth]);
  useEffect(() => {
    setDraft(EMPTY_DRAFT); setDoc(null); setPlan(null);
    api.policies.validate(auth, name).then((d) => { setDoc(d); setError(null); }).catch((e: unknown) => setError(errorText(e)));
  }, [auth, name]);

  const preset = presets.find((p) => p.name === name);
  const byName = useMemo(() => new Map(entities.map((e) => [e.name, e])), [entities]);
  const inline = useMemo(() => inlinePolicy(name, doc, draft), [name, doc, draft]);
  const changes = draftChanges(draft);

  function copy(label: string, text: string): void {
    navigator.clipboard?.writeText(text).catch(() => undefined);
    setCopied(label); window.setTimeout(() => setCopied(null), 1400);
  }

  // --- treatment matrix -------------------------------------------------------------------------
  const rows = useMemo(() => (doc ? treatmentRows(doc, draft, entities, showAll) : []), [doc, draft, entities, showAll]);
  const visible = filterRows(rows, filter, query);

  // --- declared fields --------------------------------------------------------------------------
  const [decl, setDecl] = useState({ path: '', entity_type: 'BUSINESS_SENSITIVE', method: 'bucket' });
  function declare(): void {
    setDraft((d) => declareField(d, decl));
    setDecl((d) => ({ ...d, path: '' }));
  }

  // --- recognizer builder -------------------------------------------------------------------------
  const [rec, setRec] = useState(DEFAULT_RECOGNIZER);
  const [testText, setTestText] = useState(TEST_TEXT);
  const [server, setServer] = useState<RecognizerTestOut | null>(null);
  const spec = useMemo(() => recognizerSpec(rec), [rec]);
  const local = useMemo(() => livePreview(rec.regex, listOf(rec.allow), testText), [rec.regex, rec.allow, testText]);
  useEffect(() => { setServer(null); }, [rec, testText]);
  async function runServer(): Promise<void> {
    try { setServer(await api.policies.testRecognizer(auth, spec, testText)); setError(null); } catch (e) { setError(errorText(e)); }
  }

  // --- plan preview -------------------------------------------------------------------------------
  const [sample, setSample] = useState(PLAN_SAMPLE);
  const [planMode, setPlanMode] = useState<'obfuscate' | 'synthesize'>('obfuscate');
  async function runPlan(): Promise<void> {
    try {
      const data = JSON.parse(sample) as unknown;
      setPlan(await api.policies.plan(auth, { data, policy: changes ? inline : name, mode: planMode })); setError(null);
    } catch (e) { setError(e instanceof SyntaxError ? `Sample is not valid JSON: ${e.message}` : errorText(e)); }
  }
  const summary = plan ? planSummary(plan.plan) : null;

  const yaml = useMemo(() => toYaml(inline), [inline]);
  function downloadYaml(): void {
    const url = URL.createObjectURL(new Blob([yaml], { type: 'application/yaml' }));
    const a = document.createElement('a'); a.href = url; a.download = `${name}${changes ? '-draft' : ''}.yaml`; a.click();
    window.setTimeout(() => URL.revokeObjectURL(url), 5000);
  }

  const declaredRows = [...(doc?.detect.declared_fields ?? []).map((d) => ({ d, isDraft: false })), ...draft.declared.map((d) => ({ d, isDraft: true }))];
  const modeLabel = (p: PresetSummary): string => (p.mode === 'synthesize' ? 'one-way' : p.reversible ? 'reversible' : 'irreversible');
  return (
    <main className="page">
      <div className="page-head">
        <div><h1>Policies</h1><p>A policy decides what is detected and what happens to each entity type. Every request, job and evidence pack records the hash it ran with.</p></div>
        <div className="head-actions">
          <button className="btn" disabled={!changes} onClick={() => setDraft(EMPTY_DRAFT)}>Discard draft</button>
          <a className="btn" href="#plan">Preview plan</a>
          <span title="Publishing and version history need the policy store (M5). Use the draft inline: Download YAML or Use in Playground."><button className="btn btn-primary" disabled>Publish</button></span>
        </div>
      </div>
      {error && <div className="err" role="alert">{error}</div>}
      <div className="pol">
        <div className="pol-cell"><aside className="pol-side" aria-label="Presets">
          <div className="card">
            <div className="card-head"><div className="card-title">Presets</div><Tone tone="slate">{presets.length}</Tone></div>
            <div className="plist">
              {presets.map((p) => (
                <Link key={p.name} to={`/policies/${p.name}`} className={`preset${p.name === name ? ' on' : ''}`} aria-current={p.name === name ? 'page' : undefined}>
                  <div className="p-top"><span className="p-name">{p.name}</span><span className="p-ver">v{p.version}</span></div>
                  <div className="p-hash">{shortHash(p.hash)}</div>
                  <div className="p-meta"><Tone tone={p.mode === 'synthesize' ? 'violet' : 'teal'}>{p.mode}</Tone>{modeLabel(p)}</div>
                </Link>))}
            </div>
          </div>
          <div className="card toc-card"><div className="nav-label">On this page</div>
            <nav className="toc" aria-label="On this page"><a href="#matrix">Treatment per entity <span>{rows.length}</span></a><a href="#declared">Declared fields <span>{declaredRows.length}</span></a>
              <a href="#recognizer">Custom recognizer <span>{(doc?.detect.custom_recognizers.length ?? 0) + draft.recognizers.length}</span></a><a href="#plan">Plan preview <span>{plan?.plan.length ?? 0}</span></a><a href="#versions">Versions <span>{changes ? 2 : 1}</span></a></nav></div>
        </aside></div>
        <div className="pol-main">
          <section className="card" data-testid="policy-header">
            <div className="ph-name">{name} {preset && <Tone tone={preset.mode === 'synthesize' ? 'violet' : 'teal'}>{preset.mode} · {modeLabel(preset)}</Tone>}
              <Tone tone="emerald">v{doc?.version ?? 1} shipped</Tone>{changes > 0 && <Tone tone="amber">draft · {plural(changes, 'change')}</Tone>}</div>
            <div className="ph-desc">{doc?.description}</div>
            {preset && <div className="ph-hash">Preset hash <span className="mono">sha256:{preset.hash.slice(0, 16)}…{preset.hash.slice(-12)}</span>
              <button className="btn btn-sm" onClick={() => copy('hash', preset.hash)}>{copied === 'hash' ? 'Copied' : 'Copy hash'}</button></div>}
            {doc && <div className="facts">
              <div><div className="l">Locale</div><div className="v">{doc.locale.default ?? 'service default'}<small>{doc.locale.by_country_field ? `by ${doc.locale.by_country_field}` : doc.locale.default ? 'pinned by the policy' : <>DPS_DEFAULT_<wbr />LOCALE</>}</small></div></div>
              <div><div className="l">Detection tiers</div><div className="v">{doc.detect.tiers.join(', ')}<small>{doc.detect.tiers.includes('llm_label') ? 'LLM labelling on' : 'LLM labelling off'}</small></div></div>
              <div><div className="l">Minimum score</div><div className="v">{doc.detect.min_score.toFixed(2)}<small>noisy-OR per locator</small></div></div>
              <div><div className="l">Consistency</div><div className="v">{doc.consistency.scope}<small>{doc.consistency.scope === 'key' ? 'caller holds the key' : doc.consistency.field_scoped ? 'field scoped' : 'per request'}</small></div></div>
              <div><div className="l">Token style</div><div className="v">{doc.output.token_style}<small>{doc.output.token_style === 'natural' ? 'reveal uses hints' : 'self-describing'}</small></div></div>
              <div><div className="l">Leakage check</div><div className="v">{doc.verify.leakage_check}<small>{doc.verify.leakage_check === 'strict' ? 'fails closed' : 'warns'}</small></div></div>
            </div>}
            <div className="callout" style={{ marginTop: 12 }}>{preset?.mode === 'synthesize'
              ? <>Synthetic output carries a <b>measured</b> privacy report rather than a claim of anonymity. Generation is one-way.</>
              : <>Obfuscated output is <b>pseudonymised and still personal data</b> (GDPR Art. 4(5), India's DPDP Act). Only the holder of the key can reverse it; DPS keeps no copy.</>}</div>
          </section>
          <section className="card" id="matrix" data-testid="matrix">
            <div className="card-head"><div className="card-title">Treatment per entity type {changes > 0 && <span className="faint">· draft</span>}</div>
              <div className="tools">
                <div className="seg sm" role="group" aria-label="Filter">{(['all', 'critical', 'draft'] as Filter[]).map((f) => <button key={f} className={filter === f ? 'on' : ''} aria-pressed={filter === f} onClick={() => setFilter(f)}>{f === 'draft' ? 'Changed in draft' : f === 'all' ? 'All' : 'Critical'}</button>)}</div>
                <input className="input" placeholder="Search entity or method" value={query} onChange={(e) => setQuery(e.target.value)} aria-label="Search entity types" />
              </div></div>
            <div className="tbl-scroll"><table className="tbl stack">
              <thead><tr><th>Entity type</th><th>Family</th><th>Sensitivity</th><th>Obfuscate</th><th>Synthesize</th><th>Reversible</th></tr></thead>
              <tbody>{visible.map((r) => (
                <tr key={r.entity} className={r.changed ? 'drafted' : ''}>
                  <td className="ent" data-l="Entity type"><EntityChip type={r.entity} sensitivity={toneFor(r.info?.sensitivity)} />{r.changed && <span className="chip amber" style={{ marginLeft: 6 }}>draft</span>}</td>
                  <td className="fam" data-l="Family">{r.info?.family.replace(/_/g, ' ') ?? 'custom'}{r.info?.jurisdictions.length ? ` · ${r.info.jurisdictions.join(', ')}` : ''}</td>
                  <td data-l="Sensitivity"><EntityChip type={toneFor(r.info?.sensitivity)} sensitivity={toneFor(r.info?.sensitivity)} /></td>
                  <td data-l="Obfuscate"><select className="input m-select" value={r.obf.method} aria-label={`Obfuscate method for ${r.entity}`} onChange={(e) => doc && setDraft((d) => withTreatment(d, doc, r.entity, e.target.value))}>
                    {METHODS.map((m) => <option key={m} value={m}>{m}</option>)}</select><Params spec={r.obf} />{r.inherited && <span className="syn-note">default_sensitive</span>}</td>
                  <td data-l="Synthesize"><span className="m">{r.syn.method}</span><Params spec={r.syn} /></td>
                  <td data-l="Reversible" className={`revcell${REVERSIBLE.has(r.obf.method) ? '' : ' no'}`}>{REVERSIBLE.has(r.obf.method) ? 'Yes' : 'No'}</td>
                </tr>))}
                {visible.length === 0 && <tr><td colSpan={6} className="muted">{doc ? `No entity type matches. Clear the search or the filter to see all ${rows.length}.` : 'Loading the policy…'}</td></tr>}
              </tbody></table></div>
            <div className="tbl-foot"><span>Showing {visible.length} of {rows.length} entity types. Others are tokenised by <span className="mono">default_sensitive → {doc?.treat.default_sensitive?.method ?? 'det_token'}</span>.</span>
              <button className="btn btn-sm" onClick={() => setShowAll((v) => !v)}>{showAll ? 'Show policy types only' : `Show all ${entities.length}`}</button></div>
            <div className="faint" style={{ fontSize: 12, marginTop: 8 }}><span className="mono">fpe_*</span> methods need at least 10⁶ free values; smaller ones run as <span className="mono">det_token</span> and the plan records <span className="mono">fallback:fpe_domain_too_small</span>. FF1 on this deployment: <b>{fpe === null ? 'unknown' : fpe ? 'on' : 'off'}</b>.</div>
          </section>
        </div>
        <div className="pol-rest">
          <section className="card" id="declared">
            <div className="card-head"><div className="card-title">Declared fields <span className="faint">· more than PII</span></div></div>
            <p className="card-sub">Owners name the paths and columns detectors cannot know about. Declarations apply before detection and win over inferred types; the plan records <span className="mono">declared:&lt;path&gt;</span>.</p>
            <div className="tbl-scroll"><table className="tbl stack"><thead><tr><th>Path or column</th><th>Declared as</th><th>Treatment</th><th>Source</th></tr></thead>
              <tbody>{declaredRows.map(({ d, isDraft }, i) => (
                <tr key={`${d.path}-${i}`} className={isDraft ? 'drafted' : ''}><td className="mono" data-l="Path">{d.path}</td>
                  <td data-l="Declared as"><EntityChip type={d.entity_type} sensitivity={toneFor(byName.get(d.entity_type)?.sensitivity)} /></td>
                  <td data-l="Treatment"><span className="m">{(draft.treat[d.entity_type] ?? doc?.treat[d.entity_type] ?? doc?.treat.default_sensitive)?.method}</span><Params spec={draft.treat[d.entity_type] ?? doc?.treat[d.entity_type]} /></td>
                  <td data-l="Source">{isDraft ? <Tone tone="amber">draft</Tone> : 'preset'}</td></tr>))}
                <tr><td data-l="Path"><input className="input mono" placeholder="$.loan.internal_score or OWNER.TABLE.COLUMN" value={decl.path} onChange={(e) => setDecl({ ...decl, path: e.target.value })} aria-label="Declared path" /></td>
                  <td data-l="Declared as"><input className="input mono" list="entity-names" value={decl.entity_type} onChange={(e) => setDecl({ ...decl, entity_type: e.target.value.toUpperCase() })} aria-label="Declared entity type" /></td>
                  <td data-l="Treatment"><select className="input" value={decl.method} onChange={(e) => setDecl({ ...decl, method: e.target.value })} aria-label="Declared treatment">{METHODS.map((m) => <option key={m}>{m}</option>)}</select></td>
                  <td data-l=""><button className="btn btn-sm" disabled={!decl.path.trim()} onClick={declare}>Declare a field</button></td></tr>
              </tbody></table></div>
            <datalist id="entity-names">{entities.map((e) => <option key={e.name} value={e.name} />)}</datalist>
          </section>
          <section className="card" id="recognizer" data-testid="recognizer">
            <div className="card-head"><div className="card-title">Custom recognizer <span className="faint">· {rec.name}</span></div>{draft.recognizers.some((r) => r.name === rec.name) && <Tone tone="amber">in draft</Tone>}</div>
            <div className="rb">
              <div>
                <div className="row2"><div className="rb-field"><label htmlFor="r-name">Name</label><input id="r-name" className="input mono" value={rec.name} onChange={(e) => setRec({ ...rec, name: e.target.value })} /></div>
                  <div className="rb-field"><label htmlFor="r-score">Score</label><input id="r-score" className="input mono" inputMode="decimal" value={rec.score} onChange={(e) => setRec({ ...rec, score: e.target.value })} /></div></div>
                <div className="rb-field"><label htmlFor="r-ent">Entity type</label><input id="r-ent" className="input mono" list="entity-names" value={rec.entity} onChange={(e) => setRec({ ...rec, entity: e.target.value.toUpperCase() })} /></div>
                <div className="rb-field"><label htmlFor="r-rx">Pattern (regular expression)</label><input id="r-rx" className="input mono" value={rec.regex} spellCheck={false} onChange={(e) => setRec({ ...rec, regex: e.target.value })} />
                  <div className="hint">Python <span className="mono">re</span> syntax, as in Presidio recognizers. The live preview uses the browser's engine; <b>Run on the engine</b> is authoritative.</div>
                  {local.error && <div className="callout amber" style={{ marginTop: 8 }}>{local.error}</div>}</div>
                <div className="rb-field"><label htmlFor="r-ctx">Context words</label><input id="r-ctx" className="input" value={rec.context} onChange={(e) => setRec({ ...rec, context: e.target.value })} />
                  <div className="hint">A context word just before a match raises its score.</div></div>
                <div className="rb-field"><label htmlFor="r-allow">Allow list · never match</label><input id="r-allow" className="input mono" value={rec.allow} onChange={(e) => setRec({ ...rec, allow: e.target.value })} />
                  <div className="hint">Exact values that are never reported, such as seeded test fixtures (DPS extension).</div></div>
                <div className="rb-field"><label htmlFor="r-deny">Deny list · always flag</label><input id="r-deny" className="input mono" value={rec.deny} onChange={(e) => setRec({ ...rec, deny: e.target.value })} />
                  <div className="hint">Presidio semantics: exact words that are always reported as this entity.</div></div>
                <div className="row">
                  <button className="btn btn-primary btn-sm" onClick={() => setDraft((d) => ({ ...d, recognizers: [...d.recognizers.filter((r) => r.name !== spec.name), spec] }))}>Add recognizer to draft</button>
                  <button className="btn btn-sm" onClick={() => { void runServer(); }}>Run on the engine</button>
                  <button className="btn btn-sm" disabled={!server} onClick={() => { if (server) copy('yaml', server.yaml); }}>{copied === 'yaml' ? 'Copied' : 'Copy as Presidio YAML'}</button>
                </div>
              </div>
              <div>
                <div className="rb-field"><label htmlFor="r-test">Test text <span className="faint">· not stored</span></label><textarea id="r-test" className="input" rows={4} value={testText} onChange={(e) => setTestText(e.target.value)} /></div>
                <div className="test-head"><span>Live preview</span><span className="faint">{matchCount(local.hits)}</span></div>
                <div className="test-out" data-testid="live-preview">{(() => {
                  const parts: JSX.Element[] = []; let cursor = 0;
                  local.hits.forEach((h, i) => {
                    parts.push(<span key={`s${i}`}>{testText.slice(cursor, h.start)}</span>);
                    parts.push(<mark key={`m${i}`} className={h.allowed ? 'deny' : 'hit'} title={h.allowed ? 'on the allow list: never reported' : undefined}>{testText.slice(h.start, h.end)}</mark>);
                    cursor = h.end;
                  });
                  parts.push(<span key="tail">{testText.slice(cursor)}</span>); return parts;
                })()}</div>
                {server && <><div className="test-head"><span>Engine result</span><span className="faint">{plural(server.count, 'match', 'matches')}</span></div>
                  <div className="matches">{server.matches.map((m) => (
                    <div key={`${m.start}-${m.end}`} className="match"><span className="mono">{testText.slice(m.start, m.end)}</span><span className="chip amber">{m.entity_type} · {m.score.toFixed(2)}</span>
                      <span className="why">{m.evidence}{m.context ? ' + context word' : ''} · offsets {m.start}–{m.end}</span></div>))}
                    {server.matches.length === 0 && <p className="muted">No match on the engine.</p>}</div>
                  <div className="test-head"><span>Presidio recognizer YAML</span></div><pre className="pane">{server.yaml}</pre></>}
              </div>
            </div>
          </section>
          <section className="card" id="plan" data-testid="plan">
            <div className="card-head"><div className="card-title">Plan preview <span className="faint">· {changes ? 'draft' : name} on a pasted sample</span></div>
              <div className="tools"><div className="seg sm" role="group" aria-label="Plan mode">{(['obfuscate', 'synthesize'] as const).map((m) => <button key={m} className={planMode === m ? 'on' : ''} aria-pressed={planMode === m} onClick={() => setPlanMode(m)}>{m}</button>)}</div>
                <button className="btn btn-sm btn-primary" onClick={() => { void runPlan(); }}>Re-run plan</button></div></div>
            <p className="card-sub">The resolved method per finding, with the reason. Nothing is transformed and the sample is not stored. <span className="mono">POST /api/v1/policies/plan</span></p>
            <div className="plan">
              <textarea className="input" rows={18} value={sample} onChange={(e) => setSample(e.target.value)} aria-label="Plan sample" spellCheck={false} />
              <div>
                {summary && <div className="plan-sum">{summary.counts.map(([m, n]) => <span key={m} className="chip slate">{m} ×{n}</span>)}{summary.fallbacks > 0 && <span className="chip amber">{plural(summary.fallbacks, 'fallback')}</span>}</div>}
                <div className="tbl-scroll"><table className="tbl stack"><thead><tr><th>Locator</th><th>Entity · score</th><th>Method</th><th>Reason</th><th>Reversible</th></tr></thead>
                  <tbody>{plan ? plan.plan.map((r, i) => (
                    <tr key={i}><td className="loc" data-l="Locator">{r.path}{r.span ? `[${r.span[0]}:${r.span[1]}]` : ''}</td>
                      <td data-l="Entity"><EntityChip type={r.entity_type} sensitivity={toneFor(byName.get(r.entity_type)?.sensitivity)} /><span className="score">{r.score.toFixed(2)}</span></td>
                      <td data-l="Method"><span className="m">{r.method}</span><Params spec={{ method: r.method, params: r.params }} /></td>
                      <td className="rsn" data-l="Reason">{r.reason.split('|').map((p) => p.startsWith('fallback:') ? <span key={p} className="fbk">{p}</span> : <span key={p}>{p} </span>)}</td>
                      <td data-l="Reversible" className={`revcell${r.reversible ? '' : ' no'}`}>{r.reversible ? 'Yes' : 'No'}</td></tr>))
                    : <tr><td colSpan={5} className="muted">Run the plan to see one row per finding.</td></tr>}
                    {plan && plan.plan.length === 0 && <tr><td colSpan={5} className="muted">Nothing in the sample is detected under this policy.</td></tr>}</tbody></table></div>
              </div>
            </div>
            {summary && summary.fallbacks > 0 && <div className="fb-legend">
              <div><b className="mono">fallback:fpe_domain_too_small</b><p>FF1 needs at least 10⁶ free values; such values run as <span className="mono">det_token</span>, still reversible.</p></div>
              <div><b className="mono">fallback:fpe_disabled</b><p>Every <span className="mono">fpe_*</span> method runs as <span className="mono">det_token</span> while <span className="mono">DPS_FPE_ENABLED=false</span>.</p></div></div>}
          </section>
          <section className="card" id="versions">
            <div className="card-head"><div className="card-title">Version history</div><span className="faint">Published versions and pinning arrive with the policy store (M5).</span></div>
            <div className="ver">
              <div className="vlist">
                {changes > 0 && <div className="vitem" aria-current="true"><div className="v-top"><span className="v-n">draft</span><Tone tone="amber">{plural(changes, 'change')}</Tone></div><div className="v-by">this browser session · not stored</div><div className="v-hash">hash on publish</div></div>}
                <div className="vitem" aria-current={changes === 0 ? 'true' : undefined}><div className="v-top"><span className="v-n">v{doc?.version ?? 1}</span><Tone tone="emerald">shipped preset</Tone></div><div className="v-by">DPS presets</div><div className="v-hash">{preset ? shortHash(preset.hash) : ''}</div></div>
              </div>
              <div>
                <div className="diff-head"><div><div className="t">{changes ? 'Draft changes (inline policy)' : 'Preset (inline policy form)'}</div><div className="s">Use it on any request as <span className="mono">"policy": {'{…}'}</span></div></div>
                  <div className="row"><button className="btn btn-sm" onClick={downloadYaml}>Download YAML</button><button className="btn btn-sm btn-primary" onClick={() => navigate('/playground', { state: { policy: inline } })}>Use in Playground</button></div></div>
                <pre className="diff-pane pane" data-testid="draft-yaml">{yaml}</pre>
              </div>
            </div>
          </section>
        </div>
      </div>
    </main>
  );
}
