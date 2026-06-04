import { isFounderNodeAuthError } from './sync-client';

export type SyncFailureKind = 'auth' | 'transient' | 'other';

export function classifySyncFailure(err: unknown): SyncFailureKind {
  if (isFounderNodeAuthError(err)) return 'auth';
  const msg = err instanceof Error ? err.message : String(err);
  if (
    /fetch failed|ECONNREFUSED|ENOTFOUND|ETIMEDOUT|ECONNRESET|socket hang up|network/i.test(
      msg,
    )
  ) {
    return 'transient';
  }
  return 'other';
}

/** Backoff delay before retrying after transient failures (capped). */
export function transientRetryDelayMs(consecutiveTransientFailures: number): number {
  const base = 15_000;
  const cap = 5 * 60_000;
  return Math.min(cap, base * 2 ** Math.min(consecutiveTransientFailures, 4));
}

export function authFailureUserMessage(): string {
  return 'Session expired on the server — enter a new pairing code from Founder OS (Settings → Builder).';
}
