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
  reveal_hints: Record<string, unknown>[]; synthesis_seed: { seed: string } | null; report: Report; trace_id: string;
}
export interface RevealOut { result: unknown; revealed: number; report: Report; trace_id: string }
export interface Preset { name: string; description: string }
export interface Readiness { ready: boolean; components: Record<string, { ok: boolean; detail: string } & Record<string, unknown>> }
