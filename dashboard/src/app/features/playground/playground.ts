import { Component, computed, inject, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AgentLensApi } from '../../core/api.service';
import { loadFrom } from '../../core/loadable';
import { describeError } from '../../core/problem';
import { StateView } from '../../shared/state-view';

const EXAMPLES = [
  'What was the total revenue in the most recent fiscal year, and how did it change from the prior year?',
  'Which reportable segments does the company disclose in its latest 10-K?',
  'What will the company report as its fiscal 2030 earnings per share?',
];

@Component({
  selector: 'app-playground',
  imports: [FormsModule, StateView],
  template: `
    <div class="page">
      <div class="page-head"><div><h1>Ask</h1><p>Send a question to the agent and watch its trace stream in live.</p></div></div>
      @if (api.demo) {
        <div class="alert info" role="note">Demo mode is read-only. Running the agent needs a gateway, an API key and an LLM provider key.</div>
      }
      <div class="card">
        <app-state [status]="configs.status()" [error]="configs.error()" [empty]="(configs.data()?.length ?? 0) === 0" emptyText="No agent configs yet. Create one via the API (POST /api/v1/configs) first." (retry)="configs.reload()">
          <form (ngSubmit)="submit()">
            <label>Agent config
              <select name="config" [(ngModel)]="configId">
                @for (c of configs.data(); track c.id) { <option [value]="c.id">{{ c.name }} · {{ c.model }}</option> }
              </select>
            </label>
            <label style="margin-top:12px">Question
              <textarea name="q" maxlength="2000" required [(ngModel)]="question" placeholder="Ask about a company's SEC filings…"></textarea>
            </label>
            <p class="muted">{{ question.length }}/2000</p>
            <div class="row">
              @for (e of examples; track e) { <button type="button" class="btn" (click)="question = e">Example</button> }
            </div>
            <p><button class="btn primary" type="submit" [disabled]="!canSubmit()">Run agent</button></p>
          </form>
        </app-state>
        @if (error()) { <div class="alert bad" role="alert">{{ error() }}</div> }
      </div>
    </div>
  `,
})
export class Playground {
  protected readonly api = inject(AgentLensApi);
  private readonly router = inject(Router);
  protected readonly configs = loadFrom(() => 0, () => this.api.listConfigs());

  protected question = '';
  protected configId = '';
  protected readonly examples = EXAMPLES;
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);
  protected readonly canSubmit = computed(() => !this.api.demo && !this.busy());

  protected submit(): void {
    const config_id = this.configId || this.configs.data()?.[0]?.id;
    if (!config_id || !this.question.trim() || !this.canSubmit()) return;
    this.busy.set(true);
    this.error.set(null);
    this.api.createRun({ question: this.question.trim(), config_id, source: 'interactive' }).subscribe({
      next: (run) => void this.router.navigate(['/runs', run.id]),
      error: (e: unknown) => {
        this.error.set(describeError(e));
        this.busy.set(false);
      },
    });
  }
}
