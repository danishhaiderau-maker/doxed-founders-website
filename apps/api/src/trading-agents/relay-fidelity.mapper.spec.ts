import assert from 'node:assert/strict';
import test from 'node:test';
import { buildRelayFidelitySnapshot } from './relay-fidelity.mapper';

test('compact fidelity history reconciles trades beyond the dashboard display cap', () => {
  const startedAt = new Date('2026-07-20T00:00:00.000Z');
  const filledAt = new Date('2026-07-20T01:00:01.000Z');
  const exitedAt = new Date('2026-07-20T02:00:02.000Z');
  const snapshot = buildRelayFidelitySnapshot({
    bot: {
      trades: [],
      trades_map: {},
      fidelity_trades: [{
        trade_id: 'cont-history-001',
        dir: 'LONG',
        entry: 64_000,
        exit: 64_500,
        fill_ts: '2026-07-20T01:00:00.000Z',
        closed_ts: '2026-07-20T02:00:00.000Z',
        exit_reason: 'TP',
      }, {
        trade_id: 'cont-expired-002',
        dir: 'SHORT',
        entry: 64_700,
        exit: 64_600,
        fill_ts: '2026-07-20T03:00:00.000Z',
        closed_ts: '2026-07-20T04:00:00.000Z',
        exit_reason: 'TTL',
      }],
    },
    participants: [{
      id: 'participant-1',
      fillPrice: null,
      exitPrice: null,
      createdAt: filledAt,
      updatedAt: exitedAt,
      cycle: {
        id: 'cycle-1',
        tradeId: 'cont-history-001',
        showcaseExitReason: 'TP',
        closedAt: exitedAt,
      },
      events: [
        {
          eventType: 'FILLED',
          payload: { fill_price: 64_001, qty: 0.001, direction: 'LONG' },
          createdAt: filledAt,
        },
        {
          eventType: 'EXIT',
          payload: { exit_price: 64_499, exit_reason: 'TP' },
          createdAt: exitedAt,
        },
      ],
    }, {
      id: 'participant-2',
      fillPrice: null,
      exitPrice: null,
      createdAt: new Date('2026-07-20T03:00:00.000Z'),
      updatedAt: new Date('2026-07-20T03:10:00.000Z'),
      cycle: {
        id: 'cycle-2',
        tradeId: 'cont-expired-002',
        showcaseExitReason: null,
        closedAt: new Date('2026-07-20T03:10:00.000Z'),
      },
      events: [{
        eventType: 'EXPIRED',
        payload: { reason: 'TTL' },
        createdAt: new Date('2026-07-20T03:10:00.000Z'),
      }],
    }],
    sessionStartedAt: startedAt,
  });

  assert.equal(snapshot.summary.tradeCount, 1);
  assert.equal(snapshot.summary.missingShowcaseEntryCount, 0);
  assert.equal(snapshot.summary.missingShowcaseExitCount, 0);
  assert.equal(snapshot.summary.unmatchedRelayCount, 0);
  assert.equal(snapshot.audit.orphans.length, 0);
  assert.equal(snapshot.rows[0]?.localBotTradeId, 'cont-history-001');
  assert.equal(snapshot.rows[0]?.showcaseEntry, 64_000);
  assert.equal(snapshot.rows[0]?.showcaseExit, 64_500);
});
