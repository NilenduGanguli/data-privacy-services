import { useEffect, useState } from 'react';
import { useSettings } from '../../hooks/useSettings';
import { api } from '../../lib/api';

/**
 * A scan screenshot. Artefacts need the API key, so the image is fetched through `api.scans.artefact`
 * and shown as a blob URL (CSP `img-src 'self' data: blob:`, M4 Task 18); the URL is revoked when the
 * id changes or the component unmounts.
 */
export function ArtefactImage({ id, alt }: { id: string | null | undefined; alt: string }): JSX.Element {
  const { apiKey, tenant } = useSettings();
  const [url, setUrl] = useState<string | null>(null);
  const [failed, setFailed] = useState(false);
  useEffect(() => {
    setUrl(null);
    setFailed(false);
    if (!id) return undefined;
    let alive = true;
    let objectUrl: string | null = null;
    api.scans.artefact({ apiKey, tenant }, id)
      .then((blob) => { if (!alive) return; objectUrl = URL.createObjectURL(blob); setUrl(objectUrl); })
      .catch(() => { if (alive) setFailed(true); });
    return () => { alive = false; if (objectUrl) URL.revokeObjectURL(objectUrl); };
  }, [id, apiKey, tenant]);
  if (!id) return <div className="shot-empty">Not captured</div>;
  if (failed) return <div className="shot-empty">This capture could not be loaded</div>;
  if (!url) return <div className="skeleton shot-skeleton" aria-hidden="true" />;
  return <img className="shot" src={url} alt={alt} />;
}
