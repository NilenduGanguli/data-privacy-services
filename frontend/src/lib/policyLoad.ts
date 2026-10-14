// Loading the policy on the Policies page (mockup 05) so that a quick switch between policies can
// never show the previous policy's late answer (programme plan F4 polish M1-N; M5 console review
// N4), and saving draft edits without unhandled rejections (review N3).
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

/** Load the policy on screen: a tenant policy's detail from the store, else the shipped preset's
 *  body. Every update after an await first asks `current()`, so an answer (or an error) for a
 *  policy the user has already left is dropped instead of replacing the one now shown. */
export async function loadPolicyDetail(
  name: string, tenantPolicy: boolean, src: PolicyDetailSource, sink: PolicyDetailSink, current: () => boolean,
): Promise<void> {
  sink.error(null);
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
  sink.detail(null);
  sink.presetBody(null); // never show the previous preset's body under this policy's name
  try {
    const body = await src.validate(name);
    if (current()) sink.presetBody(body);
  } catch (e) {
    if (current()) sink.error(errorText(e));
  }
}

/** Save one draft edit made on the page (matrix, declared field, recognizer). The page's container
 *  already shows a refused save as the page error, so the rejection is handled here rather than
 *  escaping as an unhandled promise rejection. No base (no editable policy): nothing is saved. */
export function saveEdit(save: (body: Body) => Promise<unknown>, base: Body | null, edit: (b: Body) => Body): void {
  if (!base) return;
  save(edit(base)).catch(() => undefined);
}
