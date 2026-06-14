import { createHmac } from 'node:crypto';
import type { ExchangeCredentials } from './exchange-adapter.interface';
import { exchangeErrorMessage, exchangeFetch } from './exchange-http.util';

export const BITFINEX_BTC_PERP_SYMBOL = 'tBTCF0:USTF0';
const MIN_POSITION_BTC = 0.000004;

function signBitfinex(secret: string, payload: string): string {
  return createHmac('sha384', secret).update(payload).digest('hex');
}

function nextNonce(): string {
  return (Date.now() * 1000).toString();
}

export async function bitfinexAuthPost<T = unknown>(
  creds: ExchangeCredentials,
  apiPath: string,
  body: Record<string, unknown> = {},
  timeoutMs = 12_000,
): Promise<T> {
  const nonce = nextNonce();
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

export type BitfinexActiveOrder = {
  id: number;
  symbol: string;
  amount: number;
  amountOrig: number;
  price: number;
  status: string;
};

/** Bitfinex order array indices (REST). */
export function parseActiveOrder(row: unknown[]): BitfinexActiveOrder | null {
  if (!Array.isArray(row) || row.length < 14) return null;
  return {
    id: Number(row[0]),
    symbol: String(row[3]),
    amount: Number(row[6]),
    amountOrig: Number(row[7]),
    price: Number(row[16] ?? row[14] ?? 0),
    status: String(row[13] ?? 'UNKNOWN'),
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

  async getMarkPrice(symbol = BITFINEX_BTC_PERP_SYMBOL): Promise<number> {
    const ticker = await bitfinexPublicGet<number[]>(`v2/ticker/${symbol}`);
    const last = ticker[7];
    const bid = ticker[1];
    const ask = ticker[3];
    if (typeof last === 'number' && last > 0) return last;
    if (typeof bid === 'number' && typeof ask === 'number' && bid > 0 && ask > 0) {
      return (bid + ask) / 2;
    }
    throw new Error('Bitfinex ticker returned no price');
  }

  async getAvailableUsd(creds: ExchangeCredentials): Promise<number> {
    const wallets = await bitfinexAuthPost<unknown[][]>(creds, 'v2/auth/r/wallets');
    let best = 0;
    for (const w of wallets) {
      if (!Array.isArray(w) || w.length < 5) continue;
      const currency = String(w[1] ?? '').toUpperCase();
      if (currency !== 'USD' && currency !== 'USDT' && currency !== 'UST') continue;
      const available = Number(w[4] ?? 0);
      if (available > best) best = available;
    }
    return best;
  }

  async submitLimitOrder(
    creds: ExchangeCredentials,
    input: { symbol?: string; direction: 'LONG' | 'SHORT'; qty: number; price: number },
  ): Promise<number> {
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const amount =
      input.direction === 'LONG'
        ? Math.abs(input.qty)
        : -Math.abs(input.qty);
    const res = await bitfinexAuthPost(creds, 'v2/auth/w/order/submit', {
      type: 'LIMIT',
      symbol,
      amount: amount.toFixed(5),
      price: input.price.toFixed(2),
      meta: { aff_code: 'doxxedcrypto' },
    });
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
    },
  ): Promise<number> {
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const amount =
      input.positionDirection === 'LONG'
        ? -Math.abs(input.qty)
        : Math.abs(input.qty);
    const res = await bitfinexAuthPost(creds, 'v2/auth/w/order/submit', {
      type: 'STOP',
      symbol,
      amount: amount.toFixed(5),
      price: input.stopPrice.toFixed(2),
      meta: { aff_code: 'doxxedcrypto' },
    });
    const id = parseBitfinexOrderId(res);
    if (!id) throw new Error('Bitfinex stop order submitted but no order id returned');
    return id;
  }

  async submitMarketClose(
    creds: ExchangeCredentials,
    input: { symbol?: string; positionDirection: 'LONG' | 'SHORT'; qty: number },
  ): Promise<number> {
    const symbol = input.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
    const amount =
      input.positionDirection === 'LONG'
        ? -Math.abs(input.qty)
        : Math.abs(input.qty);
    const res = await bitfinexAuthPost(creds, 'v2/auth/w/order/submit', {
      type: 'MARKET',
      symbol,
      amount: amount.toFixed(5),
      meta: { aff_code: 'doxxedcrypto' },
    });
    const id = parseBitfinexOrderId(res);
    if (!id) throw new Error('Bitfinex market close submitted but no order id returned');
    return id;
  }

  async cancelOrder(creds: ExchangeCredentials, orderId: number): Promise<void> {
    await bitfinexAuthPost(creds, 'v2/auth/w/order/cancel', { id: orderId });
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
    const rows = await bitfinexAuthPost<unknown[][]>(creds, 'v2/auth/r/positions');
    if (!Array.isArray(rows)) return null;
    for (const row of rows) {
      if (!Array.isArray(row) || row.length < 4) continue;
      if (String(row[0]) !== symbol) continue;
      const amount = Number(row[2] ?? 0);
      if (Math.abs(amount) < MIN_POSITION_BTC) continue;
      return { amount, basePrice: Number(row[3] ?? 0) };
    }
    return null;
  }
}
