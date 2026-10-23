import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { test } from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import {
  acquireLiveEntryFence,
  countLiveMutexOthers,
  LIVE_ENTRY_FENCE_TTL_MS,
  LIVE_MUTEX_INTENT_WINDOW_MS,
  releaseLiveEntryFence,
  liveMutexWhere,
  takeLiveMutexLock,
} from './signal-subscriber-execution.service';
import { FLY_APPROVAL_PLACEMENT_MAX_AGE_SEC } from './live-copy-approval';

/** Tiny READ COMMITTED store with xact-scoped advisory locks (rows become visible to others on commit). */
function fakeDb() {
  type Row = { id: string; userId: string; status: SignalCycleStatus; createdAt: Date; venue?: string | null };
  const committed: Row[] = [];
  type Fence = { id: string; userId: string; expiresAt: Date };
  const fences: Fence[] = [];
  const events: string[] = [];
  const locks = new Map<string, Promise<void>>();
  let seq = 0;
  const matches = (r: Row, w: ReturnType<typeof liveMutexWhere>) => {
    if (r.userId !== w.userId) return false;
    if ('id' in w && w.id && r.id === (w.id as { not: string }).not) return false;
    if (!w.AND[0].OR.some((v) => (v.venue ?? null) === (r.venue ?? null))) return false;
    return w.OR.some((c) => {
      const st = c.status as unknown;
      if (typeof st === 'object' && st && 'in' in st) return (st as { in: SignalCycleStatus[] }).in.includes(r.status);
      return r.status === st && r.createdAt >= (c as { createdAt: { gte: Date } }).createdAt.gte;
    });
  };
  const fenceCount = (pool: Fence[], where: { userId: string; expiresAt: { gt: Date } }) =>
    pool.filter((f) => f.userId === where.userId && f.expiresAt > where.expiresAt.gt).length;
  async function txn<T>(fn: (tx: any) => Promise<T>): Promise<T> {
    const mine: Row[] = [];
    const myFences: Fence[] = [];
    const held: Array<() => void> = [];
    const tx = {
      $executeRawUnsafe: async (_q: string, key: string) => {
        while (locks.has(key)) await locks.get(key);
        let release!: () => void;
        locks.set(key, new Promise<void>((r) => { release = () => { locks.delete(key); r(); }; }));
        held.push(release);
      },
      liveEntryFence: {
        create: async ({ data }: { data: Fence }) => {
          await new Promise((r) => setTimeout(r, 5));
          myFences.push({ id: data.id, userId: data.userId, expiresAt: data.expiresAt });
        },
        count: async ({ where }: { where: { userId: string; expiresAt: { gt: Date } } }) => {
          const n = fenceCount([...fences, ...myFences], where);
          await new Promise((r) => setTimeout(r, 5));
          return n;
        },
      },
      signalCycleParticipant: {
        create: async ({ data }: { data: { userId: string; venue?: string | null } }) => {
          await new Promise((r) => setTimeout(r, 5));   // widen the race window
          const row = { id: `p${++seq}`, userId: data.userId, status: SignalCycleStatus.INTENT, createdAt: new Date(),
            venue: data.venue === undefined ? 'bitfinex' : data.venue };
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
      fences.push(...myFences);
      mine.forEach((r) => events.push(`claim:${r.id}`));
      myFences.forEach((f) => events.push(`fence:${f.id}`));
      return out;
    } finally {
      held.forEach((r) => r());
    }
  }
  const db = {
    $transaction: txn,
    liveEntryFence: {
      update: async ({ where, data }: { where: { id: string }; data: { expiresAt: Date } }) => {
        const f = fences.find((x) => x.id === where.id);
        if (f) f.expiresAt = data.expiresAt;
      },
      deleteMany: async ({ where }: { where: { id: string } }) => {
        const i = fences.findIndex((x) => x.id === where.id);
        if (i >= 0) fences.splice(i, 1);
        events.push(`release:${where.id}`);
      },
    },
  };
  return { txn, committed, fences, events, db };
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

test('mutex is account-wide (no agent filter) and counts INTENT inside the 180 s placement window (HM N4)', () => {
  const now = 1_800_000_000_000;
  const w = liveMutexWhere('u1', 'me', now);
  assert.equal(JSON.stringify(w).includes('agentId'), false);
  assert.equal(LIVE_MUTEX_INTENT_WINDOW_MS, FLY_APPROVAL_PLACEMENT_MAX_AGE_SEC * 1000);
  assert.ok(LIVE_MUTEX_INTENT_WINDOW_MS >= 180_000);
  const intent = w.OR.find((c) => c.status === SignalCycleStatus.INTENT) as { createdAt: { gte: Date } };
  assert.equal(intent.createdAt.gte.getTime(), now - 180_000);
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

test('HM N5 venue filter: Bitfinex and legacy no-venue rows count; another venue never blocks the Bitfinex account', async () => {
  const w = liveMutexWhere('u1', null, 1_800_000_000_000);
  assert.deepEqual(w.AND, [{ OR: [{ venue: 'bitfinex' }, { venue: null }] }]);
  for (const [venue, expect] of [['bitfinex', 1], [null, 1], ['paper', 0]] as const) {
    const db = fakeDb();
    await db.txn(async (tx) => tx.signalCycleParticipant.create({ data: { userId: 'u1', venue } }));
    const n = await db.txn(async (tx) => {
      await takeLiveMutexLock(tx, 'u1');
      const row = await tx.signalCycleParticipant.create({ data: { userId: 'u1' } });
      return countLiveMutexOthers(tx, 'u1', row.id);
    });
    assert.equal(n, expect, String(venue));
  }
});

test('HM1 fence: an unexpired LiveEntryFence makes every claim section refuse; release / expiry lifts it', async () => {
  const db = fakeDb();
  const now = Date.now();
  await acquireLiveEntryFence(db.db, 'u1', 'f1', 'LIQ_CLAMP_FLATTEN:t1', now);
  assert.equal(await claim(db, 'u1', true), 1);               // hire / catch-up claim refuses (others >= 1)
  assert.equal(await claim(db, 'u2', true), 0);               // another account is unaffected
  await releaseLiveEntryFence(db.db, 'f1');
  assert.equal(await claim(db, 'u1', true), 1);               // (the earlier refused claim row is still INTENT)
  const db2 = fakeDb();
  await acquireLiveEntryFence(db2.db, 'u1', 'f2', 'x', now - LIVE_ENTRY_FENCE_TTL_MS - 1);   // a crashed holder's expired fence
  assert.equal(await claim(db2, 'u1', true), 0);
});

test('HM1 fence race (20x): a claim racing the fence acquisition is admitted only if it committed BEFORE the fence', async () => {
  for (let i = 0; i < 20; i += 1) {
    const db = fakeDb();
    const [others] = await Promise.all([claim(db, 'u1', true), acquireLiveEntryFence(db.db, 'u1', 'f', 'r')]);
    const claimAt = db.events.findIndex((e) => e.startsWith('claim:'));
    const fenceAt = db.events.indexOf('fence:f');
    if (others === 0) assert.ok(claimAt < fenceAt, `iteration ${i}: admitted after the fence ${db.events}`);
    else assert.ok(fenceAt < claimAt, `iteration ${i}: refused without a fence ${db.events}`);
    // the pre-submit recount (countOtherLiveParticipants, no lock) of an admitted claim now sees the fence
    if (others === 0) assert.equal(await db.txn((tx) => countLiveMutexOthers(tx, 'u1', db.committed[0].id)), 1);
  }
});

test('HM1 fence: the route holds it for its whole run and releases it in finally (both ops routes call forceFlatDisarmAccount)', () => {
  const src = readFileSync(resolve(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const body = src.slice(src.indexOf('  async forceFlatDisarmAccount('), src.indexOf('  private async forceFlatDisarmAccountInner('));
  assert.ok(body.indexOf('acquireLiveEntryFence(') < body.indexOf('this.forceFlatDisarmAccountInner('));
  assert.ok(body.indexOf('} finally {') > body.indexOf('this.forceFlatDisarmAccountInner('));
  assert.ok(body.slice(body.indexOf('} finally {')).includes('releaseLiveEntryFence('));
  const acq = src.slice(src.indexOf('export async function acquireLiveEntryFence('), src.indexOf('export async function refreshLiveEntryFence('));
  assert.ok(acq.indexOf('takeLiveMutexLock(tx, userId)') < acq.indexOf('liveEntryFence.create('));
  const ctl = readFileSync(resolve(__dirname, 'trading-agents.controller.ts'), 'utf8');
  assert.equal((ctl.match(/forceFlatDisarmAccount\(uid, slug, reason\)/g) ?? []).length, 2);
  assert.equal((ctl.match(/&& flat\.fenced/g) ?? []).length, 2);
});
