import { useEffect, useMemo, useState } from 'react';
import { ApiError } from '../lib/api';
import { DEFAULT_RECOGNIZER, TEST_TEXT, listOf, recognizerSpec, type RecognizerForm } from '../lib/policyDraft';
import { toPresidioYaml } from '../lib/policyEdit';
import type { EntityInfo, RecognizerSpec, RecognizerTestOut } from '../lib/types';

/** The live test's matches as text with `<mark>` elements (built from spans, never innerHTML). */
export function Highlighted({ text, result }: { text: string; result: RecognizerTestOut | null }): JSX.Element {
  const parts: JSX.Element[] = [];
  let cursor = 0;
  (result?.matches ?? []).filter((m) => m.start >= cursor).forEach((m, i) => {
    parts.push(<span key={`t${i}`}>{text.slice(cursor, m.start)}</span>);
    const deny = m.evidence.startsWith('custom-deny');
    parts.push(<mark key={`m${i}`} className={deny ? 'deny' : 'hit'} title={`${m.entity_type} · ${m.score.toFixed(2)}`}>{text.slice(m.start, m.end)}
      <span className="mk">{m.score.toFixed(2)}{m.context ? ' · context +0.05' : ''}{deny ? ' · deny list' : ''}</span></mark>);
    cursor = m.end;
  });
  parts.push(<span key="tail">{text.slice(cursor)}</span>);
  return <>{parts}</>;
}

const PATTERN_CODES = new Set(['invalid_recognizer', 'unsafe_pattern']);

/** Custom recognizer (mockup 05): the test text runs through `POST /policies/test-recognizer`
 *  (nothing is stored), debounced 400 ms; only the latest request renders. */
export function RecognizerBuilder({ entities, readOnly, onAdd, testRecognizer }: {
  entities: EntityInfo[]; readOnly: boolean; onAdd: (rec: RecognizerSpec) => void;
  testRecognizer: (rec: RecognizerSpec, text: string, signal: AbortSignal) => Promise<RecognizerTestOut>;
}): JSX.Element {
  const [form, setForm] = useState<RecognizerForm>(DEFAULT_RECOGNIZER);
  const [sample, setSample] = useState(TEST_TEXT);
  const [result, setResult] = useState<RecognizerTestOut | null>(null);
  const [patternError, setPatternError] = useState<string | null>(null);
  const [testError, setTestError] = useState<string | null>(null);
  const [copied, setCopied] = useState(false);
  const spec = useMemo(() => recognizerSpec(form), [form]);
  useEffect(() => {
    const ctl = new AbortController();
    const t = window.setTimeout(() => {
      testRecognizer(spec, sample, ctl.signal).then((r) => { if (!ctl.signal.aborted) { setResult(r); setPatternError(null); setTestError(null); } })
        .catch((e: unknown) => {
          if (ctl.signal.aborted || (e instanceof DOMException && e.name === 'AbortError')) return;
          setResult(null);
          if (e instanceof ApiError && e.code && PATTERN_CODES.has(e.code)) { setPatternError(e.message); setTestError(null); } else { setTestError(e instanceof Error ? e.message : 'The live test failed'); }
        });
    }, 400);
    return () => { window.clearTimeout(t); ctl.abort(); };
  }, [spec, sample, testRecognizer]);
  const allowHits = listOf(form.allow).reduce((a, w) => a + (w ? sample.split(w).length - 1 : 0), 0);
  const n = result?.count ?? 0;
  const set = (k: keyof RecognizerForm, v: string): void => setForm((f) => ({ ...f, [k]: v }));
  return (
    <section className="card" id="recognizers" aria-labelledby="rec-t">
      <div className="card-head"><div className="card-title" id="rec-t">Custom recognizer <span className="faint">· {form.name}</span></div></div>
      <div className="rb">
        <div>
          <div className="row2"><div className="rb-field"><label htmlFor="r-name">Name</label><input id="r-name" className="input mono" value={form.name} onChange={(e) => set('name', e.target.value)} /></div>
            <div className="rb-field"><label htmlFor="r-score">Score</label><input id="r-score" className="input mono" inputMode="decimal" value={form.score} onChange={(e) => set('score', e.target.value)} /></div></div>
          <div className="rb-field"><label htmlFor="r-ent">Entity type</label><input id="r-ent" className="input mono" list="rec-entities" value={form.entity} onChange={(e) => set('entity', e.target.value.toUpperCase())} />
            <datalist id="rec-entities">{entities.map((e) => <option key={e.name} value={e.name} />)}</datalist></div>
          <div className="rb-field"><label htmlFor="r-rx">Pattern (regular expression)</label><input id="r-rx" className="input mono" value={form.regex} spellCheck={false} onChange={(e) => set('regex', e.target.value)} aria-describedby="r-rx-err" />
            {patternError && <div className="callout amber" id="r-rx-err" role="alert" style={{ marginTop: 8 }}>{patternError}</div>}
            <div className="hint">Python <span className="mono">re</span> syntax, as in Presidio recognizers; nested quantifiers are refused.</div></div>
          <div className="rb-field"><label htmlFor="r-ctx">Context words</label><input id="r-ctx" className="input" value={form.context} onChange={(e) => set('context', e.target.value)} />
            <div className="hint">A context word just before a match raises its score by 0.05.</div></div>
          <div className="rb-field"><label htmlFor="r-deny">Deny list · always flag</label><input id="r-deny" className="input mono" value={form.deny} onChange={(e) => set('deny', e.target.value)} /></div>
          <div className="rb-field"><label htmlFor="r-allow">Allow list · never match</label><input id="r-allow" className="input mono" value={form.allow} onChange={(e) => set('allow', e.target.value)} />
            <div className="hint">Exact values that are never reported, such as seeded test fixtures.</div></div>
          <div className="row">
            {!readOnly && <button className="btn btn-primary btn-sm" type="button" onClick={() => onAdd(spec)}>Add recognizer to draft</button>}
            <button className="btn btn-sm" type="button" onClick={() => { navigator.clipboard?.writeText(toPresidioYaml(spec)).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1400); }).catch(() => undefined); }}>{copied ? 'Copied' : 'Copy as Presidio YAML'}</button>
          </div>
        </div>
        <div>
          <div className="rb-field"><label htmlFor="r-test">Test text <span className="faint">· not stored</span></label><textarea id="r-test" className="input" rows={4} value={sample} onChange={(e) => setSample(e.target.value)} /></div>
          <div className="test-head"><span>Live test on the engine</span><span className="faint" data-testid="rec-summary">{result ? `${n} ${n === 1 ? 'match' : 'matches'} · ${allowHits} on the allow list` : patternError ? 'pattern refused' : 'testing…'}</span></div>
          <div className="test-out" data-testid="live-test"><Highlighted text={sample} result={result} /></div>
          {testError && <div className="err" role="alert" style={{ marginTop: 8 }}>{testError}</div>}
        </div>
      </div>
    </section>
  );
}
