import assert from 'node:assert/strict';
import test from 'node:test';
import {
  assessParticipantLifecycle,
  buildTradeLifecycleIntegrity,
} from '../trade-lifecycle-integrity';

test('accepts a mirror catch-up as the entry action for an open copied lot', () => {
  assert.deepEqual(
    assessParticipantLifecycle({
      id: 'participant-1',
      status: 'OPEN',
      cycle: { tradeId: 'cont-catchup' },
      events: [
        { eventType: 'MIRROR_CATCHUP_ENTRY' },
        { eventType: 'FILLED' },
      ],
    }),
    [],
  );
});

test('accepts a mirror catch-up round trip as a complete closed lifecycle', () => {
  const snapshot = buildTradeLifecycleIntegrity([
    {
      id: 'participant-1',
      status: 'CLOSED',
      cycle: { tradeId: 'cont-catchup' },
      events: [
        { eventType: 'MIRROR_CATCHUP_ENTRY' },
        { eventType: 'FILLED' },
        { eventType: 'EXIT' },
      ],
    },
  ]);

  assert.equal(snapshot.integrityPct, 100);
  assert.equal(snapshot.completeCount, 1);
  assert.deepEqual(snapshot.recentGaps, []);
});

test('still reports a genuinely missing entry action', () => {
  assert.deepEqual(
    assessParticipantLifecycle({
      id: 'participant-1',
      status: 'OPEN',
      cycle: { tradeId: 'cont-broken' },
      events: [{ eventType: 'FILLED' }],
    }),
    ['ORDER_PLACED'],
  );
});
