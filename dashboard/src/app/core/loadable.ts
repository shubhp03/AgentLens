import { computed, Signal, signal } from '@angular/core';
import { toObservable, toSignal } from '@angular/core/rxjs-interop';
import { catchError, map, Observable, of, startWith, switchMap } from 'rxjs';
import { describeError } from './problem';

export interface Loadable<T> {
  readonly status: Signal<'loading' | 'ready' | 'error'>;
  readonly data: Signal<T | null>;
  readonly error: Signal<string | null>;
  reload(): void;
}

type Snapshot<T> = { status: 'loading' | 'ready' | 'error'; data: T | null; error: string | null };

/**
 * Re-fetches whenever `key()` changes (or reload() is called), cancelling stale requests.
 * Must be called in an injection context (field initializer).
 */
export function loadFrom<K, T>(key: () => K, fetch: (key: K) => Observable<T>): Loadable<T> {
  const nonce = signal(0);
  const source = computed(() => ({ k: key(), n: nonce() }));
  const snap = toSignal(
    toObservable(source).pipe(
      switchMap(({ k }) =>
        fetch(k).pipe(
          map((data): Snapshot<T> => ({ status: 'ready', data, error: null })),
          startWith<Snapshot<T>>({ status: 'loading', data: null, error: null }),
          catchError((e: unknown) => of<Snapshot<T>>({ status: 'error', data: null, error: describeError(e) })),
        ),
      ),
    ),
    { initialValue: { status: 'loading', data: null, error: null } as Snapshot<T> },
  );
  return {
    status: computed(() => snap().status),
    data: computed(() => snap().data),
    error: computed(() => snap().error),
    reload: () => nonce.update((n) => n + 1),
  };
}
