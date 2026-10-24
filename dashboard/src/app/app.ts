import { Component, inject } from '@angular/core';
import { Router, RouterLink, RouterLinkActive, RouterOutlet } from '@angular/router';
import { AuthService } from './core/auth.service';
import { RuntimeConfigService } from './core/runtime-config';

@Component({
  selector: 'app-root',
  imports: [RouterOutlet, RouterLink, RouterLinkActive],
  template: `
    @if (demo && recorded) {
      <div class="banner demo" role="note">
        Replay: runs <strong>recorded from a live stack</strong> (real Claude calls, real SEC data). Read-only. Individual runs, not benchmark results.
      </div>
    } @else if (demo) {
      <div class="banner demo" role="note">
        Demo mode: recorded <strong>synthetic sample data</strong> about a fictional company. These are not benchmark results.
      </div>
    }
    @if (auth.isAuthed()) {
      <header class="top">
        <a class="brand" routerLink="/">AgentLens</a>
        <nav aria-label="Main">
          <a routerLink="/" routerLinkActive="active" [routerLinkActiveOptions]="{ exact: true }">Overview</a>
          <a routerLink="/runs" routerLinkActive="active">Runs</a>
          <a routerLink="/ask" routerLinkActive="active">Ask</a>
          <a routerLink="/evals" routerLinkActive="active">Evals</a>
          <a routerLink="/configs" routerLinkActive="active">Configs</a>
        </nav>
        <span class="spacer"></span>
        @if (!demo) {
          <button class="btn" type="button" (click)="signOut()">Sign out</button>
        }
      </header>
    }
    <main><router-outlet /></main>
  `,
  styles: `
    .top { display: flex; align-items: center; gap: 24px; padding: 10px 20px; background: var(--surface); border-bottom: 1px solid var(--border); position: sticky; top: 0; z-index: 5; }
    .brand { font-weight: 750; font-size: 16px; color: var(--text); letter-spacing: 0.01em; }
    nav { display: flex; gap: 4px; flex-wrap: wrap; }
    nav a { color: var(--muted); padding: 6px 10px; border-radius: 6px; }
    nav a:hover { background: var(--surface-2); text-decoration: none; }
    nav a.active { color: var(--text); background: var(--surface-2); font-weight: 600; }
  `,
})
export class App {
  protected readonly auth = inject(AuthService);
  private readonly router = inject(Router);
  private readonly cfg = inject(RuntimeConfigService).value;
  protected readonly demo = this.cfg.demo;
  protected readonly recorded = this.cfg.dataset === 'recorded';

  protected signOut(): void {
    this.auth.logout();
    void this.router.navigate(['/login']);
  }
}
