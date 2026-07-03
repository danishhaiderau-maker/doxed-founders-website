import type { ExchangeCredentials } from './exchange-adapter.interface';

/**
 * Minimal structural contract for a trading client that can cancel and look up
 * orders. Both {@link BitfinexTradingClient} and {@link BitfinexSimTradingClient}
 * satisfy this structurally so the helper works against either active venue.
 */
export type CancelCapableClient = {
  cancelOrder(creds: ExchangeCredentials, orderId: number): Promise<void>;
  findOrder(
    creds: ExchangeCredentials,
    orderId: number,
    symbol?: string,
  ): Promise<{ id: number } | null>;
};

export type CancelResult = {
  ok: boolean;
  /** Present when `ok` is true but the cancel API reported the order already gone. */
  reason?: 'NOT_FOUND' | string;
  attempts: number;
};

type RetryLogger = {
  error: (message: string) => void;
  warn?: (message: string) => void;
};

const DEFAULT_BACKOFF_MS = [500, 1000, 2000];
const DEFAULT_MAX_ATTEMPTS = 3;

/**
 * Money-path cancel: retry with backoff and fail loud. Distinguishes "order
 * already gone" (Bitfinex 404 / "invalid order" / "already cancelled") from
 * real failures — the former is treated as success (`ok: true, reason: 'NOT_FOUND'`)
 * because the desired end state (order not on the book) already holds.
 *
 * Returns `{ ok: false, reason, attempts }` only when every attempt hit a
 * non-"gone" error. Callers MUST verify the order is actually gone (via
 * {@link confirmOrderGone}) before recording an EXPIRED ledger event when
 * `ok` is false.
 */
export async function cancelOrderWithRetry(
  client: CancelCapableClient,
  creds: ExchangeCredentials,
  orderId: number,
  opts: {
    maxAttempts?: number;
    backoffMs?: number[];
    logger?: RetryLogger;
    label?: string;
  } = {},
): Promise<CancelResult> {
  const maxAttempts = opts.maxAttempts ?? DEFAULT_MAX_ATTEMPTS;
  const backoff = opts.backoffMs ?? DEFAULT_BACKOFF_MS;
  const label = opts.label ?? `Bitfinex cancelOrder ${orderId}`;
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    try {
      await client.cancelOrder(creds, orderId);
      if (attempt > 1) {
        opts.logger?.warn?.(`${label} succeeded on attempt ${attempt}/${maxAttempts}`);
      }
      return { ok: true, attempts: attempt };
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err);
      // Bitfinex reports already-gone orders as "Order not found" / "invalid
      // order" / "already cancelled" (wording varies by API rev). The desired
      // end state holds — treat as success so the caller can mark EXPIRED.
      if (/not found|already cancel|invalid order|no order|order does not exist/i.test(msg)) {
        opts.logger?.warn?.(`${label} already gone on attempt ${attempt}: ${msg}`);
        return { ok: true, reason: 'NOT_FOUND', attempts: attempt };
      }
      opts.logger?.error(
        `${label} attempt ${attempt}/${maxAttempts} failed: ${msg}`,
      );
      if (attempt >= maxAttempts) {
        return { ok: false, reason: msg, attempts: attempt };
      }
      await new Promise((r) => setTimeout(r, backoff[attempt - 1] ?? backoff[backoff.length - 1] ?? 2000));
    }
  }
  return { ok: false, reason: 'exhausted', attempts: maxAttempts };
}

/**
 * Confirm an order is no longer on the exchange's active book. Returns true
 * only when `findOrder` resolves to null. On any lookup error, conservatively
 * returns false (treat as still live) so the caller leaves the ledger
 * PENDING_ENTRY and retries next tick — never mark EXPIRED while uncertain.
 */
export async function confirmOrderGone(
  client: CancelCapableClient,
  creds: ExchangeCredentials,
  orderId: number,
): Promise<boolean> {
  try {
    const found = await client.findOrder(creds, orderId);
    return !found;
  } catch {
    return false;
  }
}
