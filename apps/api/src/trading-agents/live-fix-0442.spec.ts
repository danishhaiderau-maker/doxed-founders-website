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
  assert.equal(exchangeMirrorMayRun({ ...base, lastMirrorAtMs: base.nowMs - EXCHANGE_MIRROR_MAX_AGE_MS }), true);
  assert.equal(exchangeMirrorMayRun({ ...base, moneyTickRunning: false, lastMirrorAtMs: base.nowMs }), true);
  assert.equal(exchangeMirrorMayRun({ ...base, mirrorRunning: true, lastMirrorAtMs: 0 }), false);
  assert.ok(EXCHANGE_MIRROR_MAX_AGE_MS <= 60_000);
});
