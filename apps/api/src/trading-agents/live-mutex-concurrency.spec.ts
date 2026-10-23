import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { test } from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import {
  countLiveMutexOthers,
  LIVE_MUTEX_INTENT_WINDOW_MS,
  liveMutexWhere,
  takeLiveMutexLock,
} from './signal-subscriber-execution.service';

/** Tiny READ COMMITTED store with xact-scoped advisory locks (rows become visible to others on commit). */
function fakeDb() {
  type Row = { id: string; userId: string; status: SignalCycleStatus; createdAt: Date };
  const committed: Row[] = [];
  const locks = new Map<string, Promise<void>>();
  let seq = 0;
  const matches = (r: Row, w: ReturnType<typeof liveMutexWhere>) => {
    if (r.userId !== w.userId) return false;
    if ('id' in w && w.id && r.id === (w.id as { not: string }).not) return false;
    return w.OR.some((c) => {
      const st = c.status as unknown;
      if (typeof st === 'object' && st && 'in' in st) return (st as { in: SignalCycleStatus[] }).in.includes(r.status);
      return r.status === st && r.createdAt >= (c as { createdAt: { gte: Date } }).createdAt.gte;
    });
  };
  async function txn<T>(fn: (tx: any) => Promise<T>): Promise<T> {
    const mine: Row[] = [];
    const held: Array<() => void> = [];
    const tx = {
      $executeRawUnsafe: async (_q: string, key: string) => {
        while (locks.has(key)) await locks.get(key);
        let release!: () => void;
        locks.set(key, new Promise<void>((r) => { release = () => { locks.delete(key); r(); }; }));
        held.push(release);
      },
      signalCycleParticipant: {
        create: async ({ data }: { data: { userId: string } }) => {
          await new Promise((r) => setTimeout(r, 5));   // widen the race window
          const row = { id: `p${++seq}`, userId: data.userId, status: SignalCycleStatus.INTENT, createdAt: new Date() };
          mine.push(row);
          return row;
        },
        count: async ({ where }: { where: ReturnType<typeof liveMutexWhere> }) => {
          const n = [...committed, ...mine].filter((r) => matches(r, where)).length;   // statement snapshot
          await new Promise((r) => setTimeout(r, 5));
          return n;
        },
      },
    };
    try {
      const out = await fn(tx);
      committed.push(...mine);
      return out;
    } finally {
      held.forEach((r) => r());
    }
  }
  return { txn, committed };
}

async function claim(db: ReturnType<typeof fakeDb>, userId: string, lock: boolean) {
  return db.txn(async (tx) => {
    if (lock) await takeLiveMutexLock(tx, userId);
    const row = await tx.signalCycleParticipant.create({ data: { userId } });
    return countLiveMutexOthers(tx, userId, row.id);
  });
}

test('HM C3 concurrency: two simultaneous claims under the account-wide lock -> at most one admitted', async () => {
  for (let i = 0; i < 20; i += 1) {
    const db = fakeDb();
    const others = await Promise.all([claim(db, 'u1', true), claim(db, 'u1', true)]);
    assert.equal(others.filter((n) => n === 0).length, 1, `iteration ${i}: ${others}`);
  }
});

test('HM C3 control: without the lock the same race admits both (what the lock prevents)', async () => {
  const db = fakeDb();
  const others = await Promise.all([claim(db, 'u1', false), claim(db, 'u1', false)]);
  assert.deepEqual(others, [0, 0]);
});

test('mutex is account-wide (no agent filter) and counts INTENT only inside the 120 s window', () => {
  const now = 1_800_000_000_000;
  const w = liveMutexWhere('u1', 'me', now);
  assert.equal(JSON.stringify(w).includes('agentId'), false);
  assert.equal(LIVE_MUTEX_INTENT_WINDOW_MS, 120_000);
  const intent = w.OR.find((c) => c.status === SignalCycleStatus.INTENT) as { createdAt: { gte: Date } };
  assert.equal(intent.createdAt.gte.getTime(), now - 120_000);
  assert.deepEqual((w.OR[0].status as { in: SignalCycleStatus[] }).in, [SignalCycleStatus.PENDING_ENTRY, SignalCycleStatus.OPEN]);
  assert.deepEqual(w.id, { not: 'me' });
});

test('hire and catch-up claims take the live-mutex lock before inserting, in the claim txn', () => {
  const src = readFileSync(resolve(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const hire = src.slice(src.indexOf('private runEntryClaimTx('), src.indexOf('private async releaseSpeculativeClaim('));
  assert.ok(hire.indexOf('takeLiveMutexLock(tx') < hire.indexOf('lockedEntryClaimInsert(tx'));
  assert.ok(hire.includes('countLiveMutexOthers(tx, instance.userId, claimed.id)'));
  const cu = src.slice(src.indexOf('let catchupMutexOthers = 0;'), src.indexOf('const catchupAdmission'));
  assert.equal((cu.match(/takeLiveMutexLock\(tx, instance\.userId\)/g) ?? []).length, 2);
  assert.ok(cu.includes('otherLiveParticipants = Math.max(otherLiveParticipants, catchupMutexOthers)'));
});
