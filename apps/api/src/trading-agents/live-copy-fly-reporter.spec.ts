import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  buildExecutorCapabilityReport,
  buildLiveCopyReports,
  EXECUTOR_CAPABILITY_SCHEMA,
  EXECUTOR_PROTECTIVE_STOP_SPEC,
  summarizeCopyStatus,
  type ReporterEvent,
} from './live-copy-fly-reporter';
import { signExecutorCapability, signLiveExecutionReport } from './live-copy-approval';
import { BITFINEX_REDUCE_ONLY_FLAG } from '../exchanges/bitfinex-api.client';

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

test('capability report matches the stop submitStopOrder really sends', () => {
  const src = readFileSync(join(__dirname, '../exchanges/bitfinex-api.client.ts'), 'utf8');
  const body = src.slice(src.indexOf('async submitStopOrder'), src.indexOf('async submitMarketClose'));
  assert.ok(body.includes("type: 'STOP'"));
  assert.ok(body.includes('flags: BITFINEX_REDUCE_ONLY_FLAG'));
  assert.deepEqual({ ...EXECUTOR_PROTECTIVE_STOP_SPEC }, { type: 'STOP', flags: BITFINEX_REDUCE_ONLY_FLAG });
});

test('capability report declares reduce-only STOP on tBTCF0:USTF0', () => {
  const r = buildExecutorCapabilityReport(1_700_000_000_000);
  assert.equal(r.schema, EXECUTOR_CAPABILITY_SCHEMA);
  assert.equal(r.symbol, 'tBTCF0:USTF0');
  assert.equal(r.reduce_only_supported, true);
  assert.deepEqual(r.protective_stop, { order_type: 'STOP', flags: 1024, reduce_only: true, placed: 'ONCE_AT_ENTRY_FILL' });
  assert.equal(r.sent_at_ts, 1_700_000_000);
});

test('capability report is signed under its own HMAC domain (Python-compatible)', () => {
  const body = JSON.stringify(buildExecutorCapabilityReport(1));
  const sig = signExecutorCapability(body, 's3cret');
  const key = createHmac('sha256', 's3cret').update('railway-executor-capability-v1').digest();
  assert.equal(sig, `sha256=${createHmac('sha256', key).update(body).digest('hex')}`);
  assert.notEqual(sig, signLiveExecutionReport(body, 's3cret'));
  assert.equal(signExecutorCapability(body, ''), null);
});

// ---- GS-01 first live trade gaps (11 Oct 2026) ----
const build = (evs: ReporterEvent[], env: unknown = envelope) => buildLiveCopyReports({
  cycle: { id: 'c1', tradeId: 'gs1-a1', intentEnvelope: env }, correlationId: 'gs1-a1',
  lane: 'FAMILY_GS01_XV_PREMIUM_ATR_TP', events: evs, participants: [{ id: 'p1', userId: 'u1' }],
  accountFor: () => 'acct-x', nowMs: T0 + 10_000,
});

test('pre-submit fence ENTRY_SUBMISSION_UNKNOWN is not an ERROR once the entry resolved', () => {
  const fence = ev('e0', 'ENTRY_SUBMISSION_UNKNOWN', 500, { schema: 'entry_submission_fence_v1', phase: 'SUBMITTING' });
  assert.equal(build([fence, ...events]).filter((r) => r.type === 'ERROR').length, 0);
  // Unresolved fence: in-flight (no error) inside the 60 s grace, a real error after it.
  assert.equal(build([fence]).filter((r) => r.type === 'ERROR').length, 0);
  const late = buildLiveCopyReports({
    cycle: { id: 'c1', tradeId: 'gs1-a1', intentEnvelope: envelope }, correlationId: 'gs1-a1',
    lane: 'FAMILY_GS01_XV_PREMIUM_ATR_TP', events: [fence], participants: [{ id: 'p1', userId: 'u1' }],
    accountFor: () => 'acct-x', nowMs: T0 + 120_000,
  });
  assert.equal(late.filter((r) => r.type === 'ERROR').length, 1);
});

test('one exchange fill recorded twice is reported once', () => {
  const dup = [
    ...events.slice(0, 2),
    ev('f1', 'FILLED', 5_000, { fill_price: 60_000, qty: 0.0004, exchange_fill_ids: ['1991601627'], stop_loss_placed: true, stopOrderId: 222 }),
    ev('f2', 'FILLED', 5_400, { fill_price: 60_000, qty: 0.0004, exchange_fill_ids: ['1991601627'], stop_loss_placed: true, stopOrderId: 333 }),
  ];
  const r = build(dup);
  assert.equal(r.filter((x) => x.type === 'ORDER_FILLED').length, 1);
  assert.equal(r.filter((x) => x.type === 'STOP_PLACED').length, 1);
});

test('website receive uses the earliest receive, not the envelope value overwritten at exit (+3600 s bug)', () => {
  const late = { context: { ...envelope.context, platform_received_at: at(3_600_400).toISOString() } };
  const evs = [{ ...ev('r0', 'INTENT', 300, {}, null), platformReceivedAt: at(400) }, ...events];
  const s = summarizeCopyStatus(build(evs, late), T0 + 10_000);
  const lags = (s.accounts as Record<string, { lags_sec: Record<string, { p50: number | null }> }>)['acct-x'].lags_sec;
  assert.equal(lags['intent->website_receive'].p50, 0.2);
  assert.ok((lags['website_receive->order_submit'].p50 ?? -1) >= 0);
});

test('POSITION_CLOSED reports the authenticated exchange close fill, not the quote side', () => {
  const r = build([...events, ev('x1', 'EXIT', 9_000, { exit_price: 83084, exit_exchange_fill_price: 83071, exit_fill_status: 'REAL_FILL', showcase_exit_price: 83071, close_exchange_order_id: 246232327680 })]);
  const close = r.find((x) => x.type === 'POSITION_CLOSED') as unknown as { close: { price: number; paper_exit_price: number } };
  assert.equal(close.close.price, 83071);
  assert.equal(close.close.paper_exit_price, 83071);
});

test('LIVE-FIX-1246: reporter failures turn copy-status AMBER/RED', async () => {
  const { reporterVerdict, worstVerdict } = await import('./live-copy-fly-reporter');
  const now = 1_000_000_000;
  assert.equal(reporterVerdict({ consecutiveFailures: 0, lastFailAt: 0, lastOkAt: now, pending: 0 }, now), 'GREEN');
  assert.equal(reporterVerdict({ consecutiveFailures: 1, lastFailAt: now, lastOkAt: now - 1, pending: 0 }, now), 'AMBER');
  assert.equal(reporterVerdict({ consecutiveFailures: 70, lastFailAt: now, lastOkAt: 0, pending: 3 }, now), 'RED');
  assert.equal(worstVerdict('GREEN', 'RED'), 'RED');
});

test('HM 02:55 / RK (1): POSITION_CLOSED report carries exit_order_class and the resident/bot-sent exit stamps', () => {
  const resident = buildLiveCopyReports({
    cycle: { id: 'c1', tradeId: 'gs1-a1', intentEnvelope: envelope }, correlationId: 'gs1-a1', lane: 'L',
    events: [...events, ev('e5', 'EXIT', 9_000, { exit_price: 59_790, exit_order_class: 'EXCHANGE_RESIDENT',
      exit_fill_record_at: at(8_500).toISOString(), close_confirmed_at: at(8_800).toISOString() })],
    participants: [{ id: 'p1', userId: 'u1' }], accountFor: () => 'acct-x', nowMs: T0 + 10_000,
  });
  const rc = resident.find((r) => r.type === 'POSITION_CLOSED') as any;
  assert.equal(rc.close.exit_order_class, 'EXCHANGE_RESIDENT');
  assert.equal(rc.timeline.exit_fill_record_at_ts, (T0 + 8_500) / 1000);
  assert.equal(rc.timeline.exit_trigger_print_at_ts, null); // not carried on Railway -> non-countable on Fly
  const botSent = buildLiveCopyReports({
    cycle: { id: 'c1', tradeId: 'gs1-a1', intentEnvelope: envelope }, correlationId: 'gs1-a1', lane: 'L',
    events: [...events, ev('e5', 'EXIT', 9_000, { exit_price: 60_100, exit_order_class: 'nonsense',
      close_exchange_ack_at: at(8_700).toISOString() })],
    participants: [{ id: 'p1', userId: 'u1' }], accountFor: () => 'acct-x', nowMs: T0 + 10_000,
  });
  const bc = botSent.find((r) => r.type === 'POSITION_CLOSED') as any;
  assert.equal(bc.close.exit_order_class, null);
  assert.equal(bc.timeline.exit_ack_at_ts, (T0 + 8_700) / 1000);
});
