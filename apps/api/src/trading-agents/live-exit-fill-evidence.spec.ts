/**
 * HM R4 B1 + RK 08:03 (R4 one helper, R17): the exit price of a closed live row is ONLY a real Bitfinex fill.
 * One shared helper (liveExitFillEvidence) feeds the Fly reporter (D3 on Fly) and both countability mappers; a
 * ticker/mark or entry fallback is price null + non-countable, never a $0 loss.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import {
  LIVE_REAL_EXIT_FILL_SOURCES,
  liveExitFillEvidence,
  liveExitFillEvidenceFromEvents,
  liveRowExitFillCountability,
} from './live-row-countability';
import { buildLiveCopyReports } from './live-copy-fly-reporter';
import { mapParticipantToExportRow } from './live-trade-export.mapper';

const ENTRY = 60_000;
// The relay's EXIT payload shapes per path (signal-subscriber-execution.service.ts):
const CASES: Record<string, { pl: Record<string, unknown>; source: string; price: number | null }> = {
  // normal close with authenticated close-order trades (resolveNormalCloseExchangeEvidence ok)
  exchange_fill: { pl: { exit_price: 60_120, exit_exchange_fill_price: 60_110, pnl_source: 'exchange_fill', exit_fill_status: 'REAL_FILL' }, source: 'exchange_fill', price: 60_110 },
  // resident stop fired, its own order trades read at the exact lot qty
  stop_fill: { pl: { exit_price: 59_790, pnl_source: 'bitfinex_order_trades', exit_reason: 'EXCHANGE_STOP', exchange_exit_fill_ids: ['9'], exchange_exit_filled_qty: 0.0004, exit_fill_status: 'REAL_FILL' }, source: 'stop_fill', price: 59_790 },
  // HM R6 B2: the same fill-looking fields WITHOUT the shared helper's REAL_FILL stamp (caller-supplied) => not a fill
  forged_exchange_fill: { pl: { exit_price: 60_120, exit_exchange_fill_price: 60_110, pnl_source: 'exchange_fill' }, source: 'mark', price: null },
  forged_stop_fill: { pl: { exit_price: 59_790, pnl_source: 'bitfinex_order_trades', exit_reason: 'EXCHANGE_STOP', exchange_exit_fill_ids: ['9'], exchange_exit_filled_qty: 0.0004 }, source: 'mark', price: null },
  // resident stop fired but no stop order id / its trades unreadable => mark-priced MANUAL_OR_EXCHANGE_CLOSE
  stop_fired_no_fill_read: { pl: { exit_price: 59_812.5, pnl_source: 'reconstructed', exit_reason: 'MANUAL_OR_EXCHANGE_CLOSE' }, source: 'mark', price: null },
  // EXCHANGE_STOP claimed but the fill ids are missing (no order id) => not a real fill
  stop_no_order_id: { pl: { exit_price: 59_790, pnl_source: 'bitfinex_order_trades', exit_reason: 'EXCHANGE_STOP', exit_fill_status: 'REAL_FILL' }, source: 'mark', price: null },
  // fill read failed or refused by the shared read budget => evidence {} => quote/mark price only
  fill_read_failed_or_budget_refused: { pl: { exit_price: 60_130, pnl_source: 'reconstructed', exit_reason: 'TP' }, source: 'mark', price: null },
  // partial close: qty mismatch => evidence {} (never a fill price)
  partial_close: { pl: { exit_price: 60_140, qty_closed: 0.0002, exit_reason: 'TP' }, source: 'mark', price: null },
  // non-USD fee => evidence {}
  non_usd_fee: { pl: { exit_price: 60_150, exit_reason: 'TP' }, source: 'mark', price: null },
  // already flat at close time: nothing executed, price = entry fallback
  already_flat: { pl: { exit_price: ENTRY, exit_reason: 'ALREADY_FLAT', pnl_usd: 0 }, source: 'entry_fallback', price: null },
  // post-close reconcile with no fill: exitPrice = ... ?? entry
  reconcile_entry_fallback: { pl: { exit_price: ENTRY, exit_post_close_reconciled: true, pnl_source: 'reconstructed', pnl_usd: 0 }, source: 'entry_fallback', price: null },
  // nothing at all
  none: { pl: { pnl_usd: -1.2 }, source: 'none', price: null },
};

test('B1: liveExitFillEvidence accepts only exchange_fill / stop_fill; every fallback is price null', () => {
  assert.deepEqual([...LIVE_REAL_EXIT_FILL_SOURCES].sort(), ['exchange_fill', 'stop_fill']);
  for (const [name, c] of Object.entries(CASES)) {
    const ev = liveExitFillEvidence(c.pl, ENTRY);
    assert.equal(ev.source, c.source, name);
    assert.equal(ev.price, c.price, name);
    const cnt = liveRowExitFillCountability(ev);
    assert.equal(cnt.nonCountable, c.price == null, name);
    if (c.price == null) assert.equal(cnt.nonCountableReason, `EXIT_FILL_UNREADABLE:${c.source}`, name);
  }
  assert.equal(liveRowExitFillCountability(null).nonCountableReason, 'EXIT_FILL_UNREADABLE:none');
});

const T0 = 1_760_000_000_000;
const at = (ms: number) => new Date(T0 + ms);
const envelope = { context: { platform_received_at: at(400).toISOString(), fly_live_approval: { correlation_id: 'c', signal_at_ts: T0 / 1000, created_at_ts: (T0 + 200) / 1000 } } };
const ev = (id: string, eventType: string, ms: number, payload: Record<string, unknown>) => ({ id, participantId: 'p1', eventType, payload, createdAt: at(ms), platformReceivedAt: null });
const base = [
  ev('e2', 'ORDER_PLACED', 850, { limit_price: 60_005, qty: 0.0004, direction: 'LONG', bitfinexOrderId: 111 }),
  ev('e3', 'FILLED', 5_000, { fill_price: ENTRY, qty: 0.0004, stop_loss_placed: true, stopOrderId: 222 }),
];
const closeOf = (pl: Record<string, unknown>) => (buildLiveCopyReports({
  cycle: { id: 'c1', tradeId: 't1', intentEnvelope: envelope }, correlationId: 'c', lane: 'L',
  events: [...base, ev('x', 'EXIT', 9_000, pl)], participants: [{ id: 'p1', userId: 'u1' }], accountFor: () => 'a', nowMs: T0 + 10_000,
}).find((r) => r.type === 'POSITION_CLOSED') as any).close;

test('B1: the Fly reporter sends close.price ONLY for a real fill, plus price_source; no ticker/entry fallback', () => {
  for (const [name, c] of Object.entries(CASES)) {
    const close = closeOf(c.pl);
    assert.equal(close.price, c.price, name);
    assert.equal(close.price_source, c.source, name);
  }
  // RK 08:03 required: stop fired, no fill read -> price null (Fly D3 UNREADABLE => entry block), never entry/$0
  assert.equal(closeOf(CASES.stop_fired_no_fill_read.pl).price, null);
  assert.equal(closeOf(CASES.fill_read_failed_or_budget_refused.pl).price, null);
  const src = readFileSync(join(__dirname, 'live-copy-fly-reporter.ts'), 'utf8');
  assert.doesNotMatch(src, /exit_exchange_fill_price\)\s*\?\?\s*num\(pl\.exit_price\)/);
  assert.match(src, /liveExitFillEvidence\(pl,/);
});

test('B1 / RK 08:03: stop-fired-no-fill and budget-refused exits are non-countable on the export row (same helper)', () => {
  const iso = (s: number) => new Date(s * 1000).toISOString();
  const env = { context: { fly_live_approval: { signal_at_ts: 9, created_at_ts: 9.1 }, platform_received_at: iso(9.2) } };
  const now = new Date('2026-10-24T00:00:00Z');
  const placed = { eventType: 'ORDER_PLACED', payload: { entry_exchange_ack_at: iso(9.6) }, createdAt: now };
  const legs = { exit_trigger_ts: 49.9, fly_send_started_at: iso(50.01), platform_received_at: iso(50.08), close_exchange_ack_at: iso(50.4) };
  // HM R5 nit 6: a countable row also needs the entry from the entry order's own trades (FILLED exchange_trades)
  const realFilled = { eventType: 'FILLED', payload: { fill_price: ENTRY, fill_price_source: 'exchange_trades' }, createdAt: now };
  const row = (pl: Record<string, unknown>, withFilled = false) => mapParticipantToExportRow({
    participant: { id: 'p', status: SignalCycleStatus.CLOSED, venue: 'bitfinex', fillPrice: ENTRY, exitPrice: Number(pl.exit_price ?? ENTRY),
      pnlUsd: 0, pnlMarginPct: 0, feeUsd: null, createdAt: now, updatedAt: now,
      events: [placed, ...(withFilled ? [realFilled, { eventType: 'STOP_LOSS_ARMED', payload: {}, createdAt: now }] : []), { eventType: 'EXIT', payload: { ...legs, ...pl }, createdAt: now }] },
    cycle: { id: 'c', tradeId: 't', intentEnvelope: env, showcaseExitReason: 'TP', createdAt: now, closedAt: now },
  });
  for (const name of ['stop_fired_no_fill_read', 'fill_read_failed_or_budget_refused', 'already_flat', 'reconcile_entry_fallback', 'partial_close', 'non_usd_fee']) {
    const r = row(CASES[name].pl);
    assert.equal(r.nonCountable, true, name);
    assert.match(String(r.nonCountableReason), /^EXIT_FILL_UNREADABLE:(mark|entry_fallback)$/, name);   // no FILLED event here => no entry ref
  }
  assert.deepEqual([row(CASES.exchange_fill.pl, true).nonCountable, row(CASES.exchange_fill.pl, true).nonCountableReason], [false, null]);
  // HM R5 nit 6: the same real exit with no real entry fill stays non-countable
  assert.equal(row(CASES.exchange_fill.pl).nonCountableReason, 'ENTRY_FILL_UNREADABLE:none');
  // FromEvents picks the LATEST EXIT and the FILLED entry price for entry-fallback detection
  assert.equal(liveExitFillEvidenceFromEvents([{ eventType: 'FILLED', payload: { fill_price: 61_000 } },
    { eventType: 'EXIT', payload: { exit_price: 61_000 } }]).source, 'entry_fallback');
  for (const f of ['live-trade-export.mapper.ts', 'subscriber-exchange-live.mapper.ts']) {
    assert.match(readFileSync(join(__dirname, f), 'utf8'), /liveExitFillEvidenceFromEvents\(/, f);
  }
});
