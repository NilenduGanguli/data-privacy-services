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
    const [top, entity] = splitPath(c.path);
    if (top?.key === 'treat' && !top.label && entity && !entity.label) out.add(entity.key);
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

/** One step of a `PolicyChange.path`: a mapping key, plus the item label when the key holds a
 *  list of mappings (`declared_fields[LOANS.APPLICANTS.EMPLOYER_NAME]`). */
export interface PathSeg { key: string; label?: string }

/** Split a backend `flatten_policy` path (M5 plan Task 15) at the dots outside brackets: item
 *  labels are a `name` or a declared `path` and keep their dots and JSONPath brackets. */
export function splitPath(path: string): PathSeg[] {
  const raw: string[] = [];
  let depth = 0;
  let cur = '';
  for (const ch of path) {
    if (ch === '[') depth += 1;
    else if (ch === ']' && depth > 0) depth -= 1;
    if (ch === '.' && depth === 0) { raw.push(cur); cur = ''; } else cur += ch;
  }
  raw.push(cur);
  return raw.map((seg) => {
    const open = seg.indexOf('[');
    return open > 0 && seg.endsWith(']') ? { key: seg.slice(0, open), label: seg.slice(open + 1, -1) } : { key: seg };
  });
}

/** A node of the diff tree: a mapping key (`item` false) or a list item (`item` true, `name` is
 *  its label); `leaf` is the change at exactly this path. */
interface DiffNode { name: string; item: boolean; leaf: PolicyChange | null; children: Map<string, DiffNode> }

const node = (name: string, item: boolean): DiffNode => ({ name, item, leaf: null, children: new Map() });
const child = (parent: DiffNode, id: string, name: string, item: boolean): DiffNode => {
  let n = parent.children.get(id);
  if (!n) { n = node(name, item); parent.children.set(id, n); }
  return n;
};
const byCodepoint = (a: string, b: string): number => (a < b ? -1 : a > b ? 1 : 0);

function changesUnder(n: DiffNode): PolicyChange[] {
  return [...(n.leaf ? [n.leaf] : []), ...[...n.children.values()].flatMap(changesUnder)];
}

/** The side of a change that a one-line add/remove shows. */
const side = (c: PolicyChange): unknown => (c.op === 'remove' ? c.before : c.after);
const isEmptyList = (v: unknown): boolean => Array.isArray(v) && v.length === 0;

/** The value a fully added or removed node had, rebuilt from its leaves (items as a list, the
 *  item's label key first). */
function rebuild(n: DiffNode): unknown {
  if (n.leaf && !n.children.size) return side(n.leaf);
  const kids = [...n.children.values()];
  if (kids.length && kids.every((k) => k.item)) return kids.map(rebuild);
  const entries: [string, unknown][] = kids.filter((k) => !k.item).map((k) => [k.name, rebuild(k)]);
  if (n.item) {
    const first = entries.findIndex(([key, v]) => (key === 'name' || key === 'path') && String(v) === n.name);
    if (first > 0) entries.unshift(...entries.splice(first, 1));
  }
  return Object.fromEntries(entries);
}

/** One line for a node whose every change is the same add or remove and that is new (or gone) as a
 *  whole: an item whose `name`/`path` leaf equal to its label is part of the change, or a treatment
 *  whose `method` is. A partly changed node is never folded — its unchanged fields are not in the
 *  diff, so a folded line would misstate it. */
function foldOp(n: DiffNode): 'add' | 'remove' | null {
  const all = changesUnder(n);
  const op = all[0]?.op;
  if ((op !== 'add' && op !== 'remove') || !all.every((c) => c.op === op)) return null;
  const leafOf = (key: string): PolicyChange | null => n.children.get(`k:${key}`)?.leaf ?? null;
  if (n.item) {
    return [leafOf('name'), leafOf('path')].some((c) => c && String(side(c)) === n.name) ? op : null;
  }
  return leafOf('method') ? op : null;
}

/** `PolicyChange`s (backend `diff_policies` shape) as YAML-like lines: nested keys and unchanged
 *  list items as context, `-` removed (rose), `+` added (emerald). A list item is a `- …` line: one
 *  flow line when the whole item is added or removed (mockup 05), else `- [label]` with its changed
 *  fields beneath. A whole new or removed treatment is one flow line; a changed list of scalars
 *  shows its kept items as context. */
export function diffLines(changes: PolicyChange[]): DiffLine[] {
  const root = node('', false);
  for (const c of [...changes].sort((a, b) => byCodepoint(a.path, b.path))) {
    let at = root;
    for (const seg of splitPath(c.path)) {
      at = child(at, `k:${seg.key}`, seg.key, false);
      if (seg.label !== undefined) at = child(at, `i:${seg.label}`, seg.label, true);
    }
    at.leaf = c;
  }
  const out: DiffLine[] = [];
  const kindOf = (op: 'add' | 'remove'): DiffLine['kind'] => (op === 'add' ? 'add' : 'del');

  function leafLines(key: string, c: PolicyChange, pad: string): void {
    const before = c.op === 'add' ? undefined : c.before;
    const after = c.op === 'remove' ? undefined : c.after;
    if ((Array.isArray(before) && before.length) || (Array.isArray(after) && after.length)) {
      const b = (Array.isArray(before) ? before : []) as unknown[];
      const a = (Array.isArray(after) ? after : []) as unknown[];
      const keyOf = (x: unknown): string => JSON.stringify(x);
      const inA = new Set(a.map(keyOf));
      const inB = new Set(b.map(keyOf));
      out.push({ kind: c.op === 'add' ? 'add' : c.op === 'remove' ? 'del' : 'ctx', text: `${pad}${key}:` });
      for (const x of b) if (!inA.has(keyOf(x))) out.push({ kind: 'del', text: `${pad}  - ${flow(x)}` });
      for (const x of a) out.push({ kind: inB.has(keyOf(x)) ? 'ctx' : 'add', text: `${pad}  - ${flow(x)}` });
      return;
    }
    if (before !== undefined) out.push({ kind: 'del', text: `${pad}${key}: ${flow(before)}` });
    if (after !== undefined) out.push({ kind: 'add', text: `${pad}${key}: ${flow(after)}` });
  }

  function render(n: DiffNode, depth: number): void {
    const pad = '  '.repeat(depth);
    const fold = foldOp(n);
    if (n.item) {
      if (fold) { out.push({ kind: kindOf(fold), text: `${pad}- ${flow(rebuild(n))}` }); return; }
      out.push({ kind: 'ctx', text: `${pad}- [${n.name}]` });
      for (const k of n.children.values()) render(k, depth + 1);
      return;
    }
    if (fold) { out.push({ kind: kindOf(fold), text: `${pad}${n.name}: ${flow(rebuild(n))}` }); return; }
    // an empty list that became items (or items that became an empty list) reads as the items alone
    if (n.leaf && !(n.children.size && isEmptyList(side(n.leaf)))) leafLines(n.name, n.leaf, pad);
    if (n.children.size) {
      out.push({ kind: 'ctx', text: `${pad}${n.name}:` });
      for (const k of n.children.values()) render(k, depth + 1);
    }
  }
  for (const k of root.children.values()) render(k, 0);
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
  // No prototype: a declared path such as `constructor`, `toString` or `__proto__` is an own key
  // like any other (with `{}` the `in` test below found the inherited name and skipped it).
  const out: Record<string, string> = Object.create(null) as Record<string, string>;
  for (const v of Object.keys(bodies).map(Number).sort((a, b) => a - b)) {
    for (const d of (bodies[v]?.detect?.declared_fields ?? []) as DeclaredField[]) if (!(d.path in out)) out[d.path] = `v${v}`;
  }
  for (const d of (draft?.detect?.declared_fields ?? []) as DeclaredField[]) if (!(d.path in out)) out[d.path] = `draft v${draftVersion}`;
  return out;
}

/** What each synthesize method produces, for the matrix's hint column. */
export const SYNTH_HINTS: Record<string, string> = {
  SSN: 'area 900–999, never issued', CARD_PAN: 'test IIN, Luhn-valid', EMAIL: 'RFC 2606 domains', PHONE: 'reserved fictional ranges',
  AADHAAR: 'Verhoeff-valid, outside the issued space', PAN_IN: 'sequence 0000, never issued', IBAN: 'checksum-valid test banks',
  PERSON_NAME: 'one persona per row', DATE_OF_BIRTH: 'persona-consistent', NINO: 'prefixes HMRC never allocates', CVV: 'random 3 digits',
  CURP: 'inconvenient-word prefix, never issued', NHS_NUMBER: '999 test range', NATIONAL_ID: 'never-issued range by shape, else review',
  PASSPORT: 'no never-issued range: the run needs review',
};

/** Whether `name` is one of the tenant's stored policies (GET /policies/{name} answers 404
 *  `policy_not_found` for a shipped preset that was never adopted, so the page does not ask). */
export function isTenantPolicy(name: string, store: boolean, tenant: { name: string }[]): boolean {
  return store && tenant.some((t) => t.name === name);
}

/** The `policy` of a plan request: the body on screen (draft or active), else `@name` for a tenant
 *  policy (resolved to its active version, dps/layers/api/policy_refs.py), else the preset name. */
export function planPolicy(name: string, body: Body | null, tenantPolicy: boolean): Body | string {
  if (body) return body;
  return tenantPolicy ? `@${name}` : name;
}
