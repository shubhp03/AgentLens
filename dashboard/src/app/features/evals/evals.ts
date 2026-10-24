import { Component, computed, inject, signal } from '@angular/core';
import { Router, RouterLink } from '@angular/router';
import { ChartConfiguration } from 'chart.js';
import { AgentLensApi } from '../../core/api.service';
import { RuntimeConfigService } from '../../core/runtime-config';
import { fmtDateTime, fmtMs, fmtPct, fmtPctCi, fmtUsd, shortId } from '../../core/format';
import { loadFrom } from '../../core/loadable';
import { EvalRun } from '../../core/models';
import { ChartView, themeColors } from '../../shared/chart';
import { StateView } from '../../shared/state-view';
import { StatusChip } from '../../shared/status-chip';

type SortKey = 'started_at' | 'pass_rate' | 'cost_per_success' | 'p95';

const sortValue = (r: EvalRun, k: SortKey): number => {
  switch (k) {
    case 'pass_rate': return r.metrics?.pass_rate ?? -1;
    case 'cost_per_success': return r.metrics?.cost_per_success_usd ?? Number.POSITIVE_INFINITY;
    case 'p95': return r.metrics?.p95_latency_ms ?? Number.POSITIVE_INFINITY;
    default: return r.started_at ? Date.parse(r.started_at) : 0;
  }
};

@Component({
  selector: 'app-evals',
  imports: [RouterLink, StateView, StatusChip, ChartView],
  template: `
    <div class="page">
      <div class="page-head">
        <div><h1>Eval runs</h1><p>Case-level pass rate (majority of repeats) with 95% Wilson intervals. Select two runs to compare them.</p></div>
        <span class="spacer"></span>
        <button class="btn primary" type="button" [disabled]="selected().length !== 2" (click)="compare()">Compare selected ({{ selected().length }}/2)</button>
      </div>

      <app-state [status]="data.status()" [error]="data.error()" [empty]="(data.data()?.length ?? 0) === 0" [emptyText]="emptyText" (retry)="data.reload()">
        <div class="card">
          <div class="table-wrap">
            <table>
              <thead>
                <tr>
                  <th><span class="sr-only">Select</span></th>
                  <th>Label</th><th>Status</th>
                  <th class="sortable right" (click)="sortBy('pass_rate')" [attr.aria-sort]="aria('pass_rate')">Pass rate (95% CI)</th>
                  <th class="right">Cases</th>
                  <th class="sortable right" (click)="sortBy('cost_per_success')" [attr.aria-sort]="aria('cost_per_success')">Cost / success</th>
                  <th class="sortable right" (click)="sortBy('p95')" [attr.aria-sort]="aria('p95')">p95 latency</th>
                  <th>Git</th>
                  <th class="sortable" (click)="sortBy('started_at')" [attr.aria-sort]="aria('started_at')">Started</th>
                </tr>
              </thead>
              <tbody>
                @for (r of sorted(); track r.id) {
                  <tr>
                    <td><input type="checkbox" [checked]="selected().includes(r.id)" (change)="toggle(r.id)" [attr.aria-label]="'Select ' + r.label" /></td>
                    <td>
                      <a [routerLink]="['/evals', r.id]">{{ r.label }}</a>
                      @if (r.is_baseline) { <app-chip label="baseline" kind="info" /> }
                      @if (r.gate_passed === false) { <app-chip label="gate failed" kind="bad" /> }
                    </td>
                    <td><app-chip [label]="r.status" /></td>
                    <td class="right nowrap">{{ pctCi(r.metrics?.pass_rate, r.metrics?.pass_rate_ci) }}</td>
                    <td class="right">{{ r.metrics?.n_cases ?? '–' }}</td>
                    <td class="right">{{ usd(r.metrics?.cost_per_success_usd) }}</td>
                    <td class="right">{{ ms(r.metrics?.p95_latency_ms) }}</td>
                    <td class="mono">{{ r.git_sha ? short(r.git_sha) : '–' }}</td>
                    <td class="nowrap">{{ dt(r.started_at) }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
          @if (selected().length > 0 && selected().length !== 2) { <p class="muted">Select exactly two runs: the first is the base, the second the candidate.</p> }
        </div>

        @if (scatter(); as cfg) {
          <div class="card">
            <h2>Cost vs quality</h2>
            <p class="muted">Each point is an eval run. Up and to the left is better. Intervals are in the table; do not rank points that overlap.</p>
            <app-chart [config]="cfg" label="Scatter plot of cost per successful task against pass rate" />
          </div>
        }
      </app-state>
    </div>
  `,
})
export class Evals {
  private readonly api = inject(AgentLensApi);
  protected readonly emptyText = inject(RuntimeConfigService).value.dataset === 'recorded'
    ? 'No scored eval runs yet. Numbers will be published here once the golden set has been verified by hand against the filings; unverified results are not shown.'
    : 'No eval runs yet. Run agentlens-eval run … and publish the results.';
  private readonly router = inject(Router);
  protected readonly data = loadFrom(() => 0, () => this.api.listEvalRuns());

  protected readonly key = signal<SortKey>('started_at');
  protected readonly dir = signal<1 | -1>(-1);
  protected readonly selected = signal<string[]>([]);

  protected readonly sorted = computed(() => {
    const k = this.key();
    const d = this.dir();
    return [...(this.data.data() ?? [])].sort((a, b) => (sortValue(a, k) - sortValue(b, k)) * d);
  });

  protected readonly scatter = computed<ChartConfiguration | null>(() => {
    const pts = (this.data.data() ?? []).filter((r) => r.metrics?.cost_per_success_usd != null && r.metrics?.pass_rate != null);
    if (pts.length === 0) return null;
    const c = themeColors();
    return {
      type: 'scatter',
      data: {
        datasets: [{
          label: 'Eval runs',
          data: pts.map((r) => ({ x: r.metrics!.cost_per_success_usd!, y: r.metrics!.pass_rate! * 100 })),
          backgroundColor: c.accent,
          pointRadius: 6,
        }],
      },
      options: {
        maintainAspectRatio: false,
        plugins: {
          legend: { display: false },
          tooltip: { callbacks: { label: (ctx) => `${pts[ctx.dataIndex].label}: ${ctx.parsed.y?.toFixed(1)}% at $${ctx.parsed.x?.toFixed(4)}` } },
        },
        scales: {
          x: { title: { display: true, text: 'Cost per successful task (USD)', color: c.muted }, ticks: { color: c.muted }, grid: { color: c.grid } },
          y: { min: 0, max: 100, title: { display: true, text: 'Pass rate (%)', color: c.muted }, ticks: { color: c.muted }, grid: { color: c.grid } },
        },
      },
    };
  });

  protected readonly usd = fmtUsd;
  protected readonly ms = fmtMs;
  protected readonly pct = fmtPct;
  protected readonly pctCi = fmtPctCi;
  protected readonly dt = fmtDateTime;
  protected readonly short = shortId;

  protected sortBy(k: SortKey): void {
    if (this.key() === k) this.dir.update((d) => (d === 1 ? -1 : 1));
    else {
      this.key.set(k);
      this.dir.set(k === 'pass_rate' || k === 'started_at' ? -1 : 1);
    }
  }
  protected aria(k: SortKey): 'ascending' | 'descending' | 'none' {
    return this.key() !== k ? 'none' : this.dir() === 1 ? 'ascending' : 'descending';
  }

  protected toggle(id: string): void {
    this.selected.update((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : [...cur, id].slice(-2)));
  }
  protected compare(): void {
    const [base, candidate] = this.selected();
    void this.router.navigate(['/evals/compare'], { queryParams: { base, candidate } });
  }
}
