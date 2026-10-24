/**
 * HM R4 B2 (RK 08:03 binding): the relay entry fence has no TTL, and on boot the money-path process blocks every live entry
 * until an authenticated Bitfinex position + active-order read, sent after the process started, is FLAT_NO_STOPS or matches
 * the DB with every stop resident. Anything else (incl. unreadable) blocks and is RED; every attempt is stamped with its read time.
 */
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { test } from 'node:test';
import {
  countLiveMutexOthers,
  LIVE_PROCESS_START_MS,
  liveBootEntryBlocked,
  liveBootGateSetPhase,
  liveBootReconcileStampFor,
  liveBootReconcileVerdict,
  LIVE_BOOT_RECONCILE_MAX_ATTEMPTS,
  LIVE_ENTRY_FENCE_NO_EXPIRY,
  LIVE_FENCE_LEASE_MS,
  staleFlattenFenceWhere,
  staleFlattenFenceRowIsStale,
  staleFlattenFenceDeleteAllowed,
  deleteStaleFlattenFences,
  STALE_FLATTEN_FENCE_DELETE_SQL,
  flattenFenceId,
  exitFillUnreadableFenceId,
  SignalSubscriberExecutionService,
} from './signal-subscriber-execution.service';

const START = 1_000_000;
const AFTER = START + 50;
const open = (id: string, direction: 'LONG' | 'SHORT', qty: number, stops: number[]) =>
  ({ id, status: 'OPEN', direction, qty, stopOrderIds: stops });

test('verdict table: only FLAT_NO_STOPS or a full DB match with resident stops allows entries', () => {
  const v = (o: Partial<Parameters<typeof liveBootReconcileVerdict>[0]>) => liveBootReconcileVerdict({
    processStartMs: START, readSentAtMs: AFTER, positionAmount: 0, activeOrders: [], dbRows: [], ...o,
  });
  assert.deepEqual([v({}).verdict, v({}).allowed], ['FLAT_NO_STOPS', true]);
  assert.equal(v({ activeOrders: [{ id: 9 }] }).verdict, 'MISMATCH');                        // orphan stop while flat
  assert.equal(v({ dbRows: [open('p', 'LONG', 0.001, [9])] }).verdict, 'MISMATCH');            // DB open, exchange flat
  assert.equal(v({ readSentAtMs: START }).verdict, 'STALE_READ');                              // read not after the boot
  assert.equal(v({ readSentAtMs: START - 1 }).allowed, false);
  for (const k of ['readSentAtMs', 'positionAmount', 'activeOrders', 'dbRows'] as const) {
    const r = v({ [k]: null });
    assert.deepEqual([r.verdict, r.allowed], ['UNREADABLE', false], k);
  }
  assert.equal(v({ positionAmount: Number.NaN }).verdict, 'UNREADABLE');
  const m = v({ positionAmount: 0.001, activeOrders: [{ id: 9 }], dbRows: [open('p', 'LONG', 0.001, [8, 9])] });
  assert.deepEqual([m.verdict, m.allowed], ['MATCH_STOPS_RESIDENT', true]);
  assert.equal(v({ positionAmount: -0.002, activeOrders: [{ id: 9 }], dbRows: [open('p', 'SHORT', 0.002, [9])] }).allowed, true);
  assert.equal(v({ positionAmount: 0.001, activeOrders: [], dbRows: [open('p', 'LONG', 0.001, [9])] }).verdict, 'STOP_NOT_RESIDENT');
  assert.equal(v({ positionAmount: 0.001, activeOrders: [{ id: 9 }], dbRows: [open('p', 'LONG', 0.001, [])] }).verdict, 'STOP_NOT_RESIDENT');
  assert.equal(v({ positionAmount: 0.002, activeOrders: [{ id: 9 }], dbRows: [open('p', 'LONG', 0.001, [9])] }).verdict, 'MISMATCH');
  assert.equal(v({ positionAmount: -0.001, activeOrders: [{ id: 9 }], dbRows: [open('p', 'LONG', 0.001, [9])] }).verdict, 'MISMATCH');
  assert.equal(v({ positionAmount: 0.001, activeOrders: [{ id: 9 }, { id: 10 }], dbRows: [open('p', 'LONG', 0.001, [9])] }).verdict, 'MISMATCH');
  assert.equal(v({ positionAmount: 0.001, activeOrders: [{ id: 9 }], dbRows: [open('p', 'LONG', 0.001, [9]), { ...open('q', 'LONG', 0.001, []), status: 'PENDING_ENTRY' }] }).verdict, 'MISMATCH');
  assert.equal(v({ positionAmount: 0.001, activeOrders: [{ id: 9 }], dbRows: [{ ...open('p', 'LONG', 0.001, [9]), qty: null }] }).verdict, 'MISMATCH');
});

type Read = { pos: { amount: number } | null; orders: Array<{ id: number }> } | Error;
function svc(o: { read: Read; rows?: Array<{ id: string; status: string; direction: 'LONG' | 'SHORT'; qty: number; stops: number[]; entryOrderId?: number }>; creds?: boolean; users?: string[] }) {
  const s = Object.create(SignalSubscriberExecutionService.prototype) as SignalSubscriberExecutionService;
  const calls: string[] = [];
  const logs: string[] = [];
  const rows = o.rows ?? [];
  Object.assign(s as unknown as Record<string, unknown>, {
    executorDestroyed: true,   // no retry timers in the test (the first attempt still runs: the guard is in the retry path)
    logger: { error: (m: string) => logs.push(`E ${m}`), warn: (m: string) => logs.push(`W ${m}`), log: () => undefined },
    swallowedError: () => () => undefined,
    exchanges: { getUserCredentials: async () => (o.creds === false ? null : { apiKey: 'k', apiSecret: 's' }) },
    bitfinex: {
      getOpenPositionDetail: async () => { calls.push(`pos@${Date.now()}`); if (o.read instanceof Error) throw o.read; return o.read.pos; },
      listActiveOrders: async () => { calls.push('orders'); if (o.read instanceof Error) throw o.read; return o.read.orders; },
    },
    loadExecutionMeta: async (id: string) => { const r = rows.find((x) => x.id === id)!; return { direction: r.direction, qty: r.qty, bitfinexOrderId: r.entryOrderId }; },
    participantRecordedStopOrderIds: async (id: string) => rows.find((x) => x.id === id)!.stops,
    prisma: {
      tradingAgentInstance: { findMany: async () => (o.users ?? ['u1']).map((userId) => ({ userId })) },
      signalCycleParticipant: { findMany: async () => rows.map((r) => ({ id: r.id, status: r.status })) },
      liveEntryFence: { deleteMany: async (a: unknown) => { calls.push(`fence-clear:${JSON.stringify(a)}`); } },
      $queryRawUnsafe: async (q: string, ...v: unknown[]) => { calls.push(`fence-clear-sql:${q.includes('now()')}:${JSON.stringify(v[0])}`); return []; },
      $executeRaw: async (..._a: unknown[]) => { calls.push('stamp'); return 1; },
    },
  });
  // run the private pass (attempt 1) the way onModuleInit does
  const run = async () => {
    liveBootGateSetPhase('PENDING');
    assert.equal(liveBootEntryBlocked('u1'), true);   // pending: every account blocked before the read
    await (s as unknown as { runLiveBootReconcile: (n: number) => Promise<void> }).runLiveBootReconcile(1);
  };
  return { s, calls, logs, run };
}
const tx = { signalCycleParticipant: { count: async () => 0 }, liveEntryFence: { count: async () => 0 } };

test('RK: restart with an open position + resident stop -> entries allowed ONLY on a DB match', async () => {
  const row = { id: 'p1', status: 'OPEN', direction: 'LONG' as const, qty: 0.001, stops: [42] };
  const ok = svc({ read: { pos: { amount: 0.001 }, orders: [{ id: 42 }] }, rows: [row] });
  await ok.run();
  assert.equal(liveBootEntryBlocked('u1'), false);
  assert.equal(await countLiveMutexOthers(tx, 'u1', null), 0);
  const st = liveBootReconcileStampFor('u1') as Record<string, unknown>;
  assert.equal(st.verdict, 'MATCH_STOPS_RESIDENT');
  assert.equal(st.red, false);
  assert.ok(Date.parse(String(st.read_sent_at)) > Date.parse(String(st.process_start_at)));     // read time after the boot
  assert.equal(st.process_start_at, new Date(LIVE_PROCESS_START_MS).toISOString());
  assert.ok(ok.calls.includes('stamp'));
  assert.ok(ok.calls.includes('fence-clear-sql:true:"u1"'));         // crashed holders' fences cleared only now, DB clock

  const cases: Array<[string, Read, typeof row | null]> = [
    ['stop not resident', { pos: { amount: 0.001 }, orders: [] }, row],
    ['qty mismatch', { pos: { amount: 0.002 }, orders: [{ id: 42 }] }, row],
    ['direction mismatch', { pos: { amount: -0.001 }, orders: [{ id: 42 }] }, row],
    ['no DB row', { pos: { amount: 0.001 }, orders: [{ id: 42 }] }, null],
  ];
  for (const [why, read, r] of cases) {
    const b = svc({ read, rows: r ? [r] : [] });
    await b.run();
    assert.equal(liveBootEntryBlocked('u1'), true, why);
    assert.equal(await countLiveMutexOthers(tx, 'u1', null), 1, why);
    assert.equal((liveBootReconcileStampFor('u1') as Record<string, unknown>).red, true, why);
    assert.ok(b.logs.some((l) => l.startsWith('E ') && l.includes('RED')), why);
    assert.ok(!b.calls.some((c) => c.startsWith('fence-clear')), `${why}: stale fences must stay`);
  }
  liveBootGateSetPhase('NOT_RUN');
});

test('RK: restart with the exchange unreadable -> blocked + RED (and credentials missing / instance list unreadable too)', async () => {
  const u = svc({ read: new Error('ETIMEDOUT') });
  await u.run();
  const st = liveBootReconcileStampFor('u1') as Record<string, unknown>;
  assert.deepEqual([st.verdict, st.allowed, st.red], ['UNREADABLE', false, true]);
  assert.ok(String(st.detail).includes('ETIMEDOUT'));
  assert.equal(liveBootEntryBlocked('u1'), true);
  assert.equal(await countLiveMutexOthers(tx, 'u1', null), 1);
  assert.ok(st.read_sent_at && st.read_at === null);                                        // stamped with the read send time

  const c = svc({ read: { pos: null, orders: [] }, creds: false });
  await c.run();
  assert.equal((liveBootReconcileStampFor('u1') as Record<string, unknown>).verdict, 'UNREADABLE');
  assert.equal(liveBootEntryBlocked('u1'), true);

  const l = svc({ read: { pos: null, orders: [] } });
  ((l.s as unknown as { prisma: { tradingAgentInstance: { findMany: () => Promise<unknown> } } }).prisma.tradingAgentInstance)
    .findMany = async () => { throw new Error('neon down'); };
  await l.run();
  assert.equal(liveBootEntryBlocked('u1'), true);                                           // gate stays PENDING
  assert.equal(liveBootEntryBlocked('any-other-user'), true);
  assert.ok(l.logs.some((x) => x.includes('RED')));
  liveBootGateSetPhase('NOT_RUN');
});

test('FLAT_NO_STOPS opens the account; a later passing re-read is the only way out of a block', async () => {
  const f = svc({ read: { pos: null, orders: [] } });
  await f.run();
  assert.equal((liveBootReconcileStampFor('u1') as Record<string, unknown>).verdict, 'FLAT_NO_STOPS');
  assert.equal(liveBootEntryBlocked('u1'), false);
  // the API process (no money path) never gates
  liveBootGateSetPhase('NOT_RUN');
  assert.equal(liveBootEntryBlocked('u1'), false);
  assert.equal(liveBootReconcileStampFor('u1'), null);
});

test('wiring: onModuleInit sets the gate PENDING before any tick; mirrors carry the stamp; Fly guardian reads it', () => {
  const src = readFileSync(resolve(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const init = src.slice(src.indexOf('  onModuleInit() {'), src.indexOf('this.scheduleReconciliation(POLL_MS);'));
  assert.ok(init.includes("liveBootGateSetPhase('PENDING');") && init.includes('void this.runLiveBootReconcile(1);'));
  assert.ok(init.indexOf('if (!executionEnabled())') < init.indexOf("liveBootGateSetPhase('PENDING')"));
  assert.ok(src.includes('mirror.boot_reconcile = liveBootReconcileStampFor(instance.userId);'));
  assert.ok(src.includes('return others + fences + (liveBootEntryBlocked(userId) ? 1 : 0) + (exitFillFenceWriteFailed(userId) ? 1 : 0);'));
  const acct = src.slice(src.indexOf('  private async liveBootReconcileRead('), src.indexOf('  private async bootCancelPendingEntriesFor('));
  assert.ok(acct.indexOf('readSentAtMs = Date.now();') > 0);
  assert.ok(acct.indexOf('readSentAtMs = Date.now();') < acct.indexOf('this.bitfinex.getOpenPositionDetail(c)'));
  assert.ok(acct.includes('this.bitfinex.listActiveOrders(c)'));
  const py = readFileSync(resolve(__dirname, '../../../../services/btc-conservative-agent/live_guardian.py'), 'utf8');
  assert.ok(py.includes('RELAY_BOOT_RECONCILE'));
});

test('HM R6 nit 5 (reverts R5 nit 5): a PENDING_ENTRY row is never accepted as resting by the verdict', () => {
  const v = (o: Partial<Parameters<typeof liveBootReconcileVerdict>[0]>) => liveBootReconcileVerdict({
    processStartMs: START, readSentAtMs: AFTER, positionAmount: 0, activeOrders: [], dbRows: [], ...o,
  });
  const pend = (id: string, eid: number | null) => ({ id, status: 'PENDING_ENTRY', direction: 'LONG' as const, qty: 0.001, stopOrderIds: [], entryOrderId: eid });
  for (const r of [
    v({ activeOrders: [{ id: 77 }], dbRows: [pend('q', 77)] }),
    v({ positionAmount: 0.001, activeOrders: [{ id: 9 }, { id: 77 }], dbRows: [open('p', 'LONG', 0.001, [9]), pend('q', 77)] }),
    v({ activeOrders: [], dbRows: [pend('q', 77)] }),
    v({ activeOrders: [{ id: 77 }], dbRows: [pend('q', null)] }),
  ]) assert.deepEqual([r.verdict, r.allowed], ['MISMATCH', false]);
  assert.ok(!readFileSync(resolve(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8').includes("'PENDING_ENTRY_MATCH'"));
});

test('HM R5 nit 4: after LIVE_BOOT_RECONCILE_MAX_ATTEMPTS failed reads -> ALERT, retries stop, entries stay BLOCKED (stamped)', async () => {
  assert.equal(LIVE_BOOT_RECONCILE_MAX_ATTEMPTS, 20);
  const u = svc({ read: new Error('ETIMEDOUT') });
  (u.s as any).executorDestroyed = false;
  liveBootGateSetPhase('PENDING');
  const timers: unknown[] = [];
  const orig = global.setTimeout;
  (global as any).setTimeout = (...a: unknown[]) => { timers.push(a); return { unref: () => undefined }; };
  try {
    await (u.s as any).runLiveBootReconcile(LIVE_BOOT_RECONCILE_MAX_ATTEMPTS - 1);
    assert.equal(timers.length, 1);   // below the cap: one retry scheduled
    await (u.s as any).runLiveBootReconcile(LIVE_BOOT_RECONCILE_MAX_ATTEMPTS);
    assert.equal(timers.length, 1);   // at the cap: no further retry
  } finally {
    (global as any).setTimeout = orig;
  }
  const st = liveBootReconcileStampFor('u1') as Record<string, unknown>;
  assert.deepEqual([st.verdict, st.allowed, st.red, st.retries_exhausted], ['UNREADABLE', false, true, true]);
  assert.equal(liveBootEntryBlocked('u1'), true);
  assert.ok(u.logs.some((l) => l.startsWith('E [LIVE-BOOT-RECONCILE] ALERT:') && l.includes('20 attempts') && l.includes('u1')));
  // instance list unreadable at the cap: ALERT, gate stays PENDING (every account blocked)
  const l = svc({ read: { pos: null, orders: [] } });
  (l.s as any).prisma.tradingAgentInstance.findMany = async () => { throw new Error('neon down'); };
  (l.s as any).executorDestroyed = false;
  liveBootGateSetPhase('PENDING');
  await (l.s as any).runLiveBootReconcile(LIVE_BOOT_RECONCILE_MAX_ATTEMPTS);
  assert.ok(l.logs.some((x) => x.includes('ALERT') && x.includes('every account')), l.logs.join('\n'));
  assert.equal(liveBootEntryBlocked('anyone'), true);
  liveBootGateSetPhase('NOT_RUN');
});

test('HM R5 nit 3: an account created after the boot pass (no stamp) is BLOCKED and gets its own boot read; opens only on a pass', async () => {
  const b = svc({ read: { pos: null, orders: [] }, users: ['u1'] });
  await b.run();
  (b.s as any).executorDestroyed = false;
  assert.equal(liveBootEntryBlocked('u1'), false);
  const seen: string[] = [];
  const { liveBootGateOnUnstamped } = await import('./signal-subscriber-execution.service');
  liveBootGateOnUnstamped((uid) => { seen.push(uid); (b.s as any).reconcileUnstampedBootUser(uid); });
  assert.equal(liveBootEntryBlocked('late'), true);                      // DONE phase, no stamp -> blocked (was: allowed)
  assert.equal(await countLiveMutexOthers(tx, 'late', null), 1);
  assert.deepEqual(liveBootReconcileStampFor('late'), { verdict: 'PENDING', allowed: false, red: false });
  assert.deepEqual(seen, ['late', 'late']);
  await (b.s as any).bootUnstampedLast;                                   // one read in flight (deduped), then stamped
  assert.equal((b.calls.filter((c) => c.startsWith('pos@'))).length, 2);  // u1 at boot + late once
  assert.equal((liveBootReconcileStampFor('late') as Record<string, unknown>).verdict, 'FLAT_NO_STOPS');
  assert.equal(liveBootEntryBlocked('late'), false);
  // a late account that does not pass stays blocked (RED) and is not re-read while its retry is pending
  const m = svc({ read: { pos: { amount: 0.001 }, orders: [] } });
  (m.s as any).executorDestroyed = false;   // its retry timer is unref'd (30 s); the gate keeps it blocked meanwhile
  liveBootGateOnUnstamped((uid) => (m.s as any).reconcileUnstampedBootUser(uid));
  assert.equal(liveBootEntryBlocked('late2'), true);
  await (m.s as any).bootUnstampedLast;
  assert.equal((liveBootReconcileStampFor('late2') as Record<string, unknown>).red, true);
  assert.equal(liveBootEntryBlocked('late2'), true);
  liveBootGateOnUnstamped(null);
  liveBootGateSetPhase('NOT_RUN');
  assert.equal(liveBootEntryBlocked('late3'), false);                     // the API process (NOT_RUN) never gates
});

test('HM R5 nit 1: boot cleanup is owner-scoped: never an exit-fill fence, never a heartbeating holder, never its own', () => {
  const now = 10_000_000;
  const procStart = now - 5_000;
  const w = staleFlattenFenceWhere('u1', now, procStart, 'me');
  // evaluate the Prisma where on in-memory rows
  const hit = (r: { id: string; userId: string; acquiredAt: Date; expiresAt: Date }) =>
    r.userId === w.userId && r.id.startsWith(w.id.startsWith) && !r.id.startsWith(w.NOT.id.startsWith)
    && w.OR.some((c: any) => (c.expiresAt instanceof Date
      ? r.expiresAt.getTime() === c.expiresAt.getTime() && r.acquiredAt < c.acquiredAt.lt
      : r.expiresAt < c.expiresAt.lt));
  const at = (ms: number) => new Date(ms);
  const rows = {
    deadHolder: { id: flattenFenceId('old'), userId: 'u1', acquiredAt: at(now - 600_000), expiresAt: at(now - LIVE_FENCE_LEASE_MS - 1) },
    liveOldWorker: { id: flattenFenceId('old'), userId: 'u1', acquiredAt: at(now - 600_000), expiresAt: at(now + 30_000) },
    justStopped: { id: flattenFenceId('old'), userId: 'u1', acquiredAt: at(now - 600_000), expiresAt: at(now - 1_000) },
    mine: { id: flattenFenceId('me'), userId: 'u1', acquiredAt: at(now - 600_000), expiresAt: at(now - 600_000) },
    legacyBeforeBoot: { id: flattenFenceId('old'), userId: 'u1', acquiredAt: at(procStart - 1), expiresAt: LIVE_ENTRY_FENCE_NO_EXPIRY },
    legacyAfterBoot: { id: flattenFenceId('old'), userId: 'u1', acquiredAt: at(procStart + 1), expiresAt: LIVE_ENTRY_FENCE_NO_EXPIRY },
    exitFill: { id: exitFillUnreadableFenceId('p1'), userId: 'u1', acquiredAt: at(now - 9e9), expiresAt: LIVE_ENTRY_FENCE_NO_EXPIRY },
    otherUser: { id: flattenFenceId('old'), userId: 'u2', acquiredAt: at(now - 600_000), expiresAt: at(now - LIVE_FENCE_LEASE_MS - 1) },
  };
  const deleted = Object.entries(rows).filter(([, r]) => hit(r)).map(([k]) => k);
  assert.deepEqual(deleted, ['deadHolder', 'legacyBeforeBoot']);
  const src = readFileSync(resolve(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const run = src.slice(src.indexOf('  private async reconcileBootUser('), src.indexOf('  private async persistBootStamp('));
  assert.ok(run.includes('deleteStaleFlattenFences(this.prisma as any, userId, LIVE_PROCESS_START_MS)'));
  assert.ok(run.includes('staleFlattenFenceDeleteAllowed(stamp)'));
  assert.ok(run.includes('[STALE-FLATTEN-FENCE-ALERT]') && run.includes('holder=${g.holder}') && run.includes('fence_key=${g.id}'));
  assert.equal(run.includes('deleteMany({ where: staleFlattenFenceWhere'), false);
  assert.equal(/acquiredAt: \{ lt: new Date\(LIVE_PROCESS_START_MS\)/.test(src), false);   // the unscoped delete is gone
});


test('RK 24 Oct: stale flatten-fence delete: DB clock, >60 s heartbeat at delete time, 59 s survives, exit-fill/entry untouched', async () => {
  const dbNow = 50_000_000; const procStart = dbNow - 5_000; const at = (ms: number) => new Date(ms);
  const hb = (ageS: number) => at(dbNow - ageS * 1000 + LIVE_FENCE_LEASE_MS);   // expiresAt written by a heartbeat ageS ago
  const rows = {
    hb59: { id: flattenFenceId('old'), userId: 'u1', acquiredAt: at(dbNow - 600_000), expiresAt: hb(59) },
    hb60: { id: flattenFenceId('old'), userId: 'u1', acquiredAt: at(dbNow - 600_000), expiresAt: hb(60) },
    hb61: { id: flattenFenceId('old'), userId: 'u1', acquiredAt: at(dbNow - 600_000), expiresAt: hb(61) },
    mineOld: { id: flattenFenceId('me'), userId: 'u1', acquiredAt: at(dbNow - 600_000), expiresAt: hb(999) },
    legacyBeforeBoot: { id: flattenFenceId('old'), userId: 'u1', acquiredAt: at(procStart - 1), expiresAt: LIVE_ENTRY_FENCE_NO_EXPIRY },
    legacyAfterBoot: { id: flattenFenceId('old'), userId: 'u1', acquiredAt: at(procStart + 1), expiresAt: LIVE_ENTRY_FENCE_NO_EXPIRY },
    exitFill: { id: exitFillUnreadableFenceId('p1'), userId: 'u1', acquiredAt: at(dbNow - 9e9), expiresAt: hb(9999) },
    entry: { id: 'entry:u1:abc', userId: 'u1', acquiredAt: at(dbNow - 9e9), expiresAt: hb(9999) },
    otherUser: { id: flattenFenceId('old'), userId: 'u2', acquiredAt: at(dbNow - 600_000), expiresAt: hb(999) },
  };
  const del = Object.entries(rows).filter(([, r]) => staleFlattenFenceRowIsStale(r, 'u1', dbNow, procStart, 'me')).map(([k]) => k);
  assert.deepEqual(del, ['hb61', 'legacyBeforeBoot']);
  // the SQL is one statement, judged on the DB clock, prefix-scoped, owner-excluded, RETURNING for the per-row log
  assert.match(STALE_FLATTEN_FENCE_DELETE_SQL, /^DELETE FROM "LiveEntryFence"/);
  assert.match(STALE_FLATTEN_FENCE_DELETE_SQL, /now\(\) - make_interval\(secs => 60\)/);
  assert.match(STALE_FLATTEN_FENCE_DELETE_SQL, /"id" LIKE 'flatten:%'/);
  assert.match(STALE_FLATTEN_FENCE_DELETE_SQL, /RETURNING "id"/);
  assert.equal(/Date\.now|new Date\(\)/.test(STALE_FLATTEN_FENCE_DELETE_SQL), false);
  const calls: unknown[][] = [];
  const out = await deleteStaleFlattenFences({ $queryRawUnsafe: async (...a: unknown[]) => { calls.push(a); return [{ id: 'flatten:old-1:x', acquiredAt: 1, expiresAt: 2 }]; } }, 'u1', procStart, 'me');
  assert.equal(calls.length, 1); assert.equal(calls[0][2], 'flatten:me:%');
  assert.deepEqual(out.map((o) => [o.id, o.holder]), [['flatten:old-1:x', 'old-1']]);
  // gate: only FLAT_NO_STOPS or MATCH_STOPS_RESIDENT (allowed)
  assert.equal(staleFlattenFenceDeleteAllowed({ verdict: 'FLAT_NO_STOPS', allowed: true }), true);
  assert.equal(staleFlattenFenceDeleteAllowed({ verdict: 'MATCH_STOPS_RESIDENT', allowed: true }), true);
  for (const v of ['MISMATCH', 'STOP_NOT_RESIDENT', 'STALE_READ', 'UNREADABLE', 'PENDING_ENTRY_CANCEL_FAILED'])
    assert.equal(staleFlattenFenceDeleteAllowed({ verdict: v, allowed: true }), false);
  assert.equal(staleFlattenFenceDeleteAllowed({ verdict: 'FLAT_NO_STOPS', allowed: false }), false);
});
