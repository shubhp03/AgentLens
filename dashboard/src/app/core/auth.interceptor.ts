import { HttpErrorResponse, HttpInterceptorFn } from '@angular/common/http';
import { inject } from '@angular/core';
import { Router } from '@angular/router';
import { catchError, throwError } from 'rxjs';
import { AuthService } from './auth.service';
import { RuntimeConfigService } from './runtime-config';

/** Adds X-API-Key to gateway calls only (never to /demo or /config.json) and logs out on 401. */
export const authInterceptor: HttpInterceptorFn = (req, next) => {
  const auth = inject(AuthService);
  const cfg = inject(RuntimeConfigService);
  const router = inject(Router);

  const base = cfg.value.apiBaseUrl;
  const isGateway = !cfg.value.demo && req.url.startsWith(`${base}/api/`);
  const key = auth.apiKey();
  const out = isGateway && key ? req.clone({ setHeaders: { 'X-API-Key': key } }) : req;

  return next(out).pipe(
    catchError((err: unknown) => {
      if (isGateway && err instanceof HttpErrorResponse && err.status === 401) {
        auth.logout();
        void router.navigate(['/login'], { queryParams: { reason: 'invalid-key' } });
      }
      return throwError(() => err);
    }),
  );
};
