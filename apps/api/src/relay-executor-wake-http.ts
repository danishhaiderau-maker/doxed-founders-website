import { timingSafeEqual } from 'node:crypto';
import type { RelayExecutorWakeRequest } from './trading-agents/signal-subscriber-execution.service';

const WAKE_TRIGGERS = new Set<RelayExecutorWakeRequest['trigger']>([
  'POSITION_CLOSED',
  'POSITION_OPENED',
  'ORDER_PLACED',
  'APPROVE_PENDING',
  'LIMIT_UPDATED',
  'USER_RESUME',
  'USER_PAUSE',
]);

export function executorWakeAuthorized(supplied: string | undefined, expected: string): boolean {
  if (!supplied || !expected) return false;
  const a = Buffer.from(supplied);
  const b = Buffer.from(expected);
  return a.length === b.length && timingSafeEqual(a, b);
}

export function parseExecutorWakeRequest(value: unknown): RelayExecutorWakeRequest | null {
  if (!value || typeof value !== 'object') return null;
  const raw = value as Record<string, unknown>;
  if (typeof raw.trigger !== 'string' || !WAKE_TRIGGERS.has(raw.trigger as RelayExecutorWakeRequest['trigger'])) {
    return null;
  }
  if (typeof raw.at !== 'string') return null;
  const atMs = Date.parse(raw.at);
  if (!Number.isFinite(atMs) || Math.abs(Date.now() - atMs) > 120_000) return null;
  if (raw.tradeId != null && (typeof raw.tradeId !== 'string' || raw.tradeId.length > 255)) {
    return null;
  }
  return {
    trigger: raw.trigger as RelayExecutorWakeRequest['trigger'],
    at: raw.at,
    tradeId: typeof raw.tradeId === 'string' ? raw.tradeId : null,
  };
}
