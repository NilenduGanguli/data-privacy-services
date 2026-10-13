import { describe, expect, it } from 'vitest';
import { axisDay, compact, count, delta, percent, plural, relative, shortHash, utc, utcDate, utcDay, utcTime } from './format';

describe('format', () => {
  it('compacts to three significant digits with a unit', () => {
    expect(compact(2_706_118)).toBe('2.71M');
    expect(compact(24_611)).toBe('24.6K');
    expect(compact(45_791_546)).toBe('45.8M');
    expect(compact(1_000)).toBe('1K');
    expect(compact(1_200_000_000)).toBe('1.2B');
    expect(compact(812)).toBe('812');
    expect(compact(null)).toBe('—');
    expect(count(1234567)).toBe('1,234,567');
  });

  it('says how long ago in words, by UTC calendar day past today', () => {
    const now = new Date('2026-10-13T12:00:00Z');
    expect(relative('2026-10-13T11:59:58Z', now)).toBe('just now');
    expect(relative('2026-10-13T11:59:48Z', now)).toBe('12 s ago');
    expect(relative('2026-10-13T11:56:00Z', now)).toBe('4 min ago');
    expect(relative('2026-10-13T10:00:00Z', now)).toBe('2 h ago');
    expect(relative('2026-10-12T23:00:00Z', now)).toBe('yesterday');
    expect(relative('2026-10-10T10:00:00Z', now)).toBe('3 days ago');
    expect(relative(null, now)).toBe('never');
  });

  it('formats UTC times, dates and hashes', () => {
    expect(utc('2026-10-11T09:49:12Z')).toBe('11 Oct 09:49');
    expect(utcTime('2026-10-11T09:49:12Z')).toBe('09:49:12');
    expect(utcDate('2026-10-01T23:59:00Z')).toBe('01 Oct 2026');
    expect(utcDay('2026-10-11')).toBe('11 Oct');
    expect(axisDay('2026-10-11')).toBe('Oct 11');
    expect(shortHash('18d2493121bae95d38598115b80269bd6c86088a59562dd9b7d0b34b1c5e3a66')).toBe('sha256:18d24931…3a66');
    expect(shortHash('sha256:18d2493121bae95d38598115b80269bd6c86088a59562dd9b7d0b34b1c5e3a66')).toBe('sha256:18d24931…3a66');
    expect(utc('not a date')).toBe('—');
  });

  it('computes percentages and changes', () => {
    expect(percent(0.94)).toBe('94%');
    expect(percent(81, false)).toBe('81%');
    expect(delta(128, 100)).toEqual({ text: '▲ 28%', up: true });
    expect(delta(96, 100)).toEqual({ text: '▼ 4%', up: false });
    expect(delta(5, 0)).toBeNull();
    expect(plural(1, 'key')).toBe('1 key');
    expect(plural(1200, 'key')).toBe('1,200 keys');
  });
});
