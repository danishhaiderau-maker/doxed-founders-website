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
  assert.equal(meta.exchangeProven, true);
});

test('participant execution metadata does not mark virtual-only events as exchange proven', () => {
  const meta = foldParticipantExecutionMeta([
    {
      eventType: 'EXIT',
      payload: { reason: 'SHOWCASE_MIRROR', fill_price: 63_918 },
    },
  ]);

  assert.equal(meta.exchangeProven, false);
});

test('participant execution metadata preserves source-absence fallback provenance', () => {
  const meta = foldParticipantExecutionMeta([
    {
      eventType: 'EXIT',
      payload: {
        reason: 'SHOWCASE_MIRROR',
        mirror_trigger: 'SHOWCASE_POSITION_ABSENT',
      },
    },
  ]);

  assert.equal(meta.terminalReason, 'SHOWCASE_MIRROR');
  assert.equal(meta.exitProvenance, 'SHOWCASE_POSITION_ABSENT');
});

test('participant execution metadata folds the newest camel-case Bitfinex replacement over the original order', () => {
  const meta = foldParticipantExecutionMeta([
    {
      eventType: 'ORDER_PLACED',
      payload: {
        bitfinexOrderId: 241852659810,
        limit_price: 64_127.81,
        qty: 0.03115,
        direction: 'LONG',
      },
    },
    {
      eventType: 'UPDATE_STOPS',
      payload: {
        event: 'BOT_ANCHOR_CHASE',
        bitfinexOrderId: 241852964885,
        limitPrice: 64_187.26,
      },
    },
  ]);

  assert.equal(meta.limitPrice, 64_187.26);
  assert.equal(meta.qty, 0.03115);
  assert.equal(meta.direction, 'LONG');
  assert.equal(meta.exchangeProven, true);
});
