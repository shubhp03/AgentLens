import { Component, computed, input, signal } from '@angular/core';
import { prettyPayload } from '../core/pretty';

const LIMIT = 6000;

/** Pretty-printed JSON that stays cheap to render: long payloads are cut until you ask for the rest. */
@Component({
  selector: 'app-json',
  template: `
    @if (text().length === 0) {
      <p class="muted">(empty)</p>
    } @else {
      <pre class="json">{{ shown() }}</pre>
      @if (truncated()) {
        <button class="btn" type="button" (click)="expanded.set(true)">
          Show all ({{ text().length.toLocaleString() }} chars)
        </button>
      }
    }
  `,
  styles: `
    .json { margin: 0; padding: 10px; background: var(--surface-2); border-radius: 6px; overflow: auto; max-height: 360px; white-space: pre-wrap; word-break: break-word; }
  `,
})
export class JsonView {
  readonly value = input<unknown>();
  protected readonly expanded = signal(false);

  protected readonly text = computed(() => prettyPayload(this.value()));
  protected readonly truncated = computed(() => !this.expanded() && this.text().length > LIMIT);
  protected readonly shown = computed(() => (this.truncated() ? this.text().slice(0, LIMIT) + '\n…' : this.text()));
}
