// Must-stay-zero numbers read from the number (M5 console review N1, R2-1): when the raw-PII
// counter or the exact-row count is above 0 the wording says so and the tile turns rose, instead
// of a fixed reassuring sentence beside a non-zero figure.
import { describe, expect, it } from 'vitest';
import * as fx from '../dev/fixtures';
import { OverviewView } from '../pages/Overview';
import { html, text } from '../test/render';
import { FidelityPanel } from './FidelityPanel';

const NOW = new Date('2026-10-13T10:15:00Z');

describe('Overview: raw PII sent to the LLM gateway', () => {
  it('keeps the zero wording and the emerald tile at 0', () => {
    const out = html(<OverviewView tenant="t" ready={fx.readiness()} summary={fx.summary(NOW)} degraded={null} policyLabel={null} />);
    expect(text(out)).toContain('placeholders only');
    expect(out).toContain('class="card kpi zero"');
    expect(out).not.toContain('kpi alert');
  });
  it('turns rose and says what happened when the count is above 0', () => {
    const s = fx.summary(NOW);
    const summary = { ...s, tiles: { ...s.tiles, raw_pii_sent_to_llm: 2 } };
    const out = html(<OverviewView tenant="t" ready={fx.readiness()} summary={summary} degraded={null} policyLabel={null} />);
    const t = text(out);
    expect(out).toContain('class="card kpi alert"');
    expect(t).toContain('Raw PII sent to LLM 2');
    expect(t).not.toContain('placeholders only');
    expect(t).toContain('raw values reached the gateway');
  });
  it('does the same in degraded mode (metrics since process start)', () => {
    const metrics = fx.METRICS_TEXT.replace('dps_llm_raw_pii_sent_total 0', 'dps_llm_raw_pii_sent_total 3');
    const out = html(<OverviewView tenant="" ready={fx.readiness()} summary={null} degraded={{ metrics, stats: null, reason: 'db' }} policyLabel={null} />);
    const t = text(out);
    expect(out).toContain('class="card kpi alert"');
    expect(t).not.toContain('placeholders only · must stay 0'); // the readiness table may still say it of the gateway
    expect(t).toContain('Raw PII sent to LLM 3 raw values reached the gateway');
  });
});

describe('FidelityPanel: exact-row matches', () => {
  const p = fx.evidencePacks(NOW).ev_01K7ZQS7B2.pack;
  it('says no row equals a source row only when the count is 0', () => {
    const t = text(html(<FidelityPanel fidelity={p.fidelity ?? null} privacy={p.privacy ?? null} utility={null} tabular={p.tabular ?? null} rowsWritten={10} />));
    expect(t).toContain('No synthetic row equals a source row on all modelled columns');
  });
  it('names the matches and turns rose when the count is above 0', () => {
    const privacy = { ...p.privacy!, exact_row_matches: 3 };
    const out = html(<FidelityPanel fidelity={p.fidelity ?? null} privacy={privacy} utility={null} tabular={p.tabular ?? null} rowsWritten={10} />);
    const t = text(out);
    expect(t).not.toContain('No synthetic row equals a source row');
    expect(t).toContain('3 synthetic rows equal a source row on all modelled columns');
    expect(out).toContain('<span class="chip rose">3 found</span>');
  });
});
