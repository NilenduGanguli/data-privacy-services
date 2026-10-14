import { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import { useNavigate, useParams } from 'react-router-dom';
import { ConfirmDialog } from '../components/ConfirmDialog';
import { DeclaredFields } from '../components/DeclaredFields';
import { PlanPreview } from '../components/PlanPreview';
import { PolicyList } from '../components/PolicyList';
import { PolicyVersions } from '../components/PolicyVersions';
import { RecognizerBuilder } from '../components/RecognizerBuilder';
import { TreatmentMatrix } from '../components/TreatmentMatrix';
import { useAuth } from '../hooks/useAuth';
import { api, ApiError, errorText, type Download } from '../lib/api';
import { utcDate } from '../lib/format';
import { EMPTY_DRAFT, treatmentRows } from '../lib/policyDraft';
import { addDeclaredField, changedEntities, declaredSince, isTenantPolicy, planPolicy, setTreatment, upsertRecognizer, type Body } from '../lib/policyEdit';
import { Latest, loadPolicyDetail, saveEdit } from '../lib/policyLoad';
import type {
  EntityInfo, PlanOut, PolicyDiff, PolicyDoc, PresetView, RecognizerSpec, RecognizerTestOut, TenantPolicyDetail, TenantPolicyView,
} from '../lib/types';

export interface PolicyActions {
  saveDraft: (body: Body) => Promise<unknown>;
  discardDraft: () => Promise<unknown>;
  publish: (ticket: string, note: string) => Promise<unknown>;
  adopt: () => Promise<unknown>;
  create: (name: string, basePreset: string) => Promise<unknown>;
  select: (v: number | 'draft') => void;
  downloadYaml: (v: number) => Promise<Download>;
  testRecognizer: (rec: RecognizerSpec, text: string, signal: AbortSignal) => Promise<RecognizerTestOut>;
  runPlan: (data: unknown, body: Body | null) => Promise<PlanOut>;
}

export interface PoliciesViewProps {
  name: string; now: Date; store: boolean; presets: PresetView[]; tenantPolicies: TenantPolicyView[]; detail: TenantPolicyDetail | null;
  presetBody: PolicyDoc | null; entities: EntityInfo[]; fpe: boolean | null; readOnly: boolean; versionBodies: Record<number, Body>;
  draftDiff: PolicyDiff | null; selected: number | 'draft' | null; selectedDiff: PolicyDiff | null; error: string | null; actions: PolicyActions;
}

const modeWord = (mode: string | undefined, reversible: boolean | undefined): string => (mode === 'synthesize' ? 'one-way' : reversible === false ? 'irreversible' : 'reversible');

function Facts({ doc }: { doc: PolicyDoc }): JSX.Element {
  return (
    <div className="facts">
      <div><div className="l">Locale</div><div className="v">{doc.locale.default ?? 'service default'}<small>{doc.locale.by_country_field ? `by ${doc.locale.by_country_field}` : doc.locale.default ? 'pinned by the policy' : 'DPS_DEFAULT_LOCALE'}</small></div></div>
      <div><div className="l">Detection tiers</div><div className="v">{doc.detect.tiers.join(', ')}<small>{doc.detect.tiers.includes('llm_label') ? 'LLM labelling on' : 'LLM labelling off'}</small></div></div>
      <div><div className="l">Minimum score</div><div className="v">{doc.detect.min_score.toFixed(2)}<small>noisy-OR per locator</small></div></div>
      <div><div className="l">Consistency</div><div className="v">{doc.consistency.scope}<small>{doc.consistency.scope === 'key' ? 'caller holds the key' : doc.consistency.field_scoped ? 'field scoped' : 'per request'}</small></div></div>
      <div><div className="l">Token style</div><div className="v">{doc.output.token_style}<small>{doc.output.token_style === 'natural' ? 'reveal uses hints' : 'self-describing'}</small></div></div>
      <div><div className="l">Leakage check</div><div className="v">{doc.verify.leakage_check}<small>{doc.verify.leakage_check === 'strict' ? 'fails closed' : 'warns'}</small></div></div>
    </div>
  );
}

/** Policies (mockup 05) as a view over the policy store's state. */
export function PoliciesView(p: PoliciesViewProps): JSX.Element {
  const { detail, actions } = p;
  const preset = p.presets.find((x) => x.name === (detail?.base_preset ?? p.name));
  const body = ((detail?.draft_body ?? detail?.active_body ?? p.presetBody) ?? null) as PolicyDoc | null;
  const editBase = (detail?.draft_body ?? detail?.active_body ?? null) as Body | null;
  const editable = p.store && !!detail && !p.readOnly;
  const active = detail?.active_version ?? preset?.version ?? 1;
  const next = Math.max(active, ...(detail?.versions.map((v) => v.version) ?? [0])) + 1;
  const changed = useMemo(() => changedEntities(p.draftDiff?.changes ?? []), [p.draftDiff]);
  const since = useMemo(() => declaredSince(p.versionBodies, (detail?.draft_body ?? null) as Body | null, next), [p.versionBodies, detail, next]);
  const publisher = (s: string): string => {
    if (s.startsWith('draft')) return detail?.draft_by ?? '—';
    const v = detail?.versions.find((x) => `v${x.version}` === s);
    return v ? (v.published_by ?? 'DPS (shipped preset)') : '—';
  };
  const [planRows, setPlanRows] = useState(0);
  const [copied, setCopied] = useState(false);
  const [dlg, setDlg] = useState<null | 'publish' | 'discard' | 'new'>(null);
  const [ticket, setTicket] = useState('');
  const [note, setNote] = useState('');
  const [newName, setNewName] = useState('');
  const [newBase, setNewBase] = useState('gdpr-pseudonymise');
  const [busy, setBusy] = useState(false);
  const [dlgError, setDlgError] = useState<string | null>(null);
  async function confirm(run: () => Promise<unknown>): Promise<void> {
    setBusy(true); setDlgError(null);
    try { await run(); setDlg(null); setTicket(''); setNote(''); setNewName(''); } catch (e) { setDlgError(errorText(e)); } finally { setBusy(false); }
  }
  const counts = {
    matrix: body ? treatmentRows(body, EMPTY_DRAFT, p.entities, false).length : 0, declared: body?.detect.declared_fields.length ?? 0,
    recognizers: body?.detect.custom_recognizers.length ?? 0, plan: planRows, versions: (detail?.versions.length ?? 1) + (detail?.draft ? 1 : 0),
  };
  const hash = detail?.active_hash ?? preset?.hash ?? '';
  return (
    <main className="page m5-page policies-page">
      <div className="crumbbar">
        <span className="crumb">Policies</span><span className="sep">/</span><span className="muted mono">{p.name}</span>
        <span className="spacer" />
        {detail?.draft && <span className="pill"><span className="dot amber" />Draft v{next} · {detail.draft_changes} {detail.draft_changes === 1 ? 'change' : 'changes'} not published</span>}
        <span className="pill mono">FF1 {p.fpe === null ? 'unknown' : p.fpe ? 'on' : 'off'}</span>
      </div>
      <div className="page-head">
        <div><h1>Policies</h1><p>A policy decides what is detected and what happens to each entity type. Every published version gets its own hash; jobs, API responses and evidence packs record the hash they ran with.</p></div>
        <div className="head-actions">
          {editable && <button className="btn btn-ghost" type="button" disabled={!detail?.draft} onClick={() => setDlg('discard')}>Discard draft</button>}
          <a className="btn" href="#plan">Preview plan</a>
          {editable && <button className="btn btn-primary" type="button" disabled={!detail?.draft} onClick={() => setDlg('publish')}>Publish v{next}</button>}
        </div>
      </div>
      {p.error && <div className="err" role="alert">{p.error}</div>}
      {!p.store && <div className="callout amber">This deployment has no policy store yet: shipped presets are shown read-only. Drafts, versions and tenant policies need the M5 backend.</div>}
      {p.store && p.readOnly && <div className="callout readonly-note">Read-only: drafts and publishing need a key with the admin scope. You can still preview plans and test recognizers.</div>}

      <div className="pol">
        <div className="pol-cell"><PolicyList presets={p.presets} tenant={p.tenantPolicies} selected={p.name} counts={counts} canCreate={p.store && !p.readOnly} onNew={() => { setDlgError(null); setDlg('new'); }} /></div>
        <div className="pol-main">
          <section className="card" data-testid="policy-header">
            <div className="ph-name">{p.name} {preset && <span className={`chip ${preset.mode === 'synthesize' ? 'violet' : 'teal'}`}>{preset.mode} · {modeWord(preset.mode, preset.reversible)}</span>}
              {detail ? <span className="chip emerald">v{active} active</span> : <span className="chip slate">v{active} · shipped</span>}
              {detail?.draft && <span className="chip amber">draft v{next} · {detail.draft_changes} {detail.draft_changes === 1 ? 'change' : 'changes'}</span>}</div>
            <div className="ph-desc">{body?.description ?? preset?.description}{detail?.base_preset && detail.base_preset !== p.name ? <> Based on the shipped preset <b>{detail.base_preset}</b>.</> : null}</div>
            {hash && <div className="ph-hash">{detail ? 'Active hash' : 'Preset hash'} <span className="mono">sha256:{hash.slice(0, 16)}…{hash.slice(-12)}</span>
              <button className="btn btn-sm btn-ghost" type="button" onClick={() => { navigator.clipboard?.writeText(hash).then(() => { setCopied(true); window.setTimeout(() => setCopied(false), 1400); }).catch(() => undefined); }}>{copied ? 'Copied' : 'Copy hash'}</button></div>}
            {detail ? <div className="ph-pub">Published {utcDate(detail.published_at)} by {detail.published_by ?? 'DPS'} · {detail.jobs} {detail.jobs === 1 ? 'job uses' : 'jobs use'} it</div>
              : p.store && <div className="ph-pub">Not adopted by this tenant. {!p.readOnly && <button className="btn btn-sm" type="button" onClick={() => void confirm(actions.adopt)}>Adopt for this tenant</button>}</div>}
            {body && <Facts doc={body} />}
            <div className="callout" style={{ marginTop: 12 }}>{preset?.mode === 'synthesize'
              ? <>Synthetic output carries a <b>measured</b> privacy report rather than a claim of anonymity. Generation is one-way.</>
              : <>Obfuscated output is <b>pseudonymised and still personal data</b> (GDPR Art. 4(5), India's DPDP Act). Only the holder of the key can reverse it; DPS keeps no copy.</>}</div>
          </section>
          {body && <TreatmentMatrix body={body} entities={p.entities} changed={changed} readOnly={!editable} fpe={p.fpe}
            onSet={(entity, spec) => saveEdit(actions.saveDraft, editBase, (b) => setTreatment(b, entity, spec))} />}
        </div>
        <div className="pol-rest">
          {body && <DeclaredFields body={body} entities={p.entities} since={since} publisher={publisher} readOnly={!editable}
            onDeclare={(f) => saveEdit(actions.saveDraft, editBase, (b) => addDeclaredField(b, f))} />}
          <RecognizerBuilder entities={p.entities} readOnly={!editable} testRecognizer={actions.testRecognizer}
            onAdd={(rec) => saveEdit(actions.saveDraft, editBase, (b) => upsertRecognizer(b, rec))} />
          <PlanPreview label={detail?.draft ? `draft v${next}` : `${p.name} v${active}`} body={body as Body | null} fpe={p.fpe} entities={p.entities}
            runPlan={(data) => actions.runPlan(data, (body as Body | null) ?? null)} onRows={setPlanRows} />
          {detail && <PolicyVersions detail={detail} selected={p.selected} diff={p.selectedDiff} now={p.now} onSelect={actions.select} downloadYaml={actions.downloadYaml}
            canPublish={editable} onPublish={() => setDlg('publish')} />}
        </div>
      </div>

      <ConfirmDialog open={dlg === 'publish'} title={`Publish v${next} of ${p.name}`} confirmLabel={`Publish v${next}`} busy={busy} error={dlgError}
        body={<>The draft becomes immutable version {next} with its own hash. Jobs that follow the latest version use it from their next run; pinned jobs keep their version.</>}
        onConfirm={() => void confirm(() => actions.publish(ticket, note))} onCancel={() => setDlg(null)}>
        <div className="frow"><label htmlFor="pub-ticket">Change ticket</label><input id="pub-ticket" className="input" value={ticket} onChange={(e) => setTicket(e.target.value)} placeholder="CHG-48213" /></div>
        <div className="frow"><label htmlFor="pub-note">Note</label><input id="pub-note" className="input" value={note} onChange={(e) => setNote(e.target.value)} placeholder="What changes for the data owners" /></div>
      </ConfirmDialog>
      <ConfirmDialog open={dlg === 'discard'} title="Discard the draft" danger confirmLabel="Discard draft" busy={busy} error={dlgError}
        body={<>The {detail?.draft_changes ?? 0} unpublished {detail?.draft_changes === 1 ? 'change' : 'changes'} of draft v{next} are deleted. Published versions are not affected.</>}
        onConfirm={() => void confirm(actions.discardDraft)} onCancel={() => setDlg(null)} />
      <ConfirmDialog open={dlg === 'new'} title="New policy from a preset" confirmLabel="Create policy" busy={busy} error={dlgError}
        body="Version 1 is a copy of the preset; edit it as a draft and publish v2." onConfirm={() => void confirm(() => actions.create(newName.trim(), newBase))} onCancel={() => setDlg(null)}>
        <div className="frow"><label htmlFor="np-name">Name</label><input id="np-name" className="input mono" value={newName} onChange={(e) => setNewName(e.target.value)} placeholder="retail-kyc" autoComplete="off" spellCheck={false} /></div>
        <div className="frow"><label htmlFor="np-base">Base preset</label><select id="np-base" className="input" value={newBase} onChange={(e) => setNewBase(e.target.value)}>{p.presets.map((x) => <option key={x.name}>{x.name}</option>)}</select></div>
      </ConfirmDialog>
    </main>
  );
}

/** Policies (mockup 05): the tenant policy store (`/policies`, drafts, versions, diffs) with M1's
 *  `/entities`, `/policies/test-recognizer`, `/policies/plan` and `/policies/presets` reused. */
export function Policies(): JSX.Element {
  const auth = useAuth();
  const navigate = useNavigate();
  const { name: routeName } = useParams();
  const [store, setStore] = useState(true);
  const [presets, setPresets] = useState<PresetView[]>([]);
  const [tenantPolicies, setTenantPolicies] = useState<TenantPolicyView[]>([]);
  const [listed, setListed] = useState(false);
  const [detail, setDetail] = useState<TenantPolicyDetail | null>(null);
  const [presetBody, setPresetBody] = useState<PolicyDoc | null>(null);
  const [entities, setEntities] = useState<EntityInfo[]>([]);
  const [fpe, setFpe] = useState<boolean | null>(null);
  const [readOnly, setReadOnly] = useState(false);
  const [versionBodies, setVersionBodies] = useState<Record<number, Body>>({});
  const [draftDiff, setDraftDiff] = useState<PolicyDiff | null>(null);
  const [selected, setSelected] = useState<number | 'draft' | null>(null);
  const [selectedDiff, setSelectedDiff] = useState<PolicyDiff | null>(null);
  const [error, setError] = useState<string | null>(null);
  const name = routeName ?? '';

  const fail = useCallback((e: unknown): void => {
    if (e instanceof ApiError && (e.status === 403 || e.status === 401)) setReadOnly(true); else setError(errorText(e));
  }, []);
  // A new key (or tenant) starts writable again; its first refused write makes the page read-only.
  useEffect(() => { setReadOnly(false); }, [auth]);

  const loadList = useCallback(async (): Promise<void> => {
    try {
      const l = await api.policies.list(auth);
      setStore(true); setPresets(l.presets); setTenantPolicies(l.tenant);
    } catch (e) {
      if (e instanceof ApiError && (e.status === 404 || e.status === 405)) {
        setStore(false); setTenantPolicies([]);
        const ps = await api.policies.presets(auth).catch(() => []);
        setPresets(ps.map((x) => ({ ...x, jobs: 0 })));
      } else setError(errorText(e));
    }
    setListed(true);
  }, [auth]);

  useEffect(() => { void loadList(); }, [loadList]);
  useEffect(() => {
    api.entities(auth).then((r) => setEntities(r.items)).catch(() => setEntities([]));
    api.readyz().then((r) => setFpe(r.components.fpe?.ok ?? null)).catch(() => setFpe(null));
  }, [auth]);
  useEffect(() => {
    if (!routeName && listed) navigate(`/policies/${encodeURIComponent(tenantPolicies[0]?.name ?? 'gdpr-pseudonymise')}`, { replace: true });
  }, [routeName, listed, tenantPolicies, navigate]);

  // a boolean, so a list reload after a save does not reload the detail and reset the selection
  const tenantPolicy = isTenantPolicy(name, store, tenantPolicies);
  // Only the newest load may update the page: a quick switch between policies drops the late
  // answer of the policy the user left (lib/policyLoad.ts).
  const detailLoads = useRef(new Latest());
  const loadDetail = useCallback(async (): Promise<void> => {
    if (!name || !listed) return;
    await loadPolicyDetail(name, tenantPolicy, { get: (n) => api.policies.get(auth, n), validate: (n) => api.policies.validate(auth, n) },
      { detail: setDetail, presetBody: setPresetBody, error: setError }, detailLoads.current.begin());
  }, [auth, name, listed, tenantPolicy]);
  useEffect(() => { setSelected(null); setVersionBodies({}); void loadDetail(); }, [loadDetail]);

  useEffect(() => {
    if (!detail) { setDraftDiff(null); return; }
    const active = detail.active_version ?? 1;
    let live = true; // false once the policy (or its draft) changed: a late answer is dropped
    if (detail.draft) api.policies.diff(auth, detail.name, active, 'draft').then((d) => { if (live) setDraftDiff(d); }).catch(() => { if (live) setDraftDiff(null); });
    else setDraftDiff(null);
    Promise.all(detail.versions.slice(0, 20).map((v) => api.policies.version(auth, detail.name, v.version).then((b) => [v.version, b] as const).catch(() => null)))
      .then((pairs) => { if (live) setVersionBodies(Object.fromEntries(pairs.filter((x): x is readonly [number, Body] => !!x))); });
    return () => { live = false; };
  }, [auth, detail]);

  const sel = selected ?? (detail?.draft ? 'draft' : detail?.active_version ?? null);
  useEffect(() => {
    if (!detail || sel === null) { setSelectedDiff(null); return; }
    if (sel === 'draft') { setSelectedDiff(draftDiff); return; }
    if (sel <= 1) { setSelectedDiff(null); return; }
    setSelectedDiff(null);
    let live = true;
    api.policies.diff(auth, detail.name, sel - 1, sel).then((d) => { if (live) setSelectedDiff(d); }).catch(() => { if (live) setSelectedDiff(null); });
    return () => { live = false; };
  }, [auth, detail, sel, draftDiff]);

  const write = async <T,>(p: Promise<T>): Promise<T> => {
    try { const out = await p; setError(null); return out; } catch (e) { fail(e); throw e; }
  };
  const actions: PolicyActions = {
    saveDraft: async (body) => { const d = await write(api.policies.saveDraft(auth, name, body)); setDetail(d); void loadList(); return d; },
    discardDraft: async () => { const d = await write(api.policies.discardDraft(auth, name)); setDetail(d); setSelected(null); void loadList(); return d; },
    publish: async (ticket, note) => { const v = await write(api.policies.publish(auth, name, ticket, note)); setSelected(v.version); await loadDetail(); void loadList(); return v; },
    adopt: async () => { const d = await write(api.policies.create(auth, name, name)); setDetail(d); void loadList(); return d; },
    create: async (newName, base) => { const d = await write(api.policies.create(auth, newName, base)); await loadList(); navigate(`/policies/${encodeURIComponent(d.name)}`); return d; },
    select: setSelected,
    downloadYaml: (v) => api.policies.versionYaml(auth, name, v),
    testRecognizer: (rec, text, signal) => api.policies.testRecognizer(auth, rec, text, signal),
    runPlan: (data, body) => api.policies.plan(auth, { data, policy: planPolicy(name, body, tenantPolicy), mode: presets.find((x) => x.name === (detail?.base_preset ?? name))?.mode ?? 'obfuscate' }),
  };
  if (!name) return <main className="page m5-page policies-page"><div className="skeleton" /></main>;
  return <PoliciesView name={name} now={new Date()} store={store} presets={presets} tenantPolicies={tenantPolicies} detail={detail} presetBody={presetBody}
    entities={entities} fpe={fpe} readOnly={readOnly} versionBodies={versionBodies} draftDiff={draftDiff} selected={sel} selectedDiff={selectedDiff} error={error} actions={actions} />;
}
