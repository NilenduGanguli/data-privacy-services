// Playground Document tab logic (mockup 08): findings by number, box geometry, token text,
// the Verify verdict, request options and the curl line.
import assert from 'node:assert/strict';
import { test } from 'node:test';
import {
  boxStyle, curlCommand, documentOptions, extensionLabel, extractionHead, findingTone, hasGeometry, isPaged, ocrWordCount, redactedName,
  sameValueAs, sizeText, timingLine, tokenFontSize, tokenSegments, uniqueFindings, verifyState,
} from '../src/lib/documents.ts';
import type { DocumentTransformOut, PageFinding, PageSummary, Report } from '../src/lib/types.ts';

function finding(n: number, over: Partial<PageFinding> = {}): PageFinding {
  return {
    n, entity_type: 'EMAIL', score: 0.99, sensitivity: 'HIGH', detectors: ['patterns'], evidence: ['pattern:email'],
    value_hash: `h${n}`, page: 1, line: 1, path: '$', span: [0, 5], boxes: [], method: 'det_token', reversible: true,
    redacted: true, replacement: `tok${n}`, out_span: null, ocr_confidence: null, review: false, ...over,
  };
}

function page(n: number, findings: PageFinding[], over: Partial<PageSummary> = {}): PageSummary {
  return {
    page: n, width: 600, height: 800, unit: 'pt', source: 'text_layer', words: 10, lines: 2, ocr_confidence: null,
    findings, preview_png_b64: null, redacted_preview_png_b64: null, ...over,
  };
}

const report = (verification: Report['verification']): Report => ({
  mode: 'obfuscate', policy: 'gdpr-pseudonymise', policy_hash: 'x', engine_version: '1', findings_total: 0,
  findings_by_type: {}, findings_by_detector: {}, record_sensitivity: 'HIGH', methods_used: {}, fallbacks: {},
  reversible_values: 0, verification, llm: { calls: 0, raw_pii_sent: 0 }, duration_ms: 1,
});

const verification = (status: 'passed' | 'partial' | 'failed', hits = 0): Report['verification'] => ({
  passed: status !== 'failed', status, checked: 4, hits: Array.from({ length: hits }, () => ({ path: '$', entity_type: 'SSN' })),
  coverage: status === 'partial'
    ? { basis: 'detected-values', detectors: ['patterns'], ner: 'off', free_text_fields: 1, complete: false, gaps: ['free text without NER'] }
    : null,
});

function out(over: Partial<DocumentTransformOut> & { redaction?: DocumentTransformOut['document']['redaction'] } = {}): DocumentTransformOut {
  const { redaction = null, ...rest } = over;
  return {
    result: 'text', findings: [], plan: [], key_material: null, reveal_hints: [], synthesis_seed: null, report: report(verification('passed')),
    trace_id: 't', redacted_pdf_b64: null, result_csv: null, pages: [],
    document: {
      format: 'pdf', pages: 1, ocr_pages: [], unreadable_pages: [], words: 10, boxes: 0, ocr_engine: null, bytes: 1, sha256: 'x',
      warnings: [], timings_ms: { total: 1200, ocr: 800, engine: 300 }, redaction, synthetic_validity: null,
    },
    ...rest,
  };
}

const redaction = (over: Partial<NonNullable<DocumentTransformOut['document']['redaction']>> = {}) => ({
  boxes: 3, pages: 2, dpi: 200, image_format: 'jpeg', bytes: 1000, sha256: 'x', text_layer_chars: 0, text_operators: 0,
  annotations: 0, ocr_checked: true, ocr_pages: 2, ocr_hits: 0, chars_under_boxes: 12, passed: true, ...over,
});

test('a finding on two pages is listed once, in number order', () => {
  const pages = [page(1, [finding(2), finding(1)]), page(2, [finding(2, { page: 2 }), finding(3, { page: 2 })])];
  assert.deepEqual(uniqueFindings(pages).map((f) => f.n), [1, 2, 3]);
});

test('"same value as K" pairs equal hashes of one entity type, never across types', () => {
  const fs = [
    finding(1, { value_hash: 'a' }), finding(2, { value_hash: 'a' }), finding(3, { value_hash: 'a', entity_type: 'PHONE' }),
    finding(4, { value_hash: null }), finding(5, { value_hash: null }), finding(6, { value_hash: 'a' }),
  ];
  assert.deepEqual(sameValueAs(fs), { 2: 1, 6: 1 });
});

test('boxes are placed in percent of the page (points, top-left origin) and tokens sized in cqh', () => {
  const p = page(1, []);
  assert.deepEqual(boxStyle({ page: 1, x0: 60, y0: 200, x1: 360, y1: 220 }, p),
    { left: '10.000%', top: '25.000%', width: '50.000%', height: '2.500%' });
  assert.equal(tokenFontSize({ page: 1, x0: 0, y0: 0, x1: 10, y1: 20 }, p), '1.800cqh');
  assert.deepEqual(boxStyle({ page: 1, x0: 10, y0: 10, x1: 5, y1: 5 }, p).width, '0.000%'); // never negative
});

test('box tone: kept, critical or sensitive', () => {
  assert.equal(findingTone(finding(1, { method: 'keep', redacted: false })), 'kept');
  assert.equal(findingTone(finding(1, { sensitivity: 'CRITICAL' })), 'crit');
  assert.equal(findingTone(finding(1, { sensitivity: 'MEDIUM' })), 'sens');
});

test('token text: replacements by output span, overlaps and spans out of range skipped', () => {
  const text = 'Mail AAAA and BBBB.';
  const segs = tokenSegments(text, [
    finding(2, { out_span: [14, 18] }), finding(1, { out_span: [5, 9] }), finding(3, { out_span: [6, 8] }),
    finding(4, { out_span: [17, 40] }), finding(5, { out_span: null }), finding(6, { out_span: [2, 4], replacement: null }),
  ]);
  assert.deepEqual(segs.map((s) => [s.text, s.n ?? null]), [['Mail ', null], ['AAAA', 1], [' and ', null], ['BBBB', 2], ['.', null]]);
  assert.equal(segs.map((s) => s.text).join(''), text);
});

test('OCR words count OCR and hybrid pages only', () => {
  assert.equal(ocrWordCount([page(1, [], { source: 'ocr', words: 40 }), page(2, [], { source: 'hybrid', words: 5 }), page(3, [])]), 45);
});

test('Verify: passed sweep and an OCR re-read redaction are verified', () => {
  const v = verifyState(out({ redaction: redaction() }));
  assert.equal(v.state, 'ok');
  assert.equal(v.leaks, 0);
  assert.ok(v.verified);
});

test('Verify: a redaction that was not OCR re-read is never "Verified" (review N3)', () => {
  const v = verifyState(out({ redaction: redaction({ ocr_checked: false, ocr_pages: 0 }) }));
  assert.equal(v.state, 'unverified');
  assert.equal(v.verified, false);
});

test('Verify: partial coverage passes with its gaps; a failed sweep or redaction fails', () => {
  const partial = verifyState(out({ report: report(verification('partial')) }));
  assert.equal(partial.state, 'partial');
  assert.ok(partial.verified);
  assert.deepEqual(partial.gaps, ['free text without NER']);
  const failed = verifyState(out({ report: report(verification('failed', 2)) }));
  assert.deepEqual([failed.state, failed.leaks, failed.verified], ['failed', 2, false]);
  const leaked = verifyState(out({ redaction: redaction({ passed: false, ocr_hits: 1 }) }));
  assert.deepEqual([leaked.state, leaked.leaks, leaked.verified], ['failed', 1, false]);
  assert.equal(verifyState(out({ report: report(null) })).state, 'unverified');
});

test('request options: previews always; a redacted PDF only in Redact mode for PDFs and images', () => {
  assert.deepEqual(documentOptions('red', 'auto', 'kyc-scanned.pdf'), { ocr: 'auto', previews: true, redacted_pdf: true });
  assert.deepEqual(documentOptions('red', 'never', 'scan.JPEG'), { ocr: 'never', previews: true, redacted_pdf: true });
  assert.deepEqual(documentOptions('red', 'auto', 'kyc.docx'), { ocr: 'auto', previews: true, redacted_pdf: false });
  assert.deepEqual(documentOptions('obf', 'always', 'kyc.pdf'), { ocr: 'always', previews: true, redacted_pdf: false });
  assert.ok(isPaged('a.png') && isPaged('b.PDF') && !isPaged('c.csv') && !isPaged(null));
});

test('curl: the key is $DPS_API_KEY, never the settings key; JSON and names are shell-quoted', () => {
  const cmd = curlCommand({
    origin: 'http://127.0.0.1:8401', mode: 'red', tenant: "o'brien", fileName: "it's.pdf",
    policy: { preset: 'pci-dss', detect: { custom_recognizers: [{ regex: "\\bO'\\d+" }] } }, ocr: 'auto',
  });
  assert.match(cmd, /^curl -sS -X POST 'http:\/\/127\.0\.0\.1:8401\/api\/v1\/documents\/obfuscate' -H "X-API-KEY: \$DPS_API_KEY"/);
  assert.ok(cmd.includes(`-H 'X-DPS-Tenant: o'\\''brien'`));
  assert.ok(cmd.includes(`-F 'file=@it'\\''s.pdf'`));
  const json = /-F 'request=(.*)'$/s.exec(cmd)?.[1].replaceAll(`'\\''`, "'");
  assert.deepEqual(JSON.parse(json ?? ''), {
    policy: { preset: 'pci-dss', detect: { custom_recognizers: [{ regex: "\\bO'\\d+" }] } },
    options: { ocr: 'auto', redacted_pdf: true },
  });
  const syn = curlCommand({ origin: 'https://h', mode: 'syn', tenant: '', fileName: null, policy: 'synthetic-realistic', ocr: 'never' });
  assert.ok(syn.includes('/api/v1/documents/synthesize') && !syn.includes('X-DPS-Tenant') && syn.includes("file=@document.pdf"));
  assert.ok(syn.includes('"redacted_pdf":false'));
});

test('labels: timing line, redacted file name, size and extension', () => {
  assert.equal(timingLine({ total: 6100, ocr: 4700, engine: 900, redact: 500 }), '6.1 s total · OCR 4.7 s · engine 0.9 s · PDF 0.5 s');
  assert.equal(timingLine({ total: 100, engine: 50 }), '0.1 s total · OCR — · engine 0.1 s');
  assert.equal(redactedName('KYC-IN-0098812.pdf'), 'KYC-IN-0098812.redacted.pdf');
  assert.equal(redactedName('scan.v2.jpeg'), 'scan.v2.redacted.pdf');
  assert.equal(redactedName('noext'), 'noext.redacted.pdf');
  assert.equal(sizeText(2.4 * (1 << 20)), '2.4 MB');
  assert.equal(sizeText(100), '1 KB');
  assert.equal(extensionLabel('kyc-scanned.pdf'), 'PDF');
  assert.equal(extensionLabel('x.jpeg'), 'JPEG');
});

test('DOCX, TXT and CSV pages have no geometry: no page image to draw boxes on', () => {
  assert.equal(hasGeometry(page(1, [])), true);
  assert.equal(hasGeometry(page(1, [], { width: 0, height: 0, source: 'docx' })), false);
});

test('the text pane says what it shows: obfuscated, synthetic, or the revealed original', () => {
  assert.deepEqual(extractionHead(false, false), { title: 'Text extraction · obfuscated', tone: 'teal', note: 'reversible with key' });
  assert.deepEqual(extractionHead(true, false), { title: 'Text extraction · synthetic', tone: 'violet', note: 'no path back' });
  // After Reveal the pane holds the original values: never labelled as obfuscated.
  assert.deepEqual(extractionHead(false, true), { title: 'Text extraction · revealed original', tone: 'amber', note: 'original values · not stored' });
});
