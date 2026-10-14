// Loading the policy on the Policies page (mockup 05) so that a quick switch between policies can
// never show the previous policy (programme plan F4 polish M1-N; M5 console review N4): neither its
// late answer nor, while the next policy loads, the detail the page still holds; an edit is saved
// only to the policy it was made on; and a refused draft save leaves no unhandled rejection (N3).
import { ApiError, errorText } from './api';
import type { Body } from './policyEdit';
import type { PolicyDoc, TenantPolicyDetail } from './types';

/** Guards async loads that a newer load supersedes: `begin()` starts a load and returns
 *  `current`, which stays true until a later `begin()`. */
export class Latest {
  private n = 0;

  begin(): () => boolean {
    this.n += 1;
    const mine = this.n;
    return () => mine === this.n;
  }
}

export interface PolicyDetailSource {
  /** `GET /policies/{name}`: a tenant policy's detail (404 when the store has no such policy). */
  get: (name: string) => Promise<TenantPolicyDetail>;
  /** `POST /policies/validate`: a shipped preset's resolved body. */
  validate: (name: string) => Promise<PolicyDoc>;
}

export interface PolicyDetailSink {
  detail: (d: TenantPolicyDetail | null) => void;
  presetBody: (b: PolicyDoc | null) => void;
  error: (message: string | null) => void;
}

/** What the page may show for the policy `name`: only a tenant detail or a preset body loaded for
 *  that name. The answer of the policy the user just left, held until this one answers, is never
 *  shown (or edited) under this name. */
export function onScreen(
  name: string, detail: TenantPolicyDetail | null, presetBody: PolicyDoc | null,
): { detail: TenantPolicyDetail | null; presetBody: PolicyDoc | null } {
  return { detail: detail?.name === name ? detail : null, presetBody: presetBody?.name === name ? presetBody : null };
}

/** Load the policy on screen: a tenant policy's detail from the store, else the shipped preset's
 *  body. A switch (`keep` false) clears the page at once, so nothing of the previous policy stays
 *  on screen or editable while this one loads; a reload of the same policy (`keep`, e.g. after
 *  publish) keeps it until the answer. Every update after an await first asks `current()`, so an
 *  answer (or an error) for a policy the user has already left is dropped. */
export async function loadPolicyDetail(
  name: string, tenantPolicy: boolean, src: PolicyDetailSource, sink: PolicyDetailSink, current: () => boolean, keep = false,
): Promise<void> {
  sink.error(null);
  if (!keep) {
    sink.detail(null);
    sink.presetBody(null);
  }
  if (tenantPolicy) {
    try {
      const d = await src.get(name);
      if (current()) { sink.detail(d); sink.presetBody(null); }
      return;
    } catch (e) {
      if (!(e instanceof ApiError && e.status === 404)) {
        if (current()) sink.error(errorText(e));
        return;
      }
    }
    if (!current()) return;
  }
  sink.detail(null); // a shipped preset, or a tenant policy the store no longer has
  try {
    const body = await src.validate(name);
    if (current()) sink.presetBody(body);
  } catch (e) {
    if (current()) sink.error(errorText(e));
  }
}

/** Where a draft edit made on the page goes: the shown detail's own policy, and the body the edit
 *  starts from (its draft, else its active version). */
export interface EditTarget { policy: string; base: Body }

/** The edit target, or null while the page shows no editable detail (a shipped preset, a read-only
 *  key, or the next policy still loading): an edit can never be saved to another policy. */
export function editTarget(detail: TenantPolicyDetail | null, editable: boolean): EditTarget | null {
  if (!detail || !editable) return null;
  const base = (detail.draft_body ?? detail.active_body) as Body | null;
  return base ? { policy: detail.name, base } : null;
}

/** Save one draft edit made on the page (matrix, declared field, recognizer) to the policy it was
 *  made on. The page's container already shows a refused save as the page error, so the rejection
 *  is handled here rather than escaping as an unhandled promise rejection. No target: nothing is
 *  saved. */
export function saveEdit(save: (policy: string, body: Body) => Promise<unknown>, target: EditTarget | null, edit: (b: Body) => Body): void {
  if (!target) return;
  save(target.policy, edit(target.base)).catch(() => undefined);
}
