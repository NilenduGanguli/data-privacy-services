/**
 * Hand the browser a file to save (no storage, no public link). `saveBlob` is M2's helper, defined
 * once in lib/api.ts and re-exported here; filenames are built from ids only, never from values.
 */
import { saveBlob } from './api';

export { saveBlob };

/** Save `text` as `filename` (JSON by default). */
export function saveText(text: string, filename: string, type = 'application/json'): void {
  saveBlob(new Blob([text], { type }), filename);
}
