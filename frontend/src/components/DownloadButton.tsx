import { useState } from 'react';
import { ApiError, saveBlob, type Download } from '../lib/api';

/** Fetch a file with the API key and hand it to the browser (PDFs, CSVs, YAML, JSON packs). */
export function DownloadButton({ label, fetcher, className = 'btn btn-sm', ariaLabel, disabled }: {
  label: string; fetcher: () => Promise<Download>; className?: string; ariaLabel?: string; disabled?: boolean;
}): JSX.Element {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function run(): Promise<void> {
    setBusy(true);
    setError(null);
    try {
      const { blob, filename } = await fetcher();
      saveBlob(blob, filename);
    } catch (e) {
      setError(e instanceof ApiError ? `${e.message}${e.code ? ` (${e.code})` : ''}` : 'Download failed');
    } finally {
      setBusy(false);
    }
  }
  return (
    <span className="dlb">
      <button type="button" className={className} onClick={() => void run()} disabled={busy || disabled} aria-label={ariaLabel ?? label}>
        {busy ? 'Preparing…' : label}
      </button>
      {error && <span className="err" role="alert">{error}</span>}
    </span>
  );
}
