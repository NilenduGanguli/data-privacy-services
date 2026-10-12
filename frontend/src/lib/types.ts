export type Sensitivity = 'LOW' | 'MEDIUM' | 'HIGH' | 'CRITICAL';

export interface Locator { path: string; start: number | null; end: number | null }
export interface Finding {
  locator: Locator; entity_type: string; score: number; detectors: string[];
  sensitivity: Sensitivity; evidence: string[]; value_hash: string | null;
}
/** What the leakage sweep could see: it checks the values the detectors found, nothing else. */
export interface Coverage {
  basis: 'detected-values'; detectors: string[]; ner: 'on' | 'off' | 'unavailable';
  free_text_fields: number; complete: boolean; gaps: string[];
}
/** `status`: failed = an original is in the output; partial = none found but coverage has gaps. */
export interface Verification {
  passed: boolean; status: 'passed' | 'partial' | 'failed'; checked: number;
  hits: { path: string; entity_type: string }[]; coverage: Coverage | null;
}
export interface Report {
  mode: string; policy: string; policy_hash: string; engine_version: string; findings_total: number;
  findings_by_type: Record<string, number>; findings_by_detector: Record<string, number>; record_sensitivity: string;
  methods_used: Record<string, number>; fallbacks: Record<string, number>; reversible_values: number;
  verification: Verification | null; llm: { calls: number; raw_pii_sent: number }; duration_ms: number;
}
export interface KeyMaterial { alg: string; kid: string; dek: string | null; kcv: string | null; wrapped_dek: string | null; wrap: Record<string, string> | null; consistency_scope: string; created_at: string }
export interface PlanRow { path: string; span: [number, number] | null; entity_type: string; score: number; method: string; params: Record<string, unknown>; reason: string; reversible: boolean }
export interface DetectOut { findings: Finding[]; record_sensitivity: Sensitivity; report: Report; trace_id: string }
export interface TransformOut {
  result: unknown; findings: Finding[]; plan: PlanRow[]; key_material: KeyMaterial | null;
  reveal_hints: Record<string, unknown>[]; synthesis_seed: { seed: string } | null; segments?: OutputSegment[] | null;
  report: Report; trace_id: string;
}
export interface RevealOut { result: unknown; revealed: number; report: Report; trace_id: string }
export interface Preset { name: string; description: string }
export interface Readiness { ready: boolean; components: Record<string, { ok: boolean; detail: string } & Record<string, unknown>> }

// --- M1: documents, entities, policy tooling (mirror the pydantic models field for field) ---
export interface OutputSegment {
  path: string; start: number | null; end: number | null; out_start: number | null; out_end: number | null;
  entity_type: string; method: string; replacement: string;
}
export type DocFormat = 'pdf' | 'docx' | 'image' | 'text' | 'csv';
export type PageSource = 'text_layer' | 'ocr' | 'hybrid' | 'docx' | 'text' | 'csv' | 'empty' | 'unreadable';
export type OcrMode = 'auto' | 'always' | 'never';
export interface PageBox { page: number; x0: number; y0: number; x1: number; y1: number }
export interface PageFinding {
  n: number; entity_type: string; score: number; sensitivity: Sensitivity; detectors: string[]; evidence: string[];
  value_hash: string | null; page: number; line: number | null; path: string; span: [number, number] | null;
  boxes: PageBox[]; method: string | null; reversible: boolean | null; redacted: boolean; replacement: string | null;
  out_span: [number, number] | null; ocr_confidence: number | null; review: boolean;
}
export interface PageSummary {
  page: number; width: number; height: number; unit: 'pt'; source: PageSource; words: number; lines: number;
  ocr_confidence: number | null; findings: PageFinding[]; preview_png_b64: string | null; redacted_preview_png_b64: string | null;
}
export interface RedactionReport {
  boxes: number; pages: number; dpi: number; image_format: string; bytes: number; sha256: string; text_layer_chars: number;
  text_operators: number; annotations: number; ocr_checked: boolean; ocr_pages: number; ocr_hits: number;
  chars_under_boxes: number; passed: boolean;
}
export interface DocumentInfo {
  format: DocFormat; pages: number; ocr_pages: number[]; unreadable_pages: number[]; words: number; boxes: number;
  ocr_engine: string | null; bytes: number; sha256: string; warnings: string[]; timings_ms: Record<string, number>;
  redaction: RedactionReport | null; synthetic_validity: { checked: number; valid: number } | null;
}
export interface DocumentDetectOut extends DetectOut { document: DocumentInfo; pages: PageSummary[] }
export interface DocumentTransformOut extends TransformOut {
  document: DocumentInfo; pages: PageSummary[]; redacted_pdf_b64: string | null; result_csv: string | null;
}
export interface DocumentOptions {
  redacted_pdf?: boolean; ocr?: OcrMode; previews?: boolean; ocr_verify?: boolean | null;
  token_style?: string; locale?: string; consistency?: Record<string, unknown>;
}
export interface PresetSummary extends Preset { version: number; hash: string; mode: 'obfuscate' | 'synthesize'; reversible: boolean }
export interface EntityInfo { name: string; family: string; sensitivity: Sensitivity; description: string; jurisdictions: string[]; builtin: boolean }
export interface EntitiesOut { items: EntityInfo[]; families: string[] }
export interface MethodSpec { method: string; params: Record<string, unknown> }
export interface RecognizerPattern { name: string; regex: string; score: number }
export interface RecognizerSpec {
  name: string; supported_entity: string; supported_language?: string; patterns: RecognizerPattern[];
  deny_list?: string[]; allow_list?: string[]; context?: string[]; sensitivity?: Sensitivity | null;
}
export interface DeclaredField { path: string; entity_type: string; score?: number; sensitivity?: Sensitivity | null }
export interface PolicyDoc {
  name: string; version: number; description: string;
  locale: { default: string | null; by_country_field: string | null };   // null = DPS_DEFAULT_LOCALE applies (main @ fix(engine) O1)
  detect: { tiers: string[]; min_score: number; custom_recognizers: RecognizerSpec[]; declared_fields: DeclaredField[] };
  treat: Record<string, MethodSpec>;
  synthesize: { default: MethodSpec; overrides: Record<string, MethodSpec>; keep_non_sensitive: boolean; llm_rewrite_text: boolean };
  consistency: { scope: string; field_scoped: boolean }; output: { token_style: string }; verify: { leakage_check: string };
}
export interface PlanOut { findings: Finding[]; plan: PlanRow[]; record_sensitivity: Sensitivity; trace_id: string }
export interface RecognizerMatch { start: number; end: number; entity_type: string; score: number; evidence: string; context: boolean }
export interface RecognizerTestOut { matches: RecognizerMatch[]; count: number; yaml: string }

// ---- M2: connections, jobs, snapshots, job groups, evidence (contract §14; field for field with
// the pydantic models in dps/jobs_models.py, dps/jobs.py, dps/connections.py, dps/groups.py and
// dps/layers/connectors/{base,evidence}.py). Key material and seeds live only in React state.
export type JobKind = 'discover' | 'run' | 'scan' | 'retention';
export type JobState = 'queued' | 'running' | 'waiting' | 'paused' | 'succeeded' | 'failed' | 'dead' | 'canceled';
export type JobPhase = 'discover' | 'prepare' | 'partitions' | 'finalize' | 'scan' | 'retention';
export type PartitionState = 'queued' | 'running' | 'done' | 'failed' | 'dead' | 'canceled';
export type WriteMode = 'in_place' | 'append' | 'table' | 'files';
export type ConnEngine = 'postgres' | 'oracle' | 'mongo' | 's3' | 'pvc';
export interface Page<T> { items: T[]; next_cursor: string | null }
export interface ObjectRef { owner: string | null; name: string }
export interface ColumnInfo { path: string; name: string; data_type: string; kind: string; length: number | null; precision: number | null; scale: number | null; ts_precision: number | null; nullable: boolean; is_key: boolean; has_default: boolean; is_identity: boolean }
export interface TableShape { object: ObjectRef; columns: ColumnInfo[]; key: string[]; key_source: 'primary_key' | 'unique_index' | 'rowid' | 'none'; row_estimate: number | null }
export interface Check { name: string; ok: boolean; severity?: 'required' | 'advisory'; detail?: string; code?: string; count?: number }
export interface ConnectionTestResult { ok: boolean; engine: ConnEngine; server_version: string | null; checks: Check[]; capabilities: Record<string, boolean>; error_code: string | null }
export interface ConnectionParams { host?: string | null; port?: number | null; database?: string | null; service_name?: string | null; options?: Record<string, string>; bucket?: string | null; prefix?: string; region?: string | null; endpoint_url?: string | null; base_path?: string | null }
export interface Connection { id: string; tenant_id: string; name: string; engine: ConnEngine; params: ConnectionParams; secret_ref: string | null; has_secret: boolean; secret_expires_at: string | null; roles: ('source' | 'target')[]; tags: string[]; capabilities: Record<string, boolean>; last_test: ConnectionTestResult | null; created_by: string | null; created_at: string; updated_at: string }
export interface ConnectionSpec { name: string; engine: ConnEngine; params: ConnectionParams; secret_ref?: string | null; credentials?: { username: string; password: string } | null; roles: ('source' | 'target')[]; tags: string[] }
export interface Selection { where?: string | null; params?: Record<string, string | number | boolean | null>; query?: string | null; columns?: string[] | null; max_rows?: number | null; sample?: { percent: number; seed?: number | null } | null }
export interface CountOut { rows: number; checks: Check[]; runs_as: string | null; binds: number }
export interface JobProgress {
  outcome: '' | 'succeeded' | 'failed' | 'canceled' | 'dry_run'; stage: string; rows_estimated: number | null; rows_read: number;
  rows_written: number; rows_skipped: number; partitions_total: number; partitions_done: number; partitions_failed: number;
  rows_per_second: number | null; throughput: [string, number][]; throughput_mark: [string, number] | null;
  values_by_method: Record<string, number>; verification: Record<string, number>; snapshot_ids: string[]; evidence_id: string | null;
  pages: number | null; fields: number | null; blocked_requests: number | null;
}
export interface Job {
  id: string; tenant_id: string; kind: JobKind; state: JobState; phase: JobPhase; mode: 'obfuscate' | 'synthesize' | null;
  group_id: string | null; connection_id: string | null; target_connection_id: string | null; object_ref: string | null;
  snapshot_id: string | null; spec: Record<string, any>; purpose: { ticket?: string; legal_basis?: string }; kid: string | null;
  progress: JobProgress; plan: Record<string, any> | null; error_code: string | null; error: string | null; created_by: string | null;
  created_at: string; updated_at: string; started_at: string | null; finished_at: string | null; expires_at: string | null;
}
export interface PartitionView { partition_no: number; state: PartitionState; rows_read: number; rows_written: number; rows_skipped: number; batch_no: number; attempts: number; resumed: number; checkpoint_fp: string | null; started_at: string | null; finished_at: string | null; updated_at: string; error_code: string | null; rows_per_second: number | null }
export interface JobStats { workers_online: number; workers_busy: number; workers_draining: number; partitions_waiting: number; oldest_waiting_s: number | null; rows_per_s: number; by_state: Record<string, number>; running: number; tenant_max_running: number; lease_reclaims_24h: number }
// MethodSpec: declared once above (M1).
export interface ColumnClassification { path: string; name: string; data_type: string; kind: string; is_key: boolean; entity_type: string | null; confidence: number; sensitivity: Sensitivity | null; detectors: string[]; value_hit_rate: number; null_rate: number; distinct_ratio: number; shapes: string[]; text_column: boolean; anchor: boolean; suggested: MethodSpec; method: MethodSpec; override: boolean; synthesize_suggested: MethodSpec | null; synthesize_method: MethodSpec | null; type_note: string | null }
export interface SnapshotOverride { path: string; entity_type?: string | null; method?: MethodSpec | null; synthesize_method?: MethodSpec | null; text_column?: boolean | null; anchor?: boolean | null }
export interface ClassificationSnapshot { id: string; tenant_id: string; job_id: string | null; connection_id: string; engine: string; object_ref: string; status: 'proposed' | 'accepted' | 'superseded'; policy_name: string; policy_hash: string; shape: TableShape; columns: ColumnClassification[]; sample_rows: number; overrides: SnapshotOverride[]; content_hash: string; created_at: string; accepted_at: string | null; accepted_by: string | null }
export interface PkStrategy { kind: 'generate' | 'offset' | 'token'; offset?: number | null; prefix?: string | null; sequence?: string | null }
export interface FilesSpec { format: 'csv' | 'parquet' | 'jsonl'; prefix: string; compression: 'none' | 'gzip' | 'snappy' | 'zstd'; rows_per_file?: number }
export interface TargetSpec { write_mode: WriteMode; connection_id?: string | null; object?: ObjectRef | null; create_if_missing?: boolean; table_load?: 'truncate_insert' | 'upsert'; pk_strategy?: PkStrategy | null; files?: FilesSpec | null; ledger_owner?: string | null; confirm_write?: boolean; expected_rows?: number | null }
export interface RunSpec {
  mode: 'obfuscate' | 'synthesize';
  source: { connection_id: string; object: ObjectRef; selection: Selection; parallelism: number; batch_rows?: number | null; partition_rows?: number | null };
  target: TargetSpec; snapshot_id: string; policy?: string | null;
  consistency: { scope: 'request' | 'key' | 'entity'; field_scoped?: boolean; key_material?: Record<string, unknown> | null; synthesis_seed?: Record<string, unknown> | null };
  group_id?: string | null; purpose: { ticket: string; legal_basis: string };
  options: { dry_run: boolean; verify: 'strict' | 'warn'; unclassified?: 'detect' | 'keep' | 'redact' };
}
export interface RunOut { job: Job; key_material?: KeyMaterial; synthesis_seed?: { alg: string; seed: string; created_at: string } }
export interface JobGroup { id: string; tenant_id: string; name: string; kid: string; seed_fingerprint: string; created_by: string | null; created_at: string; closed_at: string | null }
export interface EvidencePack {
  pack_version: string; id: string; tenant_id: string; job_id: string; job_kind: string; group_id: string | null;
  outcome: 'succeeded' | 'failed' | 'canceled' | 'dry_run'; mode: string | null; label: string; created_at: string;
  engine_version: string; image_digest: string | null; purpose: { ticket: string; legal_basis: string } | null; requested_by: string | null;
  source: Record<string, any>; target: Record<string, any>; policy: Record<string, string>; snapshot: Record<string, any>;
  consistency: Record<string, any>; columns: Record<string, any>[]; reveal_map: Record<string, any>[]; counts: Record<string, number>;
  verification: Record<string, any>; fidelity: Record<string, any> | null; privacy: Record<string, any> | null; utility: Record<string, any> | null;
  scan: Record<string, any> | null; timings: Record<string, any>; partitions: Record<string, any>[]; errors: { code: string; where: string }[]; config_hash: string;
}
export interface EvidenceOut { id: string; job_id: string; created_at: string; pack: EvidencePack; pack_sha256: string; artefacts: { id: string; kind: string; bytes: number; content_type: string }[] }
export interface EvidenceSummary { id: string; job_id: string; created_at: string; outcome: string; mode: string | null; label: string; source_object: string | null; target: string | null; write_mode: string | null; policy: string | null; ticket: string | null; passed: boolean; hits: number; rows_written: number; html_artefact_id: string | null }
