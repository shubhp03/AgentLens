import { Component, computed, effect, inject, input, OnDestroy, signal } from '@angular/core';
import { RouterLink } from '@angular/router';
import { AgentLensApi } from '../../core/api.service';
import { fmtMs, fmtNum, fmtUsd } from '../../core/format';
import { loadFrom } from '../../core/loadable';
import { Answer, RunDetail, Span } from '../../core/models';
import { LiveHandle, RunStreamService } from '../../core/run-stream.service';
import { JsonView } from '../../shared/json-view';
import { StateView } from '../../shared/state-view';
import { StatusChip } from '../../shared/status-chip';
import { TraceWaterfall } from '../../shared/trace-waterfall';
import { spanLabel } from '../../core/waterfall';

@Component({
  selector: 'app-run-detail',
  imports: [RouterLink, StateView, StatusChip, JsonView, TraceWaterfall],
  template: `
    <div class="page">
      <p><a routerLink="/runs">← Runs</a></p>
      <app-state [status]="run.status()" [error]="run.error()" (retry)="run.reload()">
        @let r = run.data();
        @if (r) {
          <div class="page-head">
            <div>
              <h1>{{ r.question }}</h1>
              <p>
                <app-chip [label]="r.status" />
                @if (r.termination_reason) { <span class="muted"> · {{ r.termination_reason }}</span> }
                @if (live()) { <span class="muted"> · live</span> }
              </p>
            </div>
            <span class="spacer"></span>
            @if (!api.demo && (r.status === 'queued' || r.status === 'running')) {
              <button class="btn danger" type="button" (click)="cancel(r.id)">Cancel run</button>
            }
          </div>

          @if (streamError()) { <div class="alert info" role="status">Live updates interrupted: {{ streamError() }}. The page refreshes when the run finishes.</div> }
          @if (r.error) { <div class="alert bad" role="alert">{{ r.error }}</div> }

          <div class="grid cols-4">
            <div class="card stat"><div class="label">Cost</div><div class="value">{{ usd(r.totals?.cost_usd) }}</div></div>
            <div class="card stat"><div class="label">Latency</div><div class="value">{{ ms(r.totals?.latency_ms) }}</div></div>
            <div class="card stat"><div class="label">LLM steps / tool calls</div><div class="value">{{ r.totals?.step_count ?? 0 }} / {{ r.totals?.tool_call_count ?? 0 }}</div></div>
            <div class="card stat"><div class="label">Tokens in / out</div><div class="value">{{ r.totals?.input_tokens ?? 0 }} / {{ r.totals?.output_tokens ?? 0 }}</div></div>
          </div>

          @if (r.final_answer; as a) {
            <section class="card" aria-labelledby="ans">
              <h2 id="ans">Answer</h2>
              @if (a.abstained) {
                <p><app-chip label="abstained" kind="warn" /> The agent declined to answer.</p>
              } @else if (a.value !== null && a.value !== undefined) {
                <p class="big">{{ num(a.value) }} <span class="muted">{{ a.unit }}</span> @if (a.period) { <app-chip [label]="a.period" kind="info" /> }</p>
              }
              <p>{{ a.text }}</p>
              @if (citations(a).length > 0) {
                <h3>Citations</h3>
                <ul class="cites">
                  @for (c of citations(a); track $index) {
                    <li><code>{{ c.accession }}</code>@if (c.concept) { · {{ c.concept }} }@if (c.section) { · item {{ c.section }} }</li>
                  }
                </ul>
              } @else if (!a.abstained) {
                <p class="muted">No citations were provided.</p>
              }
            </section>
          }

          <section class="card" aria-labelledby="trace">
            <h2 id="trace">Trace <span class="muted">({{ spans().length }} spans)</span></h2>
            <app-state [status]="'ready'" [empty]="spans().length === 0" emptyText="No spans recorded yet.">
              <app-trace-waterfall [spans]="spans()" [(selected)]="selectedId" />
            </app-state>
          </section>

          @if (selectedSpan(); as s) {
            <section class="card" aria-labelledby="insp">
              <h2 id="insp">Span: {{ label(s) }}</h2>
              <dl class="kv">
                <dt>Status</dt><dd><app-chip [label]="s.status" /></dd>
                <dt>Duration</dt><dd>{{ ms(s.latency_ms) }}</dd>
                @if (s.model) { <dt>Model</dt><dd>{{ s.model }}</dd> }
                @if (s.input_tokens !== null && s.input_tokens !== undefined) { <dt>Tokens</dt><dd>{{ s.input_tokens }} in / {{ s.output_tokens ?? 0 }} out</dd> }
                @if (s.cost_usd !== null && s.cost_usd !== undefined) { <dt>Cost</dt><dd>{{ usd(s.cost_usd) }}</dd> }
              </dl>
              @if (s.error) { <div class="alert bad" role="alert">{{ s.error }}</div> }
              @if (hasAttrs(s)) { <h3>Attributes</h3><app-json [value]="s.attributes" /> }
              <h3>Input</h3><app-json [value]="s.input" />
              <h3>Output</h3><app-json [value]="s.output" />
            </section>
          }
        }
      </app-state>
    </div>
  `,
  styles: `.big { font-size: 28px; font-weight: 650; margin: 0 0 8px; } .big app-chip { margin-left: 10px; vertical-align: middle; } section h3 { margin-top: 16px; } .cites { margin: 0; padding-left: 20px; } section { margin-top: 16px; }`,
})
export class RunDetailPage implements OnDestroy {
  readonly id = input.required<string>();
  protected readonly api = inject(AgentLensApi);
  private readonly stream = inject(RunStreamService);

  protected readonly run = loadFrom(() => this.id(), (id) => this.api.getRun(id));
  /** Spans = what the server returned, merged with spans that arrived live over SSE (keyed by id). */
  private readonly liveSpans = signal<Span[]>([]);
  protected readonly spans = computed<Span[]>(() => {
    const merged = new Map<string, Span>();
    for (const s of this.run.data()?.spans ?? []) merged.set(s.id, s);
    for (const s of this.liveSpans()) merged.set(s.id, s);
    return [...merged.values()].sort((a, b) => a.seq - b.seq);
  });

  protected readonly selectedId = signal<string | null>(null);
  protected readonly selectedSpan = computed(() => this.spans().find((s) => s.id === this.selectedId()) ?? null);
  protected readonly live = signal(false);
  protected readonly streamError = signal<string | null>(null);
  private handle: LiveHandle | null = null;
  private watching: string | null = null;

  protected readonly usd = fmtUsd;
  protected readonly ms = fmtMs;
  protected readonly num = fmtNum;
  protected readonly label = spanLabel;

  constructor() {
    effect(() => {
      const r: RunDetail | null = this.run.data();
      if (!r || this.api.demo) return;
      const active = r.status === 'queued' || r.status === 'running';
      if (active && this.watching !== r.id) this.startLive(r.id);
      if (!active) this.stopLive();
    });
    // New run id: drop live state from the previous run.
    effect(() => {
      this.id();
      this.stopLive();
      this.liveSpans.set([]);
      this.selectedId.set(null);
    });
  }

  ngOnDestroy(): void {
    this.stopLive();
  }

  protected citations(a: Answer) {
    return a.citations ?? [];
  }
  protected hasAttrs(s: Span): boolean {
    return !!s.attributes && Object.keys(s.attributes).length > 0;
  }

  protected cancel(id: string): void {
    this.api.cancelRun(id).subscribe({ next: () => this.run.reload() });
  }

  private startLive(runId: string): void {
    this.watching = runId;
    this.live.set(true);
    this.streamError.set(null);
    this.handle = this.stream.watch(runId, {
      onSpan: (s) => this.liveSpans.update((cur) => [...cur, s]),
      onFinished: () => {
        this.stopLive();
        this.run.reload(); // authoritative totals/answer come from the REST resource
      },
      onError: (msg) => {
        this.streamError.set(msg);
        this.stopLive();
      },
    });
  }

  private stopLive(): void {
    this.handle?.stop();
    this.handle = null;
    this.watching = null;
    this.live.set(false);
  }
}
