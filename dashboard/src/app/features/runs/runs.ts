import { Component, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { RouterLink } from '@angular/router';
import { AgentLensApi } from '../../core/api.service';
import { fmtDateTime, fmtMs, fmtUsd, shortId } from '../../core/format';
import { Run, RunStatus } from '../../core/models';
import { describeError } from '../../core/problem';
import { StatusChip } from '../../shared/status-chip';

@Component({
  selector: 'app-runs',
  imports: [FormsModule, RouterLink, StatusChip],
  template: `
    <div class="page">
      <div class="page-head"><div><h1>Runs</h1><p>Every agent run, newest first. Open one to see its trace.</p></div></div>
      <div class="card">
        <div class="row" style="margin-bottom:12px">
          <label>Status
            <select [ngModel]="status()" (ngModelChange)="setStatus($event)" name="status">
              <option value="">All</option>
              @for (s of statuses; track s) { <option [value]="s">{{ s }}</option> }
            </select>
          </label>
          <label>Source
            <select [ngModel]="source()" (ngModelChange)="setSource($event)" name="source">
              <option value="">All</option><option value="interactive">interactive</option><option value="eval">eval</option>
            </select>
          </label>
        </div>

        @if (error()) {
          <div class="alert bad" role="alert">{{ error() }} <button class="btn" type="button" (click)="reload()">Retry</button></div>
        }
        @if (!loading() && !error() && items().length === 0) {
          <p class="muted">No runs match these filters.</p>
        }
        @if (items().length > 0) {
          <div class="table-wrap">
            <table>
              <thead><tr><th>Started</th><th>Run</th><th>Question</th><th>Status</th><th>Source</th><th class="right">Steps</th><th class="right">Tools</th><th class="right">Cost</th><th class="right">Latency</th></tr></thead>
              <tbody>
                @for (r of items(); track r.id) {
                  <tr>
                    <td class="nowrap">{{ dt(r.created_at) }}</td>
                    <td><a [routerLink]="['/runs', r.id]" class="mono">{{ short(r.id) }}</a></td>
                    <td class="q">{{ r.question }}</td>
                    <td><app-chip [label]="r.status" />@if (r.termination_reason && r.termination_reason !== 'answered') { <span class="muted"> {{ r.termination_reason }}</span> }</td>
                    <td>{{ r.source }}</td>
                    <td class="right">{{ r.totals?.step_count ?? '–' }}</td>
                    <td class="right">{{ r.totals?.tool_call_count ?? '–' }}</td>
                    <td class="right">{{ usd(r.totals?.cost_usd) }}</td>
                    <td class="right">{{ ms(r.totals?.latency_ms) }}</td>
                  </tr>
                }
              </tbody>
            </table>
          </div>
        }
        @if (loading()) { <p class="muted" role="status">Loading…</p> }
        @if (cursor() && !loading()) {
          <p><button class="btn" type="button" (click)="more()">Load more</button></p>
        }
      </div>
    </div>
  `,
  styles: `.q { max-width: 420px; }`,
})
export class Runs {
  private readonly api = inject(AgentLensApi);
  protected readonly statuses: RunStatus[] = ['queued', 'running', 'completed', 'failed', 'cancelled'];
  protected readonly status = signal<RunStatus | ''>('');
  protected readonly source = signal<'interactive' | 'eval' | ''>('');
  protected readonly items = signal<Run[]>([]);
  protected readonly cursor = signal<string | null>(null);
  protected readonly loading = signal(false);
  protected readonly error = signal<string | null>(null);

  protected readonly usd = fmtUsd;
  protected readonly ms = fmtMs;
  protected readonly dt = fmtDateTime;
  protected readonly short = shortId;

  constructor() {
    this.reload();
  }

  protected setStatus(v: RunStatus | ''): void {
    this.status.set(v);
    this.reload();
  }
  protected setSource(v: 'interactive' | 'eval' | ''): void {
    this.source.set(v);
    this.reload();
  }
  protected reload(): void {
    this.items.set([]);
    this.cursor.set(null);
    this.fetch(null);
  }
  protected more(): void {
    this.fetch(this.cursor());
  }

  private fetch(cursor: string | null): void {
    this.loading.set(true);
    this.error.set(null);
    this.api
      .listRuns({ status: this.status() || undefined, source: this.source() || undefined, cursor, limit: 25 })
      .subscribe({
        next: (page) => {
          this.items.update((cur) => [...cur, ...page.items]);
          this.cursor.set(page.next_cursor ?? null);
          this.loading.set(false);
        },
        error: (e: unknown) => {
          this.error.set(describeError(e));
          this.loading.set(false);
        },
      });
  }
}
