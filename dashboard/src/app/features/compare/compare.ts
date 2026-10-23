import { Component, computed, inject, input } from '@angular/core';
import { RouterLink } from '@angular/router';
import { forkJoin } from 'rxjs';
import { AgentLensApi } from '../../core/api.service';
import { fmtMs, fmtP, fmtPct, fmtPctCi, fmtUsd } from '../../core/format';
import { loadFrom } from '../../core/loadable';
import { StateView } from '../../shared/state-view';
import { StatusChip } from '../../shared/status-chip';

@Component({
  selector: 'app-compare',
  imports: [RouterLink, StateView, StatusChip],
  template: `
    <div class="page">
      <p><a routerLink="/evals">← Eval runs</a></p>
      <div class="page-head"><div><h1>Compare</h1><p>Paired by case. Significance is an exact McNemar test on the cases where the two runs disagree.</p></div></div>
      @if (!base() || !candidate()) {
        <div class="alert info">Pick two runs on the Eval runs page, then press Compare.</div>
      } @else {
        <app-state [status]="data.status()" [error]="data.error()" (retry)="data.reload()">
          @let d = data.data();
          @if (d) {
            <div class="card" [class.regress]="d.cmp.regression">
              <p class="verdict">{{ verdict() }}</p>
              <dl class="kv">
                <dt>Cases compared</dt><dd>{{ d.cmp.n }}</dd>
                <dt>Delta (candidate − base)</dt><dd>{{ signedPct(d.cmp.delta) }}</dd>
                <dt>Only base passed</dt><dd>{{ d.cmp.base_only }}</dd>
                <dt>Only candidate passed</dt><dd>{{ d.cmp.candidate_only }}</dd>
                <dt>McNemar p-value (exact)</dt><dd>{{ p(d.cmp.p_value) }}</dd>
              </dl>
            </div>

            <div class="card">
              <h2>Metrics</h2>
              <div class="table-wrap">
                <table>
                  <thead><tr><th></th><th>Base: {{ d.base.label }}</th><th>Candidate: {{ d.cand.label }}</th></tr></thead>
                  <tbody>
                    <tr><th scope="row">Status</th><td><app-chip [label]="d.base.status" /></td><td><app-chip [label]="d.cand.status" /></td></tr>
                    <tr><th scope="row">Pass rate (95% CI)</th><td>{{ pctCi(d.cmp.base_pass_rate, d.cmp.base_ci) }}</td><td>{{ pctCi(d.cmp.candidate_pass_rate, d.cmp.candidate_ci) }}</td></tr>
                    <tr><th scope="row">Cost per success</th><td>{{ usd(d.base.metrics?.cost_per_success_usd) }}</td><td>{{ usd(d.cand.metrics?.cost_per_success_usd) }}</td></tr>
                    <tr><th scope="row">Latency p95</th><td>{{ ms(d.base.metrics?.p95_latency_ms) }}</td><td>{{ ms(d.cand.metrics?.p95_latency_ms) }}</td></tr>
                    <tr><th scope="row">Total cost</th><td>{{ usd(d.base.metrics?.total_cost_usd) }}</td><td>{{ usd(d.cand.metrics?.total_cost_usd) }}</td></tr>
                  </tbody>
                </table>
              </div>
            </div>

            <div class="grid cols-2" style="margin-top:16px">
              <div class="card">
                <h2>Regressed ({{ regressed().length }})</h2>
                @if (regressed().length === 0) { <p class="muted">None.</p> }
                <ul>@for (c of regressed(); track c) { <li class="mono">{{ c }}</li> }</ul>
              </div>
              <div class="card">
                <h2>Improved ({{ improved().length }})</h2>
                @if (improved().length === 0) { <p class="muted">None.</p> }
                <ul>@for (c of improved(); track c) { <li class="mono">{{ c }}</li> }</ul>
              </div>
            </div>
          }
        </app-state>
      }
    </div>
  `,
  styles: `.verdict { font-size: 18px; font-weight: 650; margin: 0 0 12px; } .regress { border-color: var(--bad); } ul { margin: 0; padding-left: 20px; }`,
})
export class Compare {
  readonly base = input<string>();
  readonly candidate = input<string>();
  private readonly api = inject(AgentLensApi);

  protected readonly data = loadFrom(
    () => ({ b: this.base(), c: this.candidate() }),
    ({ b, c }) => forkJoin({ cmp: this.api.compare(b!, c!), base: this.api.getEvalRun(b!), cand: this.api.getEvalRun(c!) }),
  );

  protected readonly regressed = computed(() => (this.data.data()?.cmp.flipped_cases ?? []).filter((f) => f.direction === 'regressed').map((f) => f.case_key!));
  protected readonly improved = computed(() => (this.data.data()?.cmp.flipped_cases ?? []).filter((f) => f.direction === 'improved').map((f) => f.case_key!));

  /** Plain-language reading of the test. Deliberately conservative: small n usually means "can't tell". */
  protected readonly verdict = computed(() => {
    const c = this.data.data()?.cmp;
    if (!c) return '';
    const sig = c.p_value < 0.05;
    if (!sig) return `No statistically significant difference (p = ${fmtP(c.p_value)}). At n=${c.n} this is the expected outcome unless the gap is large.`;
    return c.regression
      ? `Significant REGRESSION: the candidate is worse (p = ${fmtP(c.p_value)}).`
      : `Significant improvement for the candidate (p = ${fmtP(c.p_value)}).`;
  });

  protected readonly usd = fmtUsd;
  protected readonly ms = fmtMs;
  protected readonly pctCi = fmtPctCi;
  protected readonly p = fmtP;
  protected signedPct(x: number | undefined): string {
    return x === undefined ? '–' : `${x >= 0 ? '+' : ''}${fmtPct(x)}`;
  }
}
