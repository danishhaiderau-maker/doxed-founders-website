import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import { verifyFlySendStamp } from './fly-send-stamp';
import { LIVE_LEGS, liveRowLegCheck } from './live-row-countability';
import { liveLegStamps } from './live-trade-export.mapper';
import { mapSubscriberExchangeLiveBook } from './subscriber-exchange-live.mapper';
import {
  railwayExitLegStamps,
  readSignedShowcaseClose,
  residentExitLegStamps,
  signedCloseExitStamps,
} from './signal-subscriber-execution.service';
import { relayIntentEnvelope } from './showcase-relay-events.service';

// RK ruling (1), 24 Oct 2026: measured exit trigger / Fly send / relay receive stamps on the ONE Fly->relay path.
const SECRET = 'test-secret-not-real';
const iso = (s: number) => new Date(s * 1000).toISOString();
const sign = (eventId: string, ts: string, secret = SECRET) =>
  `sha256=${createHmac('sha256', secret).update(`${eventId}|${ts}`).digest('hex')}`;

test('RK1: the Fly send stamp is kept only when its HMAC verifies and it precedes the relay receive', () => {
  const ts = '1761300000.123456';
  const rx = 1761300000.2 * 1000;
  const base = { sentAtHeader: ts, eventId: 'e1', eventTs: iso(1761300000), platformReceivedAtMs: rx, secret: SECRET };
  assert.equal(verifyFlySendStamp({ ...base, signatureHeader: sign('e1', ts) }), new Date(1761300000123.456).toISOString());
  assert.equal(verifyFlySendStamp({ ...base, signatureHeader: sign('e2', ts) }), null); // other event
  assert.equal(verifyFlySendStamp({ ...base, signatureHeader: sign('e1', ts, 'other') }), null); // other key
  assert.equal(verifyFlySendStamp({ ...base, signatureHeader: undefined }), null);
  assert.equal(verifyFlySendStamp({ ...base, signatureHeader: sign('e1', ts), secret: '' }), null);
  assert.equal(verifyFlySendStamp({ ...base, sentAtHeader: 'abc', signatureHeader: sign('e1', 'abc') }), null);
  const late = '1761300009.000000'; // after the receive instant beyond slack
  assert.equal(verifyFlySendStamp({ ...base, sentAtHeader: late, signatureHeader: sign('e1', late) }), null);
  const early = '1761299990.000000'; // before the signed event ts beyond slack
  assert.equal(verifyFlySendStamp({ ...base, sentAtHeader: early, signatureHeader: sign('e1', early) }), null);
});

test('RK1: Fly (Python) and Railway (TS) agree on the signed message "<event_id>|<ts>"', () => {
  const py = readFileSync(resolve(__dirname, '../../../../services/btc-conservative-agent/relay_event_outbox.py'), 'utf8');
  assert.match(py, /f"\{event_id\}\|\{ts\}"/);
  assert.match(py, /X-Fly-Send-Started-At/);
  assert.match(py, /X-Fly-Send-Signature/);
});

test('RK1: POSITION_CLOSED context carries exit_trigger_ts, order class and the verified send stamp', () => {
  const env = relayIntentEnvelope('c1', 'cont-1', {
    schema: 'dcf-showcase-intent-v1', event: 'POSITION_CLOSED', trade_id: 'cont-1', ts: iso(100), event_id: 'e1', event_seq: 3, direction: 'LONG',
    exit_price: 100_000, exit_reason: 'TP', platform_received_at: iso(100.3),
    exit_trigger_ts: 99.9, exit_order_class: 'BOT_SENT', fly_send_started_at: iso(100.01),
  } as never) as { context: Record<string, unknown> };
  assert.equal(env.context.showcase_exit_trigger_ts, 99.9);
  assert.equal(env.context.showcase_exit_order_class, 'BOT_SENT');
  assert.equal(env.context.fly_send_started_at, iso(100.01));
  assert.deepEqual(signedCloseExitStamps(env.context, { sourceEventAtMs: 100_000, platformReceivedAtMs: 100_300 }), { exitTriggerAtMs: 99_900, flySendStartedAtMs: 100_010 });
  // unmeasured -> null, never invented
  assert.deepEqual(signedCloseExitStamps({}, { sourceEventAtMs: 100_000, platformReceivedAtMs: 100_300 }), { exitTriggerAtMs: null, flySendStartedAtMs: null });
  const nowMs = 100_500;
  const ctxEnv = { action: 'ENTER', trade_id: 'cont-1', context: { ...env.context, signed_showcase_event: true, showcase_event_id: 'e1', showcase_event_seq: 3 } };
  const close = readSignedShowcaseClose(ctxEnv, 'cont-1', nowMs);
  assert.equal(close?.exitTriggerAtMs, 99_900);
  assert.equal(close?.flySendStartedAtMs, 100_010);
});

test('RK1: Railway EXIT payload stamps: bot-sent carries all three; resident carries the BFX fill record only', () => {
  assert.deepEqual(railwayExitLegStamps({ exitTriggerAtMs: 99_900, flySendStartedAtMs: 100_010, platformReceivedAtMs: 100_080 }), {
    exit_order_class: 'BOT_SENT', exit_trigger_ts: iso(99.9), fly_send_started_at: iso(100.01), platform_received_at: iso(100.08),
  });
  assert.deepEqual(railwayExitLegStamps(undefined), { exit_order_class: 'BOT_SENT' });
  assert.deepEqual(residentExitLegStamps(200_400), { exit_order_class: 'EXCHANGE_RESIDENT', exit_fill_record_at: iso(200.4) });
  assert.deepEqual(residentExitLegStamps(null), { exit_order_class: 'EXCHANGE_RESIDENT' });
});

const envelope = { context: { fly_live_approval: { signal_at_ts: 9, created_at_ts: 9.1 }, platform_received_at: iso(9.2) } };
const placed = { eventType: 'ORDER_PLACED', payload: { entry_exchange_ack_at: iso(9.6) } };

test('RK1: exchange-resident exit legs are trade print -> Bitfinex fill record (no send leg), both required', () => {
  assert.deepEqual(LIVE_LEGS.exit_resident.map(([n]) => n), ['signal', 'ack_fill']);
  const noPrint = liveLegStamps(envelope, [placed, { eventType: 'EXIT', payload: residentExitLegStamps(200_400) }], true);
  assert.equal(noPrint[2], 'exit_resident');
  assert.deepEqual(liveRowLegCheck(noPrint[0], noPrint[1], noPrint[2]).legReasons, ['LIVE_LEG_MISSING:exit_resident.signal']);
  const full = liveLegStamps(envelope, [placed, { eventType: 'EXIT', payload: residentExitLegStamps(200_400, 200_300) }], true);
  assert.deepEqual(liveRowLegCheck(full[0], full[1], full[2]).legReasons, []);
  const rev = liveLegStamps(envelope, [placed, { eventType: 'EXIT', payload: residentExitLegStamps(200_000, 200_300) }], true);
  assert.deepEqual(liveRowLegCheck(rev[0], rev[1], rev[2]).legReasons, ['LIVE_LEG_ORDER:exit_resident.signal->ack_fill']);
});

test('RK1: subscriber-exchange-live book rows use the same leg check (missing stamps -> non-countable)', () => {
  const now = new Date('2026-10-24T00:00:00.000Z');
  const row = (tradeId: string, events: Array<{ eventType: string; payload: unknown }> | undefined) => ({
    status: SignalCycleStatus.CLOSED, fillPrice: 100_000, exitPrice: 100_100, pnlUsd: 3, pnlMarginPct: 10,
    limitPrice: 100_000, qty: 0.03, terminalReason: null, exchangeProven: true, events,
    createdAt: now, updatedAt: new Date(now.getTime() + 60_000),
    cycle: { tradeId, status: SignalCycleStatus.CLOSED, intentEnvelope: { direction: 'LONG', ...envelope }, showcaseExitReason: 'TP', createdAt: now },
  });
  const exitFull = { eventType: 'EXIT', payload: railwayExitLegStamps({ exitTriggerAtMs: 49_900, flySendStartedAtMs: 50_010, platformReceivedAtMs: 50_080 }) };
  (exitFull.payload as Record<string, unknown>).close_exchange_ack_at = iso(50.4);
  (exitFull.payload as Record<string, unknown>).exit_exchange_fill_price = 100_100;   // HM R4 B1: real exit fill
  (exitFull.payload as Record<string, unknown>).exit_fill_status = 'REAL_FILL';       // HM R6 B2: helper-stamped real fill
  const book = mapSubscriberExchangeLiveBook({
    orders: [], position: null,
    participants: [
      row('none', undefined),
      row('nosend', [placed, { eventType: 'EXIT', payload: { ...railwayExitLegStamps({ exitTriggerAtMs: 49_900, platformReceivedAtMs: 50_080 }), close_exchange_ack_at: iso(50.4) } }]),
      // HM R5 nit 6: countable only with the entry from the entry order's own trades
      row('full', [placed, { eventType: 'FILLED', payload: { fill_price: 100_000, fill_price_source: 'exchange_trades' } }, exitFull]),
    ],
  });
  const by = Object.fromEntries(book.trades.map((t) => [t.tradeId, t]));
  assert.equal(by.none?.nonCountable, true);
  assert.equal(by.nosend?.nonCountableReason, 'LIVE_LEG_MISSING:exit.fly_send');
  assert.deepEqual([by.full?.nonCountable, by.full?.nonCountableReason], [false, null]);
});

test('RK1: executor wake carries optional exit stamps; malformed stamps are dropped, never a rejection', async () => {
  const { parseExecutorWakeRequest } = await import('../relay-executor-wake-http');
  const now = Date.now();
  const tid = 'gs1-446291153ae7';
  const wake = (extra: Record<string, unknown>) => parseExecutorWakeRequest({
    trigger: 'POSITION_CLOSED', at: new Date(now).toISOString(), tradeId: tid,
    signedClose: {
      tradeId: tid, eventId: `close-${tid}`, eventSeq: 3, exitPrice: 83_900,
      exitReason: 'TP', sourceEventAtMs: now - 400, platformReceivedAtMs: now, ...extra,
    },
  });
  const ok = wake({ exitTriggerAtMs: now - 500, flySendStartedAtMs: now - 380 });
  assert.equal(ok?.signedClose?.exitTriggerAtMs, now - 500);
  assert.equal(ok?.signedClose?.flySendStartedAtMs, now - 380);
  const bad = wake({ exitTriggerAtMs: 'x', flySendStartedAtMs: -1 });
  assert.equal(bad?.trigger, 'POSITION_CLOSED');
  assert.equal(bad?.signedClose?.exitTriggerAtMs, null);
  assert.equal(bad?.signedClose?.flySendStartedAtMs, null);
  assert.equal(wake({})?.signedClose?.exitTriggerAtMs, null);
});

test('HM 02:55 blocker 4: a ledger-only live close (no leg stamps) defaults to LIVE_LEGS_UNCHECKED', () => {
  const closedAt = new Date('2026-07-23T10:55:53.717Z');
  const book = mapSubscriberExchangeLiveBook({
    orders: [], position: null, participants: [],
    ledgerCloses: [{ ledgerId: '2001', closedAt, pnlUsd: 2.25, description: 'Position closed' }],
  } as never);
  assert.ok(book.trades.length >= 1);
  for (const t of book.trades) assert.deepEqual([t.nonCountable, t.nonCountableReason], [true, 'LIVE_LEGS_UNCHECKED']);
});
