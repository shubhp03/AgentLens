import { HttpErrorResponse } from '@angular/common/http';

/** Turn anything thrown by HttpClient into one human sentence (RFC 9457 problem+json aware). */
export function describeError(err: unknown): string {
  if (err instanceof HttpErrorResponse) {
    if (err.status === 0) return 'Cannot reach the gateway. Is it running, and is the API URL right?';
    const body = err.error as { title?: string; detail?: string } | null;
    const text = body?.detail ?? body?.title;
    if (text) return `${err.status}: ${text}`;
    return `${err.status} ${err.statusText || 'error'}`;
  }
  return err instanceof Error ? err.message : String(err);
}
