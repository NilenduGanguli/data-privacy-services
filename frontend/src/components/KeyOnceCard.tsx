import { useState } from 'react';

export interface KeyFingerprint { prefix: string; sha256: string }

/** The value-free fingerprint shown after the key is hidden: its prefix and a shortened SHA-256. */
export async function keyFingerprint(rawKey: string): Promise<KeyFingerprint> {
  const prefix = rawKey.slice(0, 8);
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return { prefix, sha256: '' };
  const digest = new Uint8Array(await subtle.digest('SHA-256', new TextEncoder().encode(rawKey)));
  const hex = Array.from(digest, (b) => b.toString(16).padStart(2, '0')).join('');
  return { prefix, sha256: `${hex.slice(0, 4)}…${hex.slice(-4)}` };
}

/**
 * A new API key, shown exactly once (mockup 11). The raw key lives only in the parent's React
 * state: "I have stored the key" takes the fingerprint and calls `onStored()`, which must drop the
 * key; the card then shows the fingerprint only. Never written to storage or the URL.
 */
export function KeyOnceCard({ name, rawKey, onStored }: { name: string; rawKey: string | null; onStored: () => void }): JSX.Element | null {
  const [fp, setFp] = useState<KeyFingerprint | null>(null);
  const [copied, setCopied] = useState(false);
  if (fp) {
    return (
      <div className="newkey stored" aria-live="polite">
        <div className="t"><span>Key hidden. Calls authenticate against its hash.</span><span className="chip emerald">stored</span></div>
        <div className="mono faint fp">prefix {fp.prefix}{fp.sha256 ? ` · sha256 ${fp.sha256}` : ''}</div>
      </div>
    );
  }
  if (!rawKey) return null;
  const key = rawKey;
  async function stored(): Promise<void> {
    const next = await keyFingerprint(key);
    setFp(next);
    onStored();
  }
  return (
    <div className="newkey" aria-live="polite">
      <div className="t"><span>New key for <span className="mono">{name}</span></span><span className="chip teal">shown once</span></div>
      <div className="keyval"><code>{key}</code>
        <button className="btn btn-sm" type="button" onClick={() => { navigator.clipboard?.writeText(key).then(() => setCopied(true)).catch(() => setCopied(false)); }}>{copied ? 'Copied' : 'Copy key'}</button></div>
      <div className="callout amber sm">Store this key in your secret manager now, for example as <span className="mono">dps/keys/{name}</span>. DPS cannot show it again; if it is lost, rotate it.</div>
      <div className="nk-acts"><button className="btn btn-sm" type="button" onClick={() => void stored()}>I have stored the key</button></div>
    </div>
  );
}
