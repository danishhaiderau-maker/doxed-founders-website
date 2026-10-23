import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import { LIVE_FORCED_EXIT_REASONS, liveRowCountability, liveRowCountabilityWithLegs, liveRowLegCheck } from './live-row-countability';
import { mapParticipantToExportRow } from './live-trade-export.mapper';
import { mapSubscriberExchangeLiveBook } from './subscriber-exchange-live.mapper';

const now = new Date('2026-10-23T06:00:00.000Z');

function closed(tradeId: string, showcaseExitReason: string | null, terminalReason: string | null = null) {
  return {
    status: SignalCycleStatus.CLOSED,
    fillPrice: 100_000,
    exitPrice: 100_100,
    pnlUsd: 3,
    pnlMarginPct: 10,
    limitPrice: 100_000,
    qty: 0.03,
    terminalReason,
    exchangeProven: true,
    createdAt: now,
    updatedAt: new Date(now.getTime() + 60_000),
    cycle: { tradeId, status: SignalCycleStatus.CLOSED, intentEnvelope: { direction: 'LONG' }, showcaseExitReason, createdAt: now },
  };
}

test('B4: TS forced set covers every paper_pnl_canon FORCED_EXIT_REASONS entry', () => {
  const py = readFileSync(resolve(__dirname, '../../../../services/btc-conservative-agent/paper_pnl_canon.py'), 'utf8');
  const block = /FORCED_EXIT_REASONS = frozenset\(\{([\s\S]*?)\}\)/.exec(py)?.[1] ?? '';
  const names = [...block.matchAll(/"([A-Z_]+)"/g)].map((m) => m[1]);
  assert.ok(names.length >= 4);
  for (const n of names) assert.ok(LIVE_FORCED_EXIT_REASONS.has(n), n);
});

test('B4: force-flat, maintenance and admin closes are non_countable; strategy exits are not', () => {
  assert.deepEqual(liveRowCountability('DEPLOY_FORCE_FLAT'), { nonCountable: true, nonCountableReason: 'FORCED_CLOSE:DEPLOY_FORCE_FLAT' });
  assert.equal(liveRowCountability('SOURCE_CONFIRMED_ADMIN_MANUAL_CLOSE').nonCountable, true);
  assert.equal(liveRowCountability('ADMIN_PAUSE:DEPLOY_MAINTENANCE').nonCountable, true);
  assert.equal(liveRowCountability('LATE_ENTRY_BETTER_ONLY_CONTINUATION: DEPLOY_FORCE_FLAT').nonCountable, true);
  assert.equal(liveRowCountability(null, 'user_relay_stop').nonCountable, true);
  for (const r of ['TP', 'EXCHANGE_STOP', 'THESIS_FAST_CUT', 'TIME_EXIT', null, '']) {
    assert.deepEqual(liveRowCountability(r), { nonCountable: false, nonCountableReason: null });
  }
});

test('B4: website live book rows carry non_countable for force-flattened closes', () => {
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [closed('ff', 'DEPLOY_FORCE_FLAT'), closed('tp', 'TP'), closed('mt', null, 'ADMIN_PAUSE:DEPLOY_MAINTENANCE')],
  });
  const by = Object.fromEntries(book.trades.map((t) => [t.tradeId, t]));
  assert.equal(by.ff?.nonCountable, true);
  assert.equal(by.ff?.nonCountableReason, 'FORCED_CLOSE:DEPLOY_FORCE_FLAT');
  assert.equal(by.mt?.nonCountable, true);
  assert.equal(by.tp?.nonCountable, false);
});

test('B4: unlinked ledger rows are stamped (countable unless forced)', () => {
  const book = mapSubscriberExchangeLiveBook({
    orders: [],
    position: null,
    participants: [],
    ledgerCloses: [{ ledgerId: 7, closedAt: now, pnlUsd: 1 } as never],
  });
  assert.equal(book.trades[0]?.nonCountable, false);
});

test('B4: live trade export row and CSV carry non_countable', () => {
  const row = mapParticipantToExportRow({
    participant: {
      id: 'p1', status: SignalCycleStatus.CLOSED, venue: 'bitfinex', fillPrice: 100_000, exitPrice: 99_900,
      pnlUsd: -3, pnlMarginPct: -10, feeUsd: null, createdAt: now, updatedAt: now,
      events: [{ eventType: 'EXIT', payload: { exit_reason: 'ADMIN_FORCE_FLAT' }, createdAt: now }],
    },
    cycle: { id: 'c1', tradeId: 't1', intentEnvelope: {}, showcaseExitReason: null, createdAt: now, closedAt: now },
  });
  assert.equal(row.nonCountable, true);
  assert.equal(row.nonCountableReason, 'FORCED_CLOSE:ADMIN_FORCE_FLAT');
});

const LEGS_ENTRY = { fly_signal_at_ts: 9, fly_intent_emitted_at_ts: 9.1, railway_received_at_ts: 9.2, exchange_ack_at_ts: 9.6 };
const LEGS_EXIT = { exit_signal_at_ts: 49.9, fly_send_started_at_ts: 50.01, railway_received_at_ts: 50.08, exit_ack_at_ts: 50.4 };

test('HM 01:43 item 3: a live row needs every leg (signal, Fly send, relay receive, ack/fill) measured and ordered', () => {
  assert.deepEqual(liveRowLegCheck(LEGS_ENTRY, LEGS_EXIT), { nonCountable: false, nonCountableReason: null, legReasons: [] });
  assert.equal(liveRowLegCheck(LEGS_ENTRY, null).nonCountable, false); // open / exchange-resident stop exit
  for (const [k, side] of [['fly_intent_emitted_at_ts', 'entry.fly_send'], ['railway_received_at_ts', 'entry.relay_receive'],
    ['fly_signal_at_ts', 'entry.signal'], ['exchange_ack_at_ts', 'entry.ack_fill']] as const) {
    const r = liveRowLegCheck({ ...LEGS_ENTRY, [k]: null }, LEGS_EXIT);
    assert.equal(r.nonCountable, true, k);
    assert.ok(r.legReasons.includes(`LIVE_LEG_MISSING:${side}`), k);
  }
  assert.ok(liveRowLegCheck({ ...LEGS_ENTRY, exchange_ack_at_ts: null, fill_at_ts: 9.7 }, null).legReasons.length === 0);
  const rev = liveRowLegCheck({ ...LEGS_ENTRY, railway_received_at_ts: 9.8 }, LEGS_EXIT);
  assert.deepEqual(rev.legReasons, ['LIVE_LEG_ORDER:entry.relay_receive->ack_fill']);
  assert.ok(liveRowLegCheck(LEGS_ENTRY, {}).legReasons.includes('LIVE_LEG_MISSING:exit.fly_send'));
  // forced close still wins and keeps its B4 reason
  assert.equal(liveRowCountabilityWithLegs(['DEPLOY_FORCE_FLAT'], {}, {}).nonCountableReason, 'FORCED_CLOSE:DEPLOY_FORCE_FLAT');
});

test('HM 01:43 item 3: the export row fails closed when the exit Fly-send legs are not on the EXIT payload', () => {
  const iso = (s: number) => new Date(s * 1000).toISOString();
  const envelope = { context: { fly_live_approval: { signal_at_ts: 9, created_at_ts: 9.1 }, platform_received_at: iso(9.2) } };
  const base = {
    id: 'p2', status: SignalCycleStatus.CLOSED, venue: 'bitfinex', fillPrice: 100_000, exitPrice: 100_100,
    pnlUsd: 3, pnlMarginPct: 10, feeUsd: null, createdAt: now, updatedAt: now,
  };
  const placed = { eventType: 'ORDER_PLACED', payload: { entry_exchange_ack_at: iso(9.6) }, createdAt: now };
  const cycle = { id: 'c2', tradeId: 't2', intentEnvelope: envelope, showcaseExitReason: 'TP', createdAt: now, closedAt: now };
  const today = mapParticipantToExportRow({
    participant: { ...base, events: [placed, { eventType: 'EXIT', payload: { exit_reason: 'TP', close_exchange_ack_at: iso(50.4) }, createdAt: now }] },
    cycle,
  });
  assert.equal(today.nonCountable, true);
  assert.equal(today.nonCountableReason, 'LIVE_LEG_MISSING:exit.signal');
  const full = mapParticipantToExportRow({
    participant: {
      ...base,
      events: [placed, { eventType: 'EXIT', payload: { exit_reason: 'TP', exit_trigger_ts: 49.9, fly_send_started_at: iso(50.01),
        platform_received_at: iso(50.08), close_exchange_ack_at: iso(50.4) }, createdAt: now }],
    },
    cycle,
  });
  assert.deepEqual([full.nonCountable, full.nonCountableReason], [false, null]);
});
