import assert from 'node:assert/strict';
import test from 'node:test';
import { foldParticipantExecutionMeta } from './participant-execution-meta.util';

test('participant execution metadata keeps the latest terminal audit reason', () => {
  const meta = foldParticipantExecutionMeta([
    {
      eventType: 'SUBMITTED',
      payload: { bitfinex_order_id: 240990314099, limit_price: 65_892.83 },
    },
    {
      eventType: 'EXPIRED',
      payload: { reason: 'EXECUTOR_WATCHDOG_CANCELLED_UNFILLED' },
    },
  ]);

  assert.equal(meta.limitPrice, 65_892.83);
  assert.equal(meta.terminalReason, 'EXECUTOR_WATCHDOG_CANCELLED_UNFILLED');
});
