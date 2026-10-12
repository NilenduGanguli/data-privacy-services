import { useEffect, useState } from 'react';
import { api } from '../lib/api';
import { ocrConfigured, ocrEngineLabel, ocrNote, ocrState, type OcrState } from '../lib/ocr';
import { Tone } from './Chip';

export { ocrState, type OcrState };

/** The OCR readiness component (`/readyz` → components.ocr): engine, up/down, or not configured. */
export function OcrStatus({ compact = false }: { compact?: boolean }): JSX.Element {
  const [state, setState] = useState<OcrState | null>(null);
  const [loaded, setLoaded] = useState(false);
  useEffect(() => {
    let alive = true;
    api.readyz().then((r) => { if (alive) { setState(ocrState(r)); setLoaded(true); } })
      .catch(() => { if (alive) setLoaded(true); });
    return () => { alive = false; };
  }, []);
  if (!loaded) return <span className="faint" data-testid="ocr-status">OCR …</span>;
  if (!state || !ocrConfigured(state)) {
    return (
      <span data-testid="ocr-status"><Tone tone="slate">OCR not configured</Tone>
        {!compact && <span className="faint"> · scanned pages and images cannot be read</span>}</span>
    );
  }
  return (
    <span data-testid="ocr-status"><Tone tone={state.ok ? 'emerald' : 'rose'}>{ocrEngineLabel(state.engine)} · {state.ok ? 'up' : state.detail}</Tone>
      {!compact && <span className="faint"> · {ocrNote(state.engine)}</span>}</span>
  );
}
