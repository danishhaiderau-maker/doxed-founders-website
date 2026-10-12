/**
 * LIVE-FIX-1503 (HM/Boss Decision A, 13 Oct): one-time stale OPEN lot cleanup
 * and executor-side DB/Bitfinex timing diagnostics (logs only, read-only except
 * the cleanup's status flip on lots the exchange proves are not open).
 */
import { performance } from 'node:perf_hooks';
import { lookup } from 'node:dns/promises';
import * as tls from 'node:tls';

export const STALE_LOT_MIN_AGE_MS = 5 * 60_000;

export interface StaleLotCandidate {
  id: string;
  status: string;
  updatedAtMs: number;
  tradeId: string | null;
  hasExitEvent: boolean;
}

export interface AccountExposure {
  readOk: boolean;
  positionAmount: number;
  openOrders: number;
}

/**
 * Pure: a lot is stale only if the account read succeeded and the account is
 * flat (no position and no working orders), and the lot hasn't been touched for
 * 5 min (never races an in-flight entry). A lot with an EXIT event is stale by
 * the same flat rule; the reason records which.
 */
export function staleLotDecision(lot: StaleLotCandidate, acct: AccountExposure, nowMs: number):
  { stale: false; reason: string } | { stale: true; reason: 'EXIT_COMPLETED_NOT_CLOSED' | 'NO_EXCHANGE_EXPOSURE' } {
  if (!acct.readOk) return { stale: false, reason: 'EXCHANGE_READ_FAILED' };
  if (Math.abs(acct.positionAmount) > 0 || acct.openOrders > 0) return { stale: false, reason: 'ACCOUNT_NOT_FLAT' };
  if (nowMs - lot.updatedAtMs < STALE_LOT_MIN_AGE_MS) return { stale: false, reason: 'TOO_RECENT' };
  return { stale: true, reason: lot.hasExitEvent ? 'EXIT_COMPLETED_NOT_CLOSED' : 'NO_EXCHANGE_EXPOSURE' };
}

export function pct(values: number[], p: number): number | null {
  if (!values.length) return null;
  const s = [...values].sort((a, b) => a - b);
  return Math.round(s[Math.min(s.length - 1, Math.floor(p * (s.length - 1) + 0.5))] * 10) / 10;
}

export function summarize(values: number[]) {
  return { n: values.length, min: values.length ? Math.round(Math.min(...values) * 10) / 10 : null, p50: pct(values, 0.5), p95: pct(values, 0.95) };
}

export async function timed<T>(fn: () => Promise<T>): Promise<number> {
  const t0 = performance.now();
  await fn();
  return performance.now() - t0;
}

/** DNS, TCP connect and TLS handshake to a host:443 (no request sent). */
export async function tlsTimings(host: string): Promise<{ dns_ms: number; addr: string; tcp_ms: number; tls_ms: number }> {
  const t0 = performance.now();
  const { address } = await lookup(host);
  const dns = performance.now() - t0;
  return await new Promise((resolve, reject) => {
    const net = require('node:net') as typeof import('node:net');
    const t1 = performance.now();
    const sock = net.connect(443, address, () => {
      const t2 = performance.now();
      const ts = tls.connect({ socket: sock, servername: host }, () => {
        const t3 = performance.now();
        ts.end();
        resolve({ dns_ms: Math.round(dns * 10) / 10, addr: address, tcp_ms: Math.round((t2 - t1) * 10) / 10, tls_ms: Math.round((t3 - t2) * 10) / 10 });
      });
      ts.on('error', reject);
    });
    sock.setTimeout(5000, () => { sock.destroy(); reject(new Error('TIMEOUT')); });
    sock.on('error', reject);
  });
}
