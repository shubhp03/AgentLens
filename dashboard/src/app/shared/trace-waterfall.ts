import { Component, computed, input, model } from '@angular/core';
import { layoutWaterfall, spanLabel } from '../core/waterfall';
import { fmtMs } from '../core/format';
import { Span } from '../core/models';

/**
 * The hero view: every span as a bar on a shared time axis. LLM calls, tool calls and errors are
 * distinguished by colour AND by the text label / icon, so it does not rely on colour alone.
 * Rows are buttons: Tab to move, Enter/Space to inspect.
 */
@Component({
  selector: 'app-trace-waterfall',
  template: `
    @let w = layout();
    <div class="legend" aria-hidden="true">
      <span><i class="sw llm"></i>LLM call</span>
      <span><i class="sw tool"></i>Tool call</span>
      <span><i class="sw agent"></i>Agent (whole run)</span>
      <span><i class="sw err"></i>Error</span>
    </div>
    <div class="axis">
      <div class="label-col"></div>
      <div class="track-col axis-track">
        @for (t of w.ticks; track t.pct) {
          <span class="tick" [style.left.%]="t.pct">{{ t.label }}</span>
        }
      </div>
    </div>
    <div role="list" aria-label="Trace spans">
      @for (r of w.rows; track r.span.id) {
        <button
          type="button"
          role="listitem"
          class="row-btn"
          [class.selected]="r.span.id === selected()"
          [attr.aria-pressed]="r.span.id === selected()"
          [attr.aria-label]="describe(r.span, r.durationMs)"
          (click)="selected.set(r.span.id)"
        >
          <div class="label-col" [style.padding-left.px]="8 + r.depth * 16">
            <span class="icon" aria-hidden="true">{{ r.kind === 'llm' ? '◆' : r.kind === 'tool' ? '▣' : '●' }}</span>
            <span class="name">{{ label(r.span) }}</span>
            @if (r.error) { <span class="err-tag">error</span> }
          </div>
          <div class="track-col">
            <span
              class="bar-seg"
              [class]="r.kind"
              [class.error]="r.error"
              [style.left.%]="r.leftPct"
              [style.width.%]="r.widthPct"
            ></span>
            @if (r.leftPct + r.widthPct > 80) {
              <!-- near the right edge: put the label on the LEFT of the bar so it never overlaps it -->
              <span class="dur before" [style.right.%]="100 - r.leftPct">{{ fmt(r.durationMs) }}</span>
            } @else {
              <span class="dur" [style.left.%]="r.leftPct + r.widthPct">{{ fmt(r.durationMs) }}</span>
            }
          </div>
        </button>
      }
    </div>
  `,
  styles: `
    :host { display: block; }
    .legend { display: flex; gap: 16px; font-size: 12px; color: var(--muted); margin-bottom: 8px; flex-wrap: wrap; }
    .sw { display: inline-block; width: 10px; height: 10px; border-radius: 2px; margin-right: 6px; }
    .sw.llm { background: var(--llm); } .sw.tool { background: var(--tool); } .sw.agent { background: var(--agent); }
    .sw.err { background: transparent; border: 2px solid var(--bad); }
    .axis, .row-btn { display: grid; grid-template-columns: minmax(180px, 280px) 1fr; align-items: center; }
    .axis-track { position: relative; height: 18px; border-bottom: 1px solid var(--border); }
    .tick { position: absolute; transform: translateX(-50%); font-size: 11px; color: var(--muted); white-space: nowrap; }
    .tick:first-child { transform: none; } .tick:last-child { transform: translateX(-100%); }
    .row-btn { width: 100%; background: none; border: 0; border-bottom: 1px solid var(--border); color: inherit; font: inherit; text-align: left; padding: 0; cursor: pointer; min-height: 30px; }
    .row-btn:hover { background: var(--surface-2); }
    .row-btn.selected { background: var(--info-bg); }
    .label-col { display: flex; gap: 6px; align-items: center; padding: 4px 8px; overflow: hidden; white-space: nowrap; }
    .name { overflow: hidden; text-overflow: ellipsis; }
    .icon { font-size: 10px; color: var(--muted); }
    .err-tag { font-size: 11px; font-weight: 700; color: var(--bad); }
    .track-col { position: relative; height: 26px; }
    .bar-seg { position: absolute; top: 6px; height: 14px; border-radius: 3px; min-width: 2px; }
    .bar-seg.llm { background: var(--llm); } .bar-seg.tool { background: var(--tool); } .bar-seg.agent { background: var(--agent); opacity: 0.55; }
    .bar-seg.error { outline: 2px solid var(--bad); outline-offset: 1px; }
    .dur { position: absolute; top: 4px; margin-left: 6px; font-size: 11px; color: var(--muted); white-space: nowrap; }
    .dur.before { margin-left: 0; margin-right: 6px; }
    @media (max-width: 640px) { .axis, .row-btn { grid-template-columns: 130px 1fr; } }
  `,
})
export class TraceWaterfall {
  readonly spans = input.required<Span[]>();
  /** Two-way bound id of the selected span. */
  readonly selected = model<string | null>(null);

  protected readonly layout = computed(() => layoutWaterfall(this.spans()));
  protected readonly label = spanLabel;
  protected readonly fmt = fmtMs;

  protected describe(s: Span, durationMs: number): string {
    return `${spanLabel(s)}, ${fmtMs(durationMs)}${s.status === 'error' ? ', failed' : ''}`;
  }
}
