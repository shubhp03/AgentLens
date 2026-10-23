import { inject, Injectable } from '@angular/core';
import { AuthService } from './auth.service';
import { readSse } from './sse';
import { RuntimeConfigService } from './runtime-config';
import { Span } from './models';

export interface LiveHandle {
  stop(): void;
}

/**
 * Subscribes to GET /runs/{id}/events. The gateway replays persisted spans first, then streams live
 * ones, so a late subscriber (or a reconnect) sees the whole trace. Spans are keyed by id, so replays
 * are harmless.
 */
@Injectable({ providedIn: 'root' })
export class RunStreamService {
  private readonly auth = inject(AuthService);
  private readonly cfg = inject(RuntimeConfigService);

  watch(
    runId: string,
    handlers: { onSpan: (s: Span) => void; onFinished: () => void; onError: (msg: string) => void },
  ): LiveHandle {
    const ctrl = new AbortController();
    const key = this.auth.apiKey();
    void readSse({
      url: `${this.cfg.value.apiBaseUrl}/api/v1/runs/${runId}/events`,
      headers: key ? { 'X-API-Key': key } : {},
      signal: ctrl.signal,
      onData: (data) => {
        try {
          const ev = JSON.parse(data) as { kind?: string } & Record<string, unknown>;
          if (ev.kind === 'span.finished') handlers.onSpan(toSpan(ev));
          else if (ev.kind === 'run.finished') handlers.onFinished();
        } catch {
          /* ignore malformed event; the refetch on finish repairs state */
        }
      },
    })
      .then(() => {
        if (!ctrl.signal.aborted) handlers.onFinished();
      })
      .catch((e: unknown) => handlers.onError(e instanceof Error ? e.message : String(e)));
    return { stop: () => ctrl.abort() };
  }
}

/** Map an agent `span.finished` wire event onto the REST Span shape the waterfall renders. */
export function toSpan(ev: Record<string, unknown>): Span {
  const usage = (ev['usage'] ?? {}) as { input_tokens?: number; output_tokens?: number };
  const started = Date.parse(String(ev['started_at']));
  const ended = ev['ended_at'] ? Date.parse(String(ev['ended_at'])) : null;
  return {
    id: String(ev['span_id']),
    run_id: String(ev['run_id']),
    parent_span_id: (ev['parent_span_id'] as string | null) ?? null,
    seq: Number(ev['seq']),
    type: ev['type'] as Span['type'],
    name: String(ev['name']),
    status: (ev['status'] as Span['status']) ?? 'ok',
    model: (ev['model'] as string | null) ?? null,
    started_at: String(ev['started_at']),
    ended_at: ev['ended_at'] ? String(ev['ended_at']) : null,
    latency_ms: ended === null ? null : ended - started,
    input: (ev['input'] as Record<string, unknown>) ?? {},
    output: (ev['output'] as Record<string, unknown>) ?? {},
    error: (ev['error'] as string | null) ?? null,
    input_tokens: usage.input_tokens ?? null,
    output_tokens: usage.output_tokens ?? null,
    cost_usd: (ev['cost_usd'] as number | null) ?? null,
    attributes: (ev['attributes'] as Record<string, unknown>) ?? {},
  };
}
