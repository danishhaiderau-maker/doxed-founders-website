import { createHmac, createHash } from 'crypto';
import WebSocket from 'ws';
import type { ExchangeCredentials } from './exchange-adapter.interface';
import { allocateBitfinexAuthNonce } from './bitfinex-api.client';

export type BitfinexWsTrade = {
  tradeId: number;
  orderId: number;
  symbol: string;
  mts: number;
  execAmount: number;
  execPrice: number;
  receivedAtMs: number;
  cumulativeQty: number;
  cumulativeAveragePrice: number;
};

type SocketLike = {
  addEventListener(type: string, listener: (event: any) => void): void;
  send(data: string): void;
  close(): void;
  readyState: number;
};
type SocketFactory = (url: string) => SocketLike;

export function parseBitfinexAuthTradeMessage(raw: unknown, receivedAtMs = Date.now()): BitfinexWsTrade | null {
  let value: unknown = raw;
  if (typeof raw === 'string') {
    try { value = JSON.parse(raw); } catch { return null; }
  }
  if (!Array.isArray(value) || (value[1] !== 'te' && value[1] !== 'tu')) return null;
  const row = value[2];
  if (!Array.isArray(row)) return null;
  const [tradeId, symbol, mts, orderId, execAmount, execPrice] = row;
  if (![tradeId, mts, orderId, execAmount, execPrice].every((item) => typeof item === 'number' && Number.isFinite(item))) return null;
  if (!Number.isSafeInteger(tradeId) || !Number.isSafeInteger(orderId) || typeof symbol !== 'string') return null;
  if (!/^(?:tBTCF0:USTF0|tBTCUSD|tBTCUST)$/i.test(symbol) || execAmount === 0 || execPrice <= 0) return null;
  return { tradeId, symbol, mts, orderId, execAmount, execPrice, receivedAtMs, cumulativeQty: Math.abs(execAmount), cumulativeAveragePrice: execPrice };
}

export function buildBitfinexWsAuth(creds: ExchangeCredentials, nonce: string | number = allocateBitfinexAuthNonce(creds.apiKey)) {
  const authNonce = String(nonce);
  const authPayload = `AUTH${authNonce}`;
  return {
    event: 'auth',
    apiKey: creds.apiKey,
    authSig: createHmac('sha384', creds.apiSecret).update(authPayload).digest('hex'),
    authNonce,
    authPayload,
    filter: ['trading'],
  };
}

export class BitfinexAuthTradeStream {
  private socket: SocketLike | null = null;
  private stopped = false;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private staleTimer: ReturnType<typeof setInterval> | null = null;
  private reconnectAttempt = 0;
  private lastMessageAtMs = 0;
  private authReady = false;
  private readyWaiters: Array<{ resolve: (ready: boolean) => void }> = [];
  private readonly seenTradeIds = new Map<number, number>();
  private readonly inFlightTradeIds = new Set<number>();
  private readonly pendingDuplicateTrades = new Map<number, BitfinexWsTrade>();
  private readonly preparedTrades = new Map<number, BitfinexWsTrade>();
  private readonly orderAggregates = new Map<number, { qty: number; notional: number; lastAtMs: number }>();

  constructor(
    private readonly creds: ExchangeCredentials,
    private readonly onTrade: (trade: BitfinexWsTrade) => boolean | Promise<boolean>,
    // Railway currently runs a Node release without a global WebSocket. Keep
    // the runtime dependency explicit so production cannot silently depend on
    // browser globals that happen to exist in a developer/test process.
    private readonly socketFactory: SocketFactory = (url) => new WebSocket(url) as unknown as SocketLike,
    private readonly now: () => number = Date.now,
  ) {}

  /** Stable, redacted identity suitable only for stream de-duplication. */
  get keyId(): string { return createHash('sha256').update(this.creds.apiKey).digest('hex').slice(0, 16); }

  start(): void {
    if (this.stopped === false && this.socket) return;
    this.stopped = false;
    this.connect();
    if (!this.staleTimer) this.staleTimer = setInterval(() => {
      if (this.socket && this.lastMessageAtMs && this.now() - this.lastMessageAtMs > 45_000) {
        this.socket.close();
      }
      this.pruneCaches(this.now());
    }, 10_000);
    this.staleTimer.unref?.();
  }

  stop(): void {
    this.stopped = true;
    if (this.reconnectTimer) clearTimeout(this.reconnectTimer);
    if (this.staleTimer) clearInterval(this.staleTimer);
    this.reconnectTimer = this.staleTimer = null;
    this.socket?.close();
    this.socket = null;
    this.authReady = false;
    for (const waiter of this.readyWaiters.splice(0)) waiter.resolve(false);
  }

  waitUntilReady(timeoutMs = 2_000): Promise<boolean> {
    if (this.authReady) return Promise.resolve(true);
    return new Promise((resolve) => {
      const waiter = { resolve };
      const timer = setTimeout(() => {
        const index = this.readyWaiters.indexOf(waiter);
        if (index >= 0) this.readyWaiters.splice(index, 1);
        resolve(false);
      }, timeoutMs);
      timer.unref?.();
      waiter.resolve = (ready) => { clearTimeout(timer); resolve(ready); };
      this.readyWaiters.push(waiter);
    });
  }

  private connect(): void {
    if (this.stopped) return;
    const socket = this.socketFactory('wss://api.bitfinex.com/ws/2');
    this.socket = socket;
    socket.addEventListener('open', () => socket.send(JSON.stringify(buildBitfinexWsAuth(this.creds))));
    socket.addEventListener('message', (event) => {
      this.lastMessageAtMs = this.now();
      let control: any;
      try { control = JSON.parse(String(event.data)); } catch { control = null; }
      if (control?.event === 'auth') {
        if (control?.status !== 'OK') { socket.close(); return; }
        this.authReady = true;
        this.reconnectAttempt = 0;
        for (const waiter of this.readyWaiters.splice(0)) waiter.resolve(true);
        return;
      }
      const trade = parseBitfinexAuthTradeMessage(String(event.data), this.lastMessageAtMs);
      if (!trade || this.seenTradeIds.has(trade.tradeId)) return;
      let aggregate = this.preparedTrades.get(trade.tradeId);
      if (!aggregate) {
        const prior = this.orderAggregates.get(trade.orderId) ?? { qty: 0, notional: 0, lastAtMs: 0 };
        const qty = prior.qty + Math.abs(trade.execAmount);
        const notional = prior.notional + Math.abs(trade.execAmount) * trade.execPrice;
        this.orderAggregates.set(trade.orderId, { qty, notional, lastAtMs: this.lastMessageAtMs });
        aggregate = { ...trade, cumulativeQty: qty, cumulativeAveragePrice: notional / qty };
        this.preparedTrades.set(trade.tradeId, aggregate);
      }
      if (this.inFlightTradeIds.has(trade.tradeId)) {
        this.pendingDuplicateTrades.set(trade.tradeId, aggregate);
        return;
      }
      this.dispatchTrade(aggregate);
    });
    socket.addEventListener('error', () => socket.close());
    socket.addEventListener('close', () => {
      this.authReady = false;
      if (this.socket === socket) this.socket = null;
      if (this.stopped) return;
      const delay = Math.min(30_000, 500 * (2 ** Math.min(this.reconnectAttempt++, 6))) + Math.floor(Math.random() * 250);
      this.reconnectTimer = setTimeout(() => this.connect(), delay);
      this.reconnectTimer.unref?.();
    });
    socket.addEventListener('open', () => { this.lastMessageAtMs = this.now(); });
  }

  private dispatchTrade(trade: BitfinexWsTrade): void {
    this.inFlightTradeIds.add(trade.tradeId);
    void Promise.resolve(this.onTrade(trade)).then((handled) => {
      if (handled) this.seenTradeIds.set(trade.tradeId, this.now());
    }).catch(() => undefined).finally(() => {
      this.inFlightTradeIds.delete(trade.tradeId);
      const pending = this.pendingDuplicateTrades.get(trade.tradeId);
      this.pendingDuplicateTrades.delete(trade.tradeId);
      if (pending && !this.seenTradeIds.has(trade.tradeId)) this.dispatchTrade(pending);
    });
  }

  private pruneCaches(nowMs: number): void {
    const tradeCutoff = nowMs - 60 * 60_000;
    for (const [id, at] of this.seenTradeIds) {
      if (at < tradeCutoff && !this.inFlightTradeIds.has(id) && !this.pendingDuplicateTrades.has(id)) {
        this.seenTradeIds.delete(id); this.preparedTrades.delete(id);
      }
    }
    for (const [id, trade] of this.preparedTrades) {
      if (trade.receivedAtMs < tradeCutoff && !this.inFlightTradeIds.has(id) && !this.pendingDuplicateTrades.has(id)) {
        this.preparedTrades.delete(id); this.seenTradeIds.delete(id);
      }
    }
    // Partial orders can legitimately rest for hours. Retain cumulative fill
    // state for a full day; REST reconciliation remains the restart fallback.
    const orderCutoff = nowMs - 24 * 60 * 60_000;
    for (const [orderId, aggregate] of this.orderAggregates) {
      if (aggregate.lastAtMs < orderCutoff) this.orderAggregates.delete(orderId);
    }
    this.pruneOldest(this.seenTradeIds, 20_000, (id) => !this.inFlightTradeIds.has(id) && !this.pendingDuplicateTrades.has(id), (id) => this.preparedTrades.delete(id));
    this.pruneOldest(this.orderAggregates, 50_000, () => true);
  }

  private pruneOldest<K, V>(map: Map<K, V>, limit: number, removable: (key: K) => boolean, removed?: (key: K) => void): void {
    if (map.size <= limit) return;
    for (const key of map.keys()) {
      if (!removable(key)) continue;
      map.delete(key); removed?.(key);
      if (map.size <= limit) break;
    }
  }
}
