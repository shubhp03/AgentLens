import { Component, computed, inject, input, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { ChartConfiguration } from 'chart.js';
import { forkJoin, map } from 'rxjs';
import { AgentLensApi } from '../../core/api.service';
import { fmtDateTime, fmtMs, fmtPct, fmtPctCi, fmtUsd, shortId } from '../../core/format';
import { loadFrom } from '../../core/loadable';
import { ChartView, themeColors } from '../../shared/chart';
import { StateView } from '../../shared/state-view';
import { StatusChip } from '../../shared/status-chip';

@Component({
  selector: 'app-eval-detail',
  imports: [RouterLink, StateView, StatusChip, ChartView],
  template: `
    <div class="page">
      <p><a routerLink="/evals">← Eval runs</a></p>
      <app-state [status]="data.status()" [error]="data.error()" (retry)="data.reload()">
        @let d = data.data();
        @if (d) {
          @let e = d.run;
          <div class="page-head">
            <div>
              <h1>{{ e.label }} @if (e.is_baseline) { <app-chip label="baseline" kind="info" /> }</h1>
              <p><app-chip [label]="e.status" /> <span class="muted">· started {{ dt(e.started_at) }} · {{ e.repeats }} repeat(s) @if (e.git_sha) { · git {{ short(e.git_sha) }} }</span></p>
            </div>
          </div>

          <div class="grid cols-4">
            <div class="card stat"><div class="label">Pass rate</div><div class="value">{{ pct(e.metrics?.pass_rate) }}</div><div class="sub">95% CI {{ ci(e.metrics?.pass_rate_ci) }} · {{ e.metrics?.n_cases_passed ?? '–' }}/{{ e.metrics?.n_cases ?? '–' }} cases</div><div class="sub">majority of {{ e.repeats }} repeats · {{ e.metrics?.n_results ?? '–' }} runs · run-level {{ pct(e.metrics?.run_pass_rate) }}</div></div>
            <div class="card stat"><div class="label">Cost per success</div><div class="value">{{ usd(e.metrics?.cost_per_success_usd) }}</div><div class="sub">total {{ usd(e.metrics?.total_cost_usd) }}</div></div>
            <div class="card stat"><div class="label">Latency p50 / p95</div><div class="value">{{ ms(e.metrics?.p50_latency_ms) }} / {{ ms(e.metrics?.p95_latency_ms) }}</div></div>
          </div>

          <div class="grid cols-2" style="margin-top:16px">
            <div class="card">
              <h2>Failure categories</h2>
              @if (failureChart(); as cfg) { <app-chart [config]="cfg" label="Bar chart of failed results by failure category" /> }
              @else { <p class="muted">No failures recorded.</p> }
            </div>
            <div class="card">
              <h2>Pass rate by case category</h2>
              @if (categories().length === 0) { <p class="muted">No category data.</p> }
              @for (c of categories(); track c.name) {
                <div class="catrow">
                  <span>{{ c.name }}</span><span class="muted">{{ pct(c.rate) }}</span>
                  <div class="bar" role="img" [attr.aria-label]="c.name + ' ' + pct(c.rate)"><span [style.width.%]="c.rate * 100"></span></div>
                </div>
              }
            </div>
          </div>

          <div class="card" style="margin-top:16px">
            <div class="row" style="justify-content:space-between;margin-bottom:8px">
              <h2 style="margin:0">Results</h2>
              <label style="flex-direction:row;align-items:center;gap:8px">
                <input type="checkbox" [checked]="failedOnly()" (change)="failedOnly.set(!failedOnly())" /> Failed only
              </label>
            </div>
            <app-state [status]="'ready'" [empty]="shown().length === 0" [emptyText]="failedOnly() ? 'No failed results. 🎉' : 'No results.'">
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Case</th><th>Repeat</th><th>Result</th><th>Failure category</th><th class="right">Cost</th><th class="right">Latency</th><th>Trace</th></tr></thead>
                  <tbody>
                    @for (r of shown(); track r.id) {
                      <tr>
                        <td class="mono">{{ r.case_key }}</td>
                        <td>{{ r.repeat_idx }}</td>
                        <td><app-chip [label]="r.passed ? 'passed' : 'failed'" /></td>
                        <td>{{ r.failure_category ?? '–' }}</td>
                        <td class="right">{{ usd(r.cost_usd) }}</td>
                        <td class="right">{{ ms(r.latency_ms) }}</td>
                        <td>@if (r.run_id) { <a [routerLink]="['/runs', r.run_id]">trace</a> }</td>
                      </tr>
                    }
                  </tbody>
                </table>
              </div>
            </app-state>
          </div>
        }
      </app-state>
    </div>
  `,
  styles: `.catrow { display: grid; grid-template-columns: 1fr auto; gap: 4px 12px; margin-bottom: 10px; } .catrow .bar { grid-column: 1 / -1; }`,
})
export class EvalDetail {
  readonly id = input.required<string>();
  private readonly api = inject(AgentLensApi);

  protected readonly data = loadFrom(
    () => this.id(),
    (id) => forkJoin({ run: this.api.getEvalRun(id), results: this.api.listEvalResults(id) }).pipe(map((x) => x)),
  );
  protected readonly failedOnly = signal(true);

  protected readonly shown = computed(() => {
    const rs = this.data.data()?.results ?? [];
    return this.failedOnly() ? rs.filter((r) => !r.passed) : rs;
  });

  protected readonly categories = computed(() =>
    Object.entries(this.data.data()?.run.category_pass_rates ?? {})
      .map(([name, rate]) => ({ name, rate }))
      .sort((a, b) => a.rate - b.rate),
  );

  protected readonly failureChart = computed<ChartConfiguration | null>(() => {
    const entries = Object.entries(this.data.data()?.run.failure_breakdown ?? {}).sort((a, b) => b[1] - a[1]);
    if (entries.length === 0) return null;
    const c = themeColors();
    return {
      type: 'bar',
      data: { labels: entries.map(([k]) => k), datasets: [{ label: 'Failed results', data: entries.map(([, v]) => v), backgroundColor: c.bad }] },
      options: {
        indexAxis: 'y',
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { beginAtZero: true, ticks: { precision: 0, color: c.muted }, grid: { color: c.grid } },
          y: { ticks: { color: c.text }, grid: { display: false } },
        },
      },
    };
  });

  protected readonly usd = fmtUsd;
  protected readonly ms = fmtMs;
  protected readonly pct = fmtPct;
  protected readonly dt = fmtDateTime;
  protected readonly short = shortId;
  protected ci(ci: number[] | null | undefined): string {
    return ci && ci.length === 2 ? `${fmtPct(ci[0], 0)}–${fmtPct(ci[1], 0)}` : '–';
  }
  protected readonly pctCi = fmtPctCi;
}
