/** Pure logic of the Policies page (mockup 05): the session draft, the treatment matrix, the
 *  recognizer builder's live preview and the plan summary. No React, no fetch: unit-tested under
 *  Node (frontend/tests/policies.test.ts) and against the engine (tests/test_console_policies.py). */
import type { DeclaredField, EntityInfo, MethodSpec, PlanRow, PolicyDoc, RecognizerSpec } from './types';

/** Methods a draft may choose: the engine's METHOD_NAMES minus `surrogate` (synthesis only). */
export const METHODS = ['det_token', 'fpe_digits', 'fpe_text', 'email', 'phone', 'date_shift', 'hmac_pseudonym', 'mask_partial',
  'redact', 'null', 'bucket', 'generalize', 'dictionary', 'noise', 'keep', 'external_tokenize'];
/** The methods a caller's key can undo (`REVERSIBLE_METHODS` in dps/engine/policy.py). */
export const REVERSIBLE = new Set(['det_token', 'fpe_digits', 'fpe_text', 'email', 'phone', 'date_shift', 'external_tokenize']);

/** The plan preview's sample record (fictional; checksum-valid where it matters). */
export const PLAN_SAMPLE = JSON.stringify({
  customer_id: '884120', full_name: 'Rohan Desai', dob: '1984-02-09',
  tax_ids: { in_pan: 'AGLPD7316K', uk_nino: 'QQ 12 34 56 C', us_ssn: '078-05-1120' },
  email: 'rohan.desai@example-mail.co.uk', cards: [{ pan: '4111 1111 1111 1111', cvv: '737' }, { pan: '3782 822463 10005' }],
  loan: { internal_score: 712, product: 'HL-FLOAT-20Y' }, branch_city: 'London',
  notes: 'Escalated by EMP-204417 (staff id) under CHG-48213',
}, null, 2);
export const TEST_TEXT = 'Escalated by EMP-204417 (staff id) to the Pune branch on 3 Oct. Fixture EMP-000000 is seeded test data. CHG-48213 also cites EMP-31882, which has five digits.';

export interface RecognizerForm { name: string; entity: string; regex: string; score: string; context: string; deny: string; allow: string }
export const DEFAULT_RECOGNIZER: RecognizerForm = {
  name: 'employee-id', entity: 'EMPLOYEE_ID', regex: '\\bEMP-\\d{6}\\b', score: '0.85', context: 'staff id, employee, emp no', deny: '', allow: 'EMP-000000',
};

/** This browser session's changes to a preset (React state only; publishing needs M5's store). */
export interface Draft { treat: Record<string, MethodSpec>; declared: DeclaredField[]; recognizers: RecognizerSpec[] }
export const EMPTY_DRAFT: Draft = { treat: {}, declared: [], recognizers: [] };
export type Filter = 'all' | 'critical' | 'draft';

export function listOf(text: string): string[] { return text.split(',').map((s) => s.trim()).filter(Boolean); }

/** Inline policy for the API: the preset plus this session's draft. `treat` entries replace the
 *  preset's one by one; the lists replace the preset's, so they carry its entries first. */
export function inlinePolicy(name: string, base: PolicyDoc | null, draft: Draft): Record<string, unknown> {
  return {
    preset: name, treat: draft.treat,
    detect: {
      declared_fields: [...(base?.detect.declared_fields ?? []), ...draft.declared],
      custom_recognizers: [...(base?.detect.custom_recognizers ?? []), ...draft.recognizers],
    },
  };
}

export function draftChanges(draft: Draft): number { return Object.keys(draft.treat).length + draft.declared.length + draft.recognizers.length; }

/** The method the engine uses for `entity` in obfuscate mode (`Policy.method_for`). */
function shipped(doc: PolicyDoc, entity: string): MethodSpec {
  return doc.treat[entity] ?? doc.treat.default_sensitive ?? { method: 'det_token', params: {} };
}

/** Choose `method` for `entity`. Choosing what the preset already does drops the draft entry, so
 *  the preset's own parameters come back. */
export function withTreatment(draft: Draft, doc: PolicyDoc, entity: string, method: string): Draft {
  const treat = { ...draft.treat };
  if (shipped(doc, entity).method === method) delete treat[entity];
  else treat[entity] = { method, params: {} };
  return { ...draft, treat };
}

export interface TreatmentRow { entity: string; info: EntityInfo | undefined; obf: MethodSpec; syn: MethodSpec; changed: boolean; inherited: boolean }

/** Matrix rows: the policy's `treat` and synthesis overrides, every CRITICAL type and every drafted
 *  type (every type with `showAll`), resolved the way the planner resolves them. */
export function treatmentRows(doc: PolicyDoc, draft: Draft, entities: EntityInfo[], showAll: boolean): TreatmentRow[] {
  const byName = new Map(entities.map((e) => [e.name, e]));
  const names = new Set<string>([...Object.keys(doc.treat), ...Object.keys(doc.synthesize.overrides), ...Object.keys(draft.treat)]);
  names.delete('default_sensitive');
  for (const e of entities) if (showAll || e.sensitivity === 'CRITICAL') names.add(e.name);
  return [...names].sort((a, b) => a.localeCompare(b)).map((entity) => {
    const info = byName.get(entity);
    const obf = draft.treat[entity] ?? shipped(doc, entity);
    const syn = doc.synthesize.overrides[entity]
      ?? (doc.synthesize.keep_non_sensitive && info?.sensitivity === 'LOW' ? { method: 'keep', params: {} } : doc.synthesize.default);
    return { entity, info, obf, syn, changed: entity in draft.treat, inherited: !(entity in doc.treat) && !(entity in draft.treat) };
  });
}

export function filterRows(rows: TreatmentRow[], filter: Filter, query: string): TreatmentRow[] {
  const q = query.trim().toUpperCase();
  return rows.filter((r) => (filter === 'all' || (filter === 'critical' ? r.info?.sensitivity === 'CRITICAL' : r.changed))
    && (!q || `${r.entity} ${r.obf.method} ${r.info?.family ?? ''}`.toUpperCase().includes(q)));
}

/** Declare a path as an entity type with a treatment (a `bucket` gets score-band ranges). */
export function declareField(draft: Draft, decl: { path: string; entity_type: string; method: string }): Draft {
  const path = decl.path.trim();
  if (!path) return draft;
  return {
    ...draft,
    declared: [...draft.declared, { path, entity_type: decl.entity_type }],
    treat: { ...draft.treat, [decl.entity_type]: { method: decl.method, params: decl.method === 'bucket' ? { ranges: [0, 300, 600, 900] } : {} } },
  };
}

export function recognizerSpec(rec: RecognizerForm): RecognizerSpec {
  return {
    name: rec.name, supported_entity: rec.entity, patterns: [{ name: rec.name, regex: rec.regex, score: Number(rec.score) || 0 }],
    context: listOf(rec.context), deny_list: listOf(rec.deny), allow_list: listOf(rec.allow),
  };
}

export interface LiveHit { start: number; end: number; allowed: boolean }

/** The browser's preview of a pattern (JavaScript regex; the engine's Python `re` is authoritative). */
export function livePreview(regex: string, allow: string[], text: string): { error: string | null; hits: LiveHit[] } {
  if (!regex) return { error: 'Enter a pattern to test it.', hits: [] };
  let rx: RegExp;
  try { rx = new RegExp(regex, 'g'); } catch {
    return { error: 'The browser cannot compile this pattern: check for an unclosed bracket or a stray backslash. Python-only syntax such as (?P<name>…) is fine on the engine: use Run on the engine.', hits: [] };
  }
  const allowed = new Set(allow);
  const hits: LiveHit[] = [];
  for (let m = rx.exec(text); m !== null; m = rx.exec(text)) {
    if (m[0] === '') { rx.lastIndex += 1; continue; }
    hits.push({ start: m.index, end: m.index + m[0].length, allowed: allowed.has(m[0]) });
  }
  return { error: null, hits };
}

/** "1 match" / "N matches", counting reported (not allow-listed) hits. */
export function matchCount(hits: LiveHit[]): string {
  const n = hits.filter((h) => !h.allowed).length;
  return `${n} match${n === 1 ? '' : 'es'}`;
}

/** Plan rows per method (first-seen order) and the number of rows that fell back. */
export function planSummary(plan: PlanRow[]): { counts: [string, number][]; fallbacks: number } {
  const counts = new Map<string, number>();
  for (const r of plan) counts.set(r.method, (counts.get(r.method) ?? 0) + 1);
  return { counts: [...counts.entries()], fallbacks: plan.filter((r) => r.reason.includes('fallback:')).length };
}
