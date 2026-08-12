/** Fold signal-cycle hire events into virtual-lot execution meta for UI + exports. */
export type ParticipantExecutionMeta = {
  limitPrice: number | null;
  qty: number | null;
  stopPrice: number | null;
  profitLockFloor: number | null;
  direction: 'LONG' | 'SHORT' | null;
  terminalReason: string | null;
  exchangeProven: boolean;
  /** An opt-in, source-owned better-or-equal entry continuation is active. */
  lateEntryContinuation: boolean;
};

type EventRow = { eventType: string; payload: unknown };

function impliedHardStopPrice(
  merged: Record<string, unknown>,
  direction: 'LONG' | 'SHORT',
  entry: number,
): number | null {
  const levRaw = merged.leverage ?? merged.leverage_hint;
  const leverage =
    typeof levRaw === 'number' && Number.isFinite(levRaw) && levRaw > 0 ? levRaw : 100;
  const stopMarginRaw = merged.stop_loss_margin_pct ?? merged.stopLossMarginPct;
  const stopMarginPct =
    typeof stopMarginRaw === 'number' && Number.isFinite(stopMarginRaw) ? stopMarginRaw : -18;
  const distance = Math.abs(stopMarginPct) / (100 * Math.max(leverage, 1));
  if (direction === 'LONG') return Math.round(entry * (1 - distance) * 100) / 100;
  return Math.round(entry * (1 + distance) * 100) / 100;
}

export function foldParticipantExecutionMeta(events: EventRow[]): ParticipantExecutionMeta {
  const merged: Record<string, unknown> = {};
  let terminalReason: string | null = null;
  let exchangeProven = false;
  for (const e of events) {
    if (e.payload && typeof e.payload === 'object') {
      const payload = e.payload as Record<string, unknown>;
      Object.assign(merged, payload);
      const exchangeId =
        payload.exchangeOrderId ??
        payload.exchange_order_id ??
        payload.bitfinexOrderId ??
        payload.bitfinex_order_id ??
        payload.orderId ??
        payload.order_id;
      const exchangeIds = payload.exchangeOrderIds ?? payload.exchange_order_ids;
      if (
        (typeof exchangeId === 'string' && exchangeId.trim()) ||
        (typeof exchangeId === 'number' && Number.isFinite(exchangeId)) ||
        (Array.isArray(exchangeIds) && exchangeIds.length > 0)
      ) {
        exchangeProven = true;
      }
      if (e.eventType === 'EXPIRED' || e.eventType === 'EXIT') {
        const reason = payload.reason ?? payload.exit_reason ?? payload.event;
        if (typeof reason === 'string' && reason.trim()) terminalReason = reason.trim();
      }
    }
  }

  const limitRaw = merged.limitPrice ?? merged.limit_price;
  const qtyRaw = merged.qty;
  const stopRaw = merged.stop_price ?? merged.stopPrice;
  const floorRaw = merged.profitLockFloor ?? merged.lock_floor_margin_pct;
  const dirRaw = merged.direction;
  const lateEntryContinuationRaw = merged.lateEntryContinuation ?? merged.late_entry_continuation;
  const fillRaw = merged.fill_price ?? merged.fillPrice;

  const limitPrice =
    typeof limitRaw === 'number' && Number.isFinite(limitRaw) && limitRaw > 0 ? limitRaw : null;
  const qty = typeof qtyRaw === 'number' && Number.isFinite(qtyRaw) && qtyRaw > 0 ? qtyRaw : null;
  let stopPrice =
    typeof stopRaw === 'number' && Number.isFinite(stopRaw) && stopRaw > 0 ? stopRaw : null;
  const profitLockFloor =
    typeof floorRaw === 'number' && Number.isFinite(floorRaw) && floorRaw > 0 ? floorRaw : null;
  const direction =
    dirRaw === 'LONG' || dirRaw === 'SHORT' ? dirRaw : null;
  const lateEntryContinuation = lateEntryContinuationRaw === true;

  if (!stopPrice && direction) {
    const entry =
      typeof fillRaw === 'number' && Number.isFinite(fillRaw) && fillRaw > 0
        ? fillRaw
        : limitPrice;
    if (entry && entry > 0) {
      stopPrice = impliedHardStopPrice(merged, direction, entry);
    }
  }

  return {
    limitPrice,
    qty,
    stopPrice,
    profitLockFloor,
    direction,
    terminalReason,
    exchangeProven,
    lateEntryContinuation,
  };
}

/** @deprecated Use foldParticipantExecutionMeta */
export function latestParticipantExecutionMeta(
  events: EventRow[],
): { limitPrice: number | null; qty: number | null } {
  const m = foldParticipantExecutionMeta(events);
  return { limitPrice: m.limitPrice, qty: m.qty };
}
