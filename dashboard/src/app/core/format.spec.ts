import { describe, expect, it } from 'vitest';
import { fmtMs, fmtP, fmtPct, fmtPctCi, fmtUsd } from './format';

describe('format', () => {
  it('formats durations', () => {
    expect(fmtMs(0)).toBe('0 ms');
    expect(fmtMs(999)).toBe('999 ms');
    expect(fmtMs(1500)).toBe('1.50 s');
    expect(fmtMs(12_345)).toBe('12.3 s');
    expect(fmtMs(125_000)).toBe('2m 05s');
    expect(fmtMs(null)).toBe('–');
  });

  it('formats money with enough precision for sub-cent runs', () => {
    expect(fmtUsd(0)).toBe('$0');
    expect(fmtUsd(0.0042)).toBe('$0.0042');
    expect(fmtUsd(0.5)).toBe('$0.500');
    expect(fmtUsd(12.345)).toBe('$12.35');
  });

  it('formats a rate with its interval', () => {
    expect(fmtPct(0.625)).toBe('62.5%');
    expect(fmtPctCi(0.625, [0.481, 0.749])).toBe('62.5% (48–75)');
    expect(fmtPctCi(0.5, null)).toBe('50.0%');
    expect(fmtPctCi(null, null)).toBe('–');
  });

  it('never prints a p-value of 0.000', () => {
    expect(fmtP(0.00001)).toBe('< 0.001');
    expect(fmtP(0.0421)).toBe('0.042');
  });
});
