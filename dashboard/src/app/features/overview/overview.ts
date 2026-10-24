import { Component, computed, inject } from '@angular/core';
import { RouterLink } from '@angular/router';
import { forkJoin, map } from 'rxjs';
import { AgentLensApi } from '../../core/api.service';
import { RuntimeConfigService } from '../../core/runtime-config';
import { fmtMs, fmtPct, fmtUsd, shortId } from '../../core/format';
import { loadFrom } from '../../core/loadable';
import { StateView } from '../../shared/state-view';
import { StatusChip } from '../../shared/status-chip';

@Component({
  selector: 'app-overview',
  imports: [RouterLink, StateView, StatusChip],
  template: `
    <div class="page">
      <div class="page-head"><div><h1>Overview</h1>@if (replay) { <p>Real runs recorded from a live stack: the question, the agent's tool calls, the answer, and what each cost.</p> } @else { <p>Today's activity, spend against the daily cap, and the latest baseline.</p> }</div></div>
      <app-state [status]="data.status()" [error]="data.error()" (retry)="data.reload()">
        @let d = data.data();
        @if (d) {
          <div class="grid cols-4">
            <div class="card stat"><div class="label">{{ replay ? 'Runs recorded' : 'Runs today' }}</div><div class="value">{{ d.overview.runs_today }}</div></div>
            <div class="card stat">
              <div class="label">{{ replay ? 'Total spend' : 'Spend today' }}</div>
              <div class="value">{{ usd(d.overview.cost_today_usd) }}</div>
              @if (!replay) {
              <div class="bar" [class.warn]="spendPct() >= 0.8" [class.bad]="spendPct() >= 1" role="progressbar" aria-label="Daily spend" [attr.aria-valuenow]="(spendPct() * 100).toFixed(0)" aria-valuemin="0" aria-valuemax="100">
                <span [style.width.%]="min(spendPct() * 100, 100)"></span>
              </div>
              <div class="sub">of {{ usd(d.overview.daily_cap_usd) }} daily cap</div>
              }
            </div>
            <div class="card stat">
              <div class="label">Latest baseline pass rate</div>
              <div class="value">{{ pct(d.overview.latest_baseline_pass_rate) }}</div>
              @if (replay) { <div class="sub">Not measured yet: waiting on the human-verified golden set. <a routerLink="/evals">Evals</a></div> }
              @else { <div class="sub"><a routerLink="/evals">All eval runs</a></div> }
            </div>
          </div>
          <div class="card" style="margin-top:16px">
            <h2>Recent runs</h2>
            <app-state [status]="'ready'" [empty]="d.runs.length === 0" emptyText="No runs yet. Try the Ask page.">
              <div class="table-wrap">
                <table>
                  <thead><tr><th>Run</th><th>Question</th><th>Status</th><th class="right">Cost</th><th class="right">Latency</th></tr></thead>
                  <tbody>
                    @for (r of d.runs; track r.id) {
                      <tr>
                        <td><a [routerLink]="['/runs', r.id]" class="mono">{{ short(r.id) }}</a></td>
                        <td>{{ r.question }}</td>
                        <td><app-chip [label]="r.status" /></td>
                        <td class="right">{{ usd(r.totals?.cost_usd) }}</td>
                        <td class="right">{{ ms(r.totals?.latency_ms) }}</td>
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
})
export class Overview {
  private readonly api = inject(AgentLensApi);
  /** A static replay of recorded runs: "today" and a daily cap would be misleading. */
  protected readonly replay = inject(RuntimeConfigService).value.dataset === 'recorded';
  protected readonly data = loadFrom(
    () => 0,
    () =>
      forkJoin({ overview: this.api.overview(), page: this.api.listRuns({ limit: 8 }) }).pipe(
        map(({ overview, page }) => ({ overview, runs: page.items })),
      ),
  );

  protected readonly spendPct = computed(() => {
    const o = this.data.data()?.overview;
    return o && o.daily_cap_usd > 0 ? o.cost_today_usd / o.daily_cap_usd : 0;
  });

  protected readonly usd = fmtUsd;
  protected readonly pct = fmtPct;
  protected readonly ms = fmtMs;
  protected readonly short = shortId;
  protected readonly min = Math.min;
}
