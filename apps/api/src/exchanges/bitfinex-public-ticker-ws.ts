/**
 * LIVE-FIX-1506: websocket-cached best bid/ask for the stop-side guard.
 * Public channel only (no keys). Reconnects with backoff; a quote older than
 * maxAgeMs is treated as missing so the guard falls back to REST.
 */
import WebSocket from 'ws';

export interface CachedQuote { bid: number; ask: number; atMs: number }

type SocketLike = { on(ev: string, fn: (...a: any[]) => void): void; send(d: string): void; close(): void };

export class BitfinexPublicTickerCache {
  private quotes = new Map<string, CachedQuote>();
  private chanToSymbol = new Map<number, string>();
  private sock: SocketLike | null = null;
  private stopped = false;
  private backoffMs = 1000;

  constructor(
    private readonly symbols: string[],
    private readonly factory: (url: string) => SocketLike = (url) => new WebSocket(url) as unknown as SocketLike,
  ) {}

  start(): void {
    this.stopped = false;
    this.connect();
  }

  stop(): void {
    this.stopped = true;
    try { this.sock?.close(); } catch { /* ignore */ }
  }

  get(symbol: string, maxAgeMs = 3000, nowMs = Date.now()): CachedQuote | null {
    const q = this.quotes.get(symbol);
    return q && nowMs - q.atMs <= maxAgeMs ? q : null;
  }

  /** Exposed for tests: feed one raw WS message. */
  handle(raw: string, nowMs = Date.now()): void {
    let msg: unknown;
    try { msg = JSON.parse(raw); } catch { return; }
    if (msg && typeof msg === 'object' && !Array.isArray(msg)) {
      const m = msg as { event?: string; chanId?: number; symbol?: string };
      if (m.event === 'subscribed' && typeof m.chanId === 'number' && m.symbol) this.chanToSymbol.set(m.chanId, m.symbol);
      return;
    }
    if (!Array.isArray(msg) || typeof msg[0] !== 'number') return;
    const symbol = this.chanToSymbol.get(msg[0]);
    const body = msg[1];
    if (!symbol || !Array.isArray(body)) return; // 'hb' heartbeats
    const bid = Number(body[0]);
    const ask = Number(body[2]);
    if (bid > 0 && ask > 0) this.quotes.set(symbol, { bid, ask, atMs: nowMs });
  }

  private connect(): void {
    if (this.stopped) return;
    let s: SocketLike;
    try { s = this.factory('wss://api-pub.bitfinex.com/ws/2'); } catch { this.retry(); return; }
    this.sock = s;
    s.on('open', () => {
      this.backoffMs = 1000;
      this.chanToSymbol.clear();
      for (const symbol of this.symbols) s.send(JSON.stringify({ event: 'subscribe', channel: 'ticker', symbol }));
    });
    s.on('message', (d: unknown) => this.handle(String(d)));
    s.on('close', () => this.retry());
    s.on('error', () => { try { s.close(); } catch { /* ignore */ } });
  }

  private retry(): void {
    if (this.stopped) return;
    const wait = this.backoffMs;
    this.backoffMs = Math.min(30_000, this.backoffMs * 2);
    setTimeout(() => this.connect(), wait).unref?.();
  }
}
