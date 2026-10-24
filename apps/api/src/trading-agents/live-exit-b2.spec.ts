import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import { buildLiveCopyReports } from './live-copy-fly-reporter';
import {
  EXIT_FILL_UNREADABLE, EXIT_PRICE_NOT_HELPER_PRICED, liveOrderExitFill, terminalExitPriceFromHelper,
} from './live-row-countability';
import { SignalCyclesService } from './signal-cycles.service';

// HM R6 B2 / RK 10:26 (binding): no route stores a caller-supplied exit price; only the shared real-fill helper prices an
// EXIT, else exit_price null + EXIT_FILL_UNREADABLE; the reporter never forwards a non-fill exit.
const CALLER = 12_345.67;
const real = liveOrderExitFill({ kind: 'stop', expectedQty: 0.001, direction: 'LONG', entryFillPrice: 100_000, leverage: 10,
  reads: [{ orderId: 9, read: { price: 99_700, qty: 0.001, fillIds: [1], firstExecutedAtMs: 1, lastExecutedAtMs: 2 } }] });
const unread = liveOrderExitFill({ kind: 'stop', expectedQty: 0.001, reads: [{ orderId: 9, read: null }] });

test('B2 helper: only the shared real-fill helper prices an exit', () => {
  assert.equal(terminalExitPriceFromHelper('EXIT', real.fields).exitPrice, 99_700);
  const u = terminalExitPriceFromHelper('EXIT', unread.fields);
  assert.equal(u.exitPrice, null);
  assert.equal(u.payload.exit_fill_status, EXIT_FILL_UNREADABLE);
  assert.equal(u.payload.exit_fill_unreadable_reason, 'NO_FILL_READ');
  // caller price, forged stamp (vwap != price, no order ids), fill-looking fields: all discarded, number not stored
  for (const body of [{ exit_price: CALLER }, { ...real.fields, exit_price: CALLER },
    { exit_price: CALLER, exit_fill_status: 'REAL_FILL', exit_fill_vwap: CALLER },
    { exit_price: CALLER, exit_exchange_fill_price: CALLER, pnl_source: 'bitfinex_order_trades' }]) {
    const r = terminalExitPriceFromHelper('EXIT', body);
    assert.equal(r.exitPrice, null);
    assert.equal(r.payload.exit_price, null);
    assert.equal(r.payload.exit_fill_status, EXIT_FILL_UNREADABLE);
    assert.equal(r.payload.exit_fill_unreadable_reason, EXIT_PRICE_NOT_HELPER_PRICED);
    assert.ok(!JSON.stringify(r.payload).includes(String(CALLER)), JSON.stringify(r.payload));
  }
  // EXPIRED with no price claimed (a cancel): untouched; EXPIRED with a caller price: discarded + stamped
  assert.deepEqual(terminalExitPriceFromHelper('EXPIRED', { reason: 'CANCELLED' }), { exitPrice: null, payload: { reason: 'CANCELLED' } });
  assert.equal(terminalExitPriceFromHelper('EXPIRED', { exit_price: CALLER }).payload.exit_fill_status, EXIT_FILL_UNREADABLE);
});

function prismaMock(rec: { participant: any[]; events: any[]; claims: any[] }) {
  const prisma: any = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
    signalCycle: { findFirst: async () => ({ id: 'cycle-1', status: SignalCycleStatus.OPEN, intentEnvelope: {} }),
      update: async () => ({}), updateMany: async () => ({ count: 1 }) },
    signalCycleParticipant: {
      findUnique: async () => ({ id: 'participant-1', venue: 'bitfinex' }),
      update: async (a: any) => { rec.participant.push(a.data); return {}; },
      updateMany: async (a: any) => { rec.claims.push(a.data); return { count: 1 }; },
    },
    signalCycleEvent: { create: async (a: any) => { rec.events.push(a.data); return { id: 'e' }; } },
  };
  prisma.$transaction = async (w: (tx: any) => Promise<unknown>) => w(prisma);
  return prisma;
}

test('B2 route POST signal-cycles/:cycleId/events: a caller exit_price is never stored (EXIT, EXPIRED, other events)', async () => {
  const rec = { participant: [] as any[], events: [] as any[], claims: [] as any[] };
  const svc = new SignalCyclesService(prismaMock(rec), {} as never, {} as never, {} as never);
  const ctx = { agentId: 'agent-1', userId: 'u1' } as any;
  await svc.postEvent('slug', 'cycle-1', ctx, { event: 'EXIT', exit_price: CALLER, pnl_usd: 0, exit_fill_status: 'REAL_FILL',
    exit_fill_vwap: CALLER, exit_fill_order_ids: [9] } as any);
  await svc.postEvent('slug', 'cycle-1', ctx, { event: 'EXPIRED', exit_price: CALLER, pnl_usd: 0 } as any);
  await svc.postEvent('slug', 'cycle-1', ctx, { event: 'ORDER_PLACED', exit_price: CALLER } as any);
  assert.equal(rec.events.length, 3);
  for (const e of rec.events) assert.ok(!JSON.stringify(e.payload).includes(String(CALLER)), JSON.stringify(e.payload));
  assert.equal(rec.events[0].payload.exit_fill_status, EXIT_FILL_UNREADABLE);
  assert.equal(rec.events[1].payload.exit_fill_status, EXIT_FILL_UNREADABLE);
  const closes = rec.participant.filter((d) => 'exitPrice' in d);
  assert.equal(closes.length, 2);
  for (const d of closes) assert.equal(d.exitPrice, null);
});

const authorised = {
  terminal_authority_kind: 'EXCHANGE_OBSERVED_TERMINAL',
  terminal_authority_evidence: { schema: 'exchange_observed_terminal_v1', authenticated_exchange_read: true, submitted_close: false },
  final_reconciliation: { schema: 'relay_final_reconciliation_v1', position_reconciled: true, complete: true },
};

test('B2 route recordHireExecutionEvent: EXIT priced only by the helper stamp, else null + EXIT_FILL_UNREADABLE', async () => {
  const rec = { participant: [] as any[], events: [] as any[], claims: [] as any[] };
  const svc = new SignalCyclesService(prismaMock(rec), {} as never, {} as never, {} as never);
  await svc.recordHireExecutionEvent('u1', 'agent-1', 'cycle-1', 'EXIT', { ...authorised, exit_price: CALLER, pnl_usd: -1 });
  await svc.recordHireExecutionEvent('u1', 'agent-1', 'cycle-1', 'EXIT', { ...authorised, ...real.fields });
  await svc.recordHireExecutionEvent('u1', 'agent-1', 'cycle-1', 'EXPIRED', { exit_price: CALLER });
  assert.deepEqual(rec.claims.map((c) => c.exitPrice), [null, 99_700, null]);
  assert.equal(rec.events[0].payload.exit_fill_status, EXIT_FILL_UNREADABLE);
  assert.equal(rec.events[0].payload.exit_price, null);
  assert.equal(rec.events[1].payload.exit_fill_status, 'REAL_FILL');
  assert.equal(rec.events[2].payload.exit_fill_status, EXIT_FILL_UNREADABLE);
  for (const e of [rec.events[0], rec.events[2]]) assert.ok(!JSON.stringify(e.payload).includes(String(CALLER)));
});

test('B2 reporter: a non-fill exit is never forwarded as a price (caller price, forged fill fields)', () => {
  const T0 = 1_790_000_000_000;
  const env = { context: { fly_live_approval: { correlation_id: 'c', signal_at_ts: T0 / 1000, created_at_ts: T0 / 1000 } } };
  const ev = (id: string, eventType: string, ms: number, payload: Record<string, unknown>) =>
    ({ id, participantId: 'p1', eventType, payload, createdAt: new Date(T0 + ms), platformReceivedAt: null });
  for (const pl of [{ exit_price: CALLER }, { exit_price: CALLER, exit_exchange_fill_price: CALLER },
    { exit_price: CALLER, pnl_source: 'bitfinex_order_trades', exit_reason: 'EXCHANGE_STOP', exchange_exit_fill_ids: ['1'], exchange_exit_filled_qty: 0.001 }]) {
    const rs = buildLiveCopyReports({ cycle: { id: 'c1', tradeId: 'c', intentEnvelope: env }, correlationId: 'c', lane: 'L',
      events: [ev('a', 'FILLED', 1, { fill_price: 100_000, fill_price_source: 'exchange_trades', qty: 0.001 }), ev('b', 'EXIT', 9, pl)],
      participants: [{ id: 'p1', userId: 'u1' }], accountFor: () => 'a', nowMs: T0 + 10_000 });
    const close = (rs.find((r) => r.type === 'POSITION_CLOSED') as any).close;
    assert.equal(close.price, null, JSON.stringify(pl));
    assert.equal(close.fill_at_ts, null);
  }
});

test('B2 source: no route reads a caller exit_price any more', () => {
  const svc = readFileSync(join(__dirname, 'signal-cycles.service.ts'), 'utf8');
  const ctl = readFileSync(join(__dirname, 'signal-cycles.controller.ts'), 'utf8');
  assert.doesNotMatch(svc, /body\.exit_price/);
  assert.doesNotMatch(svc, /exit_price\?: number/);
  assert.doesNotMatch(ctl, /exit_price/);
  assert.match(svc, /terminalExitPriceFromHelper\(event, body\)/);
  assert.match(svc, /exitPrice: null,\s+\/\/ HM R6 B2/);
});
