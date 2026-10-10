import assert from 'node:assert/strict';
import { test } from 'node:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { orphanReduceOnlyStopCancellable, recordedStopOrderIdsFromPayloads } from './signal-subscriber-execution.service';
import { BITFINEX_BTC_PERP_SYMBOL, BITFINEX_REDUCE_ONLY_FLAG } from '../exchanges/bitfinex-api.client';

const stop = (over: Record<string, unknown> = {}) => ({
  id: 246228885722, symbol: BITFINEX_BTC_PERP_SYMBOL, amount: -0.0003, amountOrig: -0.0003, price: 82794.2,
  status: 'ACTIVE', orderType: 'STOP', flags: BITFINEX_REDUCE_ONLY_FLAG, ...over,
});

test('orphan stop cancel: only an active reduce-only STOP on the perp, only when flat', () => {
  assert.deepEqual(orphanReduceOnlyStopCancellable(stop(), 0), { ok: true });
  assert.equal((orphanReduceOnlyStopCancellable(stop(), 0.0003) as { reason: string }).reason, 'ACCOUNT_NOT_FLAT');
  assert.equal((orphanReduceOnlyStopCancellable(stop({ flags: 0 }), 0) as { reason: string }).reason, 'NOT_REDUCE_ONLY');
  assert.equal((orphanReduceOnlyStopCancellable(stop({ orderType: 'LIMIT' }), 0) as { reason: string }).reason, 'NOT_A_STOP');
  assert.equal((orphanReduceOnlyStopCancellable(stop({ symbol: 'tETHF0:USTF0' }), 0) as { reason: string }).reason, 'WRONG_SYMBOL');
  assert.equal((orphanReduceOnlyStopCancellable(null, 0) as { reason: string }).reason, 'ORDER_NOT_ACTIVE');
});

test('every recorded stop id of a lot is collected (close cancels duplicates too)', () => {
  const ids = recordedStopOrderIdsFromPayloads([
    { stopOrderId: 246228120782 }, { stopOrderId: '246228885722' }, { partialFillStopOrderId: 5 }, null, 'x',
  ]);
  assert.deepEqual(ids.sort(), [246228120782, 246228885722, 5].sort());
});

test('exchange-fill reconcile shares the fill-promotion claim (one stop per fill)', () => {
  const src = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const i = src.indexOf('Idempotent per exchange fill');
  assert.ok(i > 0);
  const block = src.slice(i, i + 2500);
  assert.match(block, /cancelRaceFillInFlight\.has\(row\.id\)/);
  assert.match(block, /exchangeFillAlreadyRecorded/);
  assert.match(block, /acquireFillPromotionClaim/);
  assert.match(block, /clientOrderId: reconcileClaim\.stopClientOrderId/);
});

test('whole-book fidelity guard skips Fly-approved per-tile live copy and logs trips', () => {
  const src = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  assert.match(src, /OPTION1_PER_TILE_LIVE_COPY/);
  assert.match(src, /\[LIVE-FIDELITY-GUARD\] TRIP -> PAUSED\+DISARMED/);
});
