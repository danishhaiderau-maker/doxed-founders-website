import { timingSafeEqual } from 'node:crypto';
import type { RelayExecutorWakeRequest } from './trading-agents/signal-subscriber-execution.service';
import { isMirrorableLaneTradeId } from '@dcf/utils';

const WAKE_TRIGGERS = new Set<RelayExecutorWakeRequest['trigger']>([
  'POSITION_CLOSED',
  'ORDER_EXPIRED',
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
  let signedClose: RelayExecutorWakeRequest['signedClose'];
  if (raw.trigger === 'POSITION_CLOSED') {
    if (!isMirrorableLaneTradeId(typeof raw.tradeId === 'string' ? raw.tradeId : null)) return null;
    if (!raw.signedClose || typeof raw.signedClose !== 'object' || Array.isArray(raw.signedClose)) return null;
    const close = raw.signedClose as Record<string, unknown>;
    if (typeof close.tradeId !== 'string' || close.tradeId !== raw.tradeId) return null;
    if (typeof close.eventId !== 'string' || !close.eventId.trim() || close.eventId.length > 255) return null;
    if (typeof close.eventSeq !== 'number' || !Number.isInteger(close.eventSeq) || close.eventSeq < 0) return null;
    if (typeof close.exitPrice !== 'number' || !Number.isFinite(close.exitPrice) || close.exitPrice <= 0) return null;
    if (typeof close.exitReason !== 'string' || !close.exitReason.trim() || close.exitReason.length > 255) return null;
    if (typeof close.sourceEventAtMs !== 'number' || !Number.isFinite(close.sourceEventAtMs)) return null;
    if (typeof close.platformReceivedAtMs !== 'number' || !Number.isFinite(close.platformReceivedAtMs)) return null;
    if (close.sourceEventAtMs > close.platformReceivedAtMs || close.platformReceivedAtMs > atMs) return null;
    if (atMs - close.sourceEventAtMs > 300_000 || atMs - close.platformReceivedAtMs > 5_000) return null;
    signedClose = {
      tradeId: close.tradeId,
      eventId: close.eventId.trim(),
      eventSeq: close.eventSeq,
      exitPrice: close.exitPrice,
      exitReason: close.exitReason.trim(),
      sourceEventAtMs: close.sourceEventAtMs,
      platformReceivedAtMs: close.platformReceivedAtMs,
    };
  } else if (raw.signedClose != null) return null;
  let signedOpen: RelayExecutorWakeRequest['signedOpen'];
  if (raw.trigger === 'POSITION_OPENED') {
    if (!isMirrorableLaneTradeId(typeof raw.tradeId === 'string' ? raw.tradeId : null)) return null;
    if (!raw.signedOpen || typeof raw.signedOpen !== 'object' || Array.isArray(raw.signedOpen)) return null;
    const open = raw.signedOpen as Record<string, unknown>;
    if (
      typeof open.fillPrice !== 'number' || !Number.isFinite(open.fillPrice) || open.fillPrice <= 0
      || typeof open.sourceEventAtMs !== 'number' || !Number.isFinite(open.sourceEventAtMs)
      || typeof open.platformReceivedAtMs !== 'number' || !Number.isFinite(open.platformReceivedAtMs)
    ) return null;
    if (open.sourceEventAtMs > open.platformReceivedAtMs || open.platformReceivedAtMs > atMs) return null;
    if (atMs - open.sourceEventAtMs > 300_000 || atMs - open.platformReceivedAtMs > 5_000) return null;
    signedOpen = {
      fillPrice: open.fillPrice,
      sourceEventAtMs: open.sourceEventAtMs,
      platformReceivedAtMs: open.platformReceivedAtMs,
    };
  } else if (raw.signedOpen != null) return null;
  let signedExpiry: RelayExecutorWakeRequest['signedExpiry'];
  if (raw.trigger === 'ORDER_EXPIRED') {
    if (!isMirrorableLaneTradeId(typeof raw.tradeId === 'string' ? raw.tradeId : null)) return null;
    if (!raw.signedExpiry || typeof raw.signedExpiry !== 'object' || Array.isArray(raw.signedExpiry)) return null;
    const expiry = raw.signedExpiry as Record<string, unknown>;
    if (
      typeof expiry.sourceEventAtMs !== 'number'
      || typeof expiry.sourceExpiresAtMs !== 'number'
      || typeof expiry.platformReceivedAtMs !== 'number'
      || typeof expiry.eventSeq !== 'number'
      || typeof expiry.limitPrice !== 'number'
      || typeof expiry.eventId !== 'string'
      || typeof expiry.reason !== 'string'
    ) return null;
    const { sourceEventAtMs, sourceExpiresAtMs, platformReceivedAtMs, eventSeq, limitPrice } = expiry;
    const eventId = expiry.eventId.trim();
    const reason = expiry.reason as 'SIGNAL_TTL_EXPIRED' | 'TTL_EXPIRED';
    if (![sourceEventAtMs, sourceExpiresAtMs, platformReceivedAtMs, limitPrice].every(Number.isFinite)) return null;
    if (!Number.isInteger(eventSeq) || eventSeq < 0 || limitPrice <= 0) return null;
    if (!eventId || eventId.length > 255 || !['SIGNAL_TTL_EXPIRED', 'TTL_EXPIRED'].includes(reason)) return null;
    if (sourceExpiresAtMs > sourceEventAtMs || sourceEventAtMs > platformReceivedAtMs || platformReceivedAtMs > atMs) return null;
    if (atMs - sourceExpiresAtMs > 300_000 || atMs - platformReceivedAtMs > 5_000) return null;
    signedExpiry = { sourceEventAtMs, sourceExpiresAtMs, platformReceivedAtMs, eventSeq, limitPrice, eventId, reason };
  } else if (raw.signedExpiry != null) return null;
  return {
    trigger: raw.trigger as RelayExecutorWakeRequest['trigger'],
    at: raw.at,
    tradeId: typeof raw.tradeId === 'string' ? raw.tradeId : null,
    ...(signedClose ? { signedClose } : {}),
    ...(signedOpen ? { signedOpen } : {}),
    ...(signedExpiry ? { signedExpiry } : {}),
  };
}
