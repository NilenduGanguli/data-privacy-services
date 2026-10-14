/**
 * Pure helpers for job groups and the cross-system consistency panel (mockup 07 "JOB GROUP",
 * mockup 03 header). Nothing here formats a customer value: snapshots, check results and groups
 * hold entity types, paths, method names, ids and counts only. Key and seed files are built for the
 * once-only download and never stored.
 */
import { fqn } from './jobs';
import type { CheckProblem, ClassificationSnapshot, ColumnClassification, Job, JobGroup, MemberResult } from './types';

/** The join entity preference of the server's check (consistency.JOIN_PREFERENCE). */
export const JOIN_PREFERENCE = ['CUSTOMER_ID', 'ACCOUNT_NUMBER', 'EMPLOYEE_ID', 'CASE_ID', 'USERNAME', 'EMAIL'];
export const ENGINE_BADGE: Record<string, string> = { oracle: 'ORA', mongo: 'MDB', postgres: 'PG' };
const ENGINE_NAME: Record<string, string> = { oracle: 'Oracle', mongo: 'MongoDB', postgres: 'PostgreSQL' };

/**
 * A whole-value mapping, as the server's `_joinable`: an entity, a method other than keep, not a
 * free-text column (free text is span-transformed and differs per system, so it never joins).
 */
function joinable(c: ColumnClassification): boolean {
  return !!c.entity_type && c.method.method !== 'keep' && !c.text_column && c.entity_type !== 'FREE_TEXT';
}

/** entity type → the preferred mapped column (key, then anchor, then the shortest path). */
function mapped(s: ClassificationSnapshot | null): Map<string, ColumnClassification> {
  const out = new Map<string, ColumnClassification>();
  if (!s) return out;
  const cols = [...s.columns].sort((a, b) => Number(b.is_key) - Number(a.is_key) || Number(b.anchor) - Number(a.anchor)
    || a.path.length - b.path.length || a.path.localeCompare(b.path));
  for (const c of cols) if (joinable(c) && !out.has(c.entity_type!)) out.set(c.entity_type!, c);
  return out;
}

/** Entity types mapped (method ≠ keep, not free text) in at least two members, join preference first. */
export function sharedEntities(snapshots: (ClassificationSnapshot | null)[]): string[] {
  const counts = new Map<string, number>();
  for (const s of snapshots) for (const e of mapped(s).keys()) counts.set(e, (counts.get(e) ?? 0) + 1);
  const shared = [...counts].filter(([, n]) => n >= 2).map(([e]) => e);
  const rank = (e: string): number => { const i = JOIN_PREFERENCE.indexOf(e); return i < 0 ? 99 : i; };
  return shared.sort((a, b) => rank(a) - rank(b) || a.localeCompare(b));
}

/** A column path as the cards show it: no `$.`, `[*]` as `[]`. */
export function displayPath(path: string): string {
  return path.replace(/^\$\.?/, '').replace(/\[\*\]/g, '[]');
}

/** The member's columns of the shared entities, in the shared order (display paths). */
export function memberFields(snapshot: ClassificationSnapshot | null, shared: string[]): string[] {
  const m = mapped(snapshot);
  return shared.filter((e) => m.has(e)).map((e) => displayPath(m.get(e)!.path));
}

/** "number" · "prefixed · 5-char prefix · 8 digits" · "text" · "unknown". */
export function formLabel(m: MemberResult): string {
  if (m.form === 'numeric') return 'number';
  if (m.form === 'prefixed_digits') return `prefixed · ${m.prefix_len ?? '?'}-char prefix${m.width ? ` · ${m.width} digits` : ''}`;
  return m.form === 'exact' ? 'text' : 'unknown';
}

export function engineLabel(engine: string): string {
  return ENGINE_NAME[engine] ?? engine;
}

/** "Oracle ↔ MongoDB". */
export function pairLabel(a: MemberResult | undefined, b: MemberResult | undefined): string {
  return `${engineLabel(a?.engine ?? '?')} ↔ ${engineLabel(b?.engine ?? '?')}`;
}

const PROBLEM: Record<string, string> = {
  method_mismatch: 'The join columns are not protected with the same method and parameters in every member.',
  join_form_mismatch: 'A member stores the join value in a form that cannot be compared (text against numbers).',
  field_scoped: 'A member used field-scoped tweaks, so its tokens cannot match the other systems.',
  kid_mismatch: 'A member was run with a different key than the group key.',
};

/** A check problem in plain words, prefixed with the member's object when it names one. */
export function problemText(p: CheckProblem, members: MemberResult[]): string {
  const who = p.member !== null && p.member !== undefined && members[p.member] ? `${members[p.member].object}: ` : '';
  return who + (PROBLEM[p.code] ?? p.code);
}

/** Refusals of `POST /job-groups/{id}/consistency-check` (contract §6.5) in plain words. */
const CHECK_ERROR: Record<string, string> = {
  member_not_ready: 'Fewer than two members have written rows to a database target yet. Run the check once they have.',
  no_shared_entity: 'The members share no protected entity to join on.',
  member_unsupported: 'A member writes somewhere the check cannot read (files, or no target object).',
  member_duplicate: 'Two members name the same target column.',
  member_not_in_group: 'A named job is not part of this group.',
  group_not_found: 'The job group no longer exists.',
  check_timeout: 'The check took longer than the statement timeout. Try a smaller sample.',
  secret_unavailable: 'A member target could not be read: its secret is unavailable.',
  connection_failed: 'A member target could not be reached.',
};

export function checkErrorText(code: string | undefined, fallback: string): string {
  return (code && CHECK_ERROR[code]) || fallback;
}

/** Where a run job writes: the target connection and object, else the source (in place). */
export function targetOf(job: Job): { connectionId: string; object: string } {
  const spec = (job.spec ?? {}) as { source?: { connection_id?: string; object?: { owner: string | null; name: string } };
    target?: { connection_id?: string | null; object?: { owner: string | null; name: string } | null } };
  const obj = spec.target?.object ?? spec.source?.object ?? null;
  return { connectionId: spec.target?.connection_id ?? spec.source?.connection_id ?? '', object: obj ? fqn(obj) : '' };
}

/** The group to show: the requested one, else the newest group with members, else the newest. */
export function pickGroup(groups: JobGroup[], jobs: Pick<Job, 'group_id'>[], wanted: string | null): string | null {
  if (wanted && groups.some((g) => g.id === wanted)) return wanted;
  const newest = [...groups].sort((a, b) => b.created_at.localeCompare(a.created_at));
  const withMembers = new Set(jobs.map((j) => j.group_id).filter(Boolean));
  return (newest.find((g) => withMembers.has(g.id)) ?? newest[0])?.id ?? null;
}

/** The job-detail "Cross-system joins" box. */
export function joinsText(group: JobGroup | null | undefined): { text: string; tone: 'ok' | 'bad' | 'none' } {
  const c = group?.last_check;
  if (!c) return { text: 'not checked', tone: 'none' };
  return { text: `${c.consistent_pairs} / ${c.total_pairs} consistent`, tone: c.passed ? 'ok' : 'bad' };
}

/**
 * The once-only key and seed files of a new group (`.dpskey`, `.dpsseed`): JSON the wizard's
 * "Load key file" reads (`{key_material}` / `{synthesis_seed}`), each with the group's ids only.
 */
export function groupKeyFiles(made: { group: JobGroup; key_material: Record<string, unknown>; synthesis_seed: Record<string, unknown> }): {
  key: { name: string; text: string }; seed: { name: string; text: string };
} {
  const ids = { id: made.group.id, name: made.group.name, kid: made.group.kid };
  return {
    key: { name: `${made.group.name}.dpskey`, text: JSON.stringify({ group: ids, key_material: made.key_material }, null, 2) },
    seed: { name: `${made.group.name}.dpsseed`, text: JSON.stringify({ group: ids, synthesis_seed: made.synthesis_seed }, null, 2) },
  };
}
