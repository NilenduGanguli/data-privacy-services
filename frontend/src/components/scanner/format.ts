/**
 * Display helpers of the UI-scanner pages (mockups 12 and 04). Pure and value-free: they read
 * redacted URLs, types, counts and codes — never a page value.
 */
import type { NumberedRiskFlag, RiskFlag, ScanEvent, ScanField, ScanPage, Sensitivity } from '../../lib/types';

export type Tone = 'teal' | 'amber' | 'emerald' | 'rose' | 'violet' | 'slate';

const ORIGIN = /^[a-z][a-z0-9+.-]*:\/\/[^/?#]*/i;
const PLACEHOLDER = /<[A-Za-z0-9_]+>|%3C[A-Za-z0-9_]+%3E/gi;

const mask = (s: string): string => s.replace(PLACEHOLDER, '•••');

/**
 * Path and redacted query of a redacted URL, its `<v>`/`<ENTITY>` placeholders shown as •••. A URL
 * on another origin than `origin` (when given) keeps its origin, so a foreign host never reads as a path.
 */
export function pathOf(url: string | null | undefined, origin?: string): string {
  if (!url) return '—';
  const m = ORIGIN.exec(url);
  if (m && origin && m[0].toLowerCase() !== origin.toLowerCase()) return mask(url);
  const rest = m ? url.slice(m[0].length) : url;
  return mask(rest.startsWith('/') ? rest : `/${rest}`);
}

/** `demo-site:8410` from `http://demo-site:8410/apply` ('' for anything that is not an absolute URL). */
export function hostOf(url: string | null | undefined): string {
  const m = url ? /^[a-z][a-z0-9+.-]*:\/\/([^/?#@]+)/i.exec(url) : null;
  return m ? m[1].toLowerCase() : '';
}

/** The origin of an absolute URL (`http://demo-site:8410`), '' otherwise. */
export function originOf(url: string | null | undefined): string {
  const m = url ? ORIGIN.exec(url) : null;
  return m ? m[0].toLowerCase() : '';
}

export interface StatusIcon { cls: 'ok' | 'run' | 'q' | 'skip'; glyph: string; label: string }

/** The crawl-map status dot of a page. */
export function statusIcon(page: Pick<ScanPage, 'status'>): StatusIcon {
  switch (page.status) {
    case 'done': return { cls: 'ok', glyph: '✓', label: 'visited' };
    case 'redirected': return { cls: 'ok', glyph: '↪', label: 'redirected' };
    case 'visiting': return { cls: 'run', glyph: '', label: 'in progress' };
    case 'queued': return { cls: 'q', glyph: '', label: 'queued' };
    case 'failed': return { cls: 'skip', glyph: '–', label: 'failed' };
    default: return { cls: 'skip', glyph: '–', label: 'skipped' };
  }
}

const SKIP: Record<string, string> = {
  deny: 'deny list', other_origin: 'other origin', unregistered_host: 'unregistered host', not_allowed: 'outside allow list',
  depth: 'depth limit', template: 'template limit, links not followed', budget: 'page budget',
};

/** Why a page was not visited, in words. */
export function skipLabel(reason: string | null | undefined): string {
  if (!reason) return 'skipped';
  return SKIP[reason] ?? reason.replace(/_/g, ' ');
}

const ENTITY: Record<string, string> = {
  AADHAAR: 'Aadhaar', PAN_IN: 'PAN', SSN: 'SSN', ITIN: 'ITIN', NINO: 'NINO', SIN: 'SIN', CURP: 'CURP', RFC: 'RFC',
  CARD_PAN: 'card number', CVV: 'card security code', CARD_EXPIRY: 'card expiry', EMAIL: 'e-mail', PHONE: 'phone',
  PERSON_NAME: 'name', DATE_OF_BIRTH: 'date of birth', IBAN: 'IBAN', IFSC: 'IFSC', ACCOUNT_NUMBER: 'account number',
  PASSWORD: 'password', NHS_NUMBER: 'NHS number', VOTER_ID: 'voter id',
};

/** An entity type in words (`PAN_IN` → "PAN", `STREET_ADDRESS` → "street address"). */
export function entityName(type: string): string {
  return ENTITY[type] ?? type.toLowerCase().replace(/_/g, ' ');
}

/** "a", "a and b", "a, b and c" of entity types; "personal data" when none is named. */
export function entityList(types: string[]): string {
  const names = types.map(entityName);
  if (!names.length) return 'personal data';
  return names.length === 1 ? names[0] : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/** The sentence of a risk flag (mockup 04's risk-flag list and mockup 12's crawl-map footer). */
export function flagSentence(flag: RiskFlag): string {
  switch (flag.flag) {
    case 'autocomplete_enabled_sensitive': return `autocomplete on for ${entityList(flag.entity_types)}`;
    case 'password_without_autocomplete_token': return 'password field without new-password/current-password';
    case 'pii_in_query_string': return `PII in query string: ${flag.query_keys.map((k) => `?${k}=`).join(' ') || 'a query value'}`;
    case 'sensitive_form_uses_get': return `form sends ${entityList(flag.entity_types)} in the URL (method GET)`;
    case 'pii_rendered_unmasked': return `${entityList(flag.entity_types)} rendered unmasked${flag.where === 'table' ? ' in a table' : ''}`;
    default: return String(flag.flag).replace(/_/g, ' ');
  }
}

export const flagTone = (flag: Pick<RiskFlag, 'severity'>): Tone => (flag.severity === 'high' ? 'rose' : 'amber');

/** Chip colour of an entity by sensitivity: critical rose, high/medium amber, low (or none) slate. */
export function entityChip(sensitivity: Sensitivity | null | undefined): Tone {
  if (sensitivity === 'CRITICAL') return 'rose';
  if (sensitivity === 'HIGH' || sensitivity === 'MEDIUM') return 'amber';
  return 'slate';
}

const FAMILIES = ['token', 'label', 'name', 'type', 'pattern', 'maxlength', 'value'];

/** Detector families that fired, in a fixed order: "token + label". */
export function evidenceLabel(detectors: string[]): string {
  const known = FAMILIES.filter((f) => detectors.includes(f));
  const other = detectors.filter((d) => !FAMILIES.includes(d));
  return [...known, ...other].join(' + ') || '—';
}

/** "text · name=fullName", "text · pattern …", "text · maxlength=10", "select · name=gender" (one qualifier). */
export function inputText(f: Pick<ScanField, 'tag' | 'input_type' | 'name_attr' | 'pattern' | 'maxlength'>): string {
  const kind = f.tag === 'input' ? (f.input_type ?? 'text') : f.tag;
  // one qualifier, as in mockup 04: the pattern, else the maxlength, else the name
  const q = f.pattern ? `pattern ${f.pattern}` : f.maxlength ? `maxlength=${f.maxlength}` : f.name_attr ? `name=${f.name_attr}` : null;
  return q ? `${kind} · ${q}` : kind;
}

/**
 * The autocomplete column: the token, `on`, `off` or `—`. An absent attribute reads `on` (the
 * browser default) when the field carries an autocomplete risk flag.
 */
export function autocompleteText(f: Pick<ScanField, 'autocomplete' | 'risk_codes'>, flags: NumberedRiskFlag[] = []): string {
  if (f.autocomplete) return f.autocomplete;
  const auto = flags.some((r) => r.flag === 'autocomplete_enabled_sensitive' && f.risk_codes.includes(r.code));
  return auto ? 'on' : '—';
}

/** "persona.<attr> / <obfuscate method>" ("— / keep" for a field no persona attribute fills). */
export function fillMethod(f: Pick<ScanField, 'persona_attr' | 'obfuscate_method'>): string {
  return `${f.persona_attr ? `persona.${f.persona_attr}` : '—'} / ${f.obfuscate_method ?? 'keep'}`;
}

export const plural = (n: number, one: string, many = `${one}s`): string => `${n} ${n === 1 ? one : many}`;

/** The event column of the navigation log: the HTTP method, or fill / capture / spa. */
export function eventVerb(e: Pick<ScanEvent, 'kind' | 'method'>): string {
  if (e.kind === 'navigate' || e.kind === 'skip') return e.method ?? 'GET';
  if (e.kind === 'request') return e.method ?? 'request';
  return e.kind;
}

const RESULT_TONE: Record<string, Tone> = { blocked: 'teal', skipped: 'slate', captured: 'violet', withheld: 'amber', failed: 'rose' };
export const eventResultTone = (result: string): Tone | null => RESULT_TONE[result] ?? null;

/** The note column of the navigation log (mockup 12's sentences). */
export function eventNote(e: ScanEvent, pages: ReadonlyMap<number, ScanPage>): string {
  const pg = e.page_no !== null ? pages.get(e.page_no) : undefined;
  const at = pg ? pathOf(pg.url_redacted) : null;
  switch (e.kind) {
    case 'request':
      if (e.note_code === 'method' || e.note_code === 'filled_value') return at ? `from filled page ${at} · interception` : 'non-GET request · interception';
      if (e.note_code === 'filled_navigation') return at ? `navigation from filled page ${at} · interception` : 'navigation from a filled page · interception';
      if (e.note_code === 'host') return 'host outside the scan · interception';
      if (e.note_code === 'scheme') return 'scheme not allowed · interception';
      return `${e.note_code.replace(/_/g, ' ')} · interception`;
    case 'skip': return skipLabel(e.note_code);
    case 'fill': {
      if (e.result !== 'captured') return e.note_code === 'leakage' ? 'not stored — a filled value failed the leakage check' : `fill ${e.result}`;
      const who = e.note_code === 'obfuscate' ? 'tokens' : `persona ${e.counts.persona ?? 1}`;
      const rejected = e.counts.rejected ?? 0;
      return `${who} · ${plural(e.counts.fields ?? 0, 'field')} · ${rejected ? `${plural(rejected, 'validation message')} recorded` : 'all values accepted'}`;
    }
    case 'capture':
      if (e.result === 'captured') return e.note_code === 'obfuscate' ? 'obfuscated capture' : 'obfuscated capture · synthetic replacement';
      if (e.result === 'withheld') return 'withheld — a value could not be masked';
      return 'no personal data rendered';
    case 'spa': return 'SPA route found by clicking';
    case 'navigate':
      if (e.result === 'failed') return `failed · ${e.note_code.replace(/_/g, ' ')}`;
      if (pg && (pg.status === 'done' || pg.status === 'redirected')) {
        if (pg.status === 'redirected') return `redirected to ${pathOf(pg.final_url_redacted)}`;
        return pg.form_count ? `${plural(pg.form_count, 'form')} · ${plural(pg.field_count, 'field')}` : 'no form';
      }
      return e.note_code === 'start' ? 'start page' : e.note_code === 'spa_click' ? 'SPA route' : e.note_code === 'redirect' ? 'redirect' : '';
    case 'popup': return 'popup closed';
    case 'dialog': return `${e.note_code} dialog dismissed`;
    default: return `${e.kind} · ${e.note_code.replace(/_/g, ' ')}`;
  }
}

/** "4:12" (or "1:02:05" past an hour). */
export function fmtElapsed(seconds: number | null | undefined): string {
  const s = Math.max(0, Math.floor(seconds ?? 0));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, '0');
  return h ? `${h}:${String(m).padStart(2, '0')}:${ss}` : `${m}:${ss}`;
}
