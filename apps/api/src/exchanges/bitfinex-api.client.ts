import { createHmac } from 'node:crypto';
import type { ExchangeCredentials } from './exchange-adapter.interface';
import { exchangeErrorMessage, exchangeFetch } from './exchange-http.util';

export const BITFINEX_BTC_PERP_SYMBOL = 'tBTCF0:USTF0';
/** Bitfinex defaults to 10x when lev is omitted — showcase bot uses 100x. */
export const BITFINEX_DEFAULT_DERIVATIVE_LEVERAGE = 100;
const MIN_POSITION_BTC = 0.000004;
const STABLE_CURRENCIES = new Set(['USD', 'USDT', 'UST', 'USTF0']);

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
  tail: Promise<unknown>;
};

/** Per API key: monotonic nonce + serialized auth calls (parallel ticks caused nonce: small). */
const nonceLanes = new Map<string, NonceLane>();

function nonceLaneFor(apiKey: string): NonceLane {
  let lane = nonceLanes.get(apiKey);
  if (!lane) {
    lane = { lastNonce: 0n, tail: Promise.resolve() };
    nonceLanes.set(apiKey, lane);
  }
  return lane;
}

function allocMonotonicNonce(lane: NonceLane): string {
  const now = BigInt(Date.now()) * 1000n;
  const next = lane.lastNonce >= now ? lane.lastNonce + 1n : now;
  lane.lastNonce = next;
  return next.toString();
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

export async function bitfinexAuthPost<T = unknown>(
  creds: ExchangeCredentials,
  apiPath: string,
  body: Record<string, unknown> = {},
  timeoutMs = 12_000,
): Promise<T> {
  const lane = nonceLaneFor(creds.apiKey);
  const run = async (): Promise<T> => {
    for (let attempt = 0; attempt < 4; attempt++) {
      const nonce = allocMonotonicNonce(lane);
      try {
        return await bitfinexAuthPostOnce<T>(creds, apiPath, body, timeoutMs, nonce);
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err);
        if (attempt < 3 && /nonce:\s*small/i.test(msg)) {
          await new Promise((r) => setTimeout(r, 20 * (attempt + 1)));
          continue;
        }
        throw err;
      }
    }
    throw new Error(`Bitfinex ${apiPath}: nonce retry exhausted`);
  };

  const result = lane.tail.then(run, run);
  lane.tail = result.then(
    () => undefined,
    () => undefined,
  );
  return result;
}

export async function bitfinexPublicGet<T = unknown>(apiPath: string): Promise<T> {
  const res = await exchangeFetch(`https://api-pub.bitfinex.com/${apiPath}`, {}, 10_000);
  if (!res.ok) throw new Error(`Bitfinex public ${apiPath}: HTTP ${res.status}`);
  return res.json() as Promise<T>;
}

/** Parse order id from /auth/w/order/submit notification payload. */
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
  /** Real execution price (EXEC_PRICE, index 5). */
  execPrice: number;
  /** Signed executed amount in BTC (EXEC_AMOUNT, index 4). */
  execAmount: number;
  /** Fee (signed, FEE index 9 — negative = charged). */
  fee: number;
  /** Execution timestamp ms (MTS_CREATE, index 2). */
  mtsCreate: number;
};

/** Bitfinex v2 trade array: [ID, PAIR, MTS_CREATE, ORDER_ID, EXEC_AMOUNT, EXEC_PRICE,
 *  ORDER_TYPE, ORDER_PRICE, MAKER, FEE, FEE_CURRENCY]. */
export function parseOrderTrade(row: unknown): BitfinexOrderTrade | null {
  if (!Array.isArray(row) || row.length < 11) return null;
  const execAmount = Number(row[4] ?? 0);
  const execPrice = Number(row[5] ?? 0);
  if (!Number.isFinite(execPrice) || execPrice <= 0) return null;
  return {
    execPrice,
    execAmount: Number.isFinite(execAmount) ? execAmount : 0,
    fee: Number(row[9] ?? 0) || 0,
    mtsCreate: Number(row[2] ?? 0) || 0,
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

/** Bitfinex order array indices (REST). */
export function parseActiveOrder(row: unknown[]): BitfinexActiveOrder | null {
  if (!Array.isArray(row) || row.length < 14) return null;
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
    id: Number(row[0]),
    symbol: String(row[3]),
    amount: Number(row[6]),
    amountOrig: Number(row[7]),
    orderType: String(row[8] ?? ''),
    price: Number(row[16] ?? row[14] ?? 0),
    status: String(row[13] ?? 'UNKNOWN'),
    ...(cid != null ? { cid } : {}),
    ...(createdAtMs != null ? { createdAtMs } : {}),
  };
}

export class BitfinexTradingClient {
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
    },
  ): Promise<number> {
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const lev = Math.min(100, Math.max(1, Math.round(input.leverage ?? BITFINEX_DEFAULT_DERIVATIVE_LEVERAGE)));
    const amount =
      input.direction === 'LONG'
        ? Math.abs(input.qty)
        : -Math.abs(input.qty);
    const body: Record<string, unknown> = {
      type: 'LIMIT',
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
    },
  ): Promise<number> {
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const lev = Math.min(100, Math.max(1, Math.round(input.leverage ?? BITFINEX_DEFAULT_DERIVATIVE_LEVERAGE)));
    const amount =
      input.positionDirection === 'LONG'
        ? -Math.abs(input.qty)
        : Math.abs(input.qty);
    const res = await bitfinexAuthPost(creds, 'v2/auth/w/order/submit', {
      type: 'STOP',
      symbol,
      amount: amount.toFixed(5),
      price: input.stopPrice.toFixed(2),
      lev,
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
    },
  ): Promise<number> {
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const lev = Math.min(100, Math.max(1, Math.round(input.leverage ?? BITFINEX_DEFAULT_DERIVATIVE_LEVERAGE)));
    const amount =
      input.positionDirection === 'LONG'
        ? -Math.abs(input.qty)
        : Math.abs(input.qty);
    const res = await bitfinexAuthPost(creds, 'v2/auth/w/order/submit', {
      type: 'MARKET',
      symbol,
      amount: amount.toFixed(5),
      lev,
      meta: { aff_code: 'doxxedcrypto' },
    });
    const id = parseBitfinexOrderId(res);
    if (!id) throw new Error('Bitfinex market close submitted but no order id returned');
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
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const lev = Math.min(100, Math.max(1, Math.round(input.leverage ?? BITFINEX_DEFAULT_DERIVATIVE_LEVERAGE)));
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

  async listActiveOrders(
    creds: ExchangeCredentials,
    symbol = BITFINEX_BTC_PERP_SYMBOL,
  ): Promise<BitfinexActiveOrder[]> {
    const rows = await bitfinexAuthPost<unknown[][]>(creds, 'v2/auth/r/orders', {
      sym: symbol,
    });
    if (!Array.isArray(rows)) return [];
    return rows
      .map((row) => parseActiveOrder(row as unknown[]))
      .filter((o): o is BitfinexActiveOrder => o != null);
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
  ): Promise<BitfinexPositionDetail | null> {
    const rows = await bitfinexAuthPost<unknown[][]>(creds, 'v2/auth/r/positions');
    if (!Array.isArray(rows)) return null;
    for (const row of rows) {
      if (!Array.isArray(row) || row.length < 8) continue;
      if (String(row[0]) !== symbol) continue;
      const amount = Number(row[2] ?? 0);
      if (Math.abs(amount) < MIN_POSITION_BTC) continue;
      const pnlUsd = Number(row[6] ?? 0);
      const pnlPct = Number(row[7] ?? 0);
      return {
        symbol,
        amount,
        basePrice: Number(row[3] ?? 0),
        pnlUsd,
        pnlPct,
        direction: amount > 0 ? 'LONG' : 'SHORT',
      };
    }
    return null;
  }

  /** Sum trading + funding fees from margin wallet ledgers since session start. */
  async getLedgerFeesSince(
    creds: ExchangeCredentials,
    sinceMs: number,
  ): Promise<{ tradingFeesUsd: number; fundingFeesUsd: number }> {
    let tradingFeesUsd = 0;
    let fundingFeesUsd = 0;
    try {
      const rows = await bitfinexAuthPost<unknown[][]>(creds, 'v2/auth/r/ledgers/hist', {
        wallet: 'margin',
        start: sinceMs,
        end: Date.now(),
        limit: 250,
      });
      if (!Array.isArray(rows)) return { tradingFeesUsd, fundingFeesUsd };
      for (const row of rows) {
        if (!Array.isArray(row) || row.length < 8) continue;
        const amount = Number(row[4] ?? 0);
        const description = String(row[7] ?? '').toLowerCase();
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
      /* ledger optional */
    }
    return {
      tradingFeesUsd: Number(tradingFeesUsd.toFixed(4)),
      fundingFeesUsd: Number(fundingFeesUsd.toFixed(4)),
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
      for (const row of rows) {
        if (!Array.isArray(row) || row.length < 8) continue;
        const amount = Number(row[4] ?? 0);
        if (!Number.isFinite(amount) || amount === 0) continue;
        const description = String(row[7] ?? '');
        const lower = description.toLowerCase();
        if (!/position/i.test(lower)) continue;
        if (/fee|funding|transfer|commission|margin funding/i.test(lower)) continue;
        const closedAtMs = Number(row[3] ?? 0);
        const closedAt = Number.isFinite(closedAtMs) && closedAtMs > 0 ? new Date(closedAtMs) : new Date();
        out.push({
          ledgerId: String(row[0] ?? `${closedAtMs}-${amount}`),
          closedAt,
          pnlUsd: Number(amount.toFixed(4)),
          description,
        });
      }
      return out.sort((a, b) => b.closedAt.getTime() - a.closedAt.getTime());
    } catch {
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
    const realizedPnlUsd = opts?.realizedPnlUsd ?? 0;
    const sessionPnlUsd = Number(
      (realizedPnlUsd + unrealizedPnlUsd - fees.tradingFeesUsd - fees.fundingFeesUsd).toFixed(2),
    );
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
