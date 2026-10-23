/** HM R6 nits 1-3 (#852): fence-write ALERT + in-process block; order-trades budget key + per-sweep read cap; fence view. */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { test } from 'node:test';
import {
  bitfinexAuthAdmission, bitfinexAuthBudgetKey, currentBitfinexReadClass, recordBitfinexAuthCall, resetBitfinexAuthCallLog,
} from '../exchanges/bitfinex-api.client';
import {
  countLiveMutexOthers, EXIT_FILL_CLEAR_INTERVAL_MS, EXIT_FILL_CLEAR_MAX_ORDER_READS_PER_SWEEP, EXIT_FILL_UNREADABLE_FENCE_PREFIX,
  exitFillFenceWriteFailed, exitFillFenceWriteFailuresFor, LIVE_ENTRY_FENCE_NO_EXPIRY, liveFenceView, resetExitFillFenceWriteFailures,
  SignalSubscriberExecutionService,
} from './signal-subscriber-execution.service';

const tx = { signalCycleParticipant: { count: async () => 0 }, liveEntryFence: { count: async () => 0 } };

function svc(o: { upsertFails: () => boolean; fences?: string[] }) {
  const s = Object.create(SignalSubscriberExecutionService.prototype) as SignalSubscriberExecutionService;
  const logs: string[] = [];
  const reads: Array<{ ids: number[]; cls: string }> = [];
  Object.assign(s as unknown as Record<string, unknown>, {
    logger: { error: (m: string) => logs.push(`E ${m}`), warn: (m: string) => logs.push(`W ${m}`), log: () => undefined },
    exitFillClearAtMs: new Map(), exitFillClearCursor: new Map(),
    prisma: {
      liveEntryFence: {
        upsert: async () => { if (o.upsertFails()) throw new Error('db down'); return {}; },
        findMany: async () => (o.fences ?? []).map((id) => ({ id })),
        deleteMany: async () => ({ count: 1 }),
      },
      signalCycleEvent: {
        findMany: async (a: { where: { participantId: string } }) => [{
          id: `e-${a.where.participantId}`, cycleId: 'c', eventType: 'EXIT',
          payload: { exit_fill_status: 'EXIT_FILL_UNREADABLE', exit_fill_pending_order_ids: [Number(a.where.participantId.slice(1))], exit_fill_expected_qty: 0.001, exit_fill_kind: 'stop', exit_fill_direction: 'LONG' },
        }],
      },
    },
    readLiveExitOrderFill: async (_c: unknown, i: { orderIds: number[] }) => { reads.push({ ids: i.orderIds, cls: currentBitfinexReadClass() }); return { ok: false }; },
  });
  return { s: s as unknown as { holdExitFillUnreadableFence: (u: string, p: string, r: string) => Promise<boolean>; clearUnreadableExitFills: (u: string, c: unknown, n: number) => Promise<number> }, logs, reads };
}

test('nit 1: a failed exit-fill fence write ALERTs RED, blocks entries in-process, and is retried until written', async () => {
  resetExitFillFenceWriteFailures();
  let fail = true;
  const h = svc({ upsertFails: () => fail });
  assert.equal(await h.s.holdExitFillUnreadableFence('u1', 'p1', 'no_fill'), false);
  assert.ok(h.logs.some((l) => l.startsWith('E ') && l.includes('ALERT RED') && l.includes('fence write FAILED')));
  assert.equal(exitFillFenceWriteFailed('u1'), true);
  assert.equal(await countLiveMutexOthers(tx, 'u1', null), 1);          // entries blocked even without the DB row
  await h.s.clearUnreadableExitFills('u1', {}, 1_000_000);               // retry still failing -> still blocked
  assert.equal(await countLiveMutexOthers(tx, 'u1', null), 1);
  fail = false;
  await h.s.clearUnreadableExitFills('u1', {}, 1_000_000 + EXIT_FILL_CLEAR_INTERVAL_MS);
  assert.equal(exitFillFenceWriteFailed('u1'), false);                  // durable row written -> the DB fence blocks now
  assert.equal(await countLiveMutexOthers(tx, 'u1', null), 0);
});

test('nit 2: per-order trades paths share ONE budget key; the clearing sweep caps per-order reads (routine class, round-robin)', async () => {
  assert.equal(bitfinexAuthBudgetKey('v2/auth/r/order/tBTCF0:USTF0:123/trades'), 'v2/auth/r/order/trades');
  assert.equal(bitfinexAuthBudgetKey('v2/auth/r/order/tBTCF0:USTF0:456/trades'), 'v2/auth/r/order/trades');
  assert.equal(bitfinexAuthBudgetKey('v2/auth/r/orders'), 'v2/auth/r/orders');
  resetBitfinexAuthCallLog();
  const now = Date.now();
  for (let i = 0; i < 72; i++) recordBitfinexAuthCall(`v2/auth/r/order/tBTCF0:USTF0:${1000 + i}/trades`, now);
  assert.equal(bitfinexAuthAdmission('v2/auth/r/order/tBTCF0:USTF0:9999/trades', 'tick', now).ok, false);   // a new id is NOT a fresh budget
  assert.equal(bitfinexAuthAdmission('v2/auth/r/orders', 'tick', now).ok, true);
  resetBitfinexAuthCallLog();

  resetExitFillFenceWriteFailures();
  const fences = [1, 2, 3, 4, 5, 6].map((n) => `${EXIT_FILL_UNREADABLE_FENCE_PREFIX}p${n}`);
  const h = svc({ upsertFails: () => false, fences });
  await h.s.clearUnreadableExitFills('u1', {}, 10_000_000);
  assert.equal(h.reads.length, EXIT_FILL_CLEAR_MAX_ORDER_READS_PER_SWEEP);
  assert.ok(h.reads.every((r) => r.cls === 'routine'));
  await h.s.clearUnreadableExitFills('u1', {}, 10_000_000 + EXIT_FILL_CLEAR_INTERVAL_MS);
  assert.deepEqual(h.reads.map((r) => r.ids[0]), [1, 2, 3, 4, 5, 6, 1, 2]);   // next sweep resumes where the cap stopped
});

test('nit 3: read-only fence view (exit-fill, cycle, write failures, boot stamp) published on the mirror', () => {
  resetExitFillFenceWriteFailures();
  const v = liveFenceView([
    { id: `${EXIT_FILL_UNREADABLE_FENCE_PREFIX}p1`, reason: 'EXIT_FILL_UNREADABLE:x', acquiredAt: new Date(0), expiresAt: LIVE_ENTRY_FENCE_NO_EXPIRY },
    { id: `${EXIT_FILL_UNREADABLE_FENCE_PREFIX}cycle:c9`, reason: 'EXIT_FILL_UNREADABLE:y', acquiredAt: new Date(0), expiresAt: LIVE_ENTRY_FENCE_NO_EXPIRY },
    { id: 'flatten:abc', reason: 'force-flat', acquiredAt: new Date(0), expiresAt: new Date(60_000) },
  ], exitFillFenceWriteFailuresFor('u1'), { verdict: 'FLAT_NO_STOPS' });
  assert.equal(v.blocked, true);
  assert.deepEqual(v.exit_fill.map((f) => f.id), [`${EXIT_FILL_UNREADABLE_FENCE_PREFIX}p1`]);
  assert.deepEqual(v.exit_fill_cycle.map((f) => f.id), [`${EXIT_FILL_UNREADABLE_FENCE_PREFIX}cycle:c9`]);
  assert.equal(v.exit_fill[0].expires_at, null);
  assert.equal(v.other[0].kind, 'flatten');
  assert.equal(liveFenceView([], [], null).blocked, false);
  const src = readFileSync(resolve(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const i = src.indexOf('mirror.live_fences = await this.prisma.liveEntryFence.findMany(');
  assert.ok(i > 0 && src.slice(i, i + 400).includes('liveFenceView('));
  assert.ok(!/live_fences[\s\S]{0,400}(deleteMany|upsert|update\()/.test(src.slice(i, i + 400)));   // read-only
});
