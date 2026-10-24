import { inject, Injectable } from '@angular/core';
import { HttpClient } from '@angular/common/http';
import { firstValueFrom } from 'rxjs';

export interface RuntimeConfig {
  /** Base URL of the gateway, e.g. http://localhost:8080 (empty = same origin, behind a proxy). */
  apiBaseUrl: string;
  /** Demo mode serves recorded JSON from `dataPath` and never calls a gateway. */
  demo: boolean;
  /** What the recorded JSON is. `synthetic` = fictional companies (/demo); `recorded` = real runs captured from a live stack (/replay). */
  dataset: 'synthetic' | 'recorded';
  /** Folder under the site root that holds the recorded JSON. */
  dataPath: string;
}

const DEFAULTS: RuntimeConfig = { apiBaseUrl: 'http://localhost:8080', demo: false, dataset: 'synthetic', dataPath: 'demo' };

/** Loaded from /config.json at startup so one build can run anywhere (docker, demo site, local). */
@Injectable({ providedIn: 'root' })
export class RuntimeConfigService {
  private readonly http = inject(HttpClient);
  value: RuntimeConfig = DEFAULTS;

  async load(): Promise<void> {
    try {
      this.value = { ...DEFAULTS, ...(await firstValueFrom(this.http.get<Partial<RuntimeConfig>>('config.json'))) };
    } catch {
      this.value = DEFAULTS; // no config.json: fall back to local development defaults
    }
  }
}
