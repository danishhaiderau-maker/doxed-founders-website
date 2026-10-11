import test from 'node:test';
import assert from 'node:assert/strict';
import {
  EXCHANGE_MIRROR_MAX_AGE_MS,
  exchangeMirrorMayRun,
  shouldSkipExchangeStopTrailForApprovedCopy,
} from './signal-subscriber-execution.service';

test('approved live copies never trail the exchange stop (mirror-only exits)', () => {
  assert.equal(shouldSkipExchangeStopTrailForApprovedCopy({ simActive: false, approvalBacked: true }), true);
  assert.equal(shouldSkipExchangeStopTrailForApprovedCopy({ simActive: false, approvalBacked: false }), false);
  assert.equal(shouldSkipExchangeStopTrailForApprovedCopy({ simActive: true, approvalBacked: true }), false);
});

test('exchange mirror refreshes even during a money tick once older than max age', () => {
  const base = { destroyed: false, mirrorRunning: false, moneyTickRunning: true, nowMs: 1_000_000 };
  assert.equal(exchangeMirrorMayRun({ ...base, lastMirrorAtMs: base.nowMs - 5_000 }), false);
  // LIVE-FIX-0741: never mid-tick, even when stale (stop placement latency).
  assert.equal(exchangeMirrorMayRun({ ...base, lastMirrorAtMs: base.nowMs - EXCHANGE_MIRROR_MAX_AGE_MS }), false);
  assert.equal(exchangeMirrorMayRun({ ...base, moneyTickRunning: false, lastMirrorAtMs: base.nowMs }), true);
  assert.equal(exchangeMirrorMayRun({ ...base, mirrorRunning: true, lastMirrorAtMs: 0 }), false);
  assert.ok(EXCHANGE_MIRROR_MAX_AGE_MS <= 60_000);
});

import { fastPathPreflightFailureCause } from './signal-subscriber-execution.service';
test('fast-path deferral cause is honest', () => {
  const b = { virtualLotCount: 0, virtualLotDirections: [], candidateDirection: 'SHORT' as const, maxConcurrent: 3, exchangePositionQty: -0.00029, exchangeActiveOrders: 0 };
  assert.equal(fastPathPreflightFailureCause(b), 'unattributed-exchange-exposure');
  assert.equal(fastPathPreflightFailureCause({ ...b, virtualLotCount: 1, virtualLotDirections: ['LONG'] }), 'opposite-direction-lot');
  assert.equal(fastPathPreflightFailureCause({ ...b, virtualLotCount: 3, virtualLotDirections: ['SHORT','SHORT','SHORT'] }), 'max-concurrent-reached');
});

import { kickLiveCopyReporter, setLiveCopyReportKickListener } from './live-copy-report-kick';
test('reporter kick runs the listener promptly and debounces', async () => {
  let n = 0;
  setLiveCopyReportKickListener(() => { n += 1; });
  assert.equal(kickLiveCopyReporter([10, 30]), 2);
  assert.equal(kickLiveCopyReporter([10]), 0);
  await new Promise((r) => setTimeout(r, 60));
  assert.equal(n, 2);
  setLiveCopyReportKickListener(null);
});

import { exchangeMirrorDueAfterTick } from './signal-subscriber-execution.service';
test('stale mirror runs right after a tick, not when a wake is queued', () => {
  assert.equal(exchangeMirrorDueAfterTick({ wakeQueued: false, lastMirrorAtMs: 0, nowMs: 100_000 }), true);
  assert.equal(exchangeMirrorDueAfterTick({ wakeQueued: true, lastMirrorAtMs: 0, nowMs: 100_000 }), false);
  assert.equal(exchangeMirrorDueAfterTick({ wakeQueued: false, lastMirrorAtMs: 90_000, nowMs: 100_000 }), false);
});
