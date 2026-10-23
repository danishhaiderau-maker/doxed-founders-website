import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import test from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import { LIVE_FORCED_EXIT_REASONS, liveRowCountability } from './live-row-countability';
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
