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

test('LIVE-FIX-1054: a later kick is honoured (old version dropped kicks for 3 s)', async () => {
  let n = 0;
  setLiveCopyReportKickListener(() => { n += 1; });
  assert.equal(kickLiveCopyReporter([10]), 1);
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(kickLiveCopyReporter([10]), 1);
  await new Promise((r) => setTimeout(r, 40));
  assert.equal(n, 2);
  setLiveCopyReportKickListener(null);
});

import { relinkSameTileFamily, tradeIdFamily } from './signal-subscriber-execution.service';
test('LIVE-FIX-1200: CURE-1 never relinks a DISLOC fill onto a GS-01 position', () => {
  assert.equal(tradeIdFamily('adopt:dsl-1791765540-7'), 'dsl');
  assert.equal(tradeIdFamily('relink:adopt:gs1-b24027a83f15:1'), 'gs1');
  assert.equal(relinkSameTileFamily('adopt:dsl-1791765540-7', 'gs1-b24027a83f15'), false);
  assert.equal(relinkSameTileFamily('adopt:gs1-a', 'gs1-b'), true);
  assert.equal(relinkSameTileFamily(null, 'gs1-b'), true);
});

test('LIVE-FIX-1246: taker entry fill is promoted (stop placed) at ACK, before limit chase', () => {
  const src = require('node:fs').readFileSync(require('node:path').join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const probe = src.indexOf("'POST_ACK_FAST_FILL'");
  const chase = src.indexOf('if (participant && !promotedAtAck)');
  assert.ok(probe > 0 && chase > probe);
});

test('LIVE-FIX-1445: submit-response fill only for EXECUTED full fills', async () => {
  const { submitResponseFill } = await import('./signal-subscriber-execution.service');
  const { parseBitfinexSubmitFill } = await import('../exchanges/bitfinex-api.client');
  const order = [1, null, 7, 'tBTCF0:USTF0', 0, 0, 0, 0.00029, 'LIMIT', null, null, null, 0, 'EXECUTED @ 83900.0(0.00029)', null, null, 83911, 83900];
  const res = [0, 'on-req', null, null, [order], null, 'SUCCESS', 'ok'];
  const parsed = parseBitfinexSubmitFill(res);
  assert.deepEqual(submitResponseFill(parsed, 0.00029), { filledQty: 0.00029, fillPrice: 83900, source: 'ORDER_PARTIAL', orderResting: false });
  const active = [...order]; active[6] = 0.00029; active[13] = 'ACTIVE';
  assert.equal(submitResponseFill(parseBitfinexSubmitFill([0, 'on-req', null, null, [active], null, 'SUCCESS']), 0.00029), null);
  const partial = [...order]; partial[6] = 0.0001; partial[13] = 'PARTIALLY FILLED @ 83900.0(0.00019)';
  assert.equal(submitResponseFill(parseBitfinexSubmitFill([0, 'on-req', null, null, [partial], null, 'SUCCESS']), 0.00029), null);
  assert.equal(submitResponseFill(null, 0.00029), null);
});

test('LIVE-FIX-1445: speculative claim is used only on an exact input match and released otherwise', async () => {
  const { entryClaimKey } = await import('./signal-subscriber-execution.service');
  const a = entryClaimKey({ cycleId: 'c1', direction: 'LONG', limitPrice: 83911, qty: 0.00029, marginUsd: 0.24 });
  assert.equal(a, entryClaimKey({ cycleId: 'c1', direction: 'LONG', limitPrice: 83911.001, qty: 0.00029, marginUsd: 0.24 }));
  assert.notEqual(a, entryClaimKey({ cycleId: 'c1', direction: 'LONG', limitPrice: 83912, qty: 0.00029, marginUsd: 0.24 }));
  assert.notEqual(a, entryClaimKey({ cycleId: 'c2', direction: 'LONG', limitPrice: 83911, qty: 0.00029, marginUsd: 0.24 }));
  const src = require('node:fs').readFileSync(require('node:path').join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  // one claim path for both: the (cycleId,userId) unique create inside runEntryClaimTx
  assert.equal((src.match(/tx\.signalCycleParticipant\.create\(\{\n\s+data: \{\n\s+cycleId,\n\s+userId: instance\.userId,\n\s+venue,\n\s+status: SignalCycleStatus\.INTENT/g) ?? []).length, 1);
  assert.ok(src.includes('if (spec && spec.key === specKey)'));
  assert.ok(src.includes('if (!spec.ok) throw spec.error;')); // P2002 from the speculative claim -> claim-lost, no submit
  assert.ok(src.includes("void unusedSpeculative.then((spec) => this.releaseSpeculativeClaim(spec))"));
  // fence + deterministic CID still precede the only exchange write
  const fence = src.indexOf("eventType: 'ENTRY_SUBMISSION_UNKNOWN'");
  const submit = src.indexOf('const orderId = await this.activeTrading.submitLimitOrder(creds, {');
  assert.ok(fence > 0 && submit > fence);
});
