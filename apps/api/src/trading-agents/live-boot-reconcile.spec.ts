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
function svc(o: { read: Read; rows?: Array<{ id: string; status: string; direction: 'LONG' | 'SHORT'; qty: number; stops: number[] }>; creds?: boolean }) {
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
    loadExecutionMeta: async (id: string) => { const r = rows.find((x) => x.id === id)!; return { direction: r.direction, qty: r.qty }; },
    participantRecordedStopOrderIds: async (id: string) => rows.find((x) => x.id === id)!.stops,
    prisma: {
      tradingAgentInstance: { findMany: async () => [{ userId: 'u1' }] },
      signalCycleParticipant: { findMany: async () => rows.map((r) => ({ id: r.id, status: r.status })) },
      liveEntryFence: { deleteMany: async (a: unknown) => { calls.push(`fence-clear:${JSON.stringify(a)}`); } },
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
  assert.ok(ok.calls.some((c) => c.startsWith('fence-clear:') && c.includes('"lt"')));         // crashed holders' fences cleared only now

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
    assert.ok(!b.calls.some((c) => c.startsWith('fence-clear:')), `${why}: stale fences must stay`);
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
  assert.ok(src.includes('return others + fences + (liveBootEntryBlocked(userId) ? 1 : 0);'));
  const acct = src.slice(src.indexOf('  private async liveBootReconcileAccount('), src.indexOf('  async countOpenAccountRows('));
  assert.ok(acct.indexOf('readSentAtMs = Date.now();') < acct.indexOf('this.bitfinex.getOpenPositionDetail(creds)'));
  assert.ok(acct.includes('this.bitfinex.listActiveOrders(creds)'));
  const py = readFileSync(resolve(__dirname, '../../../../services/btc-conservative-agent/live_guardian.py'), 'utf8');
  assert.ok(py.includes('RELAY_BOOT_RECONCILE'));
});
