import { AsyncLocalStorage } from 'node:async_hooks';
import { createHmac } from 'node:crypto';
import { btcToSats } from '@dcf/utils';
import type { ExchangeCredentials } from './exchange-adapter.interface';
import { exchangeErrorMessage, exchangeFetch } from './exchange-http.util';
import { untaggedOrderRefusal } from '../trading-agents/live-tile-gate';

export const BITFINEX_BTC_PERP_SYMBOL = 'tBTCF0:USTF0';
/** Bitfinex defaults to 10x when lev is omitted — showcase bot uses 100x. */
export const BITFINEX_DEFAULT_DERIVATIVE_LEVERAGE = 100;

/** The exact `lev` value every derivative order submits. */
export function bitfinexOrderLev(leverage?: number): number {
  return Math.min(100, Math.max(1, Math.round(leverage ?? BITFINEX_DEFAULT_DERIVATIVE_LEVERAGE)));
}
/** Bitfinex v2 order flags. Partial lot exits must use REDUCE_ONLY only. */
export const BITFINEX_POSITION_CLOSE_FLAG = 512;
export const BITFINEX_REDUCE_ONLY_FLAG = 1024;
export const BITFINEX_SAFE_CLOSE_FLAGS =
  BITFINEX_POSITION_CLOSE_FLAG | BITFINEX_REDUCE_ONLY_FLAG;
const STABLE_CURRENCIES = new Set(['USD', 'USDT', 'UST', 'USTF0']);

export type BitfinexFuturesPairConstraints = {
  symbol: string;
  minQtyBtc: number;
  maxQtyBtc: number;
  initialMarginFraction: number;
  maintenanceMarginFraction: number;
  priceSignificantDigits: 5;
  amountDecimals: 8;
  observedAt: string;
  source: 'BITFINEX_PUBLIC_FUTURES_CONFIG';
};

export function parseBitfinexBtcPerpConstraints(payload: unknown, observedAt = new Date().toISOString()): BitfinexFuturesPairConstraints | null {
  const rows = Array.isArray(payload) && Array.isArray(payload[0]) ? payload[0] : null;
  const match = rows?.find((row) => Array.isArray(row) && row[0] === 'BTCF0:USTF0');
  const detail = Array.isArray(match) && Array.isArray(match[1]) ? match[1] : null;
  if (!detail) return null;
  const minQtyBtc = Number(detail[3]);
  const maxQtyBtc = Number(detail[4]);
  const initialMarginFraction = Number(detail[8]);
  const maintenanceMarginFraction = Number(detail[9]);
  if (!(minQtyBtc > 0) || !(maxQtyBtc >= minQtyBtc) || !(initialMarginFraction > 0) || !(maintenanceMarginFraction > 0)) return null;
  return {
    symbol: BITFINEX_BTC_PERP_SYMBOL, minQtyBtc, maxQtyBtc,
    initialMarginFraction, maintenanceMarginFraction,
    priceSignificantDigits: 5, amountDecimals: 8,
    observedAt, source: 'BITFINEX_PUBLIC_FUTURES_CONFIG',
  };
}

export type BitfinexWalletRow = {
  walletType: string;
  currency: string;
  balance: number;
  available: number;
};

export type BitfinexWalletSnapshot = {
  derivativesUsd: number;
  derivativesTotalUsd: number;
  exchangeUsd: number;
  fundingUsd: number;
  totalStableUsd: number;
};

export type BitfinexPositionDetail = {
  symbol: string;
  amount: number;
  basePrice: number;
  pnlUsd: number;
  pnlPct: number;
  direction: 'LONG' | 'SHORT';
  // AMEND4 E5 (sha 7073ed25, L24-26): position row index 8 = PRICE_LIQ. null when Bitfinex
  // omits it or sends a non-positive / non-numeric value (the Fly guardian then fails closed).
  liqPrice?: number | null;
};

export type BitfinexPositionCloseLedgerRow = {
  ledgerId: string;
  closedAt: Date;
  pnlUsd: number;
  description: string;
};

export type BitfinexLiveAccountMetrics = {
  derivativesAvailableUsd: number;
  derivativesTotalUsd: number;
  exchangeUsd: number;
  fundingUsd: number;
  unrealizedPnlUsd: number;
  equityUsd: number;
  realizedPnlUsd: number;
  tradingFeesUsd: number;
  fundingFeesUsd: number;
  sessionPnlUsd: number;
  openPosition: BitfinexPositionDetail | null;
  fundsInWrongWallet: boolean;
};

/**
 * Session P&L = exchange realized + unrealized. R7 (HM #842, 24 Oct 2026): ledger trading/funding fees are
 * reported as separate metrics (BitfinexLiveAccountMetrics.tradingFeesUsd / fundingFeesUsd) and are NEVER
 * folded into P&L.
 */
export function calculateBitfinexSessionPnl(input: {
  realizedPnlUsd: number;
  unrealizedPnlUsd: number;
}): number {
  return Number((input.realizedPnlUsd + input.unrealizedPnlUsd).toFixed(2));
}

/**
 * Bitfinex v2 `auth/r/ledgers/hist` row layout (HM #842, 24 Oct 2026):
 * [0 ID, 1 CURRENCY, 2 WALLET, 3 MTS, 4 null, 5 AMOUNT, 6 BALANCE, 7 null, 8 DESCRIPTION].
 * The previous readers used [4]/[7] (placeholders, always null) so every amount read as 0.
 */
export const BITFINEX_LEDGER_ROW = { ID: 0, CURRENCY: 1, WALLET: 2, MTS: 3, AMOUNT: 5, BALANCE: 6, DESCRIPTION: 8 } as const;

export type BitfinexLedgerRow = { id: unknown; mts: number | null; amount: number; description: string };

/**
 * One ledger row, or null when it is UNVERIFIED: not an array of the documented length, AMOUNT null / non-numeric /
 * non-finite, or DESCRIPTION not a string. Fail closed: an unverified row is never read as amount 0.
 */
export function parseBitfinexLedgerRow(row: unknown): BitfinexLedgerRow | null {
  if (!Array.isArray(row) || row.length <= BITFINEX_LEDGER_ROW.DESCRIPTION) return null;
  const rawAmount = row[BITFINEX_LEDGER_ROW.AMOUNT];
  if (rawAmount == null || (typeof rawAmount !== 'number' && typeof rawAmount !== 'string')) return null;
  if (typeof rawAmount === 'string' && !rawAmount.trim()) return null;
  const amount = Number(rawAmount);
  if (!Number.isFinite(amount)) return null;
  const description = row[BITFINEX_LEDGER_ROW.DESCRIPTION];
  if (typeof description !== 'string') return null;
  const mts = Number(row[BITFINEX_LEDGER_ROW.MTS]);
  return { id: row[BITFINEX_LEDGER_ROW.ID], mts: Number.isFinite(mts) && mts > 0 ? mts : null, amount, description };
}

/** A ledger read containing an unverified row: the position-close set is incomplete, so callers must not use it. */
export class BitfinexLedgerUnverifiedError extends Error {
  constructor(readonly unverifiedRows: number) {
    super(`BITFINEX_LEDGER_UNVERIFIED: ${unverifiedRows} ledger row(s) with null/non-finite AMOUNT or non-string DESCRIPTION`);
    this.name = 'BitfinexLedgerUnverifiedError';
  }
}

export type EnsureDerivativesResult = {
  derivativesUsd: number;
  transferredUsd: number;
  message?: string;
};

function parseWalletRow(row: unknown): BitfinexWalletRow | null {
  if (!Array.isArray(row) || row.length < 5) return null;
  const currency = String(row[1] ?? '').toUpperCase();
  if (!STABLE_CURRENCIES.has(currency)) return null;
  return {
    walletType: String(row[0] ?? '').toLowerCase(),
    currency,
    balance: Number(row[2] ?? 0),
    available: Number(row[4] ?? 0),
  };
}

function stableAvailable(rows: BitfinexWalletRow[], walletType: string): number {
  let best = 0;
  for (const row of rows) {
    if (row.walletType !== walletType) continue;
    if (row.available > best) best = row.available;
  }
  return best;
}

function stableTotalBalance(rows: BitfinexWalletRow[], walletType: string): number {
  let best = 0;
  for (const row of rows) {
    if (row.walletType !== walletType) continue;
    if (row.balance > best) best = row.balance;
  }
  return best;
}

function signBitfinex(secret: string, payload: string): string {
  return createHmac('sha384', secret).update(payload).digest('hex');
}

type NonceLane = {
  lastNonce: bigint;
  running: boolean;
  /** LIVE-FIX-1487: protective stop submits jump ahead of every other call. */
  stops?: Array<QueuedAuthRequest<unknown>>;
  mutations: Array<QueuedAuthRequest<unknown>>;
  reads: Array<QueuedAuthRequest<unknown>>;
};

type QueuedAuthRequest<T> = {
  run: () => Promise<T>;
  resolve: (value: T) => void;
  reject: (reason: unknown) => void;
};

/**
 * Per API key: monotonic nonce + serialized auth calls (parallel ticks caused
 * nonce: small). Queued order mutations take priority over queued reads: a
 * reconciliation poll must never delay a new entry, stop, cancel, or exit.
 *
 * Bitfinex requires strictly increasing nonces. Production executor historically
 * used ms×1000; a one-off laptop probe used ms×10000 and raised the exchange
 * high-water mark — executor must stay on the larger scale (or above it).
 */
export const BITFINEX_NONCE_MS_SCALE = 10_000n;

const nonceLanes = new Map<string, NonceLane>();

function nonceLaneFor(apiKey: string): NonceLane {
  let lane = nonceLanes.get(apiKey);
  if (!lane) {
    lane = { lastNonce: 0n, running: false, mutations: [], reads: [] };
    nonceLanes.set(apiKey, lane);
  }
  return lane;
}

function isBitfinexOrderMutation(apiPath: string): boolean {
  return /^v2\/auth\/w\/order\//.test(apiPath);
}

function drainNonceLane(lane: NonceLane): void {
  if (lane.running) return;
  const next = lane.stops?.shift() ?? lane.mutations.shift() ?? lane.reads.shift();
  if (!next) return;
  lane.running = true;
  void Promise.resolve()
    .then(next.run)
    .then(next.resolve, next.reject)
    .finally(() => {
      lane.running = false;
      drainNonceLane(lane);
    });
}

/** Protective (reduce-only STOP) submit: highest priority on the nonce lane. */
export function isBitfinexProtectiveStopSubmit(apiPath: string, body: Record<string, unknown>): boolean {
  return apiPath === 'v2/auth/w/order/submit'
    && /STOP/i.test(String(body?.type ?? ''))
    && ((Number(body?.flags ?? 0) & 1024) !== 0);
}

function enqueueNonceLane<T>(
  lane: NonceLane,
  mutation: boolean | 'stop',
  run: () => Promise<T>,
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const queued: QueuedAuthRequest<T> = { run, resolve, reject };
    if (mutation === 'stop') (lane.stops ??= []).push(queued as QueuedAuthRequest<unknown>);
    else if (mutation) lane.mutations.push(queued as QueuedAuthRequest<unknown>);
    else lane.reads.push(queued as QueuedAuthRequest<unknown>);
    drainNonceLane(lane);
  });
}

/** @internal Exported for unit tests — do not call outside the nonce lane. */
export function allocMonotonicNonce(lane: NonceLane, nowMs = Date.now()): string {
  const floor = BigInt(nowMs) * BITFINEX_NONCE_MS_SCALE;
  const next = lane.lastNonce >= floor ? lane.lastNonce + 1n : floor;
  lane.lastNonce = next;
  return next.toString();
}

/** Shared by REST and authenticated WebSocket sessions for one API key. */
export function allocateBitfinexAuthNonce(apiKey: string): string {
  return allocMonotonicNonce(nonceLaneFor(apiKey));
}

/** @internal Test helper — clears per-process nonce lanes between spec cases. */
export function resetBitfinexNonceLanesForTests(): void {
  nonceLanes.clear();
}

async function bitfinexAuthPostOnce<T>(
  creds: ExchangeCredentials,
  apiPath: string,
  body: Record<string, unknown>,
  timeoutMs: number,
  nonce: string,
): Promise<T> {
  // C6 safety guard: a testnet-flagged account must NEVER place/cancel real orders on
  // the production Bitfinex API. Read paths (wallets/positions) are allowed so validation
  // still works; only order-mutation is blocked.
  if (creds.testnet === true && /\/w\/order\//.test(apiPath)) {
    throw new Error(
      'Bitfinex testnet credentials routed to production order API — refusing to place/cancel live orders',
    );
  }
  const bodyStr = JSON.stringify(body);
  const payload = `/api/${apiPath}${nonce}${bodyStr}`;
  const signature = signBitfinex(creds.apiSecret, payload);

  const res = await exchangeFetch(
    `https://api.bitfinex.com/${apiPath}`,
    {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'bfx-apikey': creds.apiKey,
        'bfx-nonce': nonce,
        'bfx-signature': signature,
      },
      body: bodyStr,
    },
    timeoutMs,
  );

  const text = await res.text();
  let parsed: unknown;
  try {
    parsed = text ? JSON.parse(text) : null;
  } catch {
    parsed = text;
  }

  if (!res.ok) {
    const msg =
      Array.isArray(parsed) && typeof parsed[2] === 'string'
        ? parsed[2]
        : typeof parsed === 'object' && parsed && 'message' in parsed
          ? String((parsed as { message: string }).message)
          : text.slice(0, 200) || `HTTP ${res.status}`;
    throw new Error(`Bitfinex ${apiPath}: ${msg}`);
  }

  return parsed as T;
}

/** LIVE-FIX-1505: pure side check. Sell stop must be below the bid; buy stop above the ask. */
export function stopSideViolation(amount: number, stopPrice: number, bid: number, ask: number): string | null {
  if (!(stopPrice > 0) || !(bid > 0) || !(ask > 0) || !Number.isFinite(amount) || amount === 0) return null;
  if (amount < 0 && stopPrice >= bid) return `SELL_STOP_AT_OR_ABOVE_BID stop=${stopPrice} bid=${bid}`;
  if (amount > 0 && stopPrice <= ask) return `BUY_STOP_AT_OR_BELOW_ASK stop=${stopPrice} ask=${ask}`;
  return null;
}

export const stopSideRefusals: Array<{ at_ms: number; symbol: string; amount: number; price: number; reason: string }> = [];
let stopSideTickerFetch: (symbol: string) => Promise<{ bid: number; ask: number } | null> = async (symbol) => {
  const res = await fetch(`https://api-pub.bitfinex.com/v2/ticker/${encodeURIComponent(symbol)}`, { signal: AbortSignal.timeout(1500) });
  if (!res.ok) return null;
  const t = (await res.json()) as unknown[];
  return Array.isArray(t) ? { bid: Number(t[0]), ask: Number(t[2]) } : null;
};
export function setStopSideTickerFetchForTests(fn: typeof stopSideTickerFetch): void { stopSideTickerFetch = fn; }

/** Throws STOP_SIDE_REFUSED for a wrong-side protective stop. Ticker failure never blocks the stop. */
export async function assertProtectiveStopSide(apiPath: string, body: Record<string, unknown>): Promise<void> {
  if (!isBitfinexProtectiveStopSubmit(apiPath, body)) return;
  const symbol = String(body.symbol ?? '');
  const amount = Number(body.amount);
  const price = Number(body.price);
  let quote: { bid: number; ask: number } | null = null;
  try { quote = await stopSideTickerFetch(symbol); } catch { quote = null; }
  if (!quote) {
    console.warn(`[STOP-SIDE-GUARD] ticker unavailable symbol=${symbol}; stop sent unchecked`);
    return;
  }
  const reason = stopSideViolation(amount, price, quote.bid, quote.ask);
  if (!reason) return;
  stopSideRefusals.push({ at_ms: Date.now(), symbol, amount, price, reason });
  if (stopSideRefusals.length > 100) stopSideRefusals.shift();
  console.error(`[STOP-SIDE-GUARD] REFUSED ${reason} symbol=${symbol} amount=${amount} cid=${String(body.cid ?? '')}`);
  throw new Error(`STOP_SIDE_REFUSED: ${reason}`);
}

/**
 * #852 E6 budget: rolling 60 s count of authenticated requests actually sent,
 * per endpoint path, process-wide. Bitfinex rate-limits REST per IP per
 * endpoint (90 req/min for v2/auth/r/orders, positions, wallets; a breach
 * blocks the IP for 60 s), and every relay account shares this process's IP.
 */
export const BITFINEX_AUTH_ENDPOINT_LIMIT_PER_MIN = 90;
const authCallLog = new Map<string, number[]>();

/**
 * HM R6 nit 2: the budget key of an authenticated path. Per-order paths (v2/auth/r/order/{sym}:{id}/trades) are ONE
 * endpoint for Bitfinex's per-endpoint rate limit, so they share one key (else every order id had its own 90/min).
 */
export function bitfinexAuthBudgetKey(apiPath: string): string {
  return apiPath.replace(/^(\/?v2\/auth\/r\/order)\/[^/]+\/trades$/, '$1/trades');
}

export function recordBitfinexAuthCall(apiPath: string, nowMs = Date.now()): void {
  const key = bitfinexAuthBudgetKey(apiPath);
  const log = authCallLog.get(key) ?? [];
  log.push(nowMs);
  while (log.length && nowMs - log[0] >= 60_000) log.shift();
  authCallLog.set(key, log);
}

/** Authenticated requests sent per endpoint in the last 60 s (process-wide, i.e. per relay IP). */
export function bitfinexAuthCallsLastMinute(nowMs = Date.now()): Record<string, number> {
  const out: Record<string, number> = {};
  for (const [path, log] of authCallLog) {
    const n = log.filter((t) => nowMs - t < 60_000).length;
    if (n > 0) out[path] = n;
  }
  return out;
}

/**
 * #852 E6 + RK binding ruling (3): ONE shared, process-wide (= per relay IP) read budget per authenticated endpoint,
 * covering the exchange mirror, the money tick, force-flat / position-flatten and any other relay read (e.g. #854's
 * positions read). A read is admitted only while the endpoint's sent count in the last 60 s is below its class cap:
 *   - routine  (exchange mirror, monitoring reads such as #854): < BITFINEX_AUTH_ROUTINE_CAP_PER_MIN (54)
 *   - tick     (default: every unmarked read, i.e. the money tick / order management): < BITFINEX_AUTH_TICK_CAP_PER_MIN (72)
 *   - critical (force-flat / position-flatten read-backs): < BITFINEX_AUTH_CRITICAL_CAP_PER_MIN (89)
 * so the total can never reach Bitfinex's 90/min (a breach blocks the IP for 60 s: stops unplaceable), the tick keeps
 * 18 reads/min of headroom above the mirror and the flatten path 17 more above the tick (never starved by a fast mirror).
 * Writes (v2/auth/w/: order submit / cancel / stop placement / flatten) are NEVER refused by this budget.
 */
export type BitfinexAuthReadClass = 'critical' | 'tick' | 'routine';
export const BITFINEX_AUTH_CRITICAL_CAP_PER_MIN = BITFINEX_AUTH_ENDPOINT_LIMIT_PER_MIN - 1;
export const BITFINEX_AUTH_TICK_CAP_PER_MIN = Math.floor(BITFINEX_AUTH_ENDPOINT_LIMIT_PER_MIN * 0.8);
export const BITFINEX_AUTH_ROUTINE_CAP_PER_MIN = Math.floor(BITFINEX_AUTH_ENDPOINT_LIMIT_PER_MIN * 0.6);
const BITFINEX_AUTH_CLASS_CAP: Record<BitfinexAuthReadClass, number> = {
  critical: BITFINEX_AUTH_CRITICAL_CAP_PER_MIN,
  tick: BITFINEX_AUTH_TICK_CAP_PER_MIN,
  routine: BITFINEX_AUTH_ROUTINE_CAP_PER_MIN,
};
const authReadClass = new AsyncLocalStorage<BitfinexAuthReadClass>();
/** Run ``fn`` with every Bitfinex authenticated read it issues charged to ``cls`` (innermost scope wins). */
export function withBitfinexReadClass<T>(cls: BitfinexAuthReadClass, fn: () => T): T {
  return authReadClass.run(cls, fn);
}
/** Force-flat / position-flatten read-backs: admitted up to the critical cap (reserved headroom). */
export function withBitfinexCriticalReads<T>(fn: () => T): T {
  return withBitfinexReadClass('critical', fn);
}
export function currentBitfinexReadClass(): BitfinexAuthReadClass {
  return authReadClass.getStore() ?? 'tick';
}
export function isBitfinexAuthWrite(apiPath: string): boolean {
  return /(^|\/)auth\/w\//.test(apiPath);
}
/** Pure-ish admission decision of the shared budget (reads the process-wide 60 s log). */
export function bitfinexAuthAdmission(
  apiPath: string,
  cls: BitfinexAuthReadClass,
  nowMs = Date.now(),
): { ok: true } | { ok: false; reason: string } {
  if (isBitfinexAuthWrite(apiPath)) return { ok: true };
  const cap = BITFINEX_AUTH_CLASS_CAP[cls] ?? BITFINEX_AUTH_TICK_CAP_PER_MIN;
  const n = (authCallLog.get(bitfinexAuthBudgetKey(apiPath)) ?? []).filter((t) => nowMs - t < 60_000).length;   // HM R6 nit 2
  if (n < cap) return { ok: true };
  return {
    ok: false,
    reason: `BITFINEX_AUTH_BUDGET: ${apiPath} ${n} sent in 60s >= ${cls} cap ${cap}/min (Bitfinex limit ${BITFINEX_AUTH_ENDPOINT_LIMIT_PER_MIN})`,
  };
}

/** @internal tests only */
export function resetBitfinexAuthCallLog(): void {
  authCallLog.clear();
  recentAuthReads.clear();
}

/**
 * #852 E6: the last successful orders / positions read per API key, stamped
 * with the time that request was ENQUEUED (<= sent: a conservative evidence age). The
 * read-only exchange mirror reuses a recent money-tick read instead of
 * issuing its own, so the fast stop-evidence cadence adds no Bitfinex load
 * while the relay is ticking. Values are parsed payloads only (no secrets).
 */
type RecentAuthRead = { sentAtMs: number; value: unknown };
const recentAuthReads = new Map<string, RecentAuthRead>();
function recentReadKey(apiKey: string, kind: 'orders' | 'positions', symbol: string): string {
  return `${createHmac('sha256', 'recent-read').update(apiKey).digest('hex').slice(0, 16)}:${kind}:${symbol}`;
}
function rememberAuthRead(apiKey: string, kind: 'orders' | 'positions', symbol: string, sentAtMs: number, value: unknown): void {
  const key = recentReadKey(apiKey, kind, symbol);
  const prev = recentAuthReads.get(key);
  if (!prev || prev.sentAtMs <= sentAtMs) recentAuthReads.set(key, { sentAtMs, value });
}
/** A successful read of ``kind`` sent at most ``maxAgeMs`` ago, or null. */
export function recentBitfinexAuthRead<T>(
  apiKey: string,
  kind: 'orders' | 'positions',
  maxAgeMs: number,
  nowMs = Date.now(),
  symbol = BITFINEX_BTC_PERP_SYMBOL,
): { sentAtMs: number; value: T } | null {
  const hit = recentAuthReads.get(recentReadKey(apiKey, kind, symbol));
  if (!hit || nowMs - hit.sentAtMs > maxAgeMs || hit.sentAtMs > nowMs) return null;
  return { sentAtMs: hit.sentAtMs, value: hit.value as T };
}

export async function bitfinexAuthPost<T = unknown>(
  creds: ExchangeCredentials,
  apiPath: string,
  body: Record<string, unknown> = {},
  timeoutMs = 12_000,
): Promise<T> {
  const readClass = currentBitfinexReadClass();   // captured before any await (#852 E6 shared budget)
  await assertProtectiveStopSide(apiPath, body);
  const lane = nonceLaneFor(creds.apiKey);
  const enqueuedAtMs = Date.now();
  const deadlineAtMs = enqueuedAtMs + Math.max(1, timeoutMs);
  let expiredBeforeStart = false;
  const run = async (): Promise<T> => {
    // The authenticated API is serialized per key because Bitfinex requires a
    // strictly increasing nonce.  A network timeout only bounded the request
    // *after* it reached the head of this lane; during exchange degradation a
    // safety read could otherwise wait behind an arbitrary queue for longer
    // than the executor watchdog.  Refuse work whose total queue + HTTP budget
    // has elapsed.  In particular, a timed-out queued mutation is never sent
    // later as an abandoned side effect.
    if (expiredBeforeStart || Date.now() >= deadlineAtMs) {
      throw new Error(
        `Bitfinex ${apiPath}: authenticated request queue deadline exceeded after ${timeoutMs}ms`,
      );
    }
    for (let attempt = 0; attempt < 4; attempt++) {
      const remainingMs = deadlineAtMs - Date.now();
      if (remainingMs <= 0) {
        throw new Error(
          `Bitfinex ${apiPath}: authenticated request deadline exceeded after ${timeoutMs}ms`,
        );
      }
      // #852 E6 / RK (3): one shared read budget; writes are never refused, reads by class (see bitfinexAuthAdmission)
      const admission = bitfinexAuthAdmission(apiPath, readClass);
      if (!admission.ok) throw new Error(admission.reason);
      const nonce = allocateBitfinexAuthNonce(creds.apiKey);
      recordBitfinexAuthCall(apiPath);
      try {
        return await bitfinexAuthPostOnce<T>(
          creds,
          apiPath,
          body,
          Math.max(1, remainingMs),
          nonce,
        );
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        if (attempt < 3 && /nonce:\s*small/i.test(msg)) {
          const bumped = BigInt(Date.now()) * BITFINEX_NONCE_MS_SCALE;
          if (lane.lastNonce < bumped) lane.lastNonce = bumped;
          await new Promise((r) => setTimeout(r, 20 * (attempt + 1)));
          continue;
        }
        throw err;
      }
    }
    throw new Error(`Bitfinex ${apiPath}: nonce retry exhausted`);
  };

  const result = enqueueNonceLane(
    lane,
    isBitfinexProtectiveStopSubmit(apiPath, body) ? 'stop' : isBitfinexOrderMutation(apiPath),
    run,
  );
  let expiryTimer: ReturnType<typeof setTimeout> | undefined;
  const deadline = new Promise<never>((_resolve, reject) => {
    expiryTimer = setTimeout(() => {
      expiredBeforeStart = true;
      reject(
        new Error(
          `Bitfinex ${apiPath}: authenticated request total deadline exceeded after ${timeoutMs}ms`,
        ),
      );
    }, Math.max(1, deadlineAtMs - Date.now()));
    expiryTimer.unref?.();
  });
  try {
    return await Promise.race([result, deadline]);
  } finally {
    if (expiryTimer) clearTimeout(expiryTimer);
  }
}

export async function bitfinexPublicGet<T = unknown>(apiPath: string): Promise<T> {
  const res = await exchangeFetch(`https://api-pub.bitfinex.com/${apiPath}`, {}, 10_000);
  if (!res.ok) throw new Error(`Bitfinex public ${apiPath}: HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

/** Parse order id from /auth/w/order/submit notification payload. */
export type BitfinexSubmitFill = { status: string; filledQty: number; remainingQty: number; avgPrice: number };
const submitFills = new Map<number, BitfinexSubmitFill>();

/** Order state from a v2/auth/w/order/submit response: [.., [[ID,GID,CID,SYM,MTS_C,MTS_U,AMOUNT,AMOUNT_ORIG,TYPE,TYPE_PREV,MTS_TIF,_,FLAGS,STATUS,_,_,PRICE,PRICE_AVG,..]], ..]. */
export function parseBitfinexSubmitFill(response: unknown): BitfinexSubmitFill | null {
  if (!Array.isArray(response) || !Array.isArray(response[4])) return null;
  const o = (response[4] as unknown[])[0];
  if (!Array.isArray(o)) return null;
  const remaining = Number(o[6]);
  const orig = Number(o[7]);
  const status = typeof o[13] === 'string' ? o[13] : '';
  const avg = Number(o[17]);
  if (!Number.isFinite(remaining) || !Number.isFinite(orig) || orig === 0) return null;
  return {
    status,
    filledQty: Math.abs(orig) - Math.abs(remaining),
    remainingQty: Math.abs(remaining),
    avgPrice: Number.isFinite(avg) ? avg : 0,
  };
}

export function parseBitfinexOrderId(response: unknown): number | null {
  if (!Array.isArray(response)) return null;
  // Success: [MTS, 'on-req', null, null, [order], null, 'SUCCESS', ...]
  const orderPayload = response[4];
  if (Array.isArray(orderPayload) && orderPayload.length > 0) {
    const inner = orderPayload[0];
    if (Array.isArray(inner) && typeof inner[0] === 'number') return inner[0];
    if (typeof inner === 'number') return inner;
  }
  if (response[6] === 'ERROR') {
    throw new Error(String(response[7] ?? 'Bitfinex order rejected'));
  }
  return null;
}

/** One execution of an order — parsed from Bitfinex v2 auth order-trades rows. */
export type BitfinexOrderTrade = {
  /** Authenticated Bitfinex execution ID (ID, index 0). */
  id: number;
  /** Authenticated parent order ID (ORDER_ID, index 3). */
  orderId: number;
  /** Real execution price (EXEC_PRICE, index 5). */
  execPrice: number;
  /** Signed executed amount in BTC (EXEC_AMOUNT, index 4). */
  execAmount: number;
  /** Fee (signed, FEE index 9 — negative = charged). */
  fee: number;
  /** Execution timestamp ms (MTS_CREATE, index 2). */
  mtsCreate: number;
  feeCurrency: string | null;
};

/** Bitfinex v2 trade array: [ID, PAIR, MTS_CREATE, ORDER_ID, EXEC_AMOUNT, EXEC_PRICE,
 *  ORDER_TYPE, ORDER_PRICE, MAKER, FEE, FEE_CURRENCY]. */
export function parseOrderTrade(row: unknown): BitfinexOrderTrade | null {
  if (!Array.isArray(row) || row.length < 11) return null;
  const execAmount = Number(row[4] ?? 0);
  const execPrice = Number(row[5] ?? 0);
  const id = Number(row[0]);
  const orderId = Number(row[3]);
  if (!Number.isSafeInteger(id) || id <= 0 || !Number.isSafeInteger(orderId) || orderId <= 0 || !Number.isFinite(execPrice) || execPrice <= 0) return null;
  return {
    id,
    orderId,
    execPrice,
    execAmount: Number.isFinite(execAmount) ? execAmount : 0,
    fee: Number(row[9] ?? 0) || 0,
    mtsCreate: Number(row[2] ?? 0) || 0,
    feeCurrency: typeof row[10] === 'string' && row[10].trim() ? row[10].trim() : null,
  };
}

export type BitfinexActiveOrder = {
  id: number;
  symbol: string;
  amount: number;
  amountOrig: number;
  price: number;
  status: string;
  orderType: string;
  /** Raw Bitfinex order flags (v2 order row index 12). */
  flags?: number;
  /**
   * Bitfinex v2 client order id (`cid`) — int32 positive. Surfaces when the
   * order was submitted with `cid` (Phase 2 placeEntry / replaceRestingLimit
   * always set one). Used by the Phase 4 reconcile-adopt match logic to tie
   * an unmanaged resting order back to a terminal participant's
   * ExecutionPayload.clientOrderId. `0` / absent means the exchange did not
   * record a cid (manual order or pre-Phase-2).
   */
  cid?: number;
  /** Order creation timestamp (ms since epoch) — Bitfinex v2 index [4]. */
  createdAtMs?: number;
};

/**
 * Bitfinex normalizes order prices to five significant digits.  Normalize
 * before submission (and when authenticating the returned active order) so a
 * request such as 63066.88 is durably identified as the venue order 63066,
 * rather than being mistaken for a different stop and submitted repeatedly.
 */
export function normalizeBitfinexOrderPrice(price: number): number {
  if (!Number.isFinite(price) || price <= 0) return price;
  const quantum = 10 ** (Math.floor(Math.log10(Math.abs(price))) - 4);
  const scaled = price / quantum;
  // The authenticated Bitfinex order book reports the venue-truncated value.
  return Math.trunc(scaled + Number.EPSILON) * quantum;
}

/** Bitfinex order array indices (REST). */
export function parseActiveOrder(row: unknown[]): BitfinexActiveOrder | null {
  if (!Array.isArray(row) || row.length < 17) return null;
  const id = Number(row[0]);
  const symbol = typeof row[3] === 'string' ? row[3] : '';
  const amount = Number(row[6]);
  const amountOrig = Number(row[7]);
  const orderType = String(row[8] ?? '');
  const flags = Number(row[12] ?? 0);
  const price = Number(row[16] ?? row[14] ?? 0);
  const status = String(row[13] ?? '');
  if (
    !Number.isFinite(id)
    || id <= 0
    || !symbol
    || !Number.isFinite(amount)
    || !Number.isFinite(amountOrig)
    || amountOrig === 0
    || !orderType
    || !Number.isFinite(flags)
    || !Number.isFinite(price)
    || price < 0
    || !status
  ) {
    return null;
  }
  const cidRaw = row[2];
  const createdAtRaw = row[4];
  const cid =
    cidRaw != null && Number.isFinite(Number(cidRaw)) && Number(cidRaw) !== 0
      ? Number(cidRaw) & 0x7fffffff
      : undefined;
  const createdAtMs =
    createdAtRaw != null && Number.isFinite(Number(createdAtRaw)) && Number(createdAtRaw) > 0
      ? Number(createdAtRaw)
      : undefined;
  return {
    id,
    symbol,
    amount,
    amountOrig,
    orderType,
    flags,
    price,
    status,
    ...(cid != null ? { cid } : {}),
    ...(createdAtMs != null ? { createdAtMs } : {}),
  };
}

export function parseActiveOrdersPayload(
  payload: unknown,
  symbol = BITFINEX_BTC_PERP_SYMBOL,
): BitfinexActiveOrder[] {
  if (!Array.isArray(payload)) {
    throw new Error('Bitfinex active-orders response is not an array');
  }
  const parsed: BitfinexActiveOrder[] = [];
  for (const [index, row] of payload.entries()) {
    const order = parseActiveOrder(row as unknown[]);
    if (!order) {
      throw new Error(`Bitfinex active-orders row ${index} is malformed`);
    }
    if (order.symbol === symbol) parsed.push(order);
  }
  return parsed;
}

export type BitfinexOrderHistoryEvidence = {
  id: number;
  status: string;
  terminal: boolean;
  filledQty: number;
};

export function parseOrderHistoryEvidence(
  payload: unknown,
  orderId: number,
): BitfinexOrderHistoryEvidence | null {
  if (!Array.isArray(payload)) throw new Error('Bitfinex order-history response is not an array');
  for (const [index, row] of payload.entries()) {
    const order = parseActiveOrder(row as unknown[]);
    if (!order) throw new Error(`Bitfinex order-history row ${index} is malformed`);
    if (order.id !== orderId) continue;
    const status = order.status.toUpperCase();
    return {
      id: order.id,
      status: order.status,
      terminal: status.includes('CANCELED') || status.includes('EXECUTED'),
      filledQty: Math.max(0, Math.abs(order.amountOrig) - Math.abs(order.amount)),
    };
  }
  return null;
}

/** AMEND4 E5: Bitfinex v2 position row index 8 is PRICE_LIQ; anything unusable is null (never 0). */
export function parsePositionLiqPrice(row: readonly unknown[]): number | null {
  if (row.length <= 8 || row[8] == null) return null;
  const liq = Number(row[8]);
  return Number.isFinite(liq) && liq > 0 ? liq : null;
}

export function parseOpenPositionPayload(
  payload: unknown,
  symbol = BITFINEX_BTC_PERP_SYMBOL,
): BitfinexPositionDetail | null {
  if (!Array.isArray(payload)) {
    throw new Error('Bitfinex positions response is not an array');
  }
  let matched: BitfinexPositionDetail | null = null;
  for (const [index, row] of payload.entries()) {
    if (!Array.isArray(row)) {
      throw new Error(`Bitfinex positions row ${index} is malformed`);
    }
    if (typeof row[0] !== 'string' || !row[0]) {
      throw new Error(`Bitfinex positions row ${index} has no symbol`);
    }
    if (row[0] !== symbol) continue;
    if (matched) {
      throw new Error(`Bitfinex returned duplicate ${symbol} position rows`);
    }
    if (row.length < 8) {
      throw new Error(`Bitfinex ${symbol} position row is incomplete`);
    }
    const amount = Number(row[2]);
    const basePrice = Number(row[3]);
    const pnlUsd = Number(row[6]);
    const pnlPct = Number(row[7]);
    if (
      !Number.isFinite(amount)
      || !Number.isFinite(basePrice)
      || !Number.isFinite(pnlUsd)
      || !Number.isFinite(pnlPct)
    ) {
      throw new Error(`Bitfinex ${symbol} position row has invalid numeric fields`);
    }
    if (amount === 0) {
      throw new Error(`Bitfinex ${symbol} returned a zero-amount position row`);
    }
    const amountSats = btcToSats(amount);
    if (
      amountSats === 0
      || Math.abs(amount - amountSats / 100_000_000) > 1e-12
      || basePrice <= 0
    ) {
      throw new Error(`Bitfinex ${symbol} position amount or base price is invalid`);
    }
    matched = {
      symbol,
      amount,
      basePrice,
      pnlUsd,
      pnlPct,
      direction: amount > 0 ? 'LONG' : 'SHORT',
      liqPrice: parsePositionLiqPrice(row),
    };
  }
  return matched;
}

export class BitfinexTradingClient {
  async getBtcPerpVenueConstraints(): Promise<BitfinexFuturesPairConstraints> {
    const payload = await bitfinexPublicGet<unknown>('v2/conf/pub:info:pair:futures');
    const parsed = parseBitfinexBtcPerpConstraints(payload);
    if (!parsed) throw new Error('Bitfinex BTC perpetual constraints missing or malformed');
    return parsed;
  }
  async validateCredentials(creds: ExchangeCredentials): Promise<{ ok: boolean; message: string }> {
    try {
      await bitfinexAuthPost(creds, 'v2/auth/r/wallets');
      return { ok: true, message: 'Bitfinex API connected' };
    } catch (err) {
      return { ok: false, message: exchangeErrorMessage(err, 'Bitfinex') };
    }
  }

  async listWallets(creds: ExchangeCredentials): Promise<BitfinexWalletRow[]> {
    const wallets = await bitfinexAuthPost<unknown[][]>(creds, 'v2/auth/r/wallets');
    if (!Array.isArray(wallets)) return [];
    return wallets.map(parseWalletRow).filter((row): row is BitfinexWalletRow => row != null);
  }

  async getWalletSnapshot(creds: ExchangeCredentials): Promise<BitfinexWalletSnapshot> {
    const rows = await this.listWallets(creds);
    const derivativesUsd = stableAvailable(rows, 'margin');
    const derivativesTotalUsd = stableTotalBalance(rows, 'margin');
    const exchangeUsd = stableAvailable(rows, 'exchange');
    const fundingUsd = stableAvailable(rows, 'funding');
    return {
      derivativesUsd,
      derivativesTotalUsd,
      exchangeUsd,
      fundingUsd,
      totalStableUsd: derivativesTotalUsd + exchangeUsd + fundingUsd,
    };
  }

  /**
   * BTC perp collateral — Bitfinex maps the UI "Derivatives" wallet to API wallet type `margin` (USTF0).
   */
  async getDerivativesAvailableUsd(creds: ExchangeCredentials): Promise<number> {
    const rows = await this.listWallets(creds);
    return stableAvailable(rows, 'margin');
  }

  /** @deprecated Prefer getDerivativesAvailableUsd — perp copy uses Derivatives (margin) only. */
  async getAvailableUsd(creds: ExchangeCredentials): Promise<number> {
    return this.getDerivativesAvailableUsd(creds);
  }

  private pickTransferSource(
    rows: BitfinexWalletRow[],
    walletType: 'exchange' | 'funding',
    minAmount: number,
  ): BitfinexWalletRow | null {
    const candidates = rows
      .filter((row) => row.walletType === walletType && row.available >= minAmount)
      .sort((a, b) => b.available - a.available);
    return candidates[0] ?? null;
  }

  private async transferStableToDerivatives(
    creds: ExchangeCredentials,
    from: 'exchange' | 'funding',
    amount: number,
    rows: BitfinexWalletRow[],
  ): Promise<number> {
    const source = this.pickTransferSource(rows, from, 0.01);
    if (!source || source.available < 0.01) return 0;

    const transferAmount = Math.min(amount, source.available);
    const currency = source.currency === 'USD' ? 'USD' : 'UST';
    const currencyTo = currency === 'USD' ? 'USD' : 'USTF0';

    await bitfinexAuthPost(creds, 'v2/auth/w/transfer', {
      from,
      to: 'margin',
      currency,
      currency_to: currencyTo,
      amount: transferAmount.toFixed(4),
    });
    return transferAmount;
  }

  /** Top up Derivatives (margin/USTF0) from Exchange or Funding before copy entries. */
  async ensureDerivativesMargin(
    creds: ExchangeCredentials,
    minUsd: number,
  ): Promise<EnsureDerivativesResult> {
    const target = Math.max(minUsd, 1);
    let rows = await this.listWallets(creds);
    let derivativesUsd = stableAvailable(rows, 'margin');

    if (derivativesUsd >= target * 0.9) {
      return { derivativesUsd, transferredUsd: 0 };
    }

    let needed = Math.max(0, target - derivativesUsd);
    let transferredUsd = 0;

    for (const from of ['exchange', 'funding'] as const) {
      if (needed < 0.01) break;
      try {
        const moved = await this.transferStableToDerivatives(creds, from, needed, rows);
        if (moved > 0) {
          transferredUsd += moved;
          needed -= moved;
          rows = await this.listWallets(creds);
          derivativesUsd = stableAvailable(rows, 'margin');
        }
      } catch {
        /* try next source */
      }
    }

    const message =
      transferredUsd > 0
        ? `Moved $${transferredUsd.toFixed(2)} USDT to Derivatives for copy trading.`
        : derivativesUsd < target * 0.9
          ? `Need ~$${target.toFixed(0)} USDT in Derivatives wallet — transfer from Exchange or Funding in Bitfinex.`
          : undefined;

    return { derivativesUsd, transferredUsd, message };
  }

  async getMarkPrice(symbol = BITFINEX_BTC_PERP_SYMBOL): Promise<number> {
    const ticker = await bitfinexPublicGet<number[]>(`v2/ticker/${symbol}`);
    // Trading pair ticker: [0]=BID [2]=ASK [6]=LAST_PRICE [7]=VOLUME (not price!)
    const last = ticker[6];
    const bid = ticker[0];
    const ask = ticker[2];
    if (typeof last === 'number' && last > 10_000) return last;
    if (typeof bid === 'number' && typeof ask === 'number' && bid > 10_000 && ask > 0) {
      return (bid + ask) / 2;
    }
    throw new Error(`Bitfinex ticker returned invalid mark for ${symbol}`);
  }

  async getBestBidAsk(symbol = BITFINEX_BTC_PERP_SYMBOL): Promise<{
    bid: number; ask: number; observedAtMs: number; source: 'BITFINEX_PUBLIC_TICKER';
  }> {
    const ticker = await bitfinexPublicGet<number[]>(`v2/ticker/${symbol}`);
    const bid = Number(ticker[0]);
    const ask = Number(ticker[2]);
    if (!(bid > 0) || !(ask >= bid)) throw new Error(`Bitfinex ticker returned invalid BBO for ${symbol}`);
    return { bid, ask, observedAtMs: Date.now(), source: 'BITFINEX_PUBLIC_TICKER' };
  }

  async submitLimitOrder(
    creds: ExchangeCredentials,
    input: {
      symbol?: string;
      direction: 'LONG' | 'SHORT';
      qty: number;
      price: number;
      leverage?: number;
      /**
       * Optional Bitfinex client order id (`cid` in v2/auth/w/order/submit).
       * Bitfinex requires `cid` to be a 32-bit signed integer — callers MUST
       * pre-hash any string key into that range. Setting `cid` gives a second
       * independent match key (beyond the returned order id) so future
       * reconcile-adopt passes can match orders even when `bitfinexOrderId`
       * was not persisted.
       */
      clientOrderId?: number;
      /** LIVE-FIX-1510: 'IOC' submits a Bitfinex IOC (never rests). Default GTC LIMIT. */
      timeInForce?: 'GTC' | 'IOC';
    },
  ): Promise<number> {
    assertTileTaggedEntry('limit entry', input.clientOrderId);
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const lev = bitfinexOrderLev(input.leverage);
    const amount =
      input.direction === 'LONG'
        ? Math.abs(input.qty)
        : -Math.abs(input.qty);
    const body: Record<string, unknown> = {
      type: input.timeInForce === 'IOC' ? 'IOC' : 'LIMIT',
      symbol,
      amount: amount.toFixed(5),
      price: input.price.toFixed(2),
      lev,
      meta: { aff_code: 'doxxedcrypto' },
    };
    if (input.clientOrderId != null && Number.isFinite(input.clientOrderId)) {
      // Bitfinex cid range is signed 32-bit. Clamp to the positive half to
      // avoid any sign-bit ambiguity on the exchange side.
      const cid = Math.trunc(input.clientOrderId) & 0x7fffffff;
      body.cid = cid;
    }
    const res = await bitfinexAuthPost(creds, 'v2/auth/w/order/submit', body);
    const id = parseBitfinexOrderId(res);
    if (!id) throw new Error('Bitfinex limit order submitted but no order id returned');
    const fill = parseBitfinexSubmitFill(res);
    if (fill) {
      submitFills.set(id, fill);
      if (submitFills.size > 200) submitFills.delete(submitFills.keys().next().value as number);
    }
    return id;
  }

  /**
   * LIVE-FIX-1445: the order/submit response already carries the order state.
   * A marketable LIMIT that matched on arrival reports EXECUTED with zero
   * remaining and its average price, so the caller can protect the fill
   * without a separate authenticated order/position read. One-shot.
   */
  takeSubmitFill(orderId: number): BitfinexSubmitFill | null {
    const fill = submitFills.get(orderId) ?? null;
    submitFills.delete(orderId);
    return fill;
  }

  /**
   * Amend an active derivative limit in place.  Keeping the exchange order id
   * avoids the cancel-then-submit gap during a fast showcase reprice.  Callers
   * must still prove the order is active and unfilled immediately beforehand.
   */
  async updateLimitOrder(
    creds: ExchangeCredentials,
    input: {
      orderId: number;
      direction: 'LONG' | 'SHORT';
      qty: number;
      price: number;
      leverage?: number;
    },
  ): Promise<number> {
    const lev = bitfinexOrderLev(input.leverage);
    const amount = input.direction === 'LONG' ? Math.abs(input.qty) : -Math.abs(input.qty);
    const res = await bitfinexAuthPost(creds, 'v2/auth/w/order/update', {
      id: input.orderId,
      amount: amount.toFixed(5),
      price: input.price.toFixed(2),
      lev,
      meta: { aff_code: 'doxxedcrypto' },
    });
    const id = parseBitfinexOrderId(res);
    if (!id) throw new Error('Bitfinex limit order updated but no order id returned');
    if (id !== input.orderId) {
      throw new Error(`Bitfinex limit order update returned unexpected id ${id} (expected ${input.orderId})`);
    }
    return id;
  }

  async submitStopOrder(
    creds: ExchangeCredentials,
    input: {
      symbol?: string;
      positionDirection: 'LONG' | 'SHORT';
      qty: number;
      stopPrice: number;
      leverage?: number;
      clientOrderId?: number;
    },
  ): Promise<number> {
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const lev = bitfinexOrderLev(input.leverage);
    const amount =
      input.positionDirection === 'LONG'
        ? -Math.abs(input.qty)
        : Math.abs(input.qty);
    const res = await bitfinexAuthPost(creds, 'v2/auth/w/order/submit', {
      type: 'STOP',
      symbol,
      amount: amount.toFixed(8),
      price: normalizeBitfinexOrderPrice(input.stopPrice).toString(),
      lev,
      // A stale protective stop must never open the opposite position.
      flags: BITFINEX_REDUCE_ONLY_FLAG,
      ...(input.clientOrderId != null ? { cid: input.clientOrderId } : {}),
      meta: { aff_code: 'doxxedcrypto' },
    });
    const id = parseBitfinexOrderId(res);
    if (!id) throw new Error('Bitfinex stop order submitted but no order id returned');
    return id;
  }

  async submitMarketClose(
    creds: ExchangeCredentials,
    input: {
      symbol?: string;
      positionDirection: 'LONG' | 'SHORT';
      qty: number;
      leverage?: number;
      clientOrderId?: number;
    },
  ): Promise<number> {
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const lev = bitfinexOrderLev(input.leverage);
    const amount =
      input.positionDirection === 'LONG'
        ? -Math.abs(input.qty)
        : Math.abs(input.qty);
    const res = await bitfinexAuthPost(creds, 'v2/auth/w/order/submit', {
      type: 'MARKET',
      symbol,
      amount: amount.toFixed(8),
      lev,
      // Partial virtual-lot exits share one merged BTC-PERP position.
      flags: BITFINEX_REDUCE_ONLY_FLAG,
      ...(input.clientOrderId != null ? { cid: input.clientOrderId & 0x7fffffff } : {}),
      meta: { aff_code: 'doxxedcrypto' },
    });
    const id = parseBitfinexOrderId(res);
    if (!id) throw new Error('Bitfinex market close submitted but no order id returned');
    return id;
  }

  /**
   * Final-account flatten only. The caller must first prove that the remaining
   * relay-ledger target is exactly zero. CLOSE + REDUCE_ONLY prevents a rounded
   * exact residual cleanup from reversing the account.
   */
  async submitPositionFlatten(
    creds: ExchangeCredentials,
    input: {
      symbol?: string;
      positionDirection: 'LONG' | 'SHORT';
      qty: number;
      leverage?: number;
    },
  ): Promise<number> {
    if (btcToSats(input.qty) === 0) {
      throw new Error('Bitfinex final flatten requires a non-zero raw position');
    }
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const lev = bitfinexOrderLev(input.leverage);
    const amount =
      input.positionDirection === 'LONG'
        ? -Math.abs(input.qty)
        : Math.abs(input.qty);
    const res = await bitfinexAuthPost(creds, 'v2/auth/w/order/submit', {
      type: 'MARKET',
      symbol,
      amount: amount.toFixed(8),
      lev,
      flags: BITFINEX_SAFE_CLOSE_FLAGS,
      meta: { aff_code: 'doxxedcrypto' },
    });
    const id = parseBitfinexOrderId(res);
    if (!id) throw new Error('Bitfinex final flatten submitted but no order id returned');
    return id;
  }

  /** Market entry — mirror catch-up and other immediate fills (opposite sign of submitMarketClose). */
  async submitMarketEntry(
    creds: ExchangeCredentials,
    input: {
      symbol?: string;
      direction: 'LONG' | 'SHORT';
      qty: number;
      leverage?: number;
      clientOrderId?: number;
    },
  ): Promise<number> {
    assertTileTaggedEntry('market entry', input.clientOrderId);
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const lev = bitfinexOrderLev(input.leverage);
    const amount =
      input.direction === 'LONG' ? Math.abs(input.qty) : -Math.abs(input.qty);
    const body: Record<string, unknown> = {
      type: 'MARKET',
      symbol,
      amount: amount.toFixed(5),
      lev,
      meta: { aff_code: 'doxxedcrypto' },
    };
    if (input.clientOrderId != null && Number.isFinite(input.clientOrderId)) {
      body.cid = Math.trunc(input.clientOrderId) & 0x7fffffff;
    }
    const res = await bitfinexAuthPost(creds, 'v2/auth/w/order/submit', body);
    const id = parseBitfinexOrderId(res);
    if (!id) throw new Error('Bitfinex market entry submitted but no order id returned');
    // LIVE-FIX-1510: a market (IOC-at-market) entry can report EXECUTED in the submit response.
    const fill = parseBitfinexSubmitFill(res);
    if (fill) {
      submitFills.set(id, fill);
      if (submitFills.size > 200) submitFills.delete(submitFills.keys().next().value as number);
    }
    return id;
  }

  async cancelOrder(creds: ExchangeCredentials, orderId: number): Promise<void> {
    await bitfinexAuthPost(creds, 'v2/auth/w/order/cancel', { id: orderId });
  }

  /**
   * Authoritative per-order executions — Bitfinex v2
   * `POST /v2/auth/r/order/{symbol}:{orderId}/trades`. Read-only (`/r/` path,
   * so the C6 testnet guard does not block it) and rides the same nonce lane /
   * HMAC signing as every other authed call. Returns [] when the order has no
   * recorded executions; callers fall back to their price approximation.
   */
  async fetchOrderTrades(
    creds: ExchangeCredentials,
    orderId: number,
    symbol = BITFINEX_BTC_PERP_SYMBOL,
  ): Promise<BitfinexOrderTrade[]> {
    const rows = await bitfinexAuthPost<unknown[][]>(
      creds,
      `v2/auth/r/order/${symbol}:${orderId}/trades`,
    );
    if (!Array.isArray(rows)) return [];
    return rows
      .map((row) => parseOrderTrade(row))
      .filter((t): t is BitfinexOrderTrade => t != null);
  }

  async fetchOrderHistoryEvidence(
    creds: ExchangeCredentials,
    orderId: number,
  ): Promise<BitfinexOrderHistoryEvidence | null> {
    const rows = await bitfinexAuthPost<unknown[][]>(
      creds,
      'v2/auth/r/orders/hist',
      { id: [orderId] },
    );
    return parseOrderHistoryEvidence(rows, orderId);
  }

  /** Recover an acknowledged request whose response/order id was lost.
   * The deterministic CID is persisted before submit; history is read-only
   * and therefore safe to consult after a process restart. */
  async findOrderHistoryByClientOrderId(
    creds: ExchangeCredentials,
    clientOrderId: number,
    symbol = BITFINEX_BTC_PERP_SYMBOL,
  ): Promise<BitfinexActiveOrder | null> {
    const rows = await bitfinexAuthPost<unknown[][]>(
      creds,
      'v2/auth/r/orders/hist',
      { limit: 250 },
    );
    const orders = parseActiveOrdersPayload(rows, symbol);
    return orders.find((order) => order.cid === (clientOrderId & 0x7fffffff)) ?? null;
  }

  async listActiveOrders(
    creds: ExchangeCredentials,
    symbol = BITFINEX_BTC_PERP_SYMBOL,
    timeoutMs?: number,
  ): Promise<BitfinexActiveOrder[]> {
    const sentAtMs = Date.now();
    const rows = await bitfinexAuthPost<unknown[][]>(creds, 'v2/auth/r/orders', {
      sym: symbol,
    }, timeoutMs);
    const orders = parseActiveOrdersPayload(rows, symbol);
    rememberAuthRead(creds.apiKey, 'orders', symbol, sentAtMs, orders);
    return orders;
  }

  async findOrder(
    creds: ExchangeCredentials,
    orderId: number,
    symbol = BITFINEX_BTC_PERP_SYMBOL,
  ): Promise<BitfinexActiveOrder | null> {
    const orders = await this.listActiveOrders(creds, symbol);
    return orders.find((o) => o.id === orderId) ?? null;
  }

  async getOpenPosition(
    creds: ExchangeCredentials,
    symbol = BITFINEX_BTC_PERP_SYMBOL,
  ): Promise<{ amount: number; basePrice: number } | null> {
    const detail = await this.getOpenPositionDetail(creds, symbol);
    if (!detail) return null;
    return { amount: detail.amount, basePrice: detail.basePrice };
  }

  async getOpenPositionDetail(
    creds: ExchangeCredentials,
    symbol = BITFINEX_BTC_PERP_SYMBOL,
    timeoutMs?: number,
  ): Promise<BitfinexPositionDetail | null> {
    const sentAtMs = Date.now();
    const rows = await bitfinexAuthPost<unknown[][]>(creds, 'v2/auth/r/positions', {}, timeoutMs);
    const detail = parseOpenPositionPayload(rows, symbol);
    rememberAuthRead(creds.apiKey, 'positions', symbol, sentAtMs, detail);
    return detail;
  }

  /**
   * Sum fees and exchange-realized position P&L from one margin-ledger request. Fees are metrics only (R7).
   * `verified` is true only when the read succeeded and EVERY row parsed (documented layout, finite AMOUNT, string
   * DESCRIPTION); otherwise callers must treat the sums as unverified (fail closed), never as 0.
   */
  async getLedgerFeesSince(
    creds: ExchangeCredentials,
    sinceMs: number,
  ): Promise<{
    tradingFeesUsd: number;
    fundingFeesUsd: number;
    realizedPnlUsd: number;
    positionCloseRows: number;
    verified: boolean;
    unverifiedRows: number;
  }> {
    let tradingFeesUsd = 0;
    let fundingFeesUsd = 0;
    let realizedPnlUsd = 0;
    let positionCloseRows = 0;
    let unverifiedRows = 0;
    let readOk = false;
    try {
      const rows = await bitfinexAuthPost<unknown[][]>(creds, 'v2/auth/r/ledgers/hist', {
        wallet: 'margin',
        start: sinceMs,
        end: Date.now(),
        limit: 250,
      });
      if (!Array.isArray(rows)) {
        return { tradingFeesUsd, fundingFeesUsd, realizedPnlUsd, positionCloseRows, verified: false, unverifiedRows };
      }
      readOk = true;
      for (const raw of rows) {
        const parsed = parseBitfinexLedgerRow(raw);
        if (!parsed) {
          unverifiedRows += 1;
          continue;
        }
        const amount = parsed.amount;
        const description = parsed.description.toLowerCase();
        if (
          amount !== 0 &&
          description.includes('position') &&
          !/fee|funding|transfer|commission|margin funding/i.test(description)
        ) {
          realizedPnlUsd += amount;
          positionCloseRows += 1;
        }
        if (amount >= 0) continue;
        const fee = Math.abs(amount);
        if (description.includes('funding') || description.includes('margin funding')) {
          fundingFeesUsd += fee;
        } else if (
          description.includes('fee') ||
          description.includes('trading') ||
          description.includes('commission')
        ) {
          tradingFeesUsd += fee;
        }
      }
    } catch {
      /* ledger optional: unverified, not zero */
      readOk = false;
    }
    return {
      tradingFeesUsd: Number(tradingFeesUsd.toFixed(4)),
      fundingFeesUsd: Number(fundingFeesUsd.toFixed(4)),
      realizedPnlUsd: Number(realizedPnlUsd.toFixed(4)),
      positionCloseRows,
      verified: readOk && unverifiedRows === 0,
      unverifiedRows,
    };
  }

  /**
   * Realized P&L attributed to derivatives position closes on the margin wallet since
   * `sinceMs`. Uses the Bitfinex authenticated ledger endpoint (`v2/auth/r/ledgers/hist`,
   * wallet=margin) — the same source as `getLedgerFeesSince` — filtered to position-close
   * PL entries (description mentions "position" but not fee/funding/transfer).
   *
   * Returns the sum of those entries' amounts (signed USD: + profit, - loss). When only a
   * single lot was open over the window (e.g. relay sim, max 1 concurrent), this IS that
   * lot's exchange-realized P&L. With multiple merged lots it is the position-level P&L
   * and the caller must fall back to per-lot reconstruction.
   */
  async getRealizedPnlSince(
    creds: ExchangeCredentials,
    sinceMs: number,
  ): Promise<number> {
    const rows = await this.fetchPositionCloseLedgerRows(creds, sinceMs);
    return Number(
      rows.reduce((sum, row) => sum + row.pnlUsd, 0).toFixed(4),
    );
  }

  /** Individual derivatives position-close ledger rows since session start. */
  async getPositionCloseLedgerEntries(
    creds: ExchangeCredentials,
    sinceMs: number,
  ): Promise<BitfinexPositionCloseLedgerRow[]> {
    return this.fetchPositionCloseLedgerRows(creds, sinceMs);
  }

  private async fetchPositionCloseLedgerRows(
    creds: ExchangeCredentials,
    sinceMs: number,
  ): Promise<BitfinexPositionCloseLedgerRow[]> {
    try {
      const rows = await bitfinexAuthPost<unknown[][]>(creds, 'v2/auth/r/ledgers/hist', {
        wallet: 'margin',
        start: sinceMs,
        end: Date.now(),
        limit: 250,
      });
      if (!Array.isArray(rows)) return [];
      const out: BitfinexPositionCloseLedgerRow[] = [];
      let unverifiedRows = 0;
      for (const raw of rows) {
        const parsed = parseBitfinexLedgerRow(raw);
        if (!parsed) {
          unverifiedRows += 1;
          continue;
        }
        const amount = parsed.amount;
        if (amount === 0) continue;
        const description = parsed.description;
        const lower = description.toLowerCase();
        if (!/position/i.test(lower)) continue;
        if (/fee|funding|transfer|commission|margin funding/i.test(lower)) continue;
        // A close row without its own MTS cannot be bounded to a close window: unverified, never "now".
        if (parsed.mts == null) {
          unverifiedRows += 1;
          continue;
        }
        const closedAtMs = parsed.mts;
        const closedAt = new Date(closedAtMs);
        out.push({
          ledgerId: String(parsed.id ?? `${closedAtMs}-${amount}`),
          closedAt,
          pnlUsd: Number(amount.toFixed(4)),
          description,
        });
      }
      // Fail closed: one unverified row makes the whole close set unverified (callers fall back / omit evidence).
      if (unverifiedRows > 0) throw new BitfinexLedgerUnverifiedError(unverifiedRows);
      return out.sort((a, b) => b.closedAt.getTime() - a.closedAt.getTime());
    } catch (err) {
      if (err instanceof BitfinexLedgerUnverifiedError) throw err;
      return [];
    }
  }

  async getLiveAccountMetrics(
    creds: ExchangeCredentials,
    opts?: { sessionStartedAt?: Date; realizedPnlUsd?: number },
  ): Promise<BitfinexLiveAccountMetrics> {
    const rows = await this.listWallets(creds);
    const derivativesAvailableUsd = stableAvailable(rows, 'margin');
    const derivativesTotalUsd = stableTotalBalance(rows, 'margin');
    const exchangeUsd = stableAvailable(rows, 'exchange');
    const fundingUsd = stableAvailable(rows, 'funding');
    const position = await this.getOpenPositionDetail(creds);
    const unrealizedPnlUsd = position?.pnlUsd ?? 0;
    const equityUsd = Number((derivativesTotalUsd + unrealizedPnlUsd).toFixed(2));
    const sinceMs = opts?.sessionStartedAt?.getTime() ?? Date.now() - 7 * 24 * 60 * 60 * 1000;
    const fees = await this.getLedgerFeesSince(creds, sinceMs);
    // The participant ledger reconstructs P&L per virtual showcase lot. Bitfinex,
    // however, merges same-symbol positions and realizes a partial close from the
    // exchange net basis. Session P&L must therefore prefer the exchange position-close
    // ledger; otherwise a remaining lot can show a profit while the actual net position
    // shows a loss and the headline mixes the two accounting bases. The participant sum
    // remains a fallback only when the exchange ledger is temporarily unavailable/empty.
    // HM #842: an unverified ledger read (malformed row / read failure) is never used as exchange truth.
    const realizedPnlUsd =
      fees.verified && fees.positionCloseRows > 0
        ? fees.realizedPnlUsd
        : (opts?.realizedPnlUsd ?? 0);
    // R7: fees stay metrics (tradingFeesUsd / fundingFeesUsd below), never subtracted from session P&L.
    const sessionPnlUsd = calculateBitfinexSessionPnl({ realizedPnlUsd, unrealizedPnlUsd });
    const fundsInWrongWallet =
      !position &&
      derivativesAvailableUsd < 5 &&
      exchangeUsd + fundingUsd > 10;

    return {
      derivativesAvailableUsd,
      derivativesTotalUsd,
      exchangeUsd,
      fundingUsd,
      unrealizedPnlUsd,
      equityUsd,
      realizedPnlUsd,
      tradingFeesUsd: fees.tradingFeesUsd,
      fundingFeesUsd: fees.fundingFeesUsd,
      sessionPnlUsd,
      openPosition: position,
      fundsInWrongWallet,
    };
  }

  async cancelOrphanStopOrders(creds: ExchangeCredentials, keepOrderId?: number): Promise<number> {
    const orders = await this.listActiveOrders(creds);
    let cancelled = 0;
    for (const order of orders) {
      if (keepOrderId != null && order.id === keepOrderId) continue;
      if (!order.orderType.toUpperCase().includes('STOP')) continue;
      try {
        await this.cancelOrder(creds, order.id);
        cancelled += 1;
      } catch {
        /* already gone */
      }
    }
    return cancelled;
  }
}

/**
 * RK 02:25 24 Oct, last line of defence at the exchange client: an order that is NOT reduce-only (every live entry) must
 * carry a tile-keyed cid; only reduce-only closing orders (stops, market close, flatten) may be untagged. Throws before
 * any request is signed or sent.
 */
export function assertTileTaggedEntry(kind: string, clientOrderId: number | null | undefined): void {
  const refusal = untaggedOrderRefusal({ clientOrderId, reduceOnly: false });
  if (refusal) throw new Error(`${refusal}: ${kind} refused: cid ${clientOrderId ?? 'none'} carries no live tile key`);
}
