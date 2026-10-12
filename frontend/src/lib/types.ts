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
