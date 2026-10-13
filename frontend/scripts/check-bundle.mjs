// Fails the build when dev-only fixture code reaches the production bundle (src/dev/*, src/lib/scanFixtures.ts).
import { readdirSync, readFileSync } from 'node:fs';
import { join } from 'node:path';

const MARKERS = ['dps-fixture-mode', 'ScanFixtureServer', 's_01K7ZR4M2D9Q8W6X3N5VTB7F1C', 'RklYVFVSRS1PTkxZ'];
const dir = new URL('../dist/assets/', import.meta.url).pathname;
const hits = [];
for (const name of readdirSync(dir)) {
  if (!name.endsWith('.js')) continue;
  const text = readFileSync(join(dir, name), 'utf8');
  for (const m of MARKERS) if (text.includes(m)) hits.push(`${name}: ${m}`);
}
if (hits.length) {
  console.error(`check-bundle: dev fixture code found in the production bundle:\n  ${hits.join('\n  ')}`);
  process.exit(1);
}
console.log(`check-bundle: no dev fixture code in dist/assets (${MARKERS.length} markers checked)`);
