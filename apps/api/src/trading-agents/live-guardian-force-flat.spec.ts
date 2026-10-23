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
  assert.deepEqual(calls.slice(0, 6), ['lots', 'pos', 'flatten:LONG:0.0003', 'orders', 'cancel:5', 'cancel:6']);
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
  assert.ok(body.includes("paused && flat.verdict === 'FLAT_NO_STOPS'"));
  assert.equal(/setInstancePaused\([^)]*false\)/.test(body), false);
  assert.equal(/adminHeader\}|authorization\}/.test(body), false); // the token is never logged
});
