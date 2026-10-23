import { describe, expect, it } from 'vitest';
import { toSpan } from './run-stream.service';

describe('toSpan', () => {
  it('maps an agent span.finished event onto the REST span shape', () => {
    const s = toSpan({
      kind: 'span.finished',
      run_id: 'r',
      span_id: 's',
      parent_span_id: 'root',
      seq: 3,
      type: 'llm_call',
      name: 'm',
      model: 'm',
      started_at: '2026-01-01T00:00:00.000Z',
      ended_at: '2026-01-01T00:00:01.500Z',
      usage: { input_tokens: 10, output_tokens: 5 },
      cost_usd: 0.001,
    });
    expect(s).toMatchObject({ id: 's', run_id: 'r', parent_span_id: 'root', seq: 3, latency_ms: 1500, input_tokens: 10, output_tokens: 5, cost_usd: 0.001 });
  });
});
