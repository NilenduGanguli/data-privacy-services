import { useState } from 'react';
import { saveBlob } from '../lib/api';
import type { KeyMaterial } from '../lib/types';

type Seed = { alg: string; seed: string; created_at: string };

/**
 * The once-only key (obfuscate) or seed (synthesize) of a submitted job. It lives in React state
 * only: Copy JSON and Download hand it to the operator, nothing writes it to storage, and ticking
 * "I have stored the key" hides the JSON from the page.
 */
export function KeyMaterialCard({ keyMaterial, seed, jobId }: { keyMaterial?: KeyMaterial | null; seed?: Seed | null; jobId: string }): JSX.Element | null {
  const [stored, setStored] = useState(false);
  const [copied, setCopied] = useState(false);
  const material = keyMaterial ?? seed;
  if (!material) return null;
  const isKey = !!keyMaterial;
  const text = JSON.stringify(material, null, 2);
  const filename = `dps-${jobId}.${isKey ? 'key' : 'seed'}.json`;

  async function copy(): Promise<void> {
    try {
      await navigator.clipboard.writeText(text);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 2000);
    } catch { setCopied(false); }
  }

  return (
    <section className="card keycard" aria-label={isKey ? 'Job key' : 'Synthesis seed'}>
      <div className="kc-head">
        <div>
          <div className="kc-title">{isKey ? <>Key <span className="mono">{keyMaterial?.kid}</span></> : 'Synthesis seed'}</div>
          <div className="kc-sub">{isKey ? 'The key reverses this job\'s tokens' : 'The seed repeats this job\'s synthetic values'}: shown once — DPS keeps no copy.</div>
        </div>
        <div className="rowx">
          <button type="button" className="btn btn-sm" onClick={() => void copy()} disabled={stored}>{copied ? 'Copied' : 'Copy JSON'}</button>
          <button type="button" className="btn btn-sm btn-primary" onClick={() => saveBlob(new Blob([text], { type: 'application/json' }), filename)}>Download</button>
        </div>
      </div>
      {!stored && <pre className="pane" style={{ marginTop: 12 }} aria-label={isKey ? 'Key JSON' : 'Seed JSON'}>{text}</pre>}
      <label className="stored" style={{ marginTop: 10 }}>
        <input type="checkbox" checked={stored} onChange={(e) => setStored(e.target.checked)} />
        I have stored the {isKey ? 'key' : 'seed'}{stored ? ` (${filename} or your vault)` : ''}
      </label>
      {isKey && <div className="faint" style={{ fontSize: 12, marginTop: 6 }}>Without it the output cannot be revealed, and a retry after the key was purged asks for this file.</div>}
    </section>
  );
}
