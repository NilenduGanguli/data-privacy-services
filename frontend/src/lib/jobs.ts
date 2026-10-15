/**
 * Pure helpers the job pages share (Jobs 07, Job detail 03, New job 06, Evidence 10, Reports 09).
 * Formatting is locale-stable (en-US grouping). Nothing here touches storage, the network or the
 * DOM, and nothing here ever formats a customer value: jobs, snapshots and packs hold names,
 * counts, codes and hashes only.
 */
import { mongoFilterTemplate, parseMongoFilter } from './engines';
import type {
  ClassificationSnapshot, ColumnClassification, ConnectionTestResult, CountOut, Job, MethodSpec, ObjectRef, PkStrategy, RunSpec, Selection,
  SnapshotOverride, TableShape, TargetSpec, WriteMode,
} from './types';

export type Tone = 'teal' | 'amber' | 'emerald' | 'rose' | 'violet' | 'slate';

const TERMINAL = new Set(['succeeded', 'failed', 'dead', 'canceled']);

/** True once a job can no longer change (pages stop polling it). */
export function isTerminal(job: Pick<Job, 'state'> | undefined | null): boolean {
  return !!job && TERMINAL.has(job.state);
}

/** The status chip of the jobs mockup (07): label and signal colour. */
export function jobStatus(job: Job, proposedSnapshotIds: Set<string> = new Set()): { label: string; tone: Tone } {
  if (job.kind === 'discover' && job.state === 'succeeded') {
    return job.progress.snapshot_ids.some((id) => proposedSnapshotIds.has(id))
      ? { label: 'awaiting review', tone: 'amber' }
      : { label: 'discovered', tone: 'emerald' };
  }
  if (job.progress.outcome === 'dry_run' && job.state === 'succeeded') return { label: 'dry run', tone: 'violet' };
  switch (job.state) {
    case 'running':
    case 'waiting':
      return job.phase === 'finalize' ? { label: 'finalizing', tone: 'teal' } : { label: 'running', tone: 'teal' };
    case 'queued':
      return job.phase === 'finalize' ? { label: 'finalizing', tone: 'teal' } : { label: 'queued', tone: 'slate' };
    case 'paused':
      return { label: 'paused', tone: 'amber' };
    case 'succeeded':
      return { label: job.kind === 'run' ? 'evidence ready' : 'succeeded', tone: 'emerald' };
    case 'failed':
    case 'dead':
      return { label: 'failed', tone: 'rose' };
    case 'canceled':
      return { label: 'canceled', tone: 'slate' };
    default:
      return { label: job.state, tone: 'slate' };
  }
}

/** "in place" | "append" | "copy to table" (+ " · truncate + insert" / " · upsert") | "export files". */
export function writeModeLabel(mode: WriteMode | string | undefined | null, load?: string | null): string {
  switch (mode) {
    case 'in_place': return 'in place';
    case 'append': return 'append';
    case 'table': return `copy to table${load === 'truncate_insert' ? ' · truncate + insert' : load === 'upsert' ? ' · upsert' : ''}`;
    case 'files': return 'export files';
    default: return '—';
  }
}

/** Tone of the write-mode chip: in place rewrites the source (rose), append grows it (amber). */
export function writeModeTone(mode: string | undefined | null): Tone {
  return mode === 'in_place' ? 'rose' : mode === 'append' ? 'amber' : 'slate';
}

/** The mockups' square engine badge. */
export function engineBadge(engine: string | undefined | null): 'PG' | 'ORA' | 'MDB' | 'S3' | 'PVC' | '—' {
  switch (engine) {
    case 'postgres': return 'PG';
    case 'oracle': return 'ORA';
    case 'mongo': return 'MDB';
    case 's3': return 'S3';
    case 'pvc': return 'PVC';
    default: return '—';
  }
}

/** A job's connection in words (S1-M4 console r1 B1): its name, or — once it was deleted (a finished
 * job keeps its history) — "connection deleted (c_…)"; its short id while it is not resolved yet. */
export function connectionLabel(id: string | null | undefined, resolved: Map<string, { name: string } | null>): string {
  if (!id) return '—';
  const conn = resolved.get(id);
  if (conn === null) return `connection deleted (${shortId(id)})`;
  return conn ? conn.name : shortId(id);
}

/** A job's head title: engine · object, then where it wrote (`engine` from its source connection, or
 * from its snapshot once that connection was deleted). */
export function jobTitle(j: Job, engine: string | null | undefined): string {
  const wm = jobWriteMode(j);
  return `${engineName(engine)} · ${j.object_ref ?? '—'}${j.kind === 'discover' ? '' : wm === 'in_place' ? ' · rewritten in place' : ` → ${jobTargetText(j)}`}`;
}

/** Engine name in words. */
export function engineName(engine: string | undefined | null): string {
  return ({ postgres: 'PostgreSQL', oracle: 'Oracle', mongo: 'MongoDB', s3: 'S3', pvc: 'PVC' } as Record<string, string>)[engine ?? ''] ?? (engine || '—');
}

/** rows_written / rows_estimated in whole percent, capped at 100; null before the estimate. */
export function progressPct(job: Job): number | null {
  const { rows_estimated: est, rows_written: done } = job.progress;
  if (est === null || est === undefined) return null;
  if (est <= 0) return job.state === 'succeeded' ? 100 : 0;
  return Math.min(100, Math.floor((done / est) * 100));
}

/** Seconds left at the current rate, or null without an estimate or a rate. */
export function etaSeconds(job: Job): number | null {
  const { rows_estimated: est, rows_written: done, rows_per_second: rate } = job.progress;
  if (est === null || est === undefined || !rate || rate <= 0) return null;
  return Math.max(0, Math.round((est - done) / rate));
}

const COUNT = new Intl.NumberFormat('en-US');

/** 1948210 → "1,948,210"; null → "—". */
export function fmtCount(n: number | null | undefined): string {
  return n === null || n === undefined || Number.isNaN(n) ? '—' : COUNT.format(Math.round(n));
}

/** 12200 → "12.2k"; 4_000_000 → "4M"; small numbers as counts. */
export function fmtCompact(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  const abs = Math.abs(n);
  const one = (v: number) => (Math.round(v * 10) / 10).toString();
  if (abs >= 1e9) return `${one(n / 1e9)}B`;
  if (abs >= 1e6) return `${one(n / 1e6)}M`;
  if (abs >= 1e4) return `${one(n / 1e3)}k`;
  return fmtCount(n);
}

/** 472 → "7 min 52 s"; 41 → "41 s"; 3725 → "1 h 2 min"; null → "—". */
export function fmtDuration(seconds: number | null | undefined): string {
  if (seconds === null || seconds === undefined || Number.isNaN(seconds)) return '—';
  const s = Math.max(0, Math.round(seconds));
  if (s < 60) return `${s} s`;
  if (s < 3600) return `${Math.floor(s / 60)} min ${s % 60} s`;
  return `${Math.floor(s / 3600)} h ${Math.floor((s % 3600) / 60)} min`;
}

/** "15:16:47" in the browser's time zone. */
export function fmtClock(iso: string | Date | null | undefined, withSeconds = true): string {
  if (!iso) return '—';
  const d = typeof iso === 'string' ? new Date(iso) : iso;
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', ...(withSeconds ? { second: '2-digit' } : {}) });
}

/** "today 15:12" / "Oct 10, 09:15" in the browser's time zone. */
export function fmtWhen(iso: string | null | undefined, now: Date = new Date()): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit' });
  if (d.toDateString() === now.toDateString()) return `today ${time}`;
  return `${d.toLocaleDateString('en-US', { month: 'short', day: 'numeric' })}, ${time}`;
}

/** "11 Oct 2026, 09:49:12 UTC". */
export function fmtUtc(iso: string | null | undefined, withSeconds = true): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  const date = d.toLocaleDateString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', timeZone: 'UTC' });
  const time = d.toLocaleTimeString('en-GB', { hour: '2-digit', minute: '2-digit', ...(withSeconds ? { second: '2-digit' } : {}), timeZone: 'UTC' });
  return `${date}, ${time} UTC`;
}

/** `j_01K7ZQ3R8D5W2N6X4H1VTB7F9Q` → `j_01K7ZQ…9Q`. */
export function shortId(id: string | null | undefined): string {
  if (!id) return '—';
  const cut = id.indexOf('_') + 1;
  return id.length <= cut + 10 ? id : `${id.slice(0, cut + 6)}…${id.slice(-2)}`;
}

const num = (v: number): string => String(Math.round(v * 10) / 10);

/** SVG path `M x y L …` over `points` scaled to width × height, baseline 0 at the bottom. */
export function sparkPath(points: [string, number][], width: number, height: number): string {
  if (!points.length) return '';
  const max = Math.max(0, ...points.map(([, v]) => v));
  const y = (v: number) => (max > 0 ? height - (Math.max(0, v) / max) * height : height);
  if (points.length === 1) return `M0 ${num(y(points[0][1]))} L${num(width)} ${num(y(points[0][1]))}`;
  const step = width / (points.length - 1);
  return points.map(([, v], i) => `${i ? 'L' : 'M'}${num(i * step)} ${num(y(v))}`).join(' ');
}

/** Split `values_by_method` counters: methods, `fallback:<reason>`, `col:<column>` (others dropped). */
export function splitCounters(vbm: Record<string, number>): { methods: Record<string, number>; fallbacks: Record<string, number>; columns: Record<string, number> } {
  const methods: Record<string, number> = {};
  const fallbacks: Record<string, number> = {};
  const columns: Record<string, number> = {};
  for (const [key, value] of Object.entries(vbm ?? {})) {
    // the engine's reason may already carry the prefix (`fallback:fallback:fpe_domain_too_small`)
    if (key.startsWith('fallback:')) fallbacks[key.replace(/^(fallback:)+/, '')] = (fallbacks[key.replace(/^(fallback:)+/, '')] ?? 0) + value;
    else if (key.startsWith('col:')) columns[key.slice('col:'.length)] = value;
    else if (!key.includes(':')) methods[key] = value;
  }
  return { methods, fallbacks, columns };
}

/** Sum of a counter map. */
export function total(map: Record<string, number> | null | undefined): number {
  return Object.values(map ?? {}).reduce((a, b) => a + (Number(b) || 0), 0);
}

const DECISION_NOTES = new Set(['fpe_domain_too_small', 'numeric_column', 'date_column', 'text_pipeline', 'mixed_entities', 'kept_with_hits', 'length_overflow', 'length_kept']);

/** A column a reviewer should look at: low confidence, a fallback note, free text, values of
 *  several personal-data types, or a kept column the detectors still found personal data in. */
export function needsDecision(col: ColumnClassification): boolean {
  return (!!col.entity_type && col.confidence < 0.8) || (!!col.type_note && DECISION_NOTES.has(col.type_note)) || col.text_column;
}

/** A kept column the detectors still found personal data in (never "not personal"). */
export function keptWithHits(col: ColumnClassification): boolean {
  return !col.entity_type && !col.text_column && (col.value_entities?.length ?? 0) > 0;
}

/** The confidence cell of a kept column: its detector hit rate when it has hits, else "—". */
export function keptHitText(col: ColumnClassification): string {
  return keptWithHits(col) ? `${Math.max(1, Math.round(col.value_hit_rate * 100))} % hits` : '—';
}

/** Heat-map tone: critical (rose), sensitive (amber), declared (teal), kept (muted). */
export function heatTone(col: ColumnClassification): 'crit' | 'sens' | 'decl' | 'kept' {
  if (!col.entity_type) return 'kept';
  if (col.sensitivity === 'CRITICAL') return 'crit';
  if (col.detectors.includes('declared')) return 'decl';
  return 'sens';
}

/** Why a column needs a decision, in words (value-free: rates and notes only). */
export function decisionReason(col: ColumnClassification): string {
  if (col.text_column) return `Free text with personal data in ${Math.round(col.value_hit_rate * 100)} % of sampled rows.`;
  const types = (col.value_entities ?? []).join(', ');
  if (col.type_note === 'mixed_entities') return `Personal data of several types in one column${types ? ` (${types})` : ''}: every value is replaced whole.`;
  if (col.type_note === 'kept_with_hits') return `Kept, but the detectors found ${types || 'personal data'} in ${Math.round(col.value_hit_rate * 100)} % of sampled rows. Classify it, or keep it on purpose.`;
  if (col.type_note === 'length_overflow') return `The suggested ${col.suggested.method} output (or the token FPE falls back to on these values) is longer than ${col.data_type} allows. A copy to a new table or files takes it; In place and Append to this table refuse it. To write it back here, choose a method that keeps the length (fpe_digits, mask_partial, or hmac_pseudonym with a shorter length).`;
  if (col.type_note === 'length_kept') return `Tokens would not fit ${col.data_type}, so format-preserving encryption of every digit keeps the length (reversible with the key).`;
  const note = typeNoteText(col.type_note);
  if (note) return note;
  return `The detectors agree on ${col.entity_type ?? 'this type'} with confidence ${col.confidence.toFixed(2)} only.`;
}

/** `type_note` codes of discovery (dps/layers/connectors/discovery.py) in words. */
export function typeNoteText(note: string | null | undefined): string | null {
  switch (note) {
    case 'fpe_domain_too_small': return 'Fewer than 10⁶ possible values, so format-preserving encryption would be easy to reverse.';
    case 'fpe_disabled': return 'Format-preserving encryption is off in this deployment, so the column is tokenized instead.';
    case 'numeric_column': return 'Numeric column: the policy method would not fit, so it is nulled or kept.';
    case 'numeric_key_kept': return 'Numeric key: kept so rows stay addressable.';
    case 'date_column': return 'Date column: dates are shifted instead of the policy method.';
    case 'type_kept': return 'UUID or boolean column: kept as it is.';
    case 'binary_kept': return 'Binary column: never transformed.';
    case 'text_pipeline': return 'Free text: personal data inside it is replaced span by span.';
    case 'synthesize_key_kept': return 'Key or join column: synthesize mode keeps it so joins still work.';
    case 'synthesize_numeric': return 'Sensitive number: synthesize mode nulls or keeps it.';
    case 'mixed_entities': return 'Personal data of several types in one column: every value is replaced whole.';
    case 'kept_with_hits': return 'Kept, but the detectors found personal data in its sampled values.';
    case 'length_overflow': return 'The suggested output is longer than the column allows: In place and Append to this table refuse it.';
    case 'length_kept': return 'Tokens would not fit the column, so format-preserving encryption keeps the length.';
    default: return note ? note.replace(/_/g, ' ') : null;
  }
}

/** The connection and selection checks in plain words (mockup 06). */
export function checkLabel(name: string): string {
  return ({
    connect: 'Reachable',
    session_tagged: 'Session tagged for audit',
    select_source: 'SELECT on the source object',
    no_dml_on_source: 'No write privilege on the source object',
    no_sql_string_functions: 'No functions that run SQL strings',
    create_target: 'CREATE in the target schema',
    insert_target: 'INSERT on the target table',
    select_target: 'SELECT on the target table',
    truncate_target: 'TRUNCATE on the target table',
    update_target: 'UPDATE on the target table',
    ledger_schema: 'Write ledger can be created in the schema',
    privileges: 'Privilege checks',
    credentials: 'Credentials available',
    store: 'File store reachable',
    put_object: 'Write a probe object',
    delete_object: 'Delete the probe object',
    one_statement: 'One statement',
    select_only: 'SELECT only',
    no_into_lock_procs: 'No INTO, procedure calls, database links or FOR UPDATE',
    identifiers_validated: 'Identifiers validated',
    literals_bound: 'Literals bound as parameters',
    keyset_able: 'Keyset-able',
  } as Record<string, string>)[name] ?? name.replace(/_/g, ' ');
}

/** Job and API error codes in words (the jobs page "Show error" box, the detail error card). */
export function errorText(code: string | null | undefined): string {
  return ({
    leakage: 'The leakage sweep found a source value in the output, so the batch was not written (fail closed).',
    ledger_conflict: 'Another holder already wrote this batch; the partition stopped without writing it twice.',
    connection_failed: 'DPS could not log in to the database.',
    secret_unavailable: 'The connection secret could not be read, or an inline secret was purged.',
    secret_ref_foreign_tenant: 'A secret reference is relative to this tenant\'s secret folder; it cannot name another tenant\'s secrets.',
    secret_ref_not_shared: 'The shared secret does not list this tenant.',
    secret_ref_invalid: 'The secret reference is not a valid relative path.',
    target_invalid: 'The target cannot be written as specified (missing table, privilege or type).',
    pk_strategy_invalid: 'The primary-key strategy does not fit the table (key type, range or sequence).',
    pk_collision: 'New keys would collide with keys already in the table; nothing was written.',
    snapshot_stale: 'The table changed since discovery; run discovery again.',
    spec_invalid: 'The job specification is not valid.',
    policy_invalid: 'The policy could not be resolved.',
    no_key: 'The table has no primary key or unique index to page by.',
    connection_missing: 'A connection used by the job was deleted.',
    source_error: 'The source database refused a read.',
    target_error: 'The target refused a write.',
    statement_timeout: 'A statement ran longer than the statement timeout.',
    object_not_found: 'The table does not exist or is not visible to the login.',
    incompatible_method: 'A method does not fit the column type, so the batch was refused.',
    length_overflow: 'A method\'s output is longer than the column it is written into; choose a method that keeps the length, or copy to a new table.',
    max_attempts: 'The job ran out of attempts after repeated failures.',
    lease_lost: 'The worker lost its lease; the reaper re-queued the work from its last checkpoint.',
    key_material_required: 'The job key was purged; load the key file to retry.',
    key_material_mismatch: 'The key file does not belong to this job.',
    admin_scope_required: 'Writing to or changing a production-tagged connection needs an API key with the admin scope.',
    connection_endpoint_change_requires_credentials: 'Pointing a connection with stored credentials at another server needs its credentials again in the same change, or the admin scope.',
    confirm_write_required: 'In place and Append need a typed confirmation.',
    snapshot_not_accepted: 'The run needs the accepted classification snapshot of this table.',
    connection_role: 'The connection lacks the role this step needs (source or target).',
    group_key_mismatch: 'The key file does not match the job group.',
    kek_unconfigured: 'The service has no job key-encryption key configured.',
    canceled: 'The job was canceled.',
  } as Record<string, string>)[code ?? ''] ?? (code ? `Error code ${code}.` : 'The job failed.');
}

/** The method of a column for a mode ("keep" when the snapshot has none). */
export function methodFor(col: ColumnClassification, mode: 'obfuscate' | 'synthesize' | null | undefined): MethodSpec {
  return (mode === 'synthesize' ? col.synthesize_method : col.method) ?? { method: 'keep', params: {} };
}

/**
 * The method a run gives a column, decided as the server decides it (`targets.method_for_column`):
 * the accepted method; in synthesize mode the accepted synthesize method, else its suggestion, else
 * `surrogate`.
 */
export function runMethodFor(col: ColumnClassification, mode: 'obfuscate' | 'synthesize'): MethodSpec {
  if (mode === 'synthesize') return col.synthesize_method ?? col.synthesize_suggested ?? { method: 'surrogate', params: {} };
  return col.method;
}

/** A key column of the accepted snapshot and the method a run gives it in each mode (names only). */
export interface KeyMethod { name: string; obfuscate: string; synthesize: string }

/** The snapshot's key columns (`shape.key`, as the server reads them) with their run methods. */
export function keyMethodsOf(snapshot: Pick<ClassificationSnapshot, 'shape' | 'columns'>): KeyMethod[] {
  const keys = new Set(snapshot.shape.key);
  return snapshot.columns.filter((c) => keys.has(c.name))
    .map((c) => ({ name: c.name, obfuscate: runMethodFor(c, 'obfuscate').method, synthesize: runMethodFor(c, 'synthesize').method }));
}

/**
 * The key columns an In place run would rewrite in the draft's mode. In place updates rows by their key
 * and never changes it, so the server refuses such a run (`pk_transform_in_place`); the operator sets
 * those columns to keep in step 3. `null` while the accepted snapshot's key methods are not known.
 */
export function inPlaceKeyRewrites(d: Pick<WizardDraft, 'keyMethods' | 'mode'>): { name: string; method: string }[] | null {
  if (d.keyMethods === null) return null;
  return d.keyMethods.filter((k) => k[d.mode] !== 'keep').map((k) => ({ name: k.name, method: k[d.mode] }));
}

/** `fpe_digits keep_suffix 4` — method name and its params, value-free. */
export function methodText(m: MethodSpec | null | undefined): string {
  if (!m) return '—';
  const params = Object.entries(m.params ?? {}).filter(([k]) => k !== 'leading_zero')
    .map(([k, v]) => (v === true ? k : v === false ? `no ${k}` : `${k} ${typeof v === 'object' ? JSON.stringify(v) : String(v)}`));
  return params.length ? `${m.method} · ${params.join(', ')}` : m.method;
}

/** Every method the engine implements (dps/engine/policy.py METHOD_NAMES). */
export const METHOD_NAMES = [
  'det_token', 'fpe_digits', 'fpe_text', 'email', 'phone', 'date_shift', 'hmac_pseudonym', 'mask_partial', 'redact',
  'null', 'bucket', 'generalize', 'dictionary', 'noise', 'keep', 'surrogate', 'external_tokenize',
] as const;

// ---------------------------------------------------------------------------------------------
// The New-job wizard draft (mockup 06). Transient secrets live in React state only;
// `draftForStorage` is the only shape that reaches localStorage.

export type Step = 1 | 2 | 3 | 4 | 5;

export interface WizardDraft {
  draftId: string;
  step: Step;
  // 1 · connect
  engine: 'postgres' | 'oracle' | 'mongo';
  connMode: 'saved' | 'new';
  connectionId: string | null;
  connectionName: string;
  connectionTags: string[];
  connectionRoles: ('source' | 'target')[];
  newName: string;
  credMode: 'secret_ref' | 'inline';
  secretRef: string;
  host: string;
  port: string;
  database: string;
  serviceName: string;                    // Oracle (M3)
  connOptions: Record<string, string>;    // allowed connection options: sslmode, replicaSet, directConnection, authSource, tls
  username: string;
  password: string;                       // transient: dropped from React state after the create
  production: boolean;
  targetConnectionId: string | null;
  test: ConnectionTestResult | null;
  testedAt: string | null;
  // 2 · select data
  owner: string;
  table: string;
  shape: TableShape | null;
  selMode: 'where' | 'query' | 'mongo';
  where: string;                          // transient: never stored
  query: string;                          // transient: never stored
  mongoFilterText: string;                // transient: never stored (MongoDB filter JSON, M3)
  params: Record<string, string | number | boolean | null>;   // transient: never stored
  count: CountOut | null;
  countedAt: string | null;
  countSeconds: number | null;
  rows: number | null;                    // rows the selection matches (expected_rows); transient
  countedKey: string | null;              // transient: `countKey` of the selection `count`/`rows` were taken for
  columns: string[] | null;               // null = every column
  maxRows: string;
  sampleRows: string;
  parallelism: number;
  // 3 · discover
  discoverJobId: string | null;
  snapshotId: string | null;
  snapshotAccepted: boolean;
  policyName: string | null;              // the snapshot's policy
  keyMethods: KeyMethod[] | null;         // transient: the accepted snapshot's key columns and run methods (null = not read yet)
  // 4 · target and write mode
  writeMode: WriteMode | null;
  targetOwner: string;
  targetTable: string;
  tableLoad: 'truncate_insert' | 'upsert';
  createIfMissing: boolean;
  appendTarget: 'same' | 'named';
  pkKind: 'generate' | 'offset' | 'token';
  pkOffset: string;
  pkPrefix: string;
  filesConnectionId: string | null;
  filesFormat: 'csv' | 'parquet' | 'jsonl';
  filesPrefix: string;
  filesCompression: 'none' | 'gzip' | 'snappy' | 'zstd';
  batchRows: number | null;
  typed: string;                          // transient: the confirmed object name ('' = not confirmed)
  confirmedAt: string | null;             // transient
  confirmedKey: string | null;            // transient: `confirmationKey` of what the dialog showed when confirmed
  confirmLapsed: boolean;                 // transient: a confirmation was withdrawn because what it covered changed
  submittedJobId: string | null;          // transient: the job Run job created; the draft is closed until Start a new job
  // 5 · review and run
  mode: 'obfuscate' | 'synthesize';
  policy: string | null;                  // preset chosen at step 5 (null = the snapshot's)
  keyChoice: 'new' | 'group';
  groupId: string | null;
  keyMaterialJson: string;                // transient: the loaded key or seed file, never stored
  ticket: string;
  legalBasis: string;
  purposeNote: string;
}

export const LEGAL_BASES = [
  'Legitimate interest — UAT provisioning',
  'Legitimate interest — fraud model testing',
  'Legal obligation — regulatory reporting',
  'Consent — customer research panel',
] as const;

/** A fresh draft (`d_<8 hex>`). */
export function emptyDraft(draftId: string = newDraftId()): WizardDraft {
  return {
    draftId, step: 1,
    engine: 'postgres', connMode: 'saved', connectionId: null, connectionName: '', connectionTags: [], connectionRoles: [],
    newName: '', credMode: 'secret_ref', secretRef: '', host: '', port: '5432', database: '', serviceName: '', connOptions: {}, username: '', password: '',
    production: false, targetConnectionId: null, test: null, testedAt: null,
    owner: '', table: '', shape: null, selMode: 'where', where: '', query: '', mongoFilterText: '', params: {}, count: null, countedAt: null,
    countSeconds: null, rows: null, countedKey: null, columns: null, maxRows: '', sampleRows: '2000', parallelism: 4,
    discoverJobId: null, snapshotId: null, snapshotAccepted: false, policyName: null, keyMethods: null,
    writeMode: null, targetOwner: '', targetTable: '', tableLoad: 'truncate_insert', createIfMissing: true,
    appendTarget: 'same', pkKind: 'offset', pkOffset: '', pkPrefix: '', filesConnectionId: null, filesFormat: 'parquet',
    filesPrefix: '', filesCompression: 'snappy', batchRows: null, typed: '', confirmedAt: null, confirmedKey: null, confirmLapsed: false,
    submittedJobId: null, mode: 'obfuscate', policy: null, keyChoice: 'new', groupId: null, keyMaterialJson: '',
    ticket: '', legalBasis: LEGAL_BASES[0], purposeNote: '',
  };
}

/** `d_` + 8 hex characters. */
export function newDraftId(): string {
  const bytes = new Uint8Array(4);
  globalThis.crypto.getRandomValues(bytes);
  return `d_${Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('')}`;
}

/**
 * Fields that never leave React state: credentials, key material, filters, binds, the row count taken
 * for a filter (meaningless once the filter is gone), the write confirmation, the submitted job and the
 * key methods (always read again from the accepted snapshot on the server).
 */
const TRANSIENT = [
  'password', 'keyMaterialJson', 'where', 'query', 'mongoFilterText', 'params', 'count', 'countedAt', 'countSeconds', 'rows', 'countedKey',
  'typed', 'confirmedAt', 'confirmedKey', 'confirmLapsed', 'submittedJobId', 'keyMethods',
] as const;

/** The draft as `localStorage['dps.jobDraft']` may hold it (contract §14). */
export function draftForStorage(d: WizardDraft): Partial<WizardDraft> {
  const out: Partial<WizardDraft> = { ...d };
  for (const k of TRANSIENT) delete out[k];
  return out;
}

/** `OWNER.NAME` (or `NAME`). */
export function fqn(o: ObjectRef | { owner: string | null; name: string } | null | undefined): string {
  if (!o) return '';
  return o.owner ? `${o.owner}.${o.name}` : o.name;
}

/** The source object of the draft. */
export function sourceObject(d: Pick<WizardDraft, 'owner' | 'table'>): ObjectRef {
  return { owner: d.owner || null, name: d.table };
}

/** The object an in-place or append run writes (the confirmation box asks for this name). */
export function writtenObject(d: WizardDraft): ObjectRef {
  if (d.writeMode === 'append' && d.appendTarget === 'named') return { owner: d.targetOwner || d.owner || null, name: d.targetTable };
  return sourceObject(d);
}

/** Confirm is possible when the typed text is exactly the object name and nothing blocks it. */
export function canConfirmWrite(typed: string, objectFqn: string, blocked: boolean): boolean {
  return !blocked && objectFqn.length > 0 && typed.trim() === objectFqn;
}

/** In place and Append change the source table and need the typed confirmation. */
export function needsConfirmation(mode: WriteMode | null): boolean {
  return mode === 'in_place' || mode === 'append';
}

/** The run's selection: the filter as entered, the column subset, the row cap. */
export function selectionOf(d: WizardDraft): Selection {
  const sel: Selection = {};
  if (d.selMode === 'where' && d.where.trim()) sel.where = d.where.trim();
  if (d.selMode === 'query' && d.query.trim()) sel.query = d.query.trim();
  if (d.selMode === 'mongo') {
    const { filter } = parseMongoFilter(d.mongoFilterText);
    if (filter) sel.mongo_filter = filter;
  }
  if (Object.keys(d.params).length) sel.params = { ...d.params };
  // a document's paths share their top-level name: compare with the distinct names (M3)
  if (d.columns && d.shape && d.columns.length < new Set(d.shape.columns.map((c) => c.name)).size) sel.columns = [...d.columns];
  const max = parseCount(d.maxRows);
  if (max) sel.max_rows = max;
  return sel;
}

/**
 * What a row count depends on: the connection, the source object and the rows the selection matches
 * (filter, binds, row cap; not the column subset). A count holds only while this is unchanged.
 */
export function countKey(d: WizardDraft): string {
  const { columns: _columns, ...rows } = selectionOf(d);
  return JSON.stringify({ connection: d.connectionId, object: fqn(sourceObject(d)), selection: rows });
}

/** The append run's primary-key strategy. */
export function pkStrategyOf(d: Pick<WizardDraft, 'pkKind' | 'pkOffset' | 'pkPrefix'>): PkStrategy {
  if (d.pkKind !== 'offset') return { kind: d.pkKind };
  return d.pkPrefix.trim() ? { kind: 'offset', prefix: d.pkPrefix.trim() } : { kind: 'offset', offset: parseCount(d.pkOffset) };
}

/**
 * Everything an In place / Append confirmation covers: what the dialog states (object, row count, mode,
 * columns rewritten, batch size, partitions, key strategy, production) and what decides it (connection,
 * selection, snapshot). A confirmation stands only while this equals the key stored when it was given.
 * The step 5 preset is not part of it: the reviewed snapshot decides each column's method.
 */
export function confirmationKey(d: WizardDraft): string {
  return JSON.stringify({
    connection: d.connectionId, source: fqn(sourceObject(d)), selection: selectionOf(d), rows: d.rows, snapshot: d.snapshotId,
    mode: d.mode, write_mode: d.writeMode, object: fqn(writtenObject(d)),
    append_target: d.writeMode === 'append' ? d.appendTarget : null,
    pk_strategy: d.writeMode === 'append' ? pkStrategyOf(d) : null,
    batch_rows: d.writeMode === 'in_place' ? d.batchRows : null,
    parallelism: d.parallelism, production: d.production,
  });
}

/**
 * The draft holds a standing In place / Append confirmation: given in the dialog for a counted
 * selection, for the typed object name, and nothing it covered has changed since.
 */
export function isWriteConfirmed(d: WizardDraft): boolean {
  return needsConfirmation(d.writeMode) && d.rows !== null && d.countedKey === countKey(d)
    && d.confirmedKey !== null && d.confirmedKey === confirmationKey(d)
    && canConfirmWrite(d.typed, fqn(writtenObject(d)), false);
}

/** "2,000" → 2000; '' or junk → null. */
export function parseCount(text: string): number | null {
  const n = Number(String(text).replace(/[,\s_]/g, ''));
  return Number.isFinite(n) && n > 0 ? Math.floor(n) : null;
}

/** The legal basis with "what the data is for", at most 256 characters (Purpose.legal_basis). */
export function legalBasisText(d: Pick<WizardDraft, 'legalBasis' | 'purposeNote'>): string {
  const note = d.purposeNote.trim();
  return (note ? `${d.legalBasis} · ${note}` : d.legalBasis).slice(0, 256);
}

function targetOf(d: WizardDraft): TargetSpec {
  const mode = d.writeMode ?? 'table';
  const confirmed = isWriteConfirmed(d);
  switch (mode) {
    case 'in_place':
      return { write_mode: 'in_place', confirm_write: confirmed, expected_rows: d.rows };
    case 'append': {
      return {
        write_mode: 'append',
        ...(d.appendTarget === 'named' ? { object: writtenObject(d) } : {}),
        pk_strategy: pkStrategyOf(d),
        confirm_write: confirmed,
        expected_rows: d.rows,
      };
    }
    case 'files':
      return {
        write_mode: 'files',
        connection_id: d.filesConnectionId,
        files: { format: d.filesFormat, prefix: d.filesPrefix.trim(), compression: d.filesCompression },
      };
    case 'table':
    default:
      return {
        write_mode: 'table',
        connection_id: d.targetConnectionId,
        object: { owner: d.targetOwner.trim() || null, name: d.targetTable.trim() },
        create_if_missing: d.createIfMissing,
        table_load: d.tableLoad,
      };
  }
}

/** The loaded key or seed file: `{key_material | synthesis_seed}` (a group create answer), or the bare object. */
export function parseKeyFile(text: string): { key_material: Record<string, unknown> | null; synthesis_seed: Record<string, unknown> | null } {
  const none = { key_material: null, synthesis_seed: null };
  if (!text.trim()) return none;
  let doc: unknown;
  try { doc = JSON.parse(text); } catch { return none; }
  if (!doc || typeof doc !== 'object' || Array.isArray(doc)) return none;
  const o = doc as Record<string, unknown>;
  const obj = (v: unknown) => (v && typeof v === 'object' && !Array.isArray(v) ? (v as Record<string, unknown>) : null);
  const km = obj(o.key_material) ?? (typeof o.kid === 'string' && 'alg' in o ? o : null);
  const seed = obj(o.synthesis_seed) ?? (typeof o.seed === 'string' && !('kid' in o) ? o : null);
  return { key_material: km, synthesis_seed: seed };
}

/** `POST /jobs/run` body from the draft (key material only from a loaded group file). */
export function buildRunSpec(d: WizardDraft): RunSpec {
  const group = d.keyChoice === 'group' && d.groupId ? d.groupId : null;
  const file = group ? parseKeyFile(d.keyMaterialJson) : { key_material: null, synthesis_seed: null };
  return {
    mode: d.mode,
    source: {
      connection_id: d.connectionId ?? '',
      object: sourceObject(d),
      selection: selectionOf(d),
      parallelism: d.parallelism,
      ...(d.writeMode === 'in_place' && d.batchRows ? { batch_rows: d.batchRows } : {}),
    },
    target: targetOf(d),
    snapshot_id: d.snapshotId ?? '',
    ...(d.policy ? { policy: d.policy } : {}),
    consistency: {
      scope: 'key',
      ...(d.mode === 'obfuscate' && file.key_material ? { key_material: file.key_material } : {}),
      ...(d.mode === 'synthesize' && file.synthesis_seed ? { synthesis_seed: file.synthesis_seed } : {}),
    },
    ...(group ? { group_id: group } : {}),
    purpose: { ticket: d.ticket.trim(), legal_basis: legalBasisText(d) },
    options: { dry_run: false, verify: 'strict' },
  };
}

/** The rail's JSON: the run spec without credentials or key material, binds shown as "<bound>". */
export function specPreview(d: WizardDraft): Record<string, unknown> {
  const spec = buildRunSpec(d) as unknown as Record<string, any>;
  const sel = { ...spec.source.selection };
  if (sel.params) sel.params = Object.fromEntries(Object.keys(sel.params).map((k) => [k, '<bound>']));
  if (sel.mongo_filter) sel.mongo_filter = mongoFilterTemplate(sel.mongo_filter);
  const consistency: Record<string, unknown> = { scope: spec.consistency.scope };
  if (spec.group_id) consistency.group = spec.group_id;
  if (spec.consistency.key_material) consistency.kid = (spec.consistency.key_material as Record<string, unknown>).kid ?? null;
  if (spec.consistency.synthesis_seed) consistency.seed = '<loaded from file>';
  return {
    mode: spec.mode,
    source: {
      engine: d.engine,
      connection: d.connectionId ? { id: d.connectionId, name: d.connectionName || null, ...(d.secretRef && d.connMode === 'new' && d.credMode === 'secret_ref' ? { secret_ref: d.secretRef } : {}) } : null,
      object: spec.source.object,
      selection: sel,
      sample_rows: parseCount(d.sampleRows),
      parallelism: spec.source.parallelism,
    },
    target: spec.target,
    snapshot_id: spec.snapshot_id || null,
    policy: spec.policy ?? d.policyName ?? null,
    consistency,
    purpose: spec.purpose,
    options: spec.options,
  };
}

/** Value-free CSV of the jobs list (ids, names, states and counts only). */
export function jobsCsv(jobs: Job[]): string {
  const head = 'id,created_at,kind,state,source,target,mode,write_mode,rows_written';
  const cell = (v: unknown): string => {
    const s = v === null || v === undefined ? '' : String(v);
    return /[",\n\r]/.test(s) ? `"${s.replace(/"/g, '""')}"` : s;
  };
  const rows = jobs.map((j) => [j.id, j.created_at, j.kind, j.state, j.object_ref, jobTargetText(j), j.mode, jobWriteMode(j), j.progress.rows_written].map(cell).join(','));
  return [head, ...rows].join('\n');
}

/** The write mode a job's (redacted) spec names. */
export function jobWriteMode(job: Job): WriteMode | null {
  const wm = job.spec?.target?.write_mode;
  return wm === 'in_place' || wm === 'append' || wm === 'table' || wm === 'files' ? wm : null;
}

/** Where a run writes, as the jobs table shows it. */
export function jobTargetText(job: Job): string {
  const t = (job.spec?.target ?? {}) as Record<string, any>;
  switch (t.write_mode) {
    case 'in_place': return 'rewritten in place';
    case 'append': return t.object ? `append → ${fqn(t.object)}` : `append · ${t.pk_strategy?.kind === 'offset' ? 'offset keys' : t.pk_strategy?.kind === 'generate' ? 'generated keys' : 'token keys'}`;
    case 'table': return t.object ? fqn(t.object) : 'table';
    case 'files': return `${String(t.files?.format ?? 'files').toUpperCase().replace('PARQUET', 'Parquet')} files${t.files?.prefix ? ` · ${t.files.prefix}` : ''}`;
    default: return '';
  }
}

// ---------------------------------------------------------------------------------------------
// Jobs page (mockup 07): client-side filters over the loaded page, health-strip ring buffers.

export type StatusBucket = 'running' | 'review' | 'completed' | 'failed' | 'canceled';

/** The Status filter's bucket of a job. */
export function statusBucket(job: Job, proposed: Set<string>): StatusBucket {
  if (job.kind === 'discover' && job.state === 'succeeded' && job.progress.snapshot_ids.some((id) => proposed.has(id))) return 'review';
  if (job.state === 'succeeded') return 'completed';
  if (job.state === 'failed' || job.state === 'dead') return 'failed';
  if (job.state === 'canceled') return 'canceled';
  return 'running';
}

export interface JobFilters { q: string; status: StatusBucket | ''; engine: string; mode: string; writeMode: string; owner: string }
export const NO_FILTERS: JobFilters = { q: '', status: '', engine: '', mode: '', writeMode: '', owner: '' };

/** The schema / owner part of `object_ref` ('' for an unqualified name). */
export function ownerOf(job: Pick<Job, 'object_ref'>): string {
  const ref = job.object_ref ?? '';
  const dot = ref.indexOf('.');
  return dot > 0 ? ref.slice(0, dot) : '';
}

/** Jobs that pass every set filter (`engines`: connection id → engine). */
export function filterJobs(jobs: Job[], f: JobFilters, ctx: { proposed: Set<string>; engines: Record<string, string> }): Job[] {
  const q = f.q.trim().toLowerCase();
  return jobs.filter((j) => {
    if (f.status && statusBucket(j, ctx.proposed) !== f.status) return false;
    if (f.engine && ctx.engines[j.connection_id ?? ''] !== f.engine) return false;
    if (f.mode && j.mode !== f.mode) return false;
    if (f.writeMode && jobWriteMode(j) !== f.writeMode) return false;
    if (f.owner && ownerOf(j) !== f.owner) return false;
    if (q) {
      const hay = [j.id, j.object_ref, j.purpose?.ticket, jobTargetText(j), j.group_id].filter(Boolean).join(' ').toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });
}

/** The job states of a Status bucket the server can filter on (`GET /jobs?state=`); "review" needs the
 * proposed snapshots, so it is never asked of the server. */
export const STATUS_STATES: Record<Exclude<StatusBucket, 'review'>, Job['state'][]> = {
  running: ['queued', 'running', 'waiting', 'paused'],
  completed: ['succeeded'],
  failed: ['failed', 'dead'],
  canceled: ['canceled'],
};

/** The Jobs card's count (S1-M4 console r1 B2): the loaded list is all the jobs only when no older
 * page is left (`more` false); otherwise it is the newest `loaded` jobs, and the page says so. */
export function jobsCountText(shown: number, loaded: number, more: boolean): string {
  return more
    ? `Showing ${fmtCount(shown)} of the newest ${fmtCount(loaded)} jobs · counts are over these`
    : `Showing ${fmtCount(shown)} of ${fmtCount(loaded)} ${loaded === 1 ? 'job' : 'jobs'}`;
}

/** Append a sample to a ring buffer of at most `max` samples (the API keeps no history). */
export function pushSample(buf: [string, number][], sample: [string, number], max = 60): [string, number][] {
  const next = [...buf, sample];
  return next.length > max ? next.slice(next.length - max) : next;
}

// ---------------------------------------------------------------------------------------------
// Job detail (mockup 03).

export type DetailStep = 'connect' | 'discover' | 'review' | 'target' | 'run' | 'evidence' | 'done';

/** The stepper's current step: prepare → Target, partitions → Run, finalize → Evidence. */
export function stepperState(job: Job): DetailStep {
  if (job.kind === 'discover') return job.state === 'succeeded' ? 'review' : 'discover';
  if (job.state === 'succeeded') return 'done';
  switch (job.phase) {
    case 'prepare': return 'target';
    case 'partitions': return 'run';
    case 'finalize': return 'evidence';
    default: return 'run';
  }
}

/** A partition's bar: rows written against its share of the estimate (done = 100). */
export function partitionPct(p: { state: string; rows_written: number }, job: Job): number {
  if (p.state === 'done') return 100;
  const { rows_estimated: est, partitions_total: n } = job.progress;
  if (!est || !n) return 0;
  return Math.min(100, Math.floor((p.rows_written / (est / n)) * 100));
}

/** The Evidence-so-far tiles from the job's value-free counters. */
export function evidenceSoFar(job: Job): { rows: number; values: number; hits: number; keptHits: number; fallbacks: number; dominantFallback: string | null; done: number; total: number; failed: number } {
  const { methods, fallbacks } = splitCounters(job.progress.values_by_method);
  const values = Object.entries(methods).filter(([m]) => m !== 'keep').reduce((a, [, v]) => a + v, 0);
  const hits = (job.progress.values_by_method['verify:hits'] ?? 0) + (job.progress.verification.hits ?? 0);
  const ranked = Object.entries(fallbacks).sort((a, b) => b[1] - a[1]);
  return {
    rows: job.progress.rows_written, values, hits, keptHits: job.progress.verification.kept_hits ?? 0, fallbacks: total(fallbacks), dominantFallback: ranked[0]?.[0] ?? null,
    done: job.progress.partitions_done, total: job.progress.partitions_total, failed: job.progress.partitions_failed,
  };
}

/** Catalogue type names shortened for tables (`character varying(120)` → `varchar(120)`). */
export function shortType(dataType: string): string {
  return dataType
    .replace(/^character varying/, 'varchar')
    .replace(/^character\b/, 'char')
    .replace(/^timestamp(\(\d+\))? with time zone$/, 'timestamptz$1')
    .replace(/^timestamp(\(\d+\))? without time zone$/, 'timestamp$1')
    .replace(/^double precision$/, 'float8');
}

/** `type_note` codes in a few words (the snapshot table's evidence column). */
export function typeNoteShort(note: string | null | undefined): string | null {
  switch (note) {
    case 'fpe_domain_too_small': return 'domain < 10⁶ → no FPE';
    case 'fpe_disabled': return 'FPE off → det_token';
    case 'numeric_column': return 'numeric → null or keep';
    case 'numeric_key_kept': return 'numeric key kept';
    case 'date_column': return 'date → date_shift';
    case 'type_kept': return 'type kept';
    case 'binary_kept': return 'binary kept';
    case 'mixed_entities': return 'mixed types → replaced whole';
    case 'kept_with_hits': return 'kept · detector hits';
    case 'length_overflow': return 'output too long for the column';
    case 'length_kept': return 'too long as a token → fpe_digits';
    case 'text_pipeline': return 'span-level text pipeline';
    case 'synthesize_key_kept': return 'key kept when synthesizing';
    case 'synthesize_numeric': return 'synthesize nulls or keeps';
    default: return note ? note.replace(/_/g, ' ') : null;
  }
}
// ---------------------------------------------------------------------------------------------
// Review overrides (step 3).

export interface ColumnEdit { entity_type?: string | null; method?: MethodSpec; synthesize_method?: MethodSpec; text_column?: boolean; anchor?: boolean }

const sameMethod = (a: MethodSpec | null | undefined, b: MethodSpec | null | undefined): boolean =>
  !!a && !!b && a.method === b.method && JSON.stringify(a.params ?? {}) === JSON.stringify(b.params ?? {});

/**
 * The overrides a review sends: per edited column only the fields that differ from the snapshot;
 * an anchor set on one column clears the anchor of every other column (and of earlier overrides).
 */
export function overridesFor(snapshot: ClassificationSnapshot, edits: Record<string, ColumnEdit>): SnapshotOverride[] {
  const byPath = new Map(snapshot.columns.map((c) => [c.path, c]));
  const out = new Map<string, SnapshotOverride>();
  let anchorPath: string | null = null;
  for (const [path, edit] of Object.entries(edits)) {
    const col = byPath.get(path);
    if (!col) continue;
    const o: SnapshotOverride = { path };
    if (edit.entity_type !== undefined && (edit.entity_type || null) !== col.entity_type) o.entity_type = edit.entity_type || null;
    if (edit.method && !sameMethod(edit.method, col.method)) o.method = edit.method;
    if (edit.synthesize_method && !sameMethod(edit.synthesize_method, col.synthesize_method)) o.synthesize_method = edit.synthesize_method;
    if (edit.text_column !== undefined && edit.text_column !== col.text_column) o.text_column = edit.text_column;
    if (edit.anchor !== undefined && edit.anchor !== col.anchor) {
      o.anchor = edit.anchor;
      if (edit.anchor) anchorPath = path;
    }
    if (Object.keys(o).length > 1) out.set(path, o);
  }
  if (anchorPath) {
    const earlier = new Set(snapshot.overrides.filter((o) => o.anchor).map((o) => o.path));
    for (const c of snapshot.columns) {
      if (c.path === anchorPath || !(c.anchor || earlier.has(c.path))) continue;
      out.set(c.path, { ...(out.get(c.path) ?? { path: c.path }), anchor: false });
    }
  }
  return [...out.values()];
}

// ---------------------------------------------------------------------------------------------
// Steps 4-5: one-way columns, the plan by method, the seed fingerprint check.

/** Methods the key reverses (dps.engine.transform.methods: `reversible`). */
export const REVERSIBLE_METHODS = new Set(['date_shift', 'det_token', 'email', 'external_tokenize', 'fpe_digits', 'fpe_text', 'phone']);

/** Transformed columns whose method has no way back (the in-place confirmation lists them). */
export function oneWayColumns(snapshot: Pick<ClassificationSnapshot, 'columns'>, mode: 'obfuscate' | 'synthesize'): { name: string; method: string }[] {
  return snapshot.columns
    .filter((c) => c.entity_type || c.text_column)
    .map((c) => ({ name: c.name, method: c.text_column && mode === 'obfuscate' ? 'det_token' : methodFor(c, mode).method }))
    .filter((c) => c.method !== 'keep' && !REVERSIBLE_METHODS.has(c.method));
}

/** The snapshot's columns grouped by method for a mode (step 5 "Plan"), kept columns apart. */
export function planGroups(snapshot: Pick<ClassificationSnapshot, 'columns'>, mode: 'obfuscate' | 'synthesize'): { groups: [string, string[]][]; kept: string[] } {
  const by = new Map<string, string[]>();
  const kept: string[] = [];
  for (const c of snapshot.columns) {
    const method = c.text_column && mode === 'obfuscate' ? 'text pipeline' : methodFor(c, mode).method;
    if (method === 'keep' || (!c.entity_type && !c.text_column)) { kept.push(c.name); continue; }
    by.set(method, [...(by.get(method) ?? []), c.name]);
  }
  return { groups: [...by.entries()].sort((a, b) => a[0].localeCompare(b[0])), kept };
}

/** First 16 hex of sha256(raw seed), as DPS stores it for a job group; null without WebCrypto. */
export async function seedFingerprint(seedB64: string): Promise<string | null> {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle) return null;
  try {
    const text = seedB64.trim().replace(/-/g, '+').replace(/_/g, '/');
    const raw = Uint8Array.from(atob(text + '='.repeat((4 - (text.length % 4)) % 4)), (ch) => ch.charCodeAt(0));
    const digest = new Uint8Array(await subtle.digest('SHA-256', raw));
    return Array.from(digest, (b) => b.toString(16).padStart(2, '0')).join('').slice(0, 16);
  } catch { return null; }
}

// ---------------------------------------------------------------------------------------------
// Evidence pack (mockup 10) and Reports (mockup 09).

/** `6ec5e6dd…d73b`: the first 8 and last 4 hex of a sha256. */
export function shortSha(sha: string): string {
  return sha.length > 12 ? `${sha.slice(0, 8)}…${sha.slice(-4)}` : sha;
}

/** A run whose verification can pass: a failed or canceled run's output is incomplete (N3). */
export function finishedOutcome(outcome: string | null | undefined): boolean {
  return !outcome || outcome === 'succeeded' || outcome === 'dry_run';
}

/**
 * The verification chip of a pack (mockups 09, 10): "passed · 0 leaks", "failed · 2 leaks caught",
 * "failed closed" for a run that stopped before its counters held a hit (a preflight leak),
 * "review required" when the post-write sample found personal data in a column kept as not
 * personal (never "passed": the output holds untransformed personal data), and "not verified · job
 * failed (length_overflow)" for a run that ended failed or canceled for another reason: its sweep
 * saw only what was written, often nothing, so it never reads as passed (packs stored before that
 * rule could say `passed: true` for such a run; the outcome decides).
 */
export function verificationText(passed: boolean, hits: number, review = false, outcome?: string | null, errorCode?: string | null): string {
  const finished = finishedOutcome(outcome);
  if (passed && finished) return `passed · ${fmtCount(hits)} leaks`;
  if (hits > 0) return `failed · ${fmtCount(hits)} ${hits === 1 ? 'leak' : 'leaks'} caught`;
  if (!finished && errorCode !== 'leakage') return `not verified · job ${outcome}${errorCode ? ` (${errorCode})` : ''}`;
  if (review) return 'review required';
  return 'failed closed';
}

/** The chip tone of a verification: emerald passed, amber review required, slate canceled, rose failed. */
export function verificationTone(passed: boolean, hits: number, review = false, outcome?: string | null): 'emerald' | 'amber' | 'rose' | 'slate' {
  const finished = finishedOutcome(outcome);
  if (passed && finished) return 'emerald';
  if (!finished && hits === 0 && outcome === 'canceled') return 'slate';
  return review && hits === 0 && finished ? 'amber' : 'rose';
}

/** The unit one source record is counted in: documents for MongoDB, rows for the SQL engines. */
export function recordNoun(engine: string | null | undefined, n: number): string {
  return engine === 'mongo' ? (n === 1 ? 'document' : 'documents') : n === 1 ? 'row' : 'rows';
}

/** Every '?' leaf of a value-free MongoDB filter template: one per bound literal. */
function templateLiterals(t: unknown): number {
  if (Array.isArray(t)) return t.reduce((a: number, x) => a + templateLiterals(x), 0);
  if (t !== null && typeof t === 'object') return Object.values(t as Record<string, unknown>).reduce((a: number, x) => a + templateLiterals(x), 0);
  return t === '?' ? 1 : 0;
}

/**
 * What a pack's run read, value-free: `scope` is the SQL where/query template or the MongoDB filter
 * template (compact JSON, literals shown as '?'), `detail` the bound-value count and the limits.
 */
export function packSelection(sel: Record<string, any>, engine: string | null | undefined): { scope: string; detail: string } {
  const mongo = engine === 'mongo';
  const flt = sel.mongo_filter_template as Record<string, unknown> | null | undefined;
  const hasFilter = mongo && flt !== null && typeof flt === 'object' && Object.keys(flt).length > 0;
  const scope = mongo
    ? (hasFilter ? JSON.stringify(flt) : 'all documents')
    : String(sel.where_template || sel.query_template || 'all rows');
  const binds = mongo ? (hasFilter ? templateLiterals(flt) : 0) : Number(sel.binds ?? 0);
  const bound = mongo ? (binds === 1 ? 'literal' : 'literals') : binds === 1 ? 'parameter' : 'parameters';
  const max = sel.max_rows ? ` · at most ${fmtCount(Number(sel.max_rows))} ${recordNoun(engine, Number(sel.max_rows))}` : '';
  const n = Array.isArray(sel.columns) ? sel.columns.length : null;
  const cols = n === null ? '' : ` · ${n} ${mongo ? (n === 1 ? 'field' : 'fields') : n === 1 ? 'column' : 'columns'}`;
  return { scope, detail: ` · ${fmtCount(binds)} bound ${bound}${max}${cols}` };
}

/** Where a pack's run wrote, as its title shows it. */
export function packTargetText(target: Record<string, any>, source: Record<string, any>): string {
  if (target.write_mode === 'files') {
    const f = (target.files ?? {}) as Record<string, any>;
    const fmt = String(f.format ?? 'files');
    return `${fmt === 'parquet' ? 'Parquet' : fmt.toUpperCase()} files · ${f.prefix || 'store root'}`;
  }
  if (target.write_mode === 'in_place') return `${target.object ?? source.object ?? '—'} (in place)`;
  return String(target.object ?? '—');
}

/** Where a job opens: a UI scan in the scanner (M4), every other kind in Jobs. */
export function jobHref(j: Pick<Job, 'id' | 'kind'>): string {
  return j.kind === 'scan' ? `/scanner/scans/${encodeURIComponent(j.id)}` : `/data-sources/jobs/${encodeURIComponent(j.id)}`;
}

/** The batch jobs of a `GET /jobs` page: scans are listed and opened in the UI scanner instead. */
export function tableJobs(items: Job[]): Job[] {
  return items.filter((j) => j.kind !== 'scan');
}
