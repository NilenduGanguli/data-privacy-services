import { useState } from 'react';
import { api, ApiError, saveBlob, type Auth } from '../../lib/api';
import { groupKeyFiles } from '../../lib/groups';
import type { JobGroup, KeyMaterial } from '../../lib/types';

const NAME_RE = /^[a-z0-9][a-z0-9-]{1,62}$/;
type Made = { group: JobGroup; key_material: KeyMaterial; synthesis_seed: Record<string, unknown> };

/**
 * New job group: a name, then the group's key and seed shown **once** as two downloads
 * (`.dpskey`, `.dpsseed`, built in memory). Done stays off until the operator ticks that both are
 * stored; closing drops the material with this component's state. Nothing reaches storage.
 */
export function NewGroupDialog({ auth, onClose }: { auth: Auth; onClose: (created: JobGroup | null) => void }): JSX.Element {
  const [name, setName] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [made, setMade] = useState<Made | null>(null);
  const [stored, setStored] = useState(false);
  const [saved, setSaved] = useState<{ key: boolean; seed: boolean }>({ key: false, seed: false });

  async function create(): Promise<void> {
    if (!NAME_RE.test(name)) { setError('Name: lower-case letters, digits and hyphens, 2 to 63 characters, starting with a letter or digit.'); return; }
    setBusy(true); setError(null);
    try {
      setMade((await api.groups.create(auth, name)) as Made);
    } catch (e) {
      setError(e instanceof ApiError ? (e.code === 'group_name_taken' ? 'A job group with this name exists. Choose another name.' : `${e.message}${e.code ? ` (${e.code})` : ''}`) : String(e));
    } finally { setBusy(false); }
  }

  function download(which: 'key' | 'seed'): void {
    if (!made) return;
    const file = groupKeyFiles({ group: made.group, key_material: made.key_material as unknown as Record<string, unknown>, synthesis_seed: made.synthesis_seed })[which];
    saveBlob(new Blob([file.text], { type: 'application/json' }), file.name);
    setSaved((s) => ({ ...s, [which]: true }));
  }

  function close(): void {
    const group = made?.group ?? null;
    setMade(null);   // the key and seed leave React state here
    onClose(group);
  }

  return (
    <div className="modal-scrim" onMouseDown={(e) => { if (e.target === e.currentTarget && !made) onClose(null); }}>
      <div className="confirm newgroup" role="dialog" aria-modal="true" aria-labelledby="ng-title"
           onKeyDown={(e) => { if (e.key === 'Escape' && !made) onClose(null); }}>
        <h3 id="ng-title">New job group</h3>
        {!made ? (
          <>
            <p className="lead">Runs of one group share a consistency key (and a synthesis seed), so the same customer gets the same token in every system. DPS returns the key and the seed once and keeps neither.</p>
            <label className="lbl" htmlFor="ng-name">Group name</label>
            <input className="input mono" id="ng-name" value={name} autoFocus spellCheck={false} placeholder="crm-uat-refresh"
                   onChange={(e) => { setName(e.target.value); setError(null); }} onKeyDown={(e) => { if (e.key === 'Enter') void create(); }} />
            <div className="help">Lower-case letters, digits and hyphens.</div>
            {error && <div className="err" style={{ marginTop: 10 }}>{error}</div>}
            <div className="rowx" style={{ justifyContent: 'flex-end', marginTop: 14 }}>
              <button className="btn" type="button" onClick={() => onClose(null)}>Cancel</button>
              <button className="btn btn-primary" type="button" disabled={busy || !name} onClick={() => void create()}>{busy ? 'Creating…' : 'Create'}</button>
            </div>
          </>
        ) : (
          <section aria-label="Group key and seed">
            <p className="lead">Group <span className="mono">{made.group.name}</span> is created. Save its key and seed now: they are shown once, and every run of the group needs them.</p>
            <dl className="kv ngkv">
              <dt>Key id</dt><dd className="mono">{made.group.kid}</dd>
              <dt>Seed fingerprint</dt><dd className="mono">{made.group.seed_fingerprint}</dd>
            </dl>
            <div className="rowx" style={{ marginTop: 12 }}>
              <button className="btn btn-sm btn-primary" type="button" onClick={() => download('key')}>Download key (.dpskey)</button>
              <button className="btn btn-sm btn-primary" type="button" onClick={() => download('seed')}>Download seed (.dpsseed)</button>
              {(saved.key || saved.seed) && <span className="faint sm">{[saved.key ? 'key' : null, saved.seed ? 'seed' : null].filter(Boolean).join(' and ')} downloaded</span>}
            </div>
            <label className="stored" style={{ marginTop: 12, display: 'flex', gap: 8, alignItems: 'center' }}>
              <input type="checkbox" checked={stored} onChange={(e) => setStored(e.target.checked)} />
              I have stored the key and the seed
            </label>
            <div className="faint sm" style={{ marginTop: 6 }}>Without them the group's tokens cannot be revealed and no further job can join the group.</div>
            <div className="rowx" style={{ justifyContent: 'flex-end', marginTop: 14 }}>
              <button className="btn btn-primary" type="button" disabled={!stored} onClick={close}>Done</button>
            </div>
          </section>
        )}
      </div>
    </div>
  );
}
