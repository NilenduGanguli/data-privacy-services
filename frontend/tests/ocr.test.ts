// OCR readiness as the console shows it (/readyz → components.ocr).
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { ocrConfigured, ocrEngineLabel, ocrNote, ocrState } from '../src/lib/ocr.ts';
import type { Readiness } from '../src/lib/types.ts';

const ready = (ocr: Record<string, unknown> | undefined): Readiness => ({
  ready: true, components: ocr ? { ocr: ocr as Readiness['components'][string] } : {},
});

test('the ocr component is read with its engine and provider', () => {
  assert.deepEqual(ocrState(ready({ ok: true, detail: 'up', provider: 'azure', engine: 'azure-read-v3.2' })),
    { ok: true, detail: 'up', engine: 'azure-read-v3.2', provider: 'azure' });
  assert.equal(ocrState(ready(undefined)), null);
  assert.equal(ocrState(null), null);
  assert.deepEqual(ocrState(ready({ ok: false, detail: 'unconfigured', provider: 'none' })),
    { ok: false, detail: 'unconfigured', engine: null, provider: 'none' });
});

test('unconfigured and a missing tesseract binary both read as "not configured"', () => {
  assert.equal(ocrConfigured(null), false);
  assert.equal(ocrConfigured(ocrState(ready({ ok: false, detail: 'unconfigured', provider: 'none' }))), false);
  assert.equal(ocrConfigured(ocrState(ready({ ok: false, detail: 'tesseract_missing', provider: 'tesseract' }))), false);
  assert.equal(ocrConfigured(ocrState(ready({ ok: false, detail: 'down', provider: 'azure', engine: 'azure-read-v3.2' }))), true);
});

test('engine labels', () => {
  assert.equal(ocrEngineLabel('azure-read-v3.2'), 'Azure Read v3.2');
  assert.equal(ocrEngineLabel('tesseract-5.5'), 'Local Tesseract 5.5');
  assert.equal(ocrEngineLabel('tesseract'), 'Local Tesseract');
  assert.equal(ocrEngineLabel('something-new'), 'something-new');
  assert.equal(ocrEngineLabel(null), 'OCR');
});

test('the note says where the text is read', () => {
  assert.match(ocrNote('azure-read-v3.2'), /Read v3\.2 contract/);
  assert.match(ocrNote('tesseract-5.5'), /inside this service/);
});
