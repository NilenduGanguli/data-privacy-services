import type { Readiness } from './types';

/** The `/readyz` → `components.ocr` entry as the console reads it. */
export interface OcrState { ok: boolean; detail: string; engine: string | null; provider: string | null }

/** Details meaning "no OCR on this deployment" (no Azure endpoint; no tesseract binary). */
const UNCONFIGURED = new Set(['unconfigured', 'tesseract_missing']);

export function ocrState(ready: Readiness | null): OcrState | null {
  const component = ready?.components?.ocr;
  if (!component) return null;
  return {
    ok: component.ok,
    detail: component.detail,
    engine: typeof component.engine === 'string' ? component.engine : null,
    provider: typeof component.provider === 'string' ? component.provider : null,
  };
}

/** False when the deployment has no OCR at all (scanned pages and images cannot be read). */
export function ocrConfigured(state: OcrState | null): boolean {
  return !!state && !UNCONFIGURED.has(state.detail);
}

/** `azure-read-v3.2` → "Azure Read v3.2"; `tesseract-5.5` → "Local Tesseract 5.5" (Task 19C). */
export function ocrEngineLabel(engine: string | null): string {
  if (!engine) return 'OCR';
  if (engine === 'azure-read-v3.2') return 'Azure Read v3.2';
  const tesseract = /^tesseract(?:-(.+))?$/.exec(engine);
  if (tesseract) return tesseract[1] ? `Local Tesseract ${tesseract[1]}` : 'Local Tesseract';
  return engine;
}

/** Where the text is read: the Read v3.2 contract (gateway or mock) or tesseract in this container. */
export function ocrNote(engine: string | null): string {
  return engine?.startsWith('tesseract') ? 'runs inside this service, no network call' : 'Read v3.2 contract (org gateway / local mock)';
}
