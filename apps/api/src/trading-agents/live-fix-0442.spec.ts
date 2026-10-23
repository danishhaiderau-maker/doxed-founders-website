import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
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
  // one claim path for both: the (cycleId,userId) unique claim inside runEntryClaimTx
  assert.equal((src.match(/lockedEntryClaimInsert\(tx,/g) ?? []).length, 1);
  assert.ok(src.includes('if (spec && spec.key === specKey)'));
  assert.ok(src.includes('if (!spec.ok) throw spec.error;')); // P2002 from the speculative claim -> claim-lost, no submit
  assert.ok(src.includes("void unusedSpeculative.then((spec) => this.releaseSpeculativeClaim(spec))"));
  // fence + deterministic CID still precede the only exchange write
  const fence = src.indexOf("eventType: 'ENTRY_SUBMISSION_UNKNOWN'");
  const submit = src.indexOf('const orderId = await this.activeTrading.submitLimitOrder(creds, {');
  assert.ok(fence > 0 && submit > fence);
});

test('LIVE-FIX-1460: one-statement lock+claim; duplicate claim -> P2002 (no second order)', async () => {
  const svc = await import('./signal-subscriber-execution.service');
  const seen: unknown[][] = [];
  const claims = new Set<string>();
  const tx = {
    $executeRawUnsafe: async () => { throw new Error('two-statement path must not run'); },
    signalCycleParticipant: { create: async () => { throw new Error('no'); } },
    $queryRawUnsafe: async (q: string, ...v: unknown[]) => {
      seen.push([q, ...v]);
      const key = `${v[2]}|${v[3]}`;
      if (claims.has(key)) throw Object.assign(new Error('duplicate key value violates unique constraint'), { meta: { code: '23505' } });
      claims.add(key);
      return [{ id: v[1] }];
    },
  };
  const first = await svc.lockedEntryClaimInsert(tx as never, { lockKey: 'copy-cluster:u:a', cycleId: 'cyc', userId: 'u', venue: 'bitfinex' });
  assert.match(first.id, /^c[0-9a-f]{24}$/);
  assert.match(String(seen[0][0]), /WITH l AS \(SELECT pg_advisory_xact_lock\(hashtext\(\$1\)\)[\s\S]*INSERT INTO "SignalCycleParticipant"[\s\S]*FROM l RETURNING "id"/);
  // Concurrent/duplicate claim for the same cycle+user loses with P2002 -> caller returns claim-lost, never submits.
  await assert.rejects(
    svc.lockedEntryClaimInsert(tx as never, { lockKey: 'copy-cluster:u:a', cycleId: 'cyc', userId: 'u', venue: 'bitfinex' }),
    (e: unknown) => (e as { code?: string }).code === 'P2002' && (e as Error).name === 'PrismaClientKnownRequestError',
  );
  // Unexpected raw failure fails closed and switches to the two-statement path.
  const bad = { ...tx, $queryRawUnsafe: async () => { throw new Error('type "SignalCycleStatus" does not exist'); } };
  await assert.rejects(svc.lockedEntryClaimInsert(bad as never, { lockKey: 'k', cycleId: 'c2', userId: 'u', venue: 'bitfinex' }));
  assert.equal(svc.lockedClaimSingleStatement.enabled, false);
  svc.lockedClaimSingleStatement.enabled = true;
  const src = require('node:fs').readFileSync(require('node:path').join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const tx0 = src.indexOf('private runEntryClaimTx(');
  const ins = src.indexOf('lockedEntryClaimInsert(tx,', tx0);
  const snap = src.indexOf('this.correlatedExposureSnapshot(', ins);
  assert.ok(tx0 > 0 && ins > tx0 && snap > ins, 'cluster snapshot runs after the lock+insert statement');
});

// LIVE-FIX-1480: every authenticated POSITION_CLOSED direct wake was rejected
// HTTP 400 because the generated platform_relay_eligible allowlist is empty.
test('1480: direct close/open wakes for active dashboard tiles are accepted', async () => {
  const { parseExecutorWakeRequest, relayWakeTradeIdRoutable } = await import('../relay-executor-wake-http');
  const now = Date.now();
  for (const tid of ['gs1-446291153ae7', 'gs6-0123456789ab', 'rnd-0123456789ab', 'gb1-0123456789ab', 'gb2-0123456789ab']) {
    assert.equal(relayWakeTradeIdRoutable(tid), true, tid);
    const close = parseExecutorWakeRequest({
      trigger: 'POSITION_CLOSED', at: new Date(now).toISOString(), tradeId: tid,
      signedClose: {
        tradeId: tid, eventId: `close-${tid}`, eventSeq: 3, exitPrice: 83_900,
        exitReason: 'TIME_EXIT_60S', sourceEventAtMs: now - 400, platformReceivedAtMs: now,
      },
    });
    assert.equal(close?.trigger, 'POSITION_CLOSED', tid);
    assert.equal(close?.signedClose?.exitPrice, 83_900);
    const open = parseExecutorWakeRequest({
      trigger: 'POSITION_OPENED', at: new Date(now).toISOString(), tradeId: tid,
      signedOpen: { fillPrice: 83_900, sourceEventAtMs: now - 300, platformReceivedAtMs: now },
    });
    assert.equal(open?.trigger, 'POSITION_OPENED', tid);
  }
});

test('1480: retired, continuous, unknown and bare ids stay unroutable', async () => {
  const { relayWakeTradeIdRoutable, parseExecutorWakeRequest } = await import('../relay-executor-wake-http');
  for (const tid of ['cont-c105efa5', 'o29atr-deadbeef1234', 'gs7-deadbeef1234', 'dsl-1791785201-4', 'dsf-1791785201-2', 'unknown-1', 'deadbeef1234', 'dsl-', 'dsl', '']) {
    assert.equal(relayWakeTradeIdRoutable(tid), false, tid);
  }
  const now = Date.now();
  assert.equal(parseExecutorWakeRequest({
    trigger: 'POSITION_CLOSED', at: new Date(now).toISOString(), tradeId: 'cont-c105efa5',
    signedClose: {
      tradeId: 'cont-c105efa5', eventId: 'e', eventSeq: 1, exitPrice: 1,
      exitReason: 'X', sourceEventAtMs: now - 1, platformReceivedAtMs: now,
    },
  }), null);
});

test('1480: executor reads the durable wake immediately on boot', () => {
  const src = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const init = src.slice(src.indexOf('  onModuleInit() {'), src.indexOf('  onModuleInit() {') + 4000);
  assert.ok(init.includes('this.schedulePersistedWakePoll(0);'));
});

test('1482: a resting stop at least as protective as requested authenticates', async () => {
  const { protectiveStopAtLeastAsTight: t } = await import('./signal-subscriber-execution.service');
  assert.equal(t('LONG', 83_760, 83_750), true);   // tighter long stop
  assert.equal(t('LONG', 83_740, 83_750), false);  // looser long stop
  assert.equal(t('SHORT', 84_400, 84_410), true);  // tighter short stop
  assert.equal(t('SHORT', 84_420, 84_410), false);
  assert.equal(t('LONG', 85_000, 83_750), false);  // >1% off: refuse
  assert.equal(t('LONG', 0, 83_750), false);
});

test('1483: concurrent mirror closes for one participant have exactly one owner', async () => {
  const { SignalSubscriberExecutionService } = await import('./signal-subscriber-execution.service');
  const svc = Object.create(SignalSubscriberExecutionService.prototype) as any;
  let runs = 0;
  let release!: (v: boolean) => void;
  svc.executeShowcaseMirrorCloseOwned = () => { runs += 1; return new Promise<boolean>((r) => { release = r; }); };
  const args = ['a', 'u', { id: 'c' }, { id: 'p1', fillPrice: null }, {}, {}, { terminalAuthority: {} }];
  const first = svc.executeShowcaseMirrorClose(...args);
  const second = svc.executeShowcaseMirrorClose(...args);
  assert.equal(runs, 1);
  assert.equal(await second, true, 'concurrent caller reports owned close without racing the claim');
  release(true);
  assert.equal(await first, true);
  const third = svc.executeShowcaseMirrorClose(...args);
  release(false);
  assert.equal(await third, false);
  assert.equal(runs, 2, 'a later close runs again after the first settles');
});

test('1485: observing ACTIVE re-arms the stopped reconciliation and durable-wake loops', async () => {
  const { SignalSubscriberExecutionService } = await import('./signal-subscriber-execution.service');
  const svc = Object.create(SignalSubscriberExecutionService.prototype) as any;
  const calls: string[] = [];
  svc.scheduleReconciliation = (ms: number) => calls.push(`rec:${ms}`);
  svc.schedulePersistedWakePoll = (ms: number) => calls.push(`wake:${ms}`);
  svc.setRelayPollActivity('PAUSED');
  assert.deepEqual(calls, []);
  svc.setRelayPollActivity('ACTIVE');
  assert.equal(svc.lastRelayPollActivity, 'ACTIVE');
  assert.ok((calls as string[]).some((c) => c.startsWith('rec:')) && (calls as string[]).some((c) => c.startsWith('wake:')));
});

test('1485: a terminal claim refused before canonical commit is retried, never paused', () => {
  const src = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const fn = src.slice(src.indexOf('private async executeShowcaseMirrorCloseOwned('));
  const body = fn.slice(0, fn.indexOf('TERMINAL_CLOSE_CLAIM_PERSIST_FAILED'));
  assert.ok(body.includes('attempt < TERMINAL_CLAIM_RETRY_ATTEMPTS'));
  const deferAt = body.indexOf('[TERMINAL-CLAIM] deferred');
  assert.ok(deferAt > 0, 'unclaimed OPEN lot defers');
  assert.ok(!src.includes('this.lastRelayPollActivity = instances'), 'all activity writes go through the setter');
});

test('1486: post-ack fill probe ladder reaches 1.4 s total in <=7 probes, first probe immediate', async () => {
  const m = await import('./signal-subscriber-execution.service');
  const d = m.POST_ACK_FILL_PROBE_DELAYS_MS;
  assert.equal(d[0], 0);
  assert.ok(d.length <= 7 && d.slice(1).every((x) => x <= 400));
  assert.ok(d.reduce((a, b) => a + b, 0) >= 1200);
});

test('1487: refused terminal claim never self-pauses the relay (stand-down)', () => {
  const src = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const fn = src.slice(src.indexOf('private async executeShowcaseMirrorCloseOwned('));
  const body = fn.slice(0, fn.indexOf('TERMINAL_CLOSE_CLAIM_PERSIST_FAILED'));
  const claimPart = body.slice(body.indexOf('let acquired ='), body.indexOf('} catch (err) {', body.indexOf('let acquired =')));
  assert.ok(!claimPart.includes('pauseUserRelayForPositionMismatch'), 'no pause between claim and persist-failed');
  assert.equal((claimPart.match(/\[TERMINAL-CLAIM\] stand-down/g) ?? []).length, 2);
});

test('1487: duplicate stop dedupe keeps one stop per lot and never the kept one', async () => {
  const { extraProtectiveStopIds } = await import('./signal-subscriber-execution.service');
  const st = (id: number, amount: number) => ({ id, symbol: 'tBTCF0:USTF0', amount, amountOrig: amount, price: 83414, status: 'ACTIVE', orderType: 'STOP', flags: 1024 }) as never;
  // one lot 0.00029 long, two identical stops -> cancel the older extra
  assert.deepEqual(extraProtectiveStopIds([st(1, -0.00029), st(2, -0.00029)], 2, 'LONG', 0.00029, 0.00029), [1]);
  // two lots on one net position -> both stops stay
  assert.deepEqual(extraProtectiveStopIds([st(1, -0.00029), st(2, -0.00029)], 2, 'LONG', 0.00029, 0.00058), []);
  // kept stop not visible yet -> do nothing
  assert.deepEqual(extraProtectiveStopIds([st(1, -0.00029)], 2, 'LONG', 0.00029, 0.00029), []);
});

test('1487: reduce-only STOP submit rides the priority stop lane', async () => {
  const { isBitfinexProtectiveStopSubmit } = await import('../exchanges/bitfinex-api.client');
  assert.equal(isBitfinexProtectiveStopSubmit('v2/auth/w/order/submit', { type: 'STOP', flags: 1024 }), true);
  assert.equal(isBitfinexProtectiveStopSubmit('v2/auth/w/order/submit', { type: 'LIMIT', flags: 0 }), false);
  assert.equal(isBitfinexProtectiveStopSubmit('v2/auth/w/order/submit', { type: 'STOP', flags: 0 }), false);
  const src = readFileSync(join(__dirname, '../exchanges/bitfinex-api.client.ts'), 'utf8');
  assert.ok(src.includes('lane.stops?.shift() ?? lane.mutations.shift() ?? lane.reads.shift()'));
});
