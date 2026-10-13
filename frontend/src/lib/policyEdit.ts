/** Pure edits of a tenant policy body for the Policies page (mockup 05, M5 policy store): every
 *  function returns a new object and never mutates its input. Plus the diff rendering, the
 *  client-side FF1 fallback simulation and "declared since". Tested in policyEdit.test.ts. */
import type { DeclaredField, MethodSpec, PlanRow, PolicyChange, RecognizerSpec } from './types';
import { toYaml } from './yaml';

export type Body = Record<string, any>;

const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

export function setTreatment(body: Body, entity: string, spec: MethodSpec): Body {
  const next = clone(body);
  next.treat = { ...(next.treat ?? {}), [entity]: { method: spec.method, params: spec.params ?? {} } };
  return next;
}

export function removeTreatment(body: Body, entity: string): Body {
  const next = clone(body);
  const treat = { ...(next.treat ?? {}) };
  delete treat[entity];
  next.treat = treat;
  return next;
}

/** Declare `field`; a declaration for the same path is replaced. */
export function addDeclaredField(body: Body, field: DeclaredField): Body {
  const next = clone(body);
  next.detect = { ...(next.detect ?? {}) };
  const list: DeclaredField[] = (next.detect.declared_fields ?? []).filter((d: DeclaredField) => d.path !== field.path);
  next.detect.declared_fields = [...list, field];
  return next;
}

/** Add `rec`, replacing a recognizer of the same name. */
export function upsertRecognizer(body: Body, rec: RecognizerSpec): Body {
  const next = clone(body);
  next.detect = { ...(next.detect ?? {}) };
  const list: RecognizerSpec[] = (next.detect.custom_recognizers ?? []).filter((r: RecognizerSpec) => r.name !== rec.name);
  next.detect.custom_recognizers = [...list, rec];
  return next;
}

/** A recognizer in Presidio's YAML recognizer-registry shape (empty lists left out). */
export function toPresidioYaml(rec: RecognizerSpec): string {
  const entry: Record<string, unknown> = { name: rec.name, supported_entity: rec.supported_entity, supported_language: rec.supported_language ?? 'en',
    patterns: rec.patterns.map((p) => ({ name: p.name, regex: p.regex, score: p.score })) };
  if (rec.context?.length) entry.context = rec.context;
  if (rec.deny_list?.length) entry.deny_list = rec.deny_list;
  return toYaml({ recognizers: [entry] });
}

/** A parameter typed in the inline editor: JSON when it parses (numbers, booleans, lists), else text. */
export function parseParam(text: string): unknown {
  const t = text.trim();
  try { return JSON.parse(t) as unknown; } catch { return t; }
}

/** Entity types whose treatment a diff touches (`treat.<ENTITY>` or `treat.<ENTITY>.…`). */
export function changedEntities(changes: PolicyChange[]): Set<string> {
  const out = new Set<string>();
  for (const c of changes) {
    const [top, entity] = c.path.split('.');
    if (top === 'treat' && entity) out.add(entity);
  }
  return out;
}

// ---- diff rendering --------------------------------------------------------------------------

const FLOW_PLAIN = /^[A-Za-z_][A-Za-z0-9_-]*$/;
const RESERVED = /^(true|false|null|yes|no|on|off|y|n|~)$/i;

/** Inline YAML ("flow") for a diff line: `{ method: generalize, keep_chars: 3 }`. */
export function flow(v: unknown): string {
  if (v === null || v === undefined) return 'null';
  if (typeof v === 'string') return FLOW_PLAIN.test(v) && !RESERVED.test(v) ? v : JSON.stringify(v);
  if (typeof v !== 'object') return String(v);
  if (Array.isArray(v)) return `[${v.map(flow).join(', ')}]`;
  const o = v as Record<string, unknown>;
  const entries = 'method' in o && Object.keys(o).every((k) => k === 'method' || k === 'params')
    ? [['method', o.method] as [string, unknown], ...Object.entries((o.params ?? {}) as Record<string, unknown>)]
    : Object.entries(o);
  return entries.length ? `{ ${entries.map(([k, x]) => `${k}: ${flow(x)}`).join(', ')} }` : '{}';
}

export interface DiffLine { kind: 'ctx' | 'add' | 'del'; text: string }

/** `PolicyChange`s as YAML-like lines: nested keys as context, `-` removed (rose), `+` added
 *  (emerald); a changed list shows its kept items as context. */
export function diffLines(changes: PolicyChange[]): DiffLine[] {
  const out: DiffLine[] = [];
  let open: string[] = [];
  for (const c of [...changes].sort((a, b) => a.path.localeCompare(b.path))) {
    const parts = c.path.split('.');
    const parents = parts.slice(0, -1);
    const key = parts[parts.length - 1];
    let same = 0;
    while (same < parents.length && same < open.length && parents[same] === open[same]) same += 1;
    for (let d = same; d < parents.length; d += 1) out.push({ kind: 'ctx', text: `${'  '.repeat(d)}${parents[d]}:` });
    open = parents;
    const pad = '  '.repeat(parents.length);
    const before = c.op === 'add' ? undefined : c.before;
    const after = c.op === 'remove' ? undefined : c.after;
    if (Array.isArray(before) || Array.isArray(after)) {
      const b = (Array.isArray(before) ? before : []) as unknown[];
      const a = (Array.isArray(after) ? after : []) as unknown[];
      const keyOf = (x: unknown): string => JSON.stringify(x);
      const inA = new Set(a.map(keyOf));
      const inB = new Set(b.map(keyOf));
      out.push({ kind: c.op === 'add' ? 'add' : c.op === 'remove' ? 'del' : 'ctx', text: `${pad}${key}:` });
      for (const x of b) if (!inA.has(keyOf(x))) out.push({ kind: 'del', text: `${pad}  - ${flow(x)}` });
      for (const x of a) out.push({ kind: inB.has(keyOf(x)) ? 'ctx' : 'add', text: `${pad}  - ${flow(x)}` });
    } else {
      if (before !== undefined) out.push({ kind: 'del', text: `${pad}${key}: ${flow(before)}` });
      if (after !== undefined) out.push({ kind: 'add', text: `${pad}${key}: ${flow(after)}` });
    }
  }
  return out;
}

// ---- plan preview and declarations -----------------------------------------------------------

/** The planner's static FF1 rule, recomputed client-side for the "other" FF1 setting: with FF1
 *  off every `fpe_*` row runs as `det_token` (reason `…|fallback:fpe_disabled`); with FF1 on such
 *  rows get the policy's method back. */
export function simulateFpe(plan: PlanRow[], fpeOn: boolean, body: Body | null): PlanRow[] {
  return plan.map((r) => {
    if (!fpeOn && r.method.startsWith('fpe_')) {
      return { ...r, method: 'det_token', params: {}, reason: `${r.reason}|fallback:fpe_disabled`, reversible: true };
    }
    if (fpeOn && r.reason.includes('|fallback:fpe_disabled')) {
      const spec: MethodSpec | undefined = body?.treat?.[r.entity_type] ?? body?.treat?.default_sensitive;
      if (spec && spec.method.startsWith('fpe_')) return { ...r, method: spec.method, params: spec.params ?? {}, reason: r.reason.replace('|fallback:fpe_disabled', ''), reversible: true };
    }
    return r;
  });
}

/** For each declared path, the first version containing it ("v2") or "draft vN" when only the
 *  draft has it. `bodies` maps version → body (any subset; missing versions are skipped). */
export function declaredSince(bodies: Record<number, Body>, draft: Body | null, draftVersion: number): Record<string, string> {
  const out: Record<string, string> = {};
  for (const v of Object.keys(bodies).map(Number).sort((a, b) => a - b)) {
    for (const d of (bodies[v]?.detect?.declared_fields ?? []) as DeclaredField[]) if (!(d.path in out)) out[d.path] = `v${v}`;
  }
  for (const d of (draft?.detect?.declared_fields ?? []) as DeclaredField[]) if (!(d.path in out)) out[d.path] = `draft v${draftVersion}`;
  return out;
}

/** What each synthesize method produces, for the matrix's hint column. */
export const SYNTH_HINTS: Record<string, string> = {
  SSN: 'area 900–999, never issued', CARD_PAN: 'test IIN, Luhn-valid', EMAIL: 'RFC 2606 domains', PHONE: 'reserved fictional ranges',
  AADHAAR: 'Verhoeff-valid, outside the issued space', PAN_IN: 'format-valid, fictional', IBAN: 'checksum-valid test banks',
  PERSON_NAME: 'one persona per row', DATE_OF_BIRTH: 'persona-consistent', NINO: 'reserved prefixes', CVV: 'random 3 digits',
};
