import { Component, inject, input, signal } from '@angular/core';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { AgentLensApi } from '../../core/api.service';
import { AuthService } from '../../core/auth.service';
import { describeError } from '../../core/problem';

@Component({
  selector: 'app-login',
  imports: [FormsModule],
  template: `
    <div class="page narrow">
      <div class="card">
        <h1>AgentLens</h1>
        <p class="muted">Enter your API key to continue. It is kept in this tab only and sent as <code>X-API-Key</code>.</p>
        @if (reason() === 'invalid-key') {
          <div class="alert bad" role="alert">That key was rejected. Check it and try again.</div>
        }
        <form (ngSubmit)="submit()" class="row">
          <label>
            API key
            <input type="password" name="key" autocomplete="off" required [(ngModel)]="key" />
          </label>
          <button class="btn primary" type="submit" [disabled]="busy() || !key.trim()">Sign in</button>
        </form>
        @if (error()) {
          <div class="alert bad" role="alert">{{ error() }}</div>
        }
      </div>
    </div>
  `,
  styles: `.narrow { max-width: 460px; padding-top: 80px; } input { min-width: 260px; }`,
})
export class Login {
  readonly reason = input<string>();
  readonly returnUrl = input<string>();
  private readonly auth = inject(AuthService);
  private readonly api = inject(AgentLensApi);
  private readonly router = inject(Router);

  protected key = '';
  protected readonly busy = signal(false);
  protected readonly error = signal<string | null>(null);

  protected submit(): void {
    this.error.set(null);
    this.busy.set(true);
    this.auth.login(this.key);
    // Verify the key with a cheap authenticated call before letting the user in.
    this.api.overview().subscribe({
      next: () => {
        this.busy.set(false);
        void this.router.navigateByUrl(this.returnUrl() || '/');
      },
      error: (e: unknown) => {
        this.busy.set(false);
        this.auth.logout();
        // The interceptor already redirects on 401; show the message for other failures.
        this.error.set(describeError(e));
      },
    });
  }
}
