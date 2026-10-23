import { btcToSats } from '@dcf/utils';
import {
  BITFINEX_BTC_PERP_SYMBOL,
  BitfinexMultiplePositionsError,
  type BitfinexPositionDetail,
} from '../exchanges/bitfinex-api.client';

/**
 * Additive Bitfinex POSITION_ID stamp (HM spec + RK ruling 2026-10-24).
 *
 * The pid is the REAL id Bitfinex returned in the EXISTING post-fill positions
 * read-back (`auth/r/positions`, row[11]) — never inferred, never picked from
 * several candidates. Every failure resolves to `bitfinex_position_id: null`
 * with a status; nothing here throws.
 */
export const BITFINEX_POSITION_ID_STAMP_EVENT = 'BITFINEX_POSITION_ID_STAMP' as const;
export const BITFINEX_POSITION_ID_STAMP_SCHEMA = 'bitfinex_position_id_stamp_v1' as const;

export type BitfinexPositionIdStampStatus =
  | 'STAMPED'
  | 'READ_FAILED'
  | 'MULTI_POSITION'
  | 'NO_POSITION'
  | 'SYMBOL_MISMATCH'
  | 'SIDE_MISMATCH'
  | 'AMOUNT_MISMATCH'
  | 'POSITION_ID_ABSENT'
  /** row[12] MTS_CREATE is later than the fill we are protecting (+ tolerance): a different position. */
  | 'CREATE_TIME_MISMATCH'
  /** row[12] MTS_CREATE missing / not a positive integer: the create-time tie cannot be proven. */
  | 'CREATE_TIME_ABSENT';

/**
 * HM N1: the read-back position must already exist when we observed the fill.
 * Tolerance absorbs Bitfinex-server vs. local clock skew only.
 */
export const BITFINEX_POSITION_CREATE_TIME_TOLERANCE_MS = 1_000;

export type BitfinexPositionReadOutcome =
  | { ok: true; position: BitfinexPositionDetail | null }
  | { ok: false; error: unknown };

/** The filled order the read-back must match exactly. */
export type BitfinexPositionIdStampExpectation = {
  symbol?: string;
  direction: 'LONG' | 'SHORT';
  /** Protected (filled) qty in BTC; the position must equal it to the satoshi. */
  qty: number;
};

export type BitfinexPositionIdStampPayload = {
  schema: typeof BITFINEX_POSITION_ID_STAMP_SCHEMA;
  venue: 'bitfinex';
  source: 'hire';
  status: BitfinexPositionIdStampStatus;
  bitfinex_position_id: number | null;
  /** When the positions read-back returned (or failed). */
  position_id_read_at: string;
  position_id_read_started_at: string;
  read_source: 'POST_STOP_POSITIONS_READBACK';
  symbol: string;
  expected_side: 'LONG' | 'SHORT';
  expected_qty: number;
  position_side: 'LONG' | 'SHORT' | null;
  position_amount: number | null;
  /** row[12] MTS_CREATE of the read-back row (ISO), or null when absent. */
  position_created_at: string | null;
  /** Local time the fill was observed; the create-time tie's upper bound (+ tolerance). */
  fill_observed_at: string;
  error?: string;
};

function errorMessage(err: unknown): string {
  const msg = err instanceof Error ? err.message : String(err);
  return msg.slice(0, 200);
}

/**
 * Pure guard. Returns the pid only when the read-back shows exactly one
 * BTCF0:USTF0 position whose symbol, side and amount match the filled order
 * and Bitfinex supplied a positive integer POSITION_ID. Otherwise null.
 */
export function resolveBitfinexPositionIdStamp(input: {
  outcome: BitfinexPositionReadOutcome;
  expected: BitfinexPositionIdStampExpectation;
  readStartedAtMs: number;
  readAtMs: number;
  /** When the executor observed the fill (before the stop was submitted). Required: no tie, no pid. */
  fillObservedAtMs: number;
}): BitfinexPositionIdStampPayload {
  const symbol = input.expected.symbol ?? BITFINEX_BTC_PERP_SYMBOL;
  const base = {
    schema: BITFINEX_POSITION_ID_STAMP_SCHEMA,
    venue: 'bitfinex' as const,
    source: 'hire' as const,
    position_id_read_at: new Date(input.readAtMs).toISOString(),
    position_id_read_started_at: new Date(input.readStartedAtMs).toISOString(),
    read_source: 'POST_STOP_POSITIONS_READBACK' as const,
    symbol,
    expected_side: input.expected.direction,
    expected_qty: input.expected.qty,
    fill_observed_at: Number.isFinite(input.fillObservedAtMs)
      ? new Date(input.fillObservedAtMs).toISOString()
      : 'INVALID',
  };
  const createdAtIso = (position: BitfinexPositionDetail | null): string | null => {
    const c = position?.createdAtMs;
    return typeof c === 'number' && Number.isSafeInteger(c) && c > 0 ? new Date(c).toISOString() : null;
  };
  const nullStamp = (
    status: Exclude<BitfinexPositionIdStampStatus, 'STAMPED'>,
    position: BitfinexPositionDetail | null,
    error?: string,
  ): BitfinexPositionIdStampPayload => ({
    ...base,
    status,
    bitfinex_position_id: null,
    position_side: position?.direction ?? null,
    position_amount: position?.amount ?? null,
    position_created_at: createdAtIso(position),
    ...(error ? { error } : {}),
  });

  try {
    if (!input.outcome.ok) {
      const err = input.outcome.error;
      const multi = err instanceof BitfinexMultiplePositionsError
        || (err instanceof Error && err.name === 'BitfinexMultiplePositionsError');
      return nullStamp(multi ? 'MULTI_POSITION' : 'READ_FAILED', null, errorMessage(err));
    }
    const position = input.outcome.position;
    if (!position) return nullStamp('NO_POSITION', null);
    if (position.symbol !== symbol) return nullStamp('SYMBOL_MISMATCH', position);
    const amount = Number(position.amount);
    const side: 'LONG' | 'SHORT' | null = amount > 0 ? 'LONG' : amount < 0 ? 'SHORT' : null;
    if (side !== input.expected.direction || position.direction !== input.expected.direction) {
      return nullStamp('SIDE_MISMATCH', position);
    }
    const expectedSats = btcToSats(Math.abs(input.expected.qty));
    if (!(expectedSats > 0) || btcToSats(Math.abs(amount)) !== expectedSats) {
      return nullStamp('AMOUNT_MISMATCH', position);
    }
    const pid = position.positionId;
    if (typeof pid !== 'number' || !Number.isSafeInteger(pid) || pid <= 0) {
      return nullStamp('POSITION_ID_ABSENT', position);
    }
    // HM N1: tie the id to THIS fill by row[12] MTS_CREATE from the same row.
    const createdAtMs = position.createdAtMs;
    if (typeof createdAtMs !== 'number' || !Number.isSafeInteger(createdAtMs) || createdAtMs <= 0) {
      return nullStamp('CREATE_TIME_ABSENT', position);
    }
    if (
      !Number.isFinite(input.fillObservedAtMs)
      || createdAtMs > input.fillObservedAtMs + BITFINEX_POSITION_CREATE_TIME_TOLERANCE_MS
    ) {
      return nullStamp('CREATE_TIME_MISMATCH', position);
    }
    return {
      ...base,
      status: 'STAMPED',
      bitfinex_position_id: pid,
      position_side: side,
      position_amount: amount,
      position_created_at: createdAtIso(position),
    };
  } catch (err) {
    return nullStamp('READ_FAILED', null, errorMessage(err));
  }
}

/** Latest stamp among a participant's events (newest wins), or null. */
export function latestBitfinexPositionIdStamp(
  events: ReadonlyArray<{ eventType: string; payload: unknown; createdAt?: Date | string | null }>,
): BitfinexPositionIdStampPayload | null {
  let best: { at: number; idx: number; stamp: BitfinexPositionIdStampPayload } | null = null;
  events.forEach((e, idx) => {
    if (e.eventType !== BITFINEX_POSITION_ID_STAMP_EVENT) return;
    const p = e.payload as Partial<BitfinexPositionIdStampPayload> | null;
    if (!p || typeof p !== 'object' || p.schema !== BITFINEX_POSITION_ID_STAMP_SCHEMA) return;
    const at = e.createdAt ? new Date(e.createdAt).getTime() : 0;
    if (!best || at > best.at || (at === best.at && idx > best.idx)) {
      best = { at, idx, stamp: p as BitfinexPositionIdStampPayload };
    }
  });
  return best ? (best as { stamp: BitfinexPositionIdStampPayload }).stamp : null;
}
