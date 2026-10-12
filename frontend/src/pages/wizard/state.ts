/**
 * The New-job wizard's state (mockup 06): one reducer over `WizardDraft`. Credentials, key files,
 * filters and the write confirmation live only here (React state); `draftForStorage` is the only
 * shape that reaches localStorage. Every action that changes what is read clears what was derived
 * from it (a new table drops the filter, the count and the discovery; a new filter drops the count).
 */
import { useReducer } from 'react';
import { emptyDraft, needsConfirmation, canConfirmWrite, fqn, writtenObject, type Step, type WizardDraft } from '../../lib/jobs';
import type { ClassificationSnapshot, Connection, ConnectionTestResult, CountOut, TableShape, WriteMode } from '../../lib/types';

export const DRAFT_KEY = 'dps.jobDraft';

export type WizardAction =
  | { type: 'set'; patch: Partial<WizardDraft> }
  | { type: 'step'; step: Step }
  | { type: 'connMode'; mode: 'saved' | 'new' }
  | { type: 'useSaved'; connection: Connection | null }
  | { type: 'credMode'; mode: 'secret_ref' | 'inline' }
  | { type: 'production'; on: boolean }
  | { type: 'created'; connection: Connection }
  | { type: 'tested'; result: ConnectionTestResult; at: string }
  | { type: 'owner'; owner: string }
  | { type: 'table'; table: string }
  | { type: 'shape'; shape: TableShape | null }
  | { type: 'selMode'; mode: 'where' | 'query' }
  | { type: 'filter'; text: string }
  | { type: 'counted'; count: CountOut; at: string; seconds: number }
  | { type: 'columns'; columns: string[] | null }
  | { type: 'discovering'; jobId: string }
  | { type: 'snapshot'; snapshot: ClassificationSnapshot }
  | { type: 'writeMode'; mode: WriteMode }
  | { type: 'confirm'; typed: string; at: string }
  | { type: 'unconfirm' }
  | { type: 'restore'; draft: Partial<WizardDraft> }
  | { type: 'reset'; draftId?: string };

/** What a new source table or connection invalidates: filter, count, discovery, confirmation. */
const CLEAR_SELECTION: Partial<WizardDraft> = {
  where: '', query: '', params: {}, count: null, countedAt: null, countSeconds: null, rows: null, columns: null,
  discoverJobId: null, snapshotId: null, snapshotAccepted: false, policyName: null, typed: '', confirmedAt: null,
};
const CLEAR_TABLE: Partial<WizardDraft> = { ...CLEAR_SELECTION, table: '', shape: null };
const CLEAR_SOURCE: Partial<WizardDraft> = { ...CLEAR_TABLE, owner: '', test: null, testedAt: null };

export function wizardReducer(d: WizardDraft, a: WizardAction): WizardDraft {
  switch (a.type) {
    case 'set':
      return { ...d, ...a.patch };
    case 'step':
      return { ...d, step: a.step };
    case 'connMode':
      return a.mode === d.connMode ? d : { ...d, ...CLEAR_SOURCE, connMode: a.mode, connectionId: null, connectionName: '', connectionTags: [], connectionRoles: [], password: '' };
    case 'useSaved':
      if (!a.connection) return { ...d, ...CLEAR_SOURCE, connectionId: null, connectionName: '', connectionTags: [], connectionRoles: [], production: false };
      if (a.connection.id === d.connectionId) return d;
      return {
        ...d, ...CLEAR_SOURCE, connMode: 'saved', connectionId: a.connection.id, connectionName: a.connection.name,
        connectionTags: a.connection.tags, connectionRoles: a.connection.roles, production: a.connection.tags.includes('production'),
        engine: a.connection.engine === 'oracle' || a.connection.engine === 'mongo' ? a.connection.engine : 'postgres',
      };
    case 'credMode':
      // a production-tagged connection must use a secret reference (contract §5.1)
      if (a.mode === 'inline' && d.production) return d;
      return { ...d, credMode: a.mode, password: a.mode === 'inline' ? d.password : '' };
    case 'production':
      return a.on ? { ...d, production: true, credMode: 'secret_ref', password: '' } : { ...d, production: false };
    case 'created':
      return {
        ...d, connectionId: a.connection.id, connectionName: a.connection.name, connectionTags: a.connection.tags,
        connectionRoles: a.connection.roles, password: '', test: null, testedAt: null,
      };
    case 'tested':
      return { ...d, test: a.result, testedAt: a.at };
    case 'owner':
      return a.owner === d.owner ? d : { ...d, ...CLEAR_TABLE, owner: a.owner };
    case 'table':
      return a.table === d.table ? d : { ...d, ...CLEAR_SELECTION, table: a.table, shape: null };
    case 'shape':
      return { ...d, shape: a.shape };
    case 'selMode':
      return a.mode === d.selMode ? d : { ...d, selMode: a.mode, count: null, countedAt: null, countSeconds: null, rows: null };
    case 'filter':
      return d.selMode === 'where'
        ? { ...d, where: a.text, count: null, countedAt: null, countSeconds: null, rows: null }
        : { ...d, query: a.text, count: null, countedAt: null, countSeconds: null, rows: null };
    case 'counted':
      return { ...d, count: a.count, countedAt: a.at, countSeconds: a.seconds, rows: a.count.rows };
    case 'columns':
      return { ...d, columns: a.columns };
    case 'discovering':
      return { ...d, discoverJobId: a.jobId, snapshotId: null, snapshotAccepted: false, policyName: null, typed: '', confirmedAt: null };
    case 'snapshot':
      return { ...d, snapshotId: a.snapshot.id, snapshotAccepted: a.snapshot.status === 'accepted', policyName: a.snapshot.policy_name };
    case 'writeMode':
      return a.mode === d.writeMode ? d : { ...d, writeMode: a.mode, typed: '', confirmedAt: null };
    case 'confirm': {
      const blocked = !needsConfirmation(d.writeMode);
      return canConfirmWrite(a.typed, fqn(writtenObject(d)), blocked) ? { ...d, typed: a.typed.trim(), confirmedAt: a.at } : d;
    }
    case 'unconfirm':
      return { ...d, typed: '', confirmedAt: null };
    case 'restore':
      return { ...d, ...a.draft, password: '', keyMaterialJson: '', where: '', query: '', params: {}, count: null, typed: '', confirmedAt: null };
    case 'reset':
      return emptyDraft(a.draftId);
    default:
      return d;
  }
}

/** Whether the step after `step` may open (the Next buttons and the stepper use this). */
export function canLeave(step: Step, d: WizardDraft): boolean {
  switch (step) {
    case 1: return !!d.connectionId && !!d.test?.ok;
    case 2: return !!d.table && !!d.shape && (!(d.selMode === 'where' ? d.where : d.query).trim() || !!d.count);
    case 3: return !!d.snapshotId && d.snapshotAccepted;
    case 4: return targetReady(d) && (!needsConfirmation(d.writeMode) || !!d.typed);
    default: return true;
  }
}

/** The farthest step the draft may show (every earlier step can be left). */
export function reachable(d: WizardDraft): Step {
  let s: Step = 1;
  while (s < 5 && canLeave(s, d)) s = (s + 1) as Step;
  return s;
}

/** Step 4 has everything its write mode needs (confirmation aside). */
export function targetReady(d: WizardDraft): boolean {
  switch (d.writeMode) {
    case 'table': return !!d.targetConnectionId && !!d.targetTable.trim();
    case 'files': return !!d.filesConnectionId;
    case 'append': return d.appendTarget === 'same' ? true : !!d.targetTable.trim();
    case 'in_place': return d.connectionRoles.includes('target');
    default: return false;
  }
}

/** The wizard's reducer with a starting draft. */
export function useWizard(initial: () => WizardDraft): [WizardDraft, React.Dispatch<WizardAction>] {
  return useReducer(wizardReducer, undefined, initial);
}

/** The saved draft, if one is stored (storage can be missing or throw). */
export function loadSavedDraft(): Partial<WizardDraft> | null {
  try {
    const raw = localStorage.getItem(DRAFT_KEY);
    return raw ? (JSON.parse(raw) as Partial<WizardDraft>) : null;
  } catch { return null; }
}

/** Remove the saved draft (after a run is submitted or on Cancel → Discard). */
export function clearSavedDraft(): void {
  try { localStorage.removeItem(DRAFT_KEY); } catch { /* private mode */ }
}
