import { Component, input, output } from '@angular/core';

/** Loading / error / empty handling in one place so every view has all three states. */
@Component({
  selector: 'app-state',
  template: `
    @switch (status()) {
      @case ('loading') {
        <p class="muted" role="status">Loading…</p>
      }
      @case ('error') {
        <div class="alert bad" role="alert">
          {{ error() }}
          <button class="btn" type="button" (click)="retry.emit()">Retry</button>
        </div>
      }
      @default {
        @if (empty()) {
          <p class="muted">{{ emptyText() }}</p>
        } @else {
          <ng-content />
        }
      }
    }
  `,
})
export class StateView {
  readonly status = input.required<'loading' | 'ready' | 'error'>();
  readonly error = input<string | null>(null);
  readonly empty = input(false);
  readonly emptyText = input('Nothing here yet.');
  readonly retry = output<void>();
}
