import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { resolve } from 'node:path';
import { test } from 'node:test';
import {
  forceFlatReadBackVerdict,
  SignalSubscriberExecutionService,
} from './signal-subscriber-execution.service';

test('force-flat read-back: FLAT_NO_STOPS only with no position AND no active order (stops included)', () => {
  assert.equal(forceFlatReadBackVerdict(0, 0), 'FLAT_NO_STOPS');
  assert.equal(forceFlatReadBackVerdict(0, 1), 'FLAT_WITH_ORDERS');
  assert.equal(forceFlatReadBackVerdict(0.0003, 0), 'NOT_FLAT');
  assert.equal(forceFlatReadBackVerdict(-0.00004, 2), 'NOT_FLAT');
  assert.equal(forceFlatReadBackVerdict(Number.NaN, 0), 'NOT_FLAT');
});

function svc(opts: { pos: Array<{ amount: number } | null>; orders: Array<Array<{ id: number }>>; cancelFail?: number }) {
  const s = Object.create(SignalSubscriberExecutionService.prototype) as SignalSubscriberExecutionService;
  const calls: string[] = [];
  const pos = [...opts.pos];
  const orders = [...opts.orders];
  Object.assign(s as unknown as Record<string, unknown>, {
    emergencyFlattenOpenCopyLots: async () => { calls.push('lots'); return { flattened: 1 }; },
    prisma: {
      $transaction: async (fn: (tx: unknown) => Promise<unknown>) => fn({
        $executeRawUnsafe: async () => { calls.push('fence:lock'); },
        liveEntryFence: { create: async () => { calls.push('fence:acquire'); } },
      }),
      liveEntryFence: {
        update: async () => { calls.push('fence:refresh'); },
        deleteMany: async () => { calls.push('fence:release'); },
      },
      tradingAgent: { findUnique: async () => ({ id: 'a1' }) },
      tradingAgentInstance: { findUnique: async () => ({ exchangeProvider: 'bitfinex' }) },
    },
    exchanges: { getUserCredentials: async () => ({ apiKey: 'k', apiSecret: 's' }) },
    bitfinex: {
      getOpenPositionDetail: async () => { calls.push('pos'); return pos.shift() ?? null; },
      submitPositionFlatten: async (_c: unknown, p: { positionDirection: string; qty: number }) => {
        calls.push(`flatten:${p.positionDirection}:${p.qty}`); return 77;
      },
      listActiveOrders: async () => { calls.push('orders'); return orders.shift() ?? []; },
      cancelOrder: async (_c: unknown, id: number) => {
        if (id === opts.cancelFail) throw new Error('nope');
        calls.push(`cancel:${id}`);
      },
    },
  });
  return { s, calls };
}

test('forceFlatDisarmAccount: lots -> reduce-only residual flatten -> cancel all (stops too) -> read back FLAT_NO_STOPS', async () => {
  const { s, calls } = svc({ pos: [{ amount: 0.0003 }, null], orders: [[{ id: 5 }, { id: 6 }], []] });
  const r = await s.forceFlatDisarmAccount('u', 'slug');
  assert.equal(r.verdict, 'FLAT_NO_STOPS');
  assert.equal(r.residualCloseOrderId, 77);
  assert.equal(r.cancelled, 2);
  const ex = calls.filter((c) => !c.startsWith('fence:'));
  assert.deepEqual(ex.slice(0, 6), ['lots', 'pos', 'flatten:LONG:0.0003', 'orders', 'cancel:5', 'cancel:6']);
  // HM1: fence acquired (under the mutex lock) before anything else, refreshed, released last
  assert.deepEqual(calls.slice(0, 2), ['fence:lock', 'fence:acquire']);
  assert.equal(calls[calls.length - 1], 'fence:release');
  assert.ok(calls.includes('fence:refresh'));
  assert.equal(r.fenced, true);
  assert.equal(JSON.stringify(r).includes('apiSecret'), false);
});

test('forceFlatDisarmAccount: a stop that will not cancel is reported, never FLAT_NO_STOPS', async () => {
  const { s } = svc({ pos: [null, null], orders: [[{ id: 9 }], [{ id: 9 }]], cancelFail: 9 });
  const r = await s.forceFlatDisarmAccount('u', 'slug');
  assert.equal(r.verdict, 'FLAT_WITH_ORDERS');
  assert.ok(r.errors.some((e) => e.startsWith('cancel:9')));
});

test('ops/force-flat-disarm route: admin token, confirmation, pause first, ok only when paused AND FLAT_NO_STOPS, never resumes', () => {
  const src = readFileSync(resolve(__dirname, 'trading-agents.controller.ts'), 'utf8');
  const i = src.indexOf("@Post(':slug/ops/force-flat-disarm')");
  assert.ok(i > 0);
  const body = src.slice(i, src.indexOf('@Post(', i + 10));
  assert.ok(body.includes('assertBotAdminToken(adminHeader, authorization)'));
  assert.ok(body.includes("'FORCE_FLAT_AND_DISARM'"));
  assert.ok(body.indexOf('setInstancePaused(uid, slug, true)') < body.indexOf('forceFlatDisarmAccount('));
  assert.ok(body.includes("paused && flat.verdict === 'FLAT_NO_STOPS' && flat.fenced"));
  assert.equal(/setInstancePaused\([^)]*false\)/.test(body), false);
  assert.equal(/adminHeader\}|authorization\}/.test(body), false); // the token is never logged
});

test('ops/position-flatten route (AMEND5 G4(b) LIQ_CLAMP_FLATTEN): admin token, confirmation, flatten + read-back, NEVER pauses or resumes', () => {
  const src = readFileSync(resolve(__dirname, 'trading-agents.controller.ts'), 'utf8');
  const i = src.indexOf("@Post(':slug/ops/position-flatten')");
  assert.ok(i > 0);
  const body = src.slice(i, src.indexOf('@Post(', i + 10));
  assert.ok(body.includes('assertBotAdminToken(adminHeader, authorization)'));
  assert.ok(body.includes("'FLATTEN_POSITION_ONLY'"));
  assert.ok(body.includes('forceFlatDisarmAccount(uid, slug, reason)'));
  assert.ok(body.includes("flat.verdict === 'FLAT_NO_STOPS' && flat.fenced"));
  assert.equal(/setInstancePaused\(/.test(body), false); // position only: the tiles stay armed (no pause, no resume)
  assert.equal(/adminHeader\}|authorization\}/.test(body), false); // the token is never logged
});

test('HM1: a fence that cannot be acquired still flattens (safety) but is reported -> fenced=false (route ok=false)', async () => {
  const { s } = svc({ pos: [null, null], orders: [[], []] });
  (s as unknown as { prisma: Record<string, unknown> }).prisma.$transaction = async () => { throw new Error('db down'); };
  const r = await s.forceFlatDisarmAccount('u', 'slug', 'why');
  assert.equal(r.verdict, 'FLAT_NO_STOPS');
  assert.equal(r.fenced, false);
  assert.ok(r.errors.some((e) => e.startsWith('fence:')));
});

/** HM2: a service whose participant table is a tiny in-memory store (OPEN/PENDING rows) and whose exchange is a toggle. */
function reconcileSvc(opts: { rows: Array<{ id: string; status: string; venue?: string | null }>; updateFails?: boolean }) {
  const s = Object.create(SignalSubscriberExecutionService.prototype) as SignalSubscriberExecutionService;
  const rows = opts.rows.map((r) => ({ venue: 'bitfinex', terminalCloseAuthority: null as unknown, ...r }));
  const ex = { amount: 0.0003 };
  const match = (r: (typeof rows)[number], where: any) =>
    where.status.in.includes(r.status)
    && (!where.id || where.id === r.id)
    && (!where.AND || [r.venue === 'bitfinex', r.venue == null].some(Boolean));
  Object.assign(s as unknown as Record<string, unknown>, {
    prisma: {
      $transaction: async (fn: (tx: unknown) => Promise<unknown>) =>
        fn({ $executeRawUnsafe: async () => undefined, liveEntryFence: { create: async () => undefined } }),
      liveEntryFence: { update: async () => undefined, deleteMany: async () => undefined },
      tradingAgent: { findUnique: async () => ({ id: 'a1' }) },
      tradingAgentInstance: { findUnique: async () => ({ exchangeProvider: 'bitfinex' }) },
      signalCycleParticipant: {
        findMany: async ({ where }: any) => rows.filter((r) => match(r, where)).map((r) => ({ ...r })),
        updateMany: async ({ where, data }: any) => {
          if (opts.updateFails) throw new Error('db write failed');
          const hit = rows.filter((r) => match(r, where));
          hit.forEach((r) => Object.assign(r, data));
          return { count: hit.length };
        },
        count: async ({ where }: any) => rows.filter((r) => match(r, where)).length,
      },
    },
    // closeVirtualLot submits the Bitfinex close (the position goes flat) and THEN throws (e.g. a DB write after the fill)
    closeVirtualLot: async () => { ex.amount = 0; throw new Error('db write after close failed'); },
    emergencyFlattenOpenCopyLots: async function (this: any) {
      await this.closeVirtualLot();
      return { flattened: 1 };
    },
    exchanges: { getUserCredentials: async () => ({ apiKey: 'k', apiSecret: 's' }) },
    bitfinex: {
      getOpenPositionDetail: async () => (ex.amount ? { amount: ex.amount } : null),
      submitPositionFlatten: async () => { ex.amount = 0; return 1; },
      listActiveOrders: async () => [],
      cancelOrder: async () => undefined,
    },
  });
  return { s, rows };
}

test('HM2: closeVirtualLot throws AFTER the Bitfinex close -> exchange FLAT_NO_STOPS -> every OPEN/PENDING row CLOSED non-countable with trigger + exchange evidence', async () => {
  const { s, rows } = reconcileSvc({ rows: [{ id: 'p1', status: 'OPEN' }, { id: 'p2', status: 'PENDING_ENTRY', venue: null }, { id: 'p3', status: 'OPEN', venue: 'paper' }] });
  const r = await s.forceFlatDisarmAccount('u', 'slug', 'LIQ_CLAMP_FLATTEN:t1');
  assert.equal(r.verdict, 'FLAT_NO_STOPS');
  assert.ok(r.errors.some((e) => e.startsWith('lots:')));       // the throw is reported...
  assert.equal(r.dbReconciled, true);                            // ...but the DB follows the exchange
  assert.equal(r.dbClosed, 2);
  for (const id of ['p1', 'p2']) {
    const row = rows.find((x) => x.id === id)!;
    assert.equal(row.status, 'CLOSED');
    const ev = row.terminalCloseAuthority as Record<string, any>;
    assert.equal(ev.trigger_reason, 'LIQ_CLAMP_FLATTEN:t1');
    assert.equal(ev.non_countable, true);
    assert.equal(ev.exchange_readback.verdict, 'FLAT_NO_STOPS');
    assert.equal(ev.exchange_readback.position_amount, 0);
  }
  assert.equal(rows.find((x) => x.id === 'p3')!.status, 'OPEN');   // another venue is never touched
});

test('HM2: a failed DB reconcile -> dbReconciled=false (the routes return ok=false -> Fly RED); no reconcile unless FLAT_NO_STOPS', async () => {
  const { s, rows } = reconcileSvc({ rows: [{ id: 'p1', status: 'OPEN' }], updateFails: true });
  const r = await s.forceFlatDisarmAccount('u', 'slug', 'AUTO_DISARM:X');
  assert.equal(r.verdict, 'FLAT_NO_STOPS');
  assert.equal(r.dbReconciled, false);
  assert.ok(r.errors.some((e) => e.startsWith('db-reconcile:')));
  assert.equal(rows[0].status, 'OPEN');
  await assert.rejects(
    s.reconcileFlatAccountRows('u', 'x', { verdict: 'NOT_FLAT', positionAmount: 1, openOrders: 0, readAt: 'now' } as any),
  );
  const ctl = readFileSync(resolve(__dirname, 'trading-agents.controller.ts'), 'utf8');
  assert.equal((ctl.match(/&& flat\.fenced && flat\.dbReconciled;/g) ?? []).length, 2);   // both routes
});

test('HM4: the trigger reason reaches emergencyFlattenOpenCopyLots (NOT as operator) and is stamped into the closed row evidence', async () => {
  const { s } = reconcileSvc({ rows: [] });
  const seen: unknown[][] = [];
  (s as any).emergencyFlattenOpenCopyLots = async (...args: unknown[]) => { seen.push(args); return { flattened: 0 }; };
  await s.forceFlatDisarmAccount('u', 'slug', 'LIQ_CLAMP_FLATTEN:t9');
  assert.equal(seen.length, 1);
  assert.equal(seen[0][2], undefined);                       // operator mode would skip the OPEN loop
  assert.deepEqual(seen[0][3], { reason: 'LIQ_CLAMP_FLATTEN:t9' });
  const src = readFileSync(resolve(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const fn = src.slice(src.indexOf('  async emergencyFlattenOpenCopyLots('));
  const ev = fn.slice(fn.indexOf("action: 'EMERGENCY_FLATTEN_OPEN_COPY_LOTS'"), fn.indexOf("action: 'EMERGENCY_FLATTEN_OPEN_COPY_LOTS'") + 400);
  assert.match(ev, /trigger_reason: trigger\.reason/);
  assert.match(ev, /non_countable: true/);
  assert.match(ev, /non_countable_reason: `FORCE_FLAT:\$\{trigger\.reason\}`/);
});
