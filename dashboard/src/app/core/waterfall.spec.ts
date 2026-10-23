import { describe, expect, it } from 'vitest';
import { layoutWaterfall, spanLabel } from './waterfall';
import { Span } from './models';

const T0 = Date.parse('2026-01-01T00:00:00.000Z');
const iso = (offsetMs: number) => new Date(T0 + offsetMs).toISOString();

function span(id: string, seq: number, type: Span['type'], start: number, end: number | null, extra: Partial<Span> = {}): Span {
  return {
    id,
    run_id: 'r1',
    parent_span_id: id === 'root' ? null : 'root',
    seq,
    type,
    name: type === 'tool_call' ? 'get_financial_facts' : 'm',
    status: 'ok',
    started_at: iso(start),
    ended_at: end === null ? null : iso(end),
    ...extra,
  } as Span;
}

describe('layoutWaterfall', () => {
  const spans = [
    span('l1', 1, 'llm_call', 0, 1000),
    span('t1', 2, 'tool_call', 1000, 2000),
    span('t2', 3, 'tool_call', 1000, 1500), // runs in parallel with t1
    span('l2', 4, 'llm_call', 2000, 4000),
    span('root', 5, 'agent', 0, 4000), // root has the highest seq: it ends last
  ];

  it('puts the root first and orders the rest by start time then seq', () => {
    const { rows } = layoutWaterfall(spans);
    expect(rows.map((r) => r.span.id)).toEqual(['root', 'l1', 't1', 't2', 'l2']);
    expect(rows.map((r) => r.depth)).toEqual([0, 1, 1, 1, 1]);
  });

  it('computes percent offsets against the whole run', () => {
    const { rows, totalMs } = layoutWaterfall(spans);
    expect(totalMs).toBe(4000);
    const t1 = rows.find((r) => r.span.id === 't1')!;
    expect(t1.leftPct).toBeCloseTo(25);
    expect(t1.widthPct).toBeCloseTo(25);
    const t2 = rows.find((r) => r.span.id === 't2')!;
    expect(t2.leftPct).toBeCloseTo(25); // overlaps t1 in time
    expect(t2.widthPct).toBeCloseTo(12.5);
  });

  it('keeps tiny spans visible and inside the track', () => {
    const w = layoutWaterfall([span('root', 2, 'agent', 0, 40_000), span('x', 1, 'tool_call', 39_999, 40_000)]);
    const x = w.rows.find((r) => r.span.id === 'x')!;
    expect(x.widthPct).toBeGreaterThanOrEqual(0.4 - 1e-9);
    expect(x.leftPct + x.widthPct).toBeLessThanOrEqual(100 + 1e-9);
  });

  it('works for a live run whose root span has not arrived yet', () => {
    const w = layoutWaterfall(spans.filter((s) => s.id !== 'root'));
    expect(w.totalMs).toBe(4000);
    expect(w.rows.every((r) => r.depth === 1)).toBe(true);
  });

  it('flags errors and treats unfinished spans as zero-length', () => {
    const w = layoutWaterfall([span('a', 1, 'tool_call', 0, null, { status: 'error' }), span('b', 2, 'llm_call', 0, 100)]);
    const a = w.rows.find((r) => r.span.id === 'a')!;
    expect(a.error).toBe(true);
    expect(a.durationMs).toBe(0);
  });

  it('handles an empty trace', () => {
    expect(layoutWaterfall([])).toEqual({ rows: [], totalMs: 0, ticks: [] });
  });

  it('labels spans by type', () => {
    expect(spanLabel(span('root', 1, 'agent', 0, 1))).toBe('agent run');
    expect(spanLabel(span('t', 1, 'tool_call', 0, 1))).toBe('tool · get_financial_facts');
  });
});
