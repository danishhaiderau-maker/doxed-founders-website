/**
 * HM R6 nit 5 (RK 10:26 binding): at boot a PENDING_ENTRY row's resting entry order is cancelled on Bitfinex and read
 * back cancelled, its real fill qty read; any fill -> force flat reduce-only + cancel stops; relay entries only on a
 * FLAT_NO_STOPS read; any failed read / cancel -> blocked + RED; the order and any fill stamped non-countable.
 */
import assert from 'node:assert/strict';
import { test } from 'node:test';
import { liveBootEntryBlocked, liveBootGateSetPhase, liveBootReconcileStampFor, SignalSubscriberExecutionService } from './signal-subscriber-execution.service';
import { BOOT_PENDING_ENTRY_NON_COUNTABLE } from './live-boot-pending-cancel';

type Opt = { cancelThrows?: boolean; tradesThrow?: boolean; filled?: number; flattenVerdict?: string };
function harness(o: Opt) {
  const s = Object.create(SignalSubscriberExecutionService.prototype) as SignalSubscriberExecutionService;
  const calls: string[] = [];
  const logs: string[] = [];
  const state = { pos: 0, active: [77] as number[], row: { id: 'q', status: 'PENDING_ENTRY', cycleId: 'c1' } };
  const filled = o.filled ?? 0;
  Object.assign(s as unknown as Record<string, unknown>, {
    executorDestroyed: true,
    logger: { error: (m: string) => logs.push(`E ${m}`), warn: (m: string) => logs.push(`W ${m}`), log: () => undefined },
    swallowedError: () => () => undefined,
    exchanges: { getUserCredentials: async () => ({ apiKey: 'k', apiSecret: 's' }) },
    bitfinex: {
      getOpenPositionDetail: async () => (state.pos ? { amount: state.pos } : null),
      listActiveOrders: async () => state.active.map((id) => ({ id })),
      cancelOrder: async (_c: unknown, id: number) => {
        calls.push(`cancel:${id}`);
        if (o.cancelThrows) throw new Error('cancel rejected');
        state.active = state.active.filter((x) => x !== id);
        if (filled > 0) { state.pos = filled; state.active.push(501); }   // partial fill + its stop resting
      },
      fetchOrderHistoryEvidence: async (_c: unknown, id: number) => ({ id, status: filled > 0 ? 'CANCELED was: PARTIALLY FILLED' : 'CANCELED', terminal: true, filledQty: filled }),
      fetchOrderTrades: async () => { calls.push('trades'); if (o.tradesThrow) throw new Error('trades 500'); return filled > 0 ? [{ id: 1, execAmount: filled, execPrice: 60000 }] : []; },
    },
    forceFlatDisarmAccount: async (_u: string, slug: string, reason: string) => {
      calls.push(`force-flat:${slug}:${reason}`);
      state.pos = 0; state.active = [];
      const verdict = o.flattenVerdict ?? 'FLAT_NO_STOPS';
      return { verdict, errors: [] };
    },
    cycles: {
      recordHireExecutionEvent: async (_u: string, agentId: string, cycleId: string, ev: string, body: Record<string, unknown>) => {
        calls.push(`stamp:${ev}:${agentId}:${cycleId}:${JSON.stringify(body)}`);
        state.row.status = 'EXPIRED';
        return { ok: true, participantId: 'q' };
      },
    },
    loadExecutionMeta: async () => ({ direction: 'LONG', qty: 0.001, bitfinexOrderId: 77 }),
    participantRecordedStopOrderIds: async () => [],
    prisma: {
      tradingAgentInstance: { findMany: async () => [{ userId: 'u1' }] },
      signalCycleParticipant: { findMany: async () => (state.row.status === 'PENDING_ENTRY' ? [state.row] : []) },
      signalCycle: { findUnique: async () => ({ agentId: 'a1' }) },
      tradingAgent: { findUnique: async () => ({ id: 'a1', slug: 'btc-agent' }) },
      liveEntryFence: { deleteMany: async () => { calls.push('fence-clear'); } },
      $executeRaw: async () => 1,
    },
  });
  const run = async () => {
    liveBootGateSetPhase('PENDING');
    await (s as unknown as { runLiveBootReconcile: (n: number) => Promise<void> }).runLiveBootReconcile(1);
    return liveBootReconcileStampFor('u1') as Record<string, unknown>;
  };
  return { calls, logs, run, state };
}

test('nit 5: cancel OK + unfilled -> cancelled, read back, stamped non-countable, FLAT_NO_STOPS, entries allowed', async () => {
  const h = harness({});
  const st = await h.run();
  assert.ok(h.calls.includes('cancel:77'));
  assert.ok(h.calls.includes('trades'));
  const stamp = h.calls.find((c) => c.startsWith('stamp:EXPIRED:a1:c1:'))!;
  assert.ok(stamp.includes(`"live_non_countable":true`) && stamp.includes(BOOT_PENDING_ENTRY_NON_COUNTABLE) && stamp.includes('"entry_filled_qty":0'));
  assert.ok(!h.calls.some((c) => c.startsWith('force-flat')));
  assert.deepEqual([st.verdict, st.allowed, st.red], ['FLAT_NO_STOPS', true, false]);
  assert.equal(liveBootEntryBlocked('u1'), false);
  liveBootGateSetPhase('NOT_RUN');
});

test('nit 5: partial fill -> stamped non-countable with the fill qty, force-flattened reduce-only + stops cancelled, FLAT_NO_STOPS', async () => {
  const h = harness({ filled: 0.0004 });
  const st = await h.run();
  const stamp = h.calls.find((c) => c.startsWith('stamp:EXPIRED'))!;
  assert.ok(stamp.includes('"entry_filled_qty":0.0004') && stamp.includes('"flatten_required":true') && stamp.includes('"live_non_countable":true'));
  assert.ok(h.calls.includes('force-flat:btc-agent:boot-pending-entry-fill'));
  assert.equal(h.state.pos, 0);
  assert.deepEqual(h.state.active, []);
  assert.deepEqual([st.verdict, st.allowed, st.red], ['FLAT_NO_STOPS', true, false]);
  // a flatten that does not read back FLAT_NO_STOPS blocks + RED
  const bad = harness({ filled: 0.0004, flattenVerdict: 'NOT_FLAT' });
  const sb = await bad.run();
  assert.deepEqual([sb.verdict, sb.allowed, sb.red], ['PENDING_ENTRY_CANCEL_FAILED', false, true]);
  assert.equal(liveBootEntryBlocked('u1'), true);
  liveBootGateSetPhase('NOT_RUN');
});

test('nit 5: cancel failure -> blocked + RED, nothing stamped', async () => {
  const h = harness({ cancelThrows: true });
  const st = await h.run();
  assert.deepEqual([st.verdict, st.allowed, st.red], ['PENDING_ENTRY_CANCEL_FAILED', false, true]);
  assert.match(String(st.detail), /CANCEL_FAILED/);
  assert.equal(liveBootEntryBlocked('u1'), true);
  assert.ok(!h.calls.some((c) => c.startsWith('stamp:')));
  assert.ok(h.logs.some((l) => l.startsWith('E ') && l.includes('RED')));
  liveBootGateSetPhase('NOT_RUN');
});

test('nit 5: unreadable fill -> blocked + RED (and flattened for safety), never stamped as unfilled', async () => {
  const h = harness({ tradesThrow: true });
  const st = await h.run();
  assert.deepEqual([st.verdict, st.allowed, st.red], ['PENDING_ENTRY_CANCEL_FAILED', false, true]);
  assert.match(String(st.detail), /FILL_UNREADABLE/);
  assert.equal(liveBootEntryBlocked('u1'), true);
  assert.ok(!h.calls.some((c) => c.startsWith('stamp:')));
  assert.ok(h.calls.some((c) => c.startsWith('force-flat')));
  liveBootGateSetPhase('NOT_RUN');
});
