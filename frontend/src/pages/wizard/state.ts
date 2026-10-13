/**
 * The New-job wizard's state (mockup 06): one reducer over `WizardDraft`. Credentials, key files,
 * filters and the write confirmation live only here (React state); `draftForStorage` is the only
 * shape that reaches localStorage. Every action that changes what is read clears what was derived
 * from it (a new table drops the filter, the count and the discovery; a new filter drops the count).
 *
 * Two bindings hold after every action, whatever the action (`bind`): a row count stands only for the
 * selection it was taken for (`countKey`), and an In place / Append confirmation stands only while
 * everything it covered is unchanged (`confirmationKey`: rows, filter, columns, mode, write mode,
 * object, key strategy, batch size, partitions). A confirmation needs a row count (spec §7 / D3).
 *
 * A confirmation is used once. A successful Run job (`submitted`) withdraws it and closes the draft:
 * every step is closed and every action except `reset` (Start a new job) is refused, so the stepper
 * cannot bring Run job back under the same confirmation, and the once-only key card (held by
 * `JobWizard`) stays on screen. A second write starts from a new draft and a new confirmation.
 */
import { useReducer } from 'react';
import {
  canConfirmWrite, confirmationKey, countKey, emptyDraft, fqn, inPlaceKeyRewrites, isWriteConfirmed, keyMethodsOf, needsConfirmation, writtenObject,
  type Step, type WizardDraft,
} from '../../lib/jobs';
import { defaultPort, needsTransactions, type SourceEngine } from '../../lib/engines';
import type { ClassificationSnapshot, Connection, ConnectionTestResult, CountOut, TableShape, WriteMode } from '../../lib/types';

export const DRAFT_KEY = 'dps.jobDraft';

export type WizardAction =
  | { type: 'set'; patch: Partial<WizardDraft> }
  | { type: 'step'; step: Step }
  /** A new source engine: every source choice of the old engine is cleared (M3). */
  | { type: 'engine'; engine: SourceEngine }
  | { type: 'connMode'; mode: 'saved' | 'new' }
  | { type: 'useSaved'; connection: Connection | null }
  | { type: 'credMode'; mode: 'secret_ref' | 'inline' }
  | { type: 'production'; on: boolean }
  | { type: 'created'; connection: Connection }
  | { type: 'tested'; result: ConnectionTestResult; at: string }
  | { type: 'owner'; owner: string }
  | { type: 'table'; table: string }
  | { type: 'shape'; shape: TableShape | null }
  | { type: 'selMode'; mode: 'where' | 'query' | 'mongo' }
  | { type: 'filter'; text: string }
  /** `key` is the `countKey` of the draft the count was asked for; a stale answer is ignored. */
  | { type: 'counted'; count: CountOut; at: string; seconds: number; key: string }
  | { type: 'columns'; columns: string[] | null }
  | { type: 'discovering'; jobId: string }
  | { type: 'snapshot'; snapshot: ClassificationSnapshot }
  | { type: 'writeMode'; mode: WriteMode }
  /** `key` is the `confirmationKey` of the draft the dialog was drawn for; a stale one is refused. */
  | { type: 'confirm'; typed: string; at: string; key: string }
  | { type: 'unconfirm' }
  | { type: 'restore'; draft: Partial<WizardDraft> }
  /** Run job (not a dry run) created `jobId`: the confirmation is used and the draft is closed. */
  | { type: 'submitted'; jobId: string }
  | { type: 'reset'; draftId?: string };

const NO_COUNT: Partial<WizardDraft> = { count: null, countedAt: null, countSeconds: null, rows: null, countedKey: null };
const NO_CONFIRMATION: Partial<WizardDraft> = { typed: '', confirmedAt: null, confirmedKey: null };

/** What a new source table or connection invalidates: filter, count, discovery, confirmation. */
const CLEAR_SELECTION: Partial<WizardDraft> = {
  where: '', query: '', mongoFilterText: '', params: {}, ...NO_COUNT, columns: null,
  discoverJobId: null, snapshotId: null, snapshotAccepted: false, policyName: null, keyMethods: null, ...NO_CONFIRMATION,
};
const CLEAR_TABLE: Partial<WizardDraft> = { ...CLEAR_SELECTION, table: '', shape: null };
const CLEAR_SOURCE: Partial<WizardDraft> = { ...CLEAR_TABLE, owner: '', test: null, testedAt: null };

/** Actions that withdraw a confirmation on purpose (no "no longer applies" note). */
const DELIBERATE = new Set<WizardAction['type']>(['unconfirm', 'writeMode', 'restore', 'submitted', 'reset']);

export function wizardReducer(d: WizardDraft, a: WizardAction): WizardDraft {
  if (isSubmitted(d) && a.type !== 'reset') return d;   // a submitted draft only starts over
  return bind(d, reduce(d, a), a);
}

/** Enforce the count and confirmation bindings on the reduced draft. */
function bind(prev: WizardDraft, next: WizardDraft, a: WizardAction): WizardDraft {
  let out = next;
  if ((out.count !== null || out.rows !== null || out.countedKey !== null) && out.countedKey !== countKey(out)) out = { ...out, ...NO_COUNT };
  if ((out.typed || out.confirmedAt !== null || out.confirmedKey !== null) && !isWriteConfirmed(out)) out = { ...out, ...NO_CONFIRMATION };
  if (!out.confirmLapsed && !DELIBERATE.has(a.type) && isWriteConfirmed(prev) && !isWriteConfirmed(out)) out = { ...out, confirmLapsed: true };
  return out;
}

function reduce(d: WizardDraft, a: WizardAction): WizardDraft {
  switch (a.type) {
    case 'set':
      return { ...d, ...a.patch };
    case 'step':
      return { ...d, step: a.step };
    case 'engine':
      return a.engine === d.engine ? d : {
        ...d, ...CLEAR_SOURCE, engine: a.engine, connectionId: null, connectionName: '', connectionTags: [], connectionRoles: [], password: '',
        port: defaultPort(a.engine), database: '', serviceName: '', connOptions: {}, targetConnectionId: null, targetOwner: '', targetTable: '',
        selMode: a.engine === 'mongo' ? 'mongo' : 'where',
      };
    case 'connMode':
      return a.mode === d.connMode ? d : { ...d, ...CLEAR_SOURCE, connMode: a.mode, connectionId: null, connectionName: '', connectionTags: [], connectionRoles: [], password: '' };
    case 'useSaved':
      if (!a.connection) return { ...d, ...CLEAR_SOURCE, connectionId: null, connectionName: '', connectionTags: [], connectionRoles: [], production: false };
      if (a.connection.id === d.connectionId) return d;
      return {
        ...d, ...CLEAR_SOURCE, connMode: 'saved', connectionId: a.connection.id, connectionName: a.connection.name,
        connectionTags: a.connection.tags, connectionRoles: a.connection.roles, production: a.connection.tags.includes('production'),
        engine: a.connection.engine === 'oracle' || a.connection.engine === 'mongo' ? a.connection.engine : 'postgres',
        selMode: a.connection.engine === 'mongo' ? 'mongo' : d.selMode === 'mongo' ? 'where' : d.selMode,
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
      return a.mode === d.selMode ? d : { ...d, selMode: a.mode, ...NO_COUNT };
    case 'filter':
      if (d.selMode === 'mongo') return { ...d, mongoFilterText: a.text, ...NO_COUNT };
      return d.selMode === 'where' ? { ...d, where: a.text, ...NO_COUNT } : { ...d, query: a.text, ...NO_COUNT };
    case 'counted':
      if (a.key !== countKey(d)) return d;   // the selection changed while the count was running
      return { ...d, count: a.count, countedAt: a.at, countSeconds: a.seconds, rows: a.count.rows, countedKey: a.key };
    case 'columns':
      return { ...d, columns: a.columns };
    case 'discovering':
      return { ...d, discoverJobId: a.jobId, snapshotId: null, snapshotAccepted: false, policyName: null, keyMethods: null, ...NO_CONFIRMATION };
    case 'snapshot':
      return {
        ...d, snapshotId: a.snapshot.id, snapshotAccepted: a.snapshot.status === 'accepted', policyName: a.snapshot.policy_name,
        keyMethods: keyMethodsOf(a.snapshot),
      };
    case 'writeMode':
      return a.mode === d.writeMode ? d : { ...d, writeMode: a.mode, ...NO_CONFIRMATION, confirmLapsed: false };
    case 'confirm': {
      // only with a row count, for the name of the written object, in the dialog drawn for this draft;
      // never for an in-place run the server would refuse because it rewrites a key column
      const blocked = !needsConfirmation(d.writeMode) || d.rows === null || a.key !== confirmationKey(d)
        || (d.writeMode === 'in_place' && !keysKeptInPlace(d));
      return canConfirmWrite(a.typed, fqn(writtenObject(d)), blocked)
        ? { ...d, typed: a.typed.trim(), confirmedAt: a.at, confirmedKey: a.key, confirmLapsed: false }
        : d;
    }
    case 'unconfirm':
      return { ...d, ...NO_CONFIRMATION, confirmLapsed: false };
    case 'restore':
      return {
        ...d, ...a.draft, password: '', keyMaterialJson: '', where: '', query: '', mongoFilterText: '', params: {}, ...NO_COUNT, ...NO_CONFIRMATION, confirmLapsed: false,
        submittedJobId: null, keyMethods: null,
      };
    case 'submitted':
      return { ...d, ...NO_CONFIRMATION, confirmLapsed: false, password: '', keyMaterialJson: '', step: 5, submittedJobId: a.jobId };
    case 'reset':
      return emptyDraft(a.draftId);
    default:
      return d;
  }
}

/** Run job created a job from this draft: nothing in it can change or run again until `reset`. */
/** The Idempotency-Key of a run submission and the request it was minted for. */
export interface SubmissionKey { key: string; dry: boolean; body: string }

/**
 * The key for submitting `body` (the serialised RunSpec): the held one while the request is the
 * same (a retry after an error must replay, never run twice), a new one when the request changed
 * — the server refuses a key reused with another body (422 `idempotency_key_reused`, contract §9).
 */
export function submissionKey(held: SubmissionKey | null, dry: boolean, body: string, mint: () => string = () => globalThis.crypto.randomUUID()): SubmissionKey {
  return held && held.dry === dry && held.body === body ? held : { key: mint(), dry, body };
}

export function isSubmitted(d: WizardDraft): boolean {
  return d.submittedJobId !== null;
}

/** The text of the selection mode in use (WHERE, SELECT or the MongoDB filter JSON). */
export function filterTextOf(d: Pick<WizardDraft, 'selMode' | 'where' | 'query' | 'mongoFilterText'>): string {
  return d.selMode === 'mongo' ? d.mongoFilterText : d.selMode === 'where' ? d.where : d.query;
}

/** Whether the step after `step` may open (the Next buttons and the stepper use this). */
export function canLeave(step: Step, d: WizardDraft): boolean {
  if (isSubmitted(d)) return false;
  switch (step) {
    case 1: return !!d.connectionId && !!d.test?.ok;
    case 2: return !!d.table && !!d.shape && (!filterTextOf(d).trim() || !!d.count);
    case 3: return !!d.snapshotId && d.snapshotAccepted;
    case 4: return targetReady(d) && (!needsConfirmation(d.writeMode) || isWriteConfirmed(d));
    default: return true;
  }
}

/** The farthest step the draft may show (every earlier step can be left). */
export function reachable(d: WizardDraft): Step {
  let s: Step = 1;
  while (s < 5 && canLeave(s, d)) s = (s + 1) as Step;
  return s;
}

/**
 * In place and Append on MongoDB write with the ledger in one transaction, so they need a replica
 * set: the tested connection says `capabilities.transactions: false` (the server would refuse the
 * run at prepare with `transactions_unsupported`).
 */
export function transactionsMissing(d: Pick<WizardDraft, 'engine' | 'writeMode' | 'test'>): boolean {
  return d.engine === 'mongo' && needsTransactions(d.writeMode) && d.test?.capabilities?.transactions === false;
}

/** Step 4 has everything its write mode needs (confirmation aside). */
export function targetReady(d: WizardDraft): boolean {
  if (transactionsMissing(d)) return false;
  switch (d.writeMode) {
    case 'table': return !!d.targetConnectionId && !!d.targetTable.trim();
    case 'files': return !!d.filesConnectionId;
    case 'append': return d.appendTarget === 'same' ? true : !!d.targetTable.trim();
    case 'in_place': return d.connectionRoles.includes('target') && keysKeptInPlace(d);
    default: return false;
  }
}

/** The accepted snapshot keeps every key column in the run's mode, as In place requires. */
export function keysKeptInPlace(d: WizardDraft): boolean {
  return inPlaceKeyRewrites(d)?.length === 0;
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
