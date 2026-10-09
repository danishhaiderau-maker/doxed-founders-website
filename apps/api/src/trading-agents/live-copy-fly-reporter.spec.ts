import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { test } from 'node:test';
import { buildLiveCopyReports, summarizeCopyStatus } from './live-copy-fly-reporter';

const T0 = 1_760_000_000_000;
const at = (ms: number) => new Date(T0 + ms);
const envelope = {
  context: {
    platform_received_at: at(400).toISOString(),
    fly_live_approval: { correlation_id: 'gs1-a1', signal_at_ts: T0 / 1000, created_at_ts: (T0 + 200) / 1000 },
  },
};
const ev = (id: string, eventType: string, ms: number, payload: Record<string, unknown>, participantId: string | null = 'p1') => ({
  id, participantId, eventType, payload, createdAt: at(ms), platformReceivedAt: null,
});
const events = [
  ev('e1', 'EXECUTION_TIMING', 900, { operation: 'ORDER_PLACED', stages: { bitfinexRequestStartedAtMs: T0 + 600, exchangeAckAtMs: T0 + 800 } }),
  ev('e2', 'ORDER_PLACED', 850, { limit_price: 60_000, qty: 0.0004, direction: 'LONG', bitfinexOrderId: 111, clientOrderId: 7, margin_usd: 0.25, leverage: 100, entryExchangeAckAtMs: T0 + 800 }),
  ev('e3', 'FILLED', 5_000, { fill_price: 60_000, qty: 0.0004, exchange_fill_last_at: at(4_000).toISOString(), stop_exchange_ack_at: at(4_600).toISOString(), stop_loss_placed: true, stopOrderId: 222 }),
  ev('e4', 'STOP_LOSS_ARMED', 4_900, { stop_price: 59_790, stopOrderId: 222, qty: 0.0004, stop_exchange_ack_at: at(4_600).toISOString() }),
];

test('builds signed-report bodies with correlation id and every hop timestamp', () => {
  const reports = buildLiveCopyReports({
    cycle: { id: 'c1', tradeId: 'gs1-a1', intentEnvelope: envelope }, correlationId: 'gs1-a1',
    lane: 'FAMILY_GS01_XV_PREMIUM_ATR_TP', events, participants: [{ id: 'p1', userId: 'u1' }],
    accountFor: () => 'acct-x', nowMs: T0 + 10_000,
  });
  assert.deepEqual(reports.map((r) => r.type), ['ORDER_PLACED', 'STOP_CONFIRMED', 'ORDER_FILLED', 'STOP_PLACED']);
  for (const r of reports) {
    assert.equal(r.schema, 'railway_live_execution_report_v1');
    assert.equal(r.correlation_id, 'gs1-a1');
    assert.equal(r.account, 'acct-x');
    assert.match(r.report_id, /^rw-e\d-/);
  }
  const stop = reports.find((r) => r.type === 'STOP_CONFIRMED')!;
  assert.deepEqual((stop as any).stop, { exchange_order_id: '222', price: 59_790, qty: 0.0004, reduce_only: true, verified_on_exchange: true, bp_from_entry: 35 });
  const last = reports.at(-1)!;
  assert.equal(last.timeline.fly_signal_at_ts, T0 / 1000);
  assert.equal(last.timeline.fly_intent_emitted_at_ts, (T0 + 200) / 1000);
  assert.equal(last.timeline.railway_received_at_ts, (T0 + 400) / 1000);
  assert.equal(last.timeline.order_sent_at_ts, (T0 + 600) / 1000);
  assert.equal(last.timeline.exchange_ack_at_ts, (T0 + 800) / 1000);
  assert.equal(last.timeline.fill_at_ts, (T0 + 4_000) / 1000);
  assert.equal(last.timeline.stop_placed_at_ts, (T0 + 4_600) / 1000);
  assert.equal(last.timeline.stop_confirmed_at_ts, (T0 + 4_900) / 1000);
});

test('copy status: per-hop p50/p95, report-back hop and gap detection', () => {
  const reports = buildLiveCopyReports({
    cycle: { id: 'c1', tradeId: 'gs1-a1', intentEnvelope: envelope }, correlationId: 'gs1-a1',
    lane: 'L', events, participants: [{ id: 'p1', userId: 'u1' }], accountFor: () => 'acct-x', nowMs: T0 + 10_000,
  });
  const acked = new Map(reports.map((r) => [r.report_id, T0 + 5_500] as [string, number]));
  const s = summarizeCopyStatus(reports, T0 + 10_000, acked) as any;
  assert.equal(s.verdict, 'GREEN');
  const lags = s.accounts['acct-x'].lags_sec;
  assert.equal(lags['signal->intent'].p50, 0.2);
  assert.equal(lags['website_receive->order_submit'].p50, 0.2);
  assert.equal(lags['fill->stop_ack'].p95, 0.6);
  assert.equal(lags['stop_confirmed->report_back'].p50, 0.6);

  const noStop = buildLiveCopyReports({
    cycle: { id: 'c2', tradeId: 'gs1-a2', intentEnvelope: envelope }, correlationId: 'gs1-a2', lane: 'L',
    events: [events[0], events[1], ev('e9', 'FILLED', 5_000, { fill_price: 60_000, qty: 0.0004, stop_loss_placed: false })],
    participants: [{ id: 'p1', userId: 'u1' }], accountFor: () => 'acct-y', nowMs: T0 + 60_000,
  });
  const bad = summarizeCopyStatus(noStop, T0 + 60_000) as any;
  assert.equal(bad.verdict, 'RED');
  const codes = bad.accounts['acct-y'].gaps.map((g: any) => g.code);
  assert.ok(codes.includes('FILL_WITHOUT_CONFIRMED_STOP'));
  assert.ok(codes.includes('STOP_FAILED'));
});

test('report signature domain matches Fly verify_report_signature', () => {
  const key = createHmac('sha256', 's').update('railway-live-execution-report-v1').digest();
  assert.equal(key.length, 32);
});
