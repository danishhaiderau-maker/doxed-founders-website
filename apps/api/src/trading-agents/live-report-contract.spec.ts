import assert from 'node:assert/strict';
import { readFileSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { buildLiveCopyReports, type ReporterEvent } from './live-copy-fly-reporter';
import { EXIT_FILL_RECONCILED_EVENT, liveOrderExitFill } from './live-row-countability';

// HM R6 B1 (contract): the EXACT reporter output Fly ingests (live_copy_monitor.build_chains -> live_tile_gate / live_guardian).
// The fixture is generated here from the real buildLiveCopyReports + the shared exit-fill helper; the Python contract test
// (test_hm_r6_report_contract.py) feeds it to the monitor, the tile gate and the guardian. Drift on either side fails CI.
// Regenerate: UPDATE_LIVE_REPORT_FIXTURE=1 npx tsx --test src/trading-agents/live-report-contract.spec.ts
export const FIXTURE = join(__dirname, '../../../../services/btc-conservative-agent/fixtures/live_report_contract.json');

const T0 = Date.UTC(2026, 9, 24, 1, 0, 0);          // 24 Oct 2026 01:00 UTC
const ENTRY = 100_000;
const QTY = 0.002;

function trade(cid: string, startMs: number, exits: Array<{ type: string; ms: number; fill: 'real' | 'partials' | 'none' }>) {
  const ev: ReporterEvent[] = [];
  let n = 0;
  const add = (eventType: string, atMs: number, payload: Record<string, unknown>) =>
    ev.push({ id: `${cid}-e${++n}`, participantId: 'p1', eventType, payload, createdAt: new Date(atMs), platformReceivedAt: null });
  add('ORDER_PLACED', startMs, { limit_price: ENTRY + 5, direction: 'LONG', qty: QTY, order_type: 'LIMIT', time_in_force: 'GTC', bitfinexOrderId: 501 });
  add('FILLED', startMs + 2_000, { fill_price: ENTRY, fill_price_source: 'exchange_trades', qty: QTY,
    exchange_fill_last_at: new Date(startMs + 1_500).toISOString() });
  add('STOP_LOSS_ARMED', startMs + 3_000, { stop_price: 99_700, stopOrderId: 7001, qty: QTY, stop_exchange_ack_at: new Date(startMs + 2_800).toISOString() });
  for (const x of exits) {
    const reads = x.fill === 'none' ? [{ orderId: 7001, read: null }]
      : x.fill === 'real' ? [{ orderId: 7001, read: { price: 99_690, qty: QTY, fillIds: [1], firstExecutedAtMs: x.ms - 400, lastExecutedAtMs: x.ms - 400 } }]
        : [{ orderId: 7001, read: { price: (0.0012 * 99_690 + 0.0008 * 99_640) / QTY, qty: QTY, fillIds: [2, 3], firstExecutedAtMs: x.ms - 900, lastExecutedAtMs: x.ms - 300 } }];
    const f = liveOrderExitFill({ kind: 'stop', expectedQty: QTY, reads, direction: 'LONG', entryFillPrice: ENTRY, leverage: 10 });
    add(x.type, x.ms, { reason: 'EXCHANGE_STOP', exit_order_class: 'EXCHANGE_RESIDENT', ...f.fields });
  }
  return buildLiveCopyReports({
    cycle: { id: `cyc-${cid}`, tradeId: cid, intentEnvelope: { context: { fly_live_approval: { correlation_id: cid, signal_at_ts: startMs / 1000 - 1, created_at_ts: startMs / 1000 - 0.5 } } } },
    correlationId: cid, lane: 'gs1', events: ev, participants: [{ id: 'p1', userId: 'u1' }],
    accountFor: () => 'A', nowMs: T0 + 3_600_000,
  });
}

export function contractReports() {
  return {
    t0_ms: T0,
    // a stop-out loss at T0 + 10 min (real stop fill)
    loss: trade('gs1-loss', T0, [{ type: 'EXIT', ms: T0 + 600_000, fill: 'real' }]),
    // unreadable at the stop-out, then the clearing read re-stamps with the VWAP of two partial fills
    cleared: trade('gs1-cleared', T0 + 60_000, [
      { type: 'EXIT', ms: T0 + 700_000, fill: 'none' },
      { type: EXIT_FILL_RECONCILED_EVENT, ms: T0 + 900_000, fill: 'partials' },
    ]),
    // unreadable and never cleared
    unreadable: trade('gs1-unread', T0 + 120_000, [{ type: 'EXIT', ms: T0 + 800_000, fill: 'none' }]),
  };
}

test('HM R6 B1: the committed reporter-contract fixture is exactly what buildLiveCopyReports emits', () => {
  const now = JSON.parse(JSON.stringify(contractReports()));
  if (process.env.UPDATE_LIVE_REPORT_FIXTURE === '1') writeFileSync(FIXTURE, `${JSON.stringify(now, null, 2)}\n`);
  assert.deepEqual(JSON.parse(readFileSync(FIXTURE, 'utf8')), now);
});

test('HM R6 B1: every POSITION_CLOSED with a real price carries close.fill_at_ts = Bitfinex last fill (s); unreadable => null', () => {
  const r = contractReports();
  const closes = (rs: any[]) => rs.filter((x) => x.type === 'POSITION_CLOSED').map((x) => x.close);
  assert.equal(closes(r.loss)[0].fill_at_ts, (T0 + 600_000 - 400) / 1000);
  assert.equal(closes(r.loss)[0].price, 99_690);
  const [u, c] = closes(r.cleared);
  assert.equal(u.price, null);
  assert.equal(u.fill_at_ts, null);
  assert.equal(c.fill_at_ts, (T0 + 900_000 - 300) / 1000);
  assert.equal(c.reconciled, true);
  for (const k of ['ts', 'closed_at_ts']) assert.ok(!(k in closes(r.loss)[0]), `reporter sends no close.${k}`);
});
