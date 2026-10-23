import { describe, expect, it } from 'vitest';
import { prettyPayload } from './pretty';

describe('prettyPayload', () => {
  it('unwraps tool output whose content is JSON', () => {
    const out = prettyPayload({ content: '[{"value": 1}]' });
    expect(out).toBe('[\n  {\n    "value": 1\n  }\n]');
  });

  it('shows non-JSON tool output as plain text', () => {
    expect(prettyPayload({ content: '14515.0' })).toBe('14515.0');
  });

  it('falls back to the raw text when the JSON is truncated', () => {
    const cut = '[{"value": 1}, {"val';
    expect(prettyPayload({ content: cut })).toBe(cut);
  });

  it('does not unwrap objects that have other keys', () => {
    expect(prettyPayload({ content: '[1]', extra: true })).toContain('"extra"');
  });

  it('returns empty text for empty payloads', () => {
    expect(prettyPayload({})).toBe('');
    expect(prettyPayload(null)).toBe('');
    expect(prettyPayload(undefined)).toBe('');
  });

  it('pretty-prints ordinary objects', () => {
    expect(prettyPayload({ cik: '1' })).toBe('{\n  "cik": "1"\n}');
  });
});
