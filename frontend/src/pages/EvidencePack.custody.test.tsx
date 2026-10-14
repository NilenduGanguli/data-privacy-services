// The evidence pack states key custody once (M5 console review N2): section 2's "Key id" row
// carries the key id and scope only; the "Key custody" row says who holds the key.
import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import { html, occurrences, text } from '../test/render';
import { EvidenceSheet } from './EvidencePack';

const NOW = new Date('2026-10-13T10:15:00Z');
const packs = fx.evidencePacks(NOW);
const noop = async (): Promise<{ blob: Blob; filename: string }> => ({ blob: new Blob([]), filename: 'x' });

describe('EvidenceSheet key custody', () => {
  for (const id of ['ev_01K7ZQ9QA1', 'ev_01K7ZQS7B2'] as const) {
    it(`says it once (${id})`, () => {
      const out = html(<EvidenceSheet ev={packs[id]} job={null} snapshot={null} onJson={noop} onPdf={noop} />);
      expect(occurrences(text(out), 'DPS stored no key')).toBe(1);
      expect(text(out)).toContain('Key custody');
    });
  }
});
