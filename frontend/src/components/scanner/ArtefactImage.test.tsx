// Screenshots are fetched with the API key and shown as blob URLs; the first frame is a placeholder.
import { describe, expect, it } from 'vitest';
import { render, text } from '../../test/render';
import { ArtefactImage } from './ArtefactImage';

describe('ArtefactImage', () => {
  it('says "Not captured" when the page has no such artefact', () => {
    expect(text(render(<ArtefactImage id={null} alt="Annotated original" />))).toBe('Not captured');
  });
  it('shows a skeleton until the authenticated fetch resolves (never a public src)', () => {
    const html = render(<ArtefactImage id="art_2_annotated" alt="Annotated original" />);
    expect(html).toContain('shot-skeleton');
    expect(html).not.toContain('<img');
    expect(html).not.toContain('/api/v1/artefacts');
  });
});
