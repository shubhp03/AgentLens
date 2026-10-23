import { afterRenderEffect, Component, DestroyRef, ElementRef, inject, input, viewChild } from '@angular/core';
import {
  BarController, BarElement, CategoryScale, Chart, ChartConfiguration, Legend, LinearScale,
  PointElement, ScatterController, Tooltip,
} from 'chart.js';

Chart.register(BarController, BarElement, ScatterController, PointElement, CategoryScale, LinearScale, Tooltip, Legend);

/** Reads the active theme so charts follow light/dark mode. */
export function themeColors() {
  const css = getComputedStyle(document.documentElement);
  const v = (name: string) => css.getPropertyValue(name).trim();
  return { text: v('--text'), muted: v('--muted'), grid: v('--border'), accent: v('--accent'), bad: v('--bad'), ok: v('--ok') };
}

/** Thin Chart.js wrapper. Pass a NEW config object when data changes. */
@Component({
  selector: 'app-chart',
  template: `<div class="wrap"><canvas #c role="img" [attr.aria-label]="label()"></canvas></div>`,
  styles: `.wrap { position: relative; height: var(--chart-h, 260px); }`,
})
export class ChartView {
  readonly config = input.required<ChartConfiguration>();
  readonly label = input('chart');
  private readonly canvas = viewChild.required<ElementRef<HTMLCanvasElement>>('c');
  private chart?: Chart;

  constructor() {
    afterRenderEffect(() => {
      const cfg = this.config();
      this.chart?.destroy();
      this.chart = new Chart(this.canvas().nativeElement, cfg);
    });
    inject(DestroyRef).onDestroy(() => this.chart?.destroy());
  }
}
