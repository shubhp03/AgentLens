import { computed, inject, Injectable, signal } from '@angular/core';
import { RuntimeConfigService } from './runtime-config';

const STORAGE_KEY = 'agentlens.apiKey';

/**
 * Holds the API key in memory, mirrored to sessionStorage so a refresh does not log you out.
 * Never localStorage: the key should not outlive the tab. (Portfolio simplification; production
 * would use OIDC with short-lived tokens.)
 */
@Injectable({ providedIn: 'root' })
export class AuthService {
  private readonly cfg = inject(RuntimeConfigService);
  private readonly key = signal<string | null>(this.read());

  readonly apiKey = this.key.asReadonly();
  /** Demo mode needs no key. */
  readonly isAuthed = computed(() => this.cfg.value.demo || !!this.key());

  login(key: string): void {
    this.key.set(key.trim());
    try {
      sessionStorage.setItem(STORAGE_KEY, key.trim());
    } catch {
      /* storage blocked: stay in-memory only */
    }
  }

  logout(): void {
    this.key.set(null);
    try {
      sessionStorage.removeItem(STORAGE_KEY);
    } catch {
      /* ignore */
    }
  }

  private read(): string | null {
    try {
      return sessionStorage.getItem(STORAGE_KEY);
    } catch {
      return null;
    }
  }
}
