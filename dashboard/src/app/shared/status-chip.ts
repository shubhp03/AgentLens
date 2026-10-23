import { Component, computed, input } from '@angular/core';

const KIND: Record<string, string> = {
  completed: 'ok', passed: 'ok', answered: 'ok', ok: 'ok',
  failed: 'bad', error: 'bad',
  running: 'info', queued: 'warn', cancelled: '',
};

/** One coloured pill for any status-like string; colour is never the only signal (the text is shown). */
@Component({
  selector: 'app-chip',
  template: `<span class="chip" [class]="cls()">{{ label() ?? '–' }}</span>`,
})
export class StatusChip {
  readonly label = input<string | null | undefined>();
  readonly kind = input<'ok' | 'bad' | 'warn' | 'info' | ''>();
  protected readonly cls = computed(() => this.kind() ?? KIND[this.label() ?? ''] ?? '');
}
