import { useState, type FormEvent } from 'react';
import { createKeyError, EXPIRY_CHOICES, KEY_NAME, PRIVILEGED, SCOPES } from '../lib/admin';
import { ApiError } from '../lib/api';
import type { CreatedKey, CreateKeyBody } from '../lib/types';
import { KeyOnceCard } from './KeyOnceCard';

/** A raw key shown once: `n` remounts the card for each new key; `key` is dropped (null) once the
 *  operator confirms it is stored. Held in React state only — never storage, URL or logs. */
export interface OneTimeKey { n: number; name: string; key: string | null }

/** Create key (mockup 11): the raw key comes back once; the page holds it (`created`, set through
 *  `onCreated`) so it survives a reload that the server refuses (r1 B1). `showKey` is false when the
 *  page shows the key elsewhere. */
export function CreateKeyCard({ tenants, defaultTenant, onCreate, created, onCreated, onStored, showKey = true }: {
  tenants: string[]; defaultTenant: string; onCreate: (body: CreateKeyBody) => Promise<CreatedKey>;
  created: OneTimeKey | null; onCreated: (out: CreatedKey) => void; onStored: () => void; showKey?: boolean;
}): JSX.Element {
  const [name, setName] = useState('');
  const [tenant, setTenant] = useState('');
  // the tenant list arrives after the first render: fall back to the default, then the first tenant
  const chosen = tenants.includes(tenant) ? tenant : tenants.includes(defaultTenant) ? defaultTenant : tenants[0] ?? defaultTenant;
  const [scopes, setScopes] = useState<Set<string>>(new Set(['detect', 'obfuscate']));
  const [expiry, setExpiry] = useState(90);
  const [nameErr, setNameErr] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: FormEvent): Promise<void> {
    e.preventDefault();
    setErr(null); setNameErr(null);
    if (!KEY_NAME.test(name)) { setNameErr(createKeyError('invalid_name', '')); return; }
    if (!scopes.size) { setErr(createKeyError('invalid_grant', '')); return; }
    setBusy(true);
    try {
      const out = await onCreate({ name, tenant_ids: [chosen], scopes: SCOPES.filter((s) => scopes.has(s)), expires_in_days: expiry });
      onCreated(out);
      setName('');
    } catch (x) {
      const msg = x instanceof ApiError ? createKeyError(x.code, x.message) : 'The key could not be created.';
      if (x instanceof ApiError && x.code === 'invalid_name') setNameErr(msg); else setErr(msg);
    } finally { setBusy(false); }
  }

  return (
    <section className="card" id="createCard" aria-labelledby="createT">
      <div className="card-head"><div className="card-title" id="createT">Create key</div><span className="chip slate">shown once</span></div>
      <form onSubmit={(e) => void submit(e)} noValidate>
        <div className="frow"><label htmlFor="kName">Name</label>
          <input className="input" id="kName" value={name} onChange={(e) => setName(e.target.value)} autoComplete="off" spellCheck={false} placeholder="collections-voice-redaction" aria-describedby="kNameHelp" />
          <div className="help" id="kNameHelp">Lowercase letters, digits and dashes. Name the pipeline, not a person.</div>
          {nameErr && <div className="ferr" role="alert">{nameErr}</div>}</div>
        <div className="frow"><label htmlFor="kTenant">Tenant</label>
          <select className="input" id="kTenant" value={chosen} onChange={(e) => setTenant(e.target.value)}>{tenants.map((t) => <option key={t}>{t}</option>)}</select></div>
        <div className="frow" role="group" aria-labelledby="scopeLbl"><span className="lbl" id="scopeLbl">Scopes</span>
          <div className="scopechecks">{SCOPES.map((s) => (
            <label key={s} className={`check${PRIVILEGED.has(s) ? ' priv' : ''}`}><input type="checkbox" value={s} checked={scopes.has(s)}
              onChange={(e) => setScopes((cur) => { const next = new Set(cur); if (e.target.checked) next.add(s); else next.delete(s); return next; })} />{s}</label>))}</div></div>
        <div className="frow"><span className="lbl">Expires in</span>
          <div className="seg" role="group" aria-label="Expiry">{EXPIRY_CHOICES.map(([d, label]) => (
            <button key={d} type="button" className={expiry === d ? 'on' : ''} aria-pressed={expiry === d} onClick={() => setExpiry(d)}>{label}</button>))}</div></div>
        {err && <div className="err" role="alert" style={{ marginBottom: 10 }}>{err}</div>}
        <div className="rowx" style={{ gap: 10, flexWrap: 'wrap', display: 'flex', alignItems: 'center' }}><button className="btn btn-primary" type="submit" disabled={busy}>{busy ? 'Creating…' : 'Create key'}</button><span className="faint sm">DPS keeps only a SHA-256 hash of the key.</span></div>
      </form>
      {showKey && created && <KeyOnceCard key={created.n} name={created.name} rawKey={created.key} onStored={onStored} />}
    </section>
  );
}
