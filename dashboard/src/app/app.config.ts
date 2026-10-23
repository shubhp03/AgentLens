import { ApplicationConfig, inject, provideAppInitializer, provideBrowserGlobalErrorListeners } from '@angular/core';
import { provideHttpClient, withInterceptors } from '@angular/common/http';
import { provideRouter, withComponentInputBinding } from '@angular/router';
import { routes } from './app.routes';
import { AgentLensApi, DemoAgentLensApi, HttpAgentLensApi } from './core/api.service';
import { authInterceptor } from './core/auth.interceptor';
import { RuntimeConfigService } from './core/runtime-config';

export const appConfig: ApplicationConfig = {
  providers: [
    provideBrowserGlobalErrorListeners(),
    provideRouter(routes, withComponentInputBinding()),
    provideHttpClient(withInterceptors([authInterceptor])),
    // Load /config.json before anything renders so every service sees the final settings.
    provideAppInitializer(() => inject(RuntimeConfigService).load()),
    {
      provide: AgentLensApi,
      useFactory: () => (inject(RuntimeConfigService).value.demo ? new DemoAgentLensApi() : new HttpAgentLensApi()),
    },
  ],
};
