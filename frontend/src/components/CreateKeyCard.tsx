import { useState, type FormEvent } from 'react';
import { createKeyError, EXPIRY_CHOICES, KEY_NAME, PRIVILEGED, SCOPES } from '../lib/admin';
import { ApiError } from '../lib/api';
import type { CreatedKey, CreateKeyBody } from '../lib/types';
import { KeyOnceCard } from './KeyOnceCard';

/** Create key (mockup 11): the raw key comes back once and lives only in this card's state until
 *  the operator confirms it is stored. */
export function CreateKeyCard({ tenants, defaultTenant, onCreate }: {
  tenants: string[]; defaultTenant: string; onCreate: (body: CreateKeyBody) => Promise<CreatedKey>;
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
  const [created, setCreated] = useState<{ n: number; name: string; key: string | null } | null>(null);

  async function submit(e: FormEvent): Promise<void> {
    e.preventDefault();
    setErr(null); setNameErr(null);
    if (!KEY_NAME.test(name)) { setNameErr(createKeyError('invalid_name', '')); return; }
    if (!scopes.size) { setErr(createKeyError('invalid_scopes', '')); return; }
    setBusy(true);
    try {
      const out = await onCreate({ name, tenant_ids: [chosen], scopes: SCOPES.filter((s) => scopes.has(s)), expires_in_days: expiry });
      setCreated((c) => ({ n: (c?.n ?? 0) + 1, name: out.record.name, key: out.key }));
      setName('');
    } catch (x) {
      const msg = x instanceof ApiError ? createKeyError(x.code, x.message) : 'The key could not be created.';
      if (x instanceof ApiError && (x.code === 'invalid_name' || x.code === 'key_name_exists')) setNameErr(msg); else setErr(msg);
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
      {created && <KeyOnceCard key={created.n} name={created.name} rawKey={created.key} onStored={() => setCreated((c) => (c ? { ...c, key: null } : c))} />}
    </section>
  );
}
