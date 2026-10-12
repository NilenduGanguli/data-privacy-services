/** Pure helpers of the Playground Document tab (mockup 08): no React, no fetch, so they are
 *  unit-tested under Node (frontend/tests/documents.test.ts). */
import type { DocumentOptions, DocumentTransformOut, OcrMode, PageBox, PageFinding, PageSummary } from './types';

export type OutMode = 'red' | 'obf' | 'syn';

/** One entry per finding number (a finding spanning two pages appears on both), in number order. */
export function uniqueFindings(pages: PageSummary[]): PageFinding[] {
  const seen = new Map<number, PageFinding>();
  for (const p of pages) for (const f of p.findings) if (!seen.has(f.n)) seen.set(f.n, f);
  return [...seen.values()].sort((a, b) => a.n - b.n);
}

/** `n → K` for each finding whose value (same entity type, same `value_hash`) first appeared as K. */
export function sameValueAs(findings: PageFinding[]): Record<number, number> {
  const first = new Map<string, number>();
  const out: Record<number, number> = {};
  for (const f of findings) {
    if (!f.value_hash) continue;
    const key = `${f.entity_type}:${f.value_hash}`;
    const k = first.get(key);
    if (k === undefined) first.set(key, f.n); else out[f.n] = k;
  }
  return out;
}

/** PDFs and images have a page size in points; DOCX, TXT and CSV pages are text (0 × 0, no image). */
export function hasGeometry(page: PageSummary): boolean { return page.width > 0 && page.height > 0; }

function pct(v: number, of: number): string { return `${((Math.max(v, 0) / of) * 100).toFixed(3)}%`; }

/** A box in percent of its page: the API's boxes are PDF points, origin top-left (normalised page). */
export function boxStyle(b: PageBox, page: PageSummary): { left: string; top: string; width: string; height: string } {
  return { left: pct(b.x0, page.width), top: pct(b.y0, page.height), width: pct(b.x1 - b.x0, page.width), height: pct(b.y1 - b.y0, page.height) };
}

/** Token text sized to its box, in container-height units of the page frame (`container-type: size`). */
export function tokenFontSize(b: PageBox, page: PageSummary): string {
  return `${((Math.max(b.y1 - b.y0, 0) / page.height) * 100 * 0.72).toFixed(3)}cqh`;
}

/** Box colour: kept by policy, critical, or sensitive. */
export function findingTone(f: PageFinding): 'kept' | 'crit' | 'sens' {
  if (!f.redacted && f.method === 'keep') return 'kept';
  return f.sensitivity === 'CRITICAL' ? 'crit' : 'sens';
}

export interface TextSegment { text: string; n?: number; entity_type?: string }

/** The result text cut at each replacement's output span (overlapping or out-of-range spans are
 *  left as plain text); joining the segments gives the text back. */
export function tokenSegments(text: string, findings: PageFinding[]): TextSegment[] {
  const spans = findings
    .filter((f) => f.out_span && f.replacement)
    .sort((a, b) => a.out_span![0] - b.out_span![0]);
  const parts: TextSegment[] = [];
  let cursor = 0;
  for (const f of spans) {
    const [start, end] = f.out_span!;
    if (start < cursor || end > text.length || start >= end) continue;
    if (start > cursor) parts.push({ text: text.slice(cursor, start) });
    parts.push({ text: text.slice(start, end), n: f.n, entity_type: f.entity_type });
    cursor = end;
  }
  if (cursor < text.length) parts.push({ text: text.slice(cursor) });
  return parts;
}

/** Words read by OCR (OCR and hybrid pages). */
export function ocrWordCount(pages: PageSummary[]): number {
  return pages.filter((p) => p.source === 'ocr' || p.source === 'hybrid').reduce((n, p) => n + p.words, 0);
}

export interface VerifyVerdict {
  /** ok: sweep passed (and a redacted PDF passed its OCR re-read); partial: passed with coverage
   *  gaps; unverified: no sweep, or a redacted PDF nobody OCR re-read; failed: an original leaked. */
  state: 'ok' | 'partial' | 'unverified' | 'failed';
  verified: boolean; leaks: number; checked: number; gaps: string[];
}

/** The Verify step and line. A redacted PDF counts as verified only when OCR re-read it
 *  (`ocr_checked`): `passed` alone also holds when the re-read was skipped (review G3-r2 N3). */
export function verifyState(res: DocumentTransformOut): VerifyVerdict {
  const v = res.report.verification;
  const r = res.document.redaction;
  const leaks = (v?.hits.length ?? 0) + (r?.ocr_hits ?? 0);
  const base = { leaks, checked: v?.checked ?? 0, gaps: v?.coverage?.gaps ?? [] };
  if (v?.status === 'failed' || (r !== null && !r.passed)) return { ...base, state: 'failed', verified: false };
  if (!v || (r !== null && !r.ocr_checked)) return { ...base, state: 'unverified', verified: false };
  if (v.status === 'partial') return { ...base, state: 'partial', verified: true };
  return { ...base, state: 'ok', verified: true };
}

/** PDFs and images are paged (previews, boxes, a redacted PDF); DOCX, TXT and CSV are text. */
export function isPaged(name: string | null): boolean { return !!name && /\.(pdf|png|jpe?g)$/i.test(name); }

/** Every request asks for previews; a redacted PDF only in Redact mode for a paged file. */
export function documentOptions(mode: OutMode, ocr: OcrMode, fileName: string | null): DocumentOptions {
  return { ocr, previews: true, redacted_pdf: mode === 'red' && isPaged(fileName) };
}

/** Single-quote a shell word (`'` → `'\''`). */
function sq(s: string): string { return `'${s.replace(/'/g, "'\\''")}'`; }

/** The same request as curl. The key is always `$DPS_API_KEY` (never the console's key). */
export function curlCommand(a: {
  origin: string; mode: OutMode; tenant: string; fileName: string | null; policy: unknown; ocr: OcrMode;
}): string {
  const name = a.fileName ?? 'document.pdf';
  const endpoint = `/api/v1/documents/${a.mode === 'syn' ? 'synthesize' : 'obfuscate'}`;
  const { ocr, redacted_pdf: redactedPdf } = documentOptions(a.mode, a.ocr, name);
  const request = JSON.stringify({ policy: a.policy, options: { ocr, redacted_pdf: redactedPdf } });
  const tenant = a.tenant ? ` -H ${sq(`X-DPS-Tenant: ${a.tenant}`)}` : '';
  return `curl -sS -X POST ${sq(`${a.origin}${endpoint}`)} -H "X-API-KEY: $DPS_API_KEY"${tenant} \\\n  -F ${sq(`file=@${name}`)} \\\n  -F ${sq(`request=${request}`)}`;
}

function seconds(ms: number | undefined): string { return ms === undefined ? '—' : `${(ms / 1000).toFixed(1)} s`; }

/** "6.1 s total · OCR 4.7 s · engine 0.9 s · PDF 0.5 s" from `document.timings_ms`. */
export function timingLine(t: Record<string, number>): string {
  return `${seconds(t.total)} total · OCR ${seconds(t.ocr)} · engine ${seconds(t.engine)}${t.redact !== undefined ? ` · PDF ${seconds(t.redact)}` : ''}`;
}

export function secondsText(ms: number | undefined): string { return seconds(ms); }

/** `<name without extension>.redacted.pdf`. */
export function redactedName(fileName: string): string { return `${fileName.replace(/\.[^.]+$/, '')}.redacted.pdf`; }

export function sizeText(bytes: number): string {
  return bytes > 1 << 20 ? `${(bytes / (1 << 20)).toFixed(1)} MB` : `${Math.max(1, Math.round(bytes / 1024))} KB`;
}

export function extensionLabel(name: string): string { return (name.split('.').pop() ?? '').toUpperCase().slice(0, 4); }
