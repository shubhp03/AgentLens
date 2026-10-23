// Types come from contracts/openapi.yaml via `npm run gen:api` (openapi-typescript). Do not hand-write models.
import type { components } from './api-types';

type S = components['schemas'];

export type Run = S['Run'];
export type RunDetail = S['RunDetail'];
export type RunPage = S['RunPage'];
export type RunCreate = S['RunCreate'];
export type Span = S['Span'];
export type Answer = S['Answer'];
export type AgentConfig = S['AgentConfig'];
export type EvalRun = S['EvalRun'];
export type EvalRunDetail = S['EvalRunDetail'];
export type EvalResult = S['EvalResult'];
export type EvalMetrics = S['EvalMetrics'];
export type Comparison = S['Comparison'];
export type RunStatus = S['RunStatus'];

export interface Overview {
  runs_today: number;
  cost_today_usd: number;
  daily_cap_usd: number;
  latest_baseline_pass_rate?: number | null;
}

export interface RunQuery {
  status?: RunStatus;
  source?: 'interactive' | 'eval';
  eval_run_id?: string;
  limit?: number;
  cursor?: string | null;
}

export interface ResultQuery {
  passed?: boolean;
  failure_category?: string;
  category?: string;
}

/** An agent event as streamed over SSE (contracts/agent_events.schema.json). Only the fields we read. */
export type AgentEvent =
  | { kind: 'run.started'; run_id: string }
  | { kind: 'span.started'; run_id: string; span_id: string }
  | { kind: 'span.finished'; run_id: string; span_id: string; seq: number } & Record<string, unknown>
  | { kind: 'run.finished'; run_id: string; status: string; termination_reason: string };
