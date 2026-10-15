import { useEffect, useState } from 'react';
import { useAuth } from '../../hooks/useAuth';
import { api, errorText } from '../../lib/api';
import type { PersonaPreview as Preview, SynthesisSeed } from '../../lib/types';

// identifiers read better with their name in front ("Aadhaar 9173 …", as in mockup 12)
const ID_LABEL: Record<string, string> = { aadhaar: 'Aadhaar', pan: 'PAN', ssn: 'SSN', nino: 'NINO', sin: 'SIN', curp: 'CURP', rfc: 'RFC' };

/** Persona 1's values as synthetic tokens, with the generator's notes (no seed shown). */
export function PersonaChips({ preview }: { preview: Preview }): JSX.Element {
  const values = Object.entries(preview.personas[0]?.values ?? {}).slice(0, 6);
  return (
    <>
      <div className="persona">{values.map(([k, v]) => <span key={k} className="tok syn">{ID_LABEL[k] ? `${ID_LABEL[k]} ${v}` : v}</span>)}</div>
      <div className="hint">{preview.notes.join(' · ')}</div>
    </>
  );
}

/**
 * "Persona 1 preview" (mockup 12): asks the API for persona 1 of `locale` with the seed held so far,
 * and hands the returned seed up so the scan reuses it — persona 1 of the scan is the one previewed.
 */
export function PersonaPreview({ locale, seed, onSeed }: { locale: string; seed: SynthesisSeed | null; onSeed: (s: SynthesisSeed) => void }): JSX.Element {
  const auth = useAuth();
  const [preview, setPreview] = useState<Preview | null>(null);
  const [error, setError] = useState<string | null>(null);
  useEffect(() => {
    let alive = true;
    setError(null);
    api.scans.personaPreview(auth, { locale, count: 1, synthesis_seed: seed })
      .then((p) => { if (!alive) return; setPreview(p); if (!seed) onSeed(p.synthesis_seed); })
      .catch((e: unknown) => { if (alive) setError(errorText(e)); });
    return () => { alive = false; };
    // the seed is fixed once received; a locale change re-previews with it
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [locale, auth]);
  return (
    <div className="field"><span className="lbl">Persona 1 preview</span>
      {error ? <div className="err" role="alert">The preview is unavailable ({error}); the scan still generates its personas.</div>
        : preview ? <PersonaChips preview={preview} /> : <div className="skeleton" style={{ width: '70%' }} aria-hidden="true" />}
    </div>
  );
}
