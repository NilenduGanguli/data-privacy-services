/** Number and time formatting shared by the M5 pages (Overview, Reports, Admin, Policies).
 *  Pure functions: unit-tested under Vitest (format.test.ts). Times are UTC. */

const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const pad2 = (n: number): string => String(n).padStart(2, '0');

/** Grouped integer, `en-US` (1,234,567); `—` for null/undefined/NaN. */
export function count(n: number | null | undefined): string {
  return n === null || n === undefined || Number.isNaN(n) ? '—' : Math.round(n).toLocaleString('en-US');
}

/** Three significant digits with a unit: 2.71M, 24.6K, 1.2B; plain grouped number under 1,000. */
export function compact(n: number | null | undefined): string {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  const abs = Math.abs(n);
  for (const [size, unit] of [[1e9, 'B'], [1e6, 'M'], [1e3, 'K']] as const) {
    if (abs >= size) return `${Number((n / size).toPrecision(3))}${unit}`;
  }
  return count(n);
}

function parse(ts: string | Date | null | undefined): Date | null {
  if (!ts) return null;
  const d = ts instanceof Date ? ts : new Date(ts);
  return Number.isNaN(d.getTime()) ? null : d;
}

/** "just now", "12 s ago", "4 min ago", "2 h ago", "yesterday", "3 days ago"; "never" for null. */
export function relative(ts: string | Date | null | undefined, now: Date = new Date()): string {
  const d = parse(ts);
  if (!d) return 'never';
  const s = Math.max(0, Math.round((now.getTime() - d.getTime()) / 1000));
  if (s < 5) return 'just now';
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.floor(s / 60)} min ago`;
  const startOfToday = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  if (d.getTime() >= startOfToday) return `${Math.floor(s / 3600)} h ago`;
  if (d.getTime() >= startOfToday - 86_400_000) return 'yesterday';
  const days = Math.max(2, Math.floor((startOfToday - d.getTime()) / 86_400_000) + 1);
  return `${days} days ago`;
}

/** "11 Oct 09:49" (UTC). */
export function utc(ts: string | Date | null | undefined): string {
  const d = parse(ts);
  return d ? `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}` : '—';
}

/** "09:49:12" (UTC). */
export function utcTime(ts: string | Date | null | undefined): string {
  const d = parse(ts);
  return d ? `${pad2(d.getUTCHours())}:${pad2(d.getUTCMinutes())}:${pad2(d.getUTCSeconds())}` : '—';
}

/** "01 Oct 2026" (UTC). */
export function utcDate(ts: string | Date | null | undefined): string {
  const d = parse(ts);
  return d ? `${pad2(d.getUTCDate())} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}` : '—';
}

/** "11 Oct" (UTC) — also accepts an ISO day ("2026-10-11"). */
export function utcDay(ts: string | Date | null | undefined): string {
  const d = parse(ts);
  return d ? `${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]}` : '—';
}

/** "Oct 11" for chart axes from an ISO day. */
export function axisDay(day: string): string {
  const d = parse(day);
  return d ? `${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}` : day;
}

/** `sha256:18d24931…3a66` from a hex digest (with or without the `sha256:` prefix). */
export function shortHash(h: string | null | undefined): string {
  if (!h) return '—';
  const hex = h.replace(/^sha256:/, '');
  return hex.length <= 12 ? `sha256:${hex}` : `sha256:${hex.slice(0, 8)}…${hex.slice(-4)}`;
}

/** "28%" from a ratio or a percentage already in 0–100 (`isRatio` false). */
export function percent(value: number | null | undefined, isRatio = true, digits = 0): string {
  if (value === null || value === undefined || Number.isNaN(value)) return '—';
  return `${(isRatio ? value * 100 : value).toFixed(digits)}%`;
}

/** "▲ 28%" / "▼ 4%" / "= 0%" change of `now` against `prev`; null when there is no previous value. */
export function delta(now: number, prev: number | null | undefined): { text: string; up: boolean | null } | null {
  if (prev === null || prev === undefined || prev <= 0) return null;
  const change = Math.round(((now - prev) / prev) * 100);
  if (change === 0) return { text: '= 0%', up: null };
  return { text: `${change > 0 ? '▲' : '▼'} ${Math.abs(change)}%`, up: change > 0 };
}

/** "1 key" / "3 keys". */
export function plural(n: number, word: string, many = `${word}s`): string {
  return `${count(n)} ${n === 1 ? word : many}`;
}
