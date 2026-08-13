import assert from 'node:assert/strict';
import test from 'node:test';
import type { BotApiState } from './bot-state.mapper';
import { buildIntentEnvelope } from './signal-envelope.mapper';

function botState(overrides: Partial<BotApiState> = {}): BotApiState {
  return {
    bot_version: 'test-rev',
    last_ai: {
      direction: 'LONG',
      final_direction: 'LONG',
      win_prob: 0,
    },
    last_approve_outcome: {
      trade_id: 'cont-exact',
      status: 'EXECUTED',
      direction: 'LONG',
    },
    orders: [],
    positions: [],
    ...overrides,
  };
}

test('APPROVE_PENDING without a canonical resting order cannot create an ENTER envelope', () => {
  const envelope = buildIntentEnvelope(
    'cyc-pending',
    'cont-exact',
    botState({
      last_approve_outcome: {
        trade_id: 'cont-exact',
        status: 'PENDING',
        direction: 'LONG',
      },
    }),
  );
  assert.equal(envelope, null);
});

test('canonical pending order produces an exact-limit envelope with no percentage derivation', () => {
  const envelope = buildIntentEnvelope(
    'cyc-exact',
    'cont-exact',
    botState({
      pullback_threshold: 0.009,
      orders: [{
        trade_id: 'cont-exact',
        status: 'PENDING',
        signal_dir: 'LONG',
        side: 'buy',
        limit_price: 63_915,
        entry_limit_policy: 'micro_sr_structural_limit_v1',
      }],
    }),
  );
  assert.ok(envelope);
  assert.equal(envelope.direction, 'LONG');
  assert.equal(envelope.entry.mode, 'EXACT_LIMIT');
  assert.equal(envelope.entry.reference, 'SHOWCASE_EXACT_LIMIT');
  assert.equal(envelope.entry.exact_limit_price, 63_915);
  assert.equal(envelope.entry.offset_pct, 0);
  assert.equal(envelope.risk.stop_loss_margin_pct, -13);
});

test('a filled position cannot be reconstructed into a stale entry envelope', () => {
  const envelope = buildIntentEnvelope(
    'cyc-filled',
    'cont-exact',
    botState({
      positions: [{
        trade_id: 'cont-exact',
        dir: 'LONG',
        entry: 63_915,
      }],
    }),
  );
  assert.equal(envelope, null);
});

test('blocked approval cannot produce an entry even if an order row is present', () => {
  const envelope = buildIntentEnvelope(
    'cyc-blocked',
    'cont-exact',
    botState({
      last_approve_outcome: {
        trade_id: 'cont-exact',
        status: 'BLOCKED',
      },
      orders: [{
        trade_id: 'cont-exact',
        status: 'PENDING',
        signal_dir: 'LONG',
        limit_price: 63_915,
        entry_limit_policy: 'micro_sr_structural_limit_v1',
      }],
    }),
  );
  assert.equal(envelope, null);
});

test('a blocked approval for another trade cannot suppress the exact pending order', () => {
  const envelope = buildIntentEnvelope(
    'cyc-current',
    'cont-current',
    botState({
      last_approve_outcome: {
        trade_id: 'cont-newer-blocked',
        status: 'BLOCKED',
      },
      orders: [{
        trade_id: 'cont-current',
        status: 'PENDING',
        signal_dir: 'LONG',
        limit_price: 63_915,
        entry_limit_policy: 'micro_sr_structural_limit_v1',
      }],
    }),
  );
  assert.ok(envelope);
  assert.equal(envelope.signalId, 'cont-current');
  assert.equal(envelope.entry.exact_limit_price, 63_915);
});

test('a legacy percentage-derived order cannot be relabelled as an exact structural entry', () => {
  const envelope = buildIntentEnvelope(
    'cyc-legacy',
    'cont-exact',
    botState({
      orders: [{
        trade_id: 'cont-exact',
        status: 'PENDING',
        signal_dir: 'LONG',
        limit_price: 63_936,
        entry_limit_policy: 'legacy_pullback_pct',
      }],
    }),
  );
  assert.equal(envelope, null);
});
