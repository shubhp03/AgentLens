import { Span } from './models';
import { fmtMs } from './format';

export type SpanKind = 'agent' | 'llm' | 'tool';

export interface WaterfallRow {
  span: Span;
  depth: number;
  kind: SpanKind;
  /** Left offset and width as percentages of the whole run (0..100). */
  leftPct: number;
  widthPct: number;
  offsetMs: number;
  durationMs: number;
  error: boolean;
}

export interface Waterfall {
  rows: WaterfallRow[];
  totalMs: number;
  ticks: { pct: number; label: string }[];
}

/** A bar must stay visible even for a 3 ms span in a 40 s run. */
const MIN_WIDTH_PCT = 0.4;

const KIND: Record<Span['type'], SpanKind> = { agent: 'agent', llm_call: 'llm', tool_call: 'tool' };

function ms(iso: string): number {
  return Date.parse(iso);
}

/**
 * Turn a flat span list into drawable rows.
 *
 * - Time axis: from the earliest start to the latest end. This also works for a LIVE run, where the root
 *   span is emitted last and is therefore not in the list yet.
 * - Depth: root = 0, its children = 1. A span whose parent is not (yet) known is drawn at depth 1.
 * - Order: root first, then by start time, then by seq. Parallel tool calls therefore stack and overlap.
 */
export function layoutWaterfall(spans: readonly Span[]): Waterfall {
  if (spans.length === 0) return { rows: [], totalMs: 0, ticks: [] };

  const ids = new Set(spans.map((s) => s.id));
  const t0 = Math.min(...spans.map((s) => ms(s.started_at)));
  const tEnd = Math.max(...spans.map((s) => (s.ended_at ? ms(s.ended_at) : ms(s.started_at))));
  const totalMs = Math.max(tEnd - t0, 1);

  const depthOf = (s: Span): number => {
    if (!s.parent_span_id) return 0;
    const parent = spans.find((p) => p.id === s.parent_span_id);
    return parent && ids.has(parent.id) ? depthOf(parent) + 1 : 1;
  };

  const rows = [...spans]
    .sort((a, b) => {
      const ra = a.parent_span_id ? 1 : 0;
      const rb = b.parent_span_id ? 1 : 0;
      return ra - rb || ms(a.started_at) - ms(b.started_at) || a.seq - b.seq;
    })
    .map((span): WaterfallRow => {
      const start = ms(span.started_at);
      const end = span.ended_at ? ms(span.ended_at) : start;
      // Keep the bar visible: enforce a minimum width, and slide it left if that would overflow the track.
      const widthPct = Math.max(((end - start) / totalMs) * 100, MIN_WIDTH_PCT);
      const leftPct = Math.min(((start - t0) / totalMs) * 100, 100 - widthPct);
      return {
        span,
        depth: depthOf(span),
        kind: KIND[span.type],
        leftPct,
        widthPct,
        offsetMs: start - t0,
        durationMs: end - start,
        error: span.status === 'error',
      };
    });

  const ticks = [0, 25, 50, 75, 100].map((pct) => ({ pct, label: fmtMs((totalMs * pct) / 100) }));
  return { rows, totalMs, ticks };
}

/** Human label for the left column. */
export function spanLabel(s: Span): string {
  if (s.type === 'agent') return 'agent run';
  if (s.type === 'llm_call') return `LLM · ${s.model ?? s.name}`;
  return `tool · ${s.name}`;
}
