import { HttpClient, HttpParams } from '@angular/common/http';
import { inject, Injectable } from '@angular/core';
import { map, Observable, of, throwError } from 'rxjs';
import {
  AgentConfig,
  Comparison,
  EvalResult,
  EvalRun,
  EvalRunDetail,
  Overview,
  ResultQuery,
  Run,
  RunCreate,
  RunDetail,
  RunPage,
  RunQuery,
} from './models';
import { RuntimeConfigService } from './runtime-config';

/** What the UI needs from a backend. Two implementations: the real gateway and recorded demo data. */
export abstract class AgentLensApi {
  abstract readonly demo: boolean;
  abstract overview(): Observable<Overview>;
  abstract listRuns(q: RunQuery): Observable<RunPage>;
  abstract getRun(id: string): Observable<RunDetail>;
  abstract createRun(body: RunCreate): Observable<Run>;
  abstract cancelRun(id: string): Observable<void>;
  abstract listConfigs(): Observable<AgentConfig[]>;
  abstract listEvalRuns(): Observable<EvalRun[]>;
  abstract getEvalRun(id: string): Observable<EvalRunDetail>;
  abstract listEvalResults(id: string, q?: ResultQuery): Observable<EvalResult[]>;
  abstract compare(base: string, candidate: string): Observable<Comparison>;
}

function params(q: object): HttpParams {
  let p = new HttpParams();
  for (const [k, v] of Object.entries(q)) if (v !== undefined && v !== null && v !== '') p = p.set(k, String(v));
  return p;
}

@Injectable()
export class HttpAgentLensApi extends AgentLensApi {
  readonly demo = false;
  private readonly http = inject(HttpClient);
  private readonly cfg = inject(RuntimeConfigService);
  private url(path: string): string {
    return `${this.cfg.value.apiBaseUrl}/api/v1${path}`;
  }

  overview() {
    return this.http.get<Overview>(this.url('/stats/overview'));
  }
  listRuns(q: RunQuery) {
    return this.http.get<RunPage>(this.url('/runs'), { params: params(q) });
  }
  getRun(id: string) {
    return this.http.get<RunDetail>(this.url(`/runs/${id}`), { params: { include: 'spans' } });
  }
  createRun(body: RunCreate) {
    return this.http.post<Run>(this.url('/runs'), body);
  }
  cancelRun(id: string) {
    return this.http.delete<void>(this.url(`/runs/${id}`));
  }
  listConfigs() {
    return this.http.get<AgentConfig[]>(this.url('/configs'));
  }
  listEvalRuns() {
    return this.http.get<EvalRun[]>(this.url('/eval-runs'));
  }
  getEvalRun(id: string) {
    return this.http.get<EvalRunDetail>(this.url(`/eval-runs/${id}`));
  }
  listEvalResults(id: string, q: ResultQuery = {}) {
    return this.http.get<EvalResult[]>(this.url(`/eval-runs/${id}/results`), { params: params(q) });
  }
  compare(base: string, candidate: string) {
    return this.http.get<Comparison>(this.url('/eval-runs/compare'), { params: { base, candidate } });
  }
}

/**
 * Serves static JSON recorded under `dataPath`: /demo (scripts/gen_demo_data.py, SYNTHETIC data about fictional companies)
 * or /replay (scripts/replay.py, real runs captured from a live stack). No gateway, no LLM, no spend. Writes are refused.
 */
@Injectable()
export class DemoAgentLensApi extends AgentLensApi {
  readonly demo = true;
  private readonly http = inject(HttpClient);
  private readonly dataPath = inject(RuntimeConfigService).value.dataPath;
  private readonly get = <T>(path: string) => this.http.get<T>(`${this.dataPath}/${path}`);

  overview() {
    return this.get<Overview>('overview.json');
  }
  listRuns(q: RunQuery) {
    return this.get<Run[]>('runs.json').pipe(
      map((all) => all.filter((r) => (!q.status || r.status === q.status) && (!q.eval_run_id || r.eval_run_id === q.eval_run_id))),
      map((items) => ({ items: items.slice(0, q.limit ?? 50), next_cursor: null })),
    );
  }
  getRun(id: string) {
    return this.get<RunDetail>(`runs/${id}.json`);
  }
  createRun(): Observable<Run> {
    return throwError(() => new Error('Demo mode is read-only: running live needs a gateway and an API key.'));
  }
  cancelRun(): Observable<void> {
    return of(undefined);
  }
  listConfigs() {
    return this.get<AgentConfig[]>('configs.json');
  }
  listEvalRuns() {
    return this.get<EvalRun[]>('eval-runs.json');
  }
  getEvalRun(id: string) {
    return this.get<EvalRunDetail>(`eval-runs/${id}.json`);
  }
  listEvalResults(id: string, q: ResultQuery = {}) {
    return this.get<EvalResult[]>(`eval-results/${id}.json`).pipe(
      map((rs) =>
        rs.filter(
          (r) =>
            (q.passed === undefined || r.passed === q.passed) &&
            (!q.failure_category || r.failure_category === q.failure_category),
        ),
      ),
    );
  }
  compare(base: string, candidate: string) {
    return this.get<Comparison>(`compare/${base}__${candidate}.json`);
  }
}
