/**
 * Test factories for the console helpers (Vitest only): a job, a snapshot column and a wizard
 * draft with complete, value-free defaults that each test overrides in part.
 */
import { emptyDraft, type WizardDraft } from './jobs';
import type { ColumnClassification, Job, JobProgress } from './types';

type JobInput = Partial<Omit<Job, 'progress'>> & { progress?: Partial<JobProgress> };

export function progress(p: Partial<JobProgress> = {}): JobProgress {
  return {
    outcome: '', stage: '', rows_estimated: null, rows_read: 0, rows_written: 0, rows_skipped: 0, partitions_total: 0,
    partitions_done: 0, partitions_failed: 0, rows_per_second: null, throughput: [], throughput_mark: null,
    values_by_method: {}, verification: {}, snapshot_ids: [], evidence_id: null, pages: null, fields: null,
    blocked_requests: null, ...p,
  };
}

export function job(j: JobInput = {}): Job {
  const { progress: p, ...rest } = j;
  return {
    id: 'j_01K7ZQ3R8D5W2N6X4H1VTB7F9Q', tenant_id: 'default', kind: 'run', state: 'queued', phase: 'prepare',
    mode: 'obfuscate', group_id: null, connection_id: 'c_1', target_connection_id: null, object_ref: 'bank.customers',
    snapshot_id: 'snap_1', spec: { target: { write_mode: 'table', object: { owner: 'bank_uat', name: 'customers' } } },
    purpose: { ticket: 'CHG-1', legal_basis: 'Legitimate interest — UAT provisioning' }, kid: null, progress: progress(p),
    plan: null, error_code: null, error: null, created_by: 'k_1', created_at: '2026-10-12T09:00:00+00:00',
    updated_at: '2026-10-12T09:00:00+00:00', started_at: null, finished_at: null, expires_at: null, ...rest,
  };
}

export function column(c: Partial<ColumnClassification> = {}): ColumnClassification {
  return {
    path: `$.${c.name ?? 'email'}`, name: 'email', data_type: 'text', kind: 'text', is_key: false, entity_type: 'EMAIL',
    confidence: 0.99, sensitivity: 'MEDIUM', detectors: ['pattern'], value_hit_rate: 0.98, null_rate: 0,
    distinct_ratio: 1, shapes: ['xxxx@xxxx.xxx'], text_column: false, anchor: false,
    suggested: { method: 'email', params: {} }, method: { method: 'email', params: {} }, override: false,
    synthesize_suggested: { method: 'surrogate', params: {} }, synthesize_method: { method: 'surrogate', params: {} },
    type_note: null, ...c,
  };
}

export function draft(d: Partial<WizardDraft> = {}): WizardDraft {
  return {
    ...emptyDraft('d_0a1b2c3d'),
    connectionId: 'c_src', connectionName: 'bank-restore-rw', connectionRoles: ['source', 'target'], secretRef: 'dps/src/bank-restore-rw',
    test: { ok: true, engine: 'postgres', server_version: '16.4', checks: [], capabilities: {}, error_code: null },
    owner: 'bank_restore', table: 'contacts', snapshotId: 'snap_1', snapshotAccepted: true, policyName: 'gdpr-pseudonymise',
    targetConnectionId: 'c_tgt', targetOwner: 'bank_uat', targetTable: 'contacts', writeMode: 'table',
    ticket: 'CHG-12345', ...d,
  };
}
