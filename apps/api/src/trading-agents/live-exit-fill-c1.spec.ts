import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import { buildLiveCopyReports } from './live-copy-fly-reporter';
import {
  EXIT_FILL_RECONCILED_EVENT,
  EXIT_FILL_UNREADABLE,
  liveExitFillEvidenceFromEvents,
  liveRowExitFillCountability,
} from './live-row-countability';
import { SignalSubscriberExecutionService } from './signal-subscriber-execution.service';

// HM R5 C1 + RK 09:10 (binding), service level: every live exit path reads the exiting order's OWN Bitfinex trades by order
// id for the exact lot qty through the ONE shared helper (readLiveExitOrderFill). Read OK => the real fill (VWAP of partial
// fills, fill time stamped), countable. Genuine read failure => exit_price null, EXIT_FILL_UNREADABLE, entries fenced,
// non-countable; never the mark. A later successful read (clearUnreadableExitFills) re-stamps and lifts the fence.

const PID = 'p-c1';
const USER = 'user-c1';
const AGENT = 'agent-c1';
const CYCLE = 'cycle-c1';
const STOP = 7001;
const QTY = 0.01;
const ENTRY = 100_000;
const MARK = 123_456; // a mark that must NEVER become the exit price
const FILL_MS = 1_761_300_000_000;
const creds = { apiKey: 'k', apiSecret: 's' };

type Ev = { id: string; participantId: string; cycleId: string; eventType: string; payload: Record<string, any>; createdAt: Date };
type Mode = 'ok' | 'throw' | 'partials';

function harness() {
  const svc = Object.create(SignalSubscriberExecutionService.prototype) as any;
  const st = {
    mode: 'ok' as Mode,
    reads: [] as number[],
    events: [] as Ev[],
    fences: new Map<string, { id: string; userId: string; reason: string }>(),
    participantUpdates: [] as Array<Record<string, unknown>>,
  };
  let n = 0;
  const push = (cycleId: string, eventType: string, payload: Record<string, any>) => {
    n += 1;
    st.events.push({ id: `ev-${n}`, participantId: PID, cycleId, eventType, payload, createdAt: new Date(FILL_MS + n) });
    return `ev-${n}`;
  };
  push(CYCLE, 'FILLED', { fill_price: ENTRY, fill_price_source: 'exchange_trades' }); // nit 6: real entry fill
  const trades = (orderId: number) => {
    if (orderId !== STOP) return [];
    if (st.mode === 'partials') {
      return [
        { id: 11, execAmount: -0.006, execPrice: 99_000, mtsCreate: FILL_MS + 10, fee: -0.1, feeCurrency: 'USD' },
        { id: 12, execAmount: -0.004, execPrice: 98_500, mtsCreate: FILL_MS + 30, fee: -0.1, feeCurrency: 'USD' },
      ];
    }
    return [{ id: 10, execAmount: -QTY, execPrice: 99_000, mtsCreate: FILL_MS + 10, fee: -0.2, feeCurrency: 'USD' }];
  };
  Object.assign(svc, {
    logger: { log() {}, warn() {}, error() {}, debug() {} },
    swallowedError: () => () => undefined,
    exitingLots: new Set<string>(),
    positionRuntime: new Map(),
    stopManagerCircuitOpen: new Map(),
    showcaseVanishedMisses: new Map(),
    showcasePositionAbsentMisses: new Map(),
    showcasePositionAbsentSince: new Map(),
    showcaseFlatOpenSince: new Map(),
    exitFillClearAtMs: new Map<string, number>(),
    bitfinex: {
      fetchOrderTrades: async (_c: unknown, orderId: number) => {
        st.reads.push(orderId);
        if (st.mode === 'throw') throw new Error('ETIMEDOUT order trades');
        return trades(orderId);
      },
    },
    activeTrading: {
      getMarkPrice: async () => MARK,
      findOrder: async () => null,
    },
    cycles: {
      recordHireExecutionEvent: async (_u: string, _a: string, cycleId: string, type: string, body: Record<string, any>) => {
        const eventId = push(cycleId, type, body);
        return { ok: true, participantId: PID, eventId };
      },
    },
    prisma: {
      signalCycleEvent: {
        findFirst: async ({ where }: any) =>
          [...st.events].reverse().find((e) => e.participantId === where.participantId && e.eventType === where.eventType) ?? null,
        findMany: async ({ where }: any) =>
          st.events.filter((e) => e.participantId === where.participantId && where.eventType.in.includes(e.eventType)),
      },
      liveEntryFence: {
        upsert: async ({ where, create }: any) => { if (!st.fences.has(where.id)) st.fences.set(where.id, create); return create; },
        findMany: async ({ where }: any) =>
          [...st.fences.values()].filter((f) => f.userId === where.userId && f.id.startsWith(where.id.startsWith)),
        deleteMany: async ({ where }: any) => ({ count: st.fences.delete(where.id) ? 1 : 0 }),
      },
      signalCycleParticipant: {
        findUnique: async () => ({ cycle: { agentId: AGENT } }),
        updateMany: async ({ data }: any) => { st.participantUpdates.push(data); return { count: 1 }; },
      },
      tradingAgentInstance: { updateMany: async () => ({ count: 1 }) },
    },
    authenticatedExchangeObservedTerminalProvenance: async () => ({}),
    pauseUserRelayForPositionMismatch: async () => undefined,
    cancelManagedOrderGone: async () => ({ gone: true, attempts: 1 }),
    hasParticipantExited: async () => false,
  });
  const exits = () => st.events.filter((e) => e.eventType === 'EXIT');
  const countability = () => liveRowExitFillCountability(liveExitFillEvidenceFromEvents(st.events));
  return { svc, st, exits, countability };
}

const meta = { qty: QTY, direction: 'LONG', stopOrderId: STOP, bitfinexOrderId: 6001 } as any;

// --- the four paths (each with read OK / read failure / later clearing read) ------------------------------------------
const PATHS: Record<string, (h: ReturnType<typeof harness>) => Promise<unknown>> = {
  reconcileAttributedLotClose: async (h) => {
    // ledger 0.02 (two lots), exchange 0.01: this lot's stop took exactly its qty
    Object.assign(h.svc, {
      readOpenPositionDetailWithRetry: async () => ({ amount: 0.01, basePrice: ENTRY }),
      loadExecutionMeta: async () => ({ qty: QTY }),
    });
    h.svc.prisma.signalCycleParticipant.findMany = async () => [{ id: PID }, { id: 'p-other' }];
    return h.svc.reconcileAttributedLotClose(AGENT, USER, { id: CYCLE }, { id: PID, fillPrice: { toNumber: () => ENTRY } }, meta, creds);
  },
  reconcileManualClose: async (h) => {
    h.svc.readOpenPositionDetailWithRetry = async () => null;
    return h.svc.reconcileManualClose(
      AGENT, USER, { id: CYCLE, status: SignalCycleStatus.OPEN, showcasePnlUsd: null },
      { id: PID, fillPrice: { toNumber: () => ENTRY }, status: SignalCycleStatus.OPEN }, meta, creds,
    );
  },
  reconcileImmediateExchangeFlat: async (h) => {
    Object.assign(h.svc, {
      readOpenPositionDetailWithRetry: async () => null,
      loadExecutionMeta: async () => meta,
    });
    h.svc.prisma.signalCycleParticipant.findMany = async () => [{ id: PID, cycleId: CYCLE, cycle: { id: CYCLE } }];
    return h.svc.reconcileImmediateExchangeFlat(AGENT, { userId: USER }, creds);
  },
  'closeVirtualLot (already flat)': async (h) => {
    const claim = { ownerToken: 't', requestId: 'r', phase: 'CLAIMED', recovered: false,
      authority: { kind: 'AUTHENTICATED_EMERGENCY_CLOSE', canonicalTradeId: 'cont-c1', lifecycleGeneration: 1, evidence: {} } };
    Object.assign(h.svc, {
      acquireTerminalCloseClaim: async () => claim,
      cancelLinkedPendingLimits: async () => undefined,
      terminalCloseClaimStillValid: async () => true,
      closeParticipantPositionToLedgerTarget: async () => ({
        ok: true, currentAmount: 0, targetAmount: 0, closeQty: 0, finalAccountFlatten: true, confirmedExchangeAmount: 0,
        remainingManagedOrderIds: [], accountWideActiveOrderIds: [], accountWideManagedOrderIds: [], orphanOrderIds: [], foreignOrderIds: [],
      }),
      resolveNormalCloseExchangeEvidence: async () => ({}),
    });
    return h.svc.closeVirtualLot(AGENT, USER, CYCLE, PID, meta, creds, {
      reason: 'AUTHENTICATED_EMERGENCY_CLOSE', mark: MARK, fillPrice: ENTRY, leverage: 1, peakMarginPct: 0,
      unrealMarginPct: 0, stopLossMarginPct: 1, terminalAuthority: claim.authority,
    });
  },
};

for (const [name, run] of Object.entries(PATHS)) {
  test(`HM R5 C1 ${name}: read OK => the stop's own fill by order id is the exit price, countable, no fence`, async () => {
    const h = harness();
    assert.equal(await run(h), true);
    assert.ok(h.st.reads.includes(STOP), 'read the stop order by its id');
    const [exit] = h.exits();
    assert.ok(exit, 'EXIT written');
    assert.equal(h.exits().length, 1);
    assert.equal(exit.payload.exit_price, 99_000);
    assert.notEqual(exit.payload.exit_price, MARK);
    assert.equal(exit.payload.exit_fill_status, 'REAL_FILL');
    assert.equal(exit.payload.exit_price_source, 'stop_fill');
    assert.equal(exit.payload.exit_fill_at, new Date(FILL_MS + 10).toISOString());
    assert.equal(exit.payload.pnl_usd, -10); // (99_000 - 100_000) x 0.01 LONG, no fee/funding term
    assert.deepEqual(h.countability(), { nonCountable: false, nonCountableReason: null });
    assert.equal(h.st.fences.size, 0);
  });

  test(`HM R5 C1 ${name}: genuine read failure => exit_price null, EXIT_FILL_UNREADABLE, entries fenced, non-countable; a later read clears`, async () => {
    const h = harness();
    h.st.mode = 'throw';
    assert.equal(await run(h), true);
    const [exit] = h.exits();
    assert.ok(exit, 'EXIT written');
    assert.equal(exit.payload.exit_price, null); // RK 09:10: NULL is stored, never the mark
    assert.equal(exit.payload.pnl_usd, null);
    assert.equal(exit.payload.exit_fill_status, EXIT_FILL_UNREADABLE);
    assert.deepEqual(exit.payload.exit_fill_pending_order_ids, [STOP]);
    assert.ok(!JSON.stringify(exit.payload).includes(String(MARK)) || exit.payload.mark_price_audit === MARK,
      'the mark appears at most as the audit field');
    assert.equal(h.countability().nonCountable, true);
    assert.match(String(h.countability().nonCountableReason), /^EXIT_FILL_UNREADABLE:/);
    assert.deepEqual([...h.st.fences.keys()], [`exit-fill-unreadable:${PID}`]); // every fence row blocks entries
    // still unreadable on the next sweep => nothing changes
    assert.equal(await h.svc.clearUnreadableExitFills(USER, creds, FILL_MS + 120_000), 0);
    assert.equal(h.st.fences.size, 1);
    assert.equal(h.st.events.filter((e) => e.eventType === EXIT_FILL_RECONCILED_EVENT).length, 0);
    // the clearing read: same helper, same order id, exact qty => re-stamp, participant corrected, fence lifted
    h.st.mode = 'ok';
    assert.equal(await h.svc.clearUnreadableExitFills(USER, creds, FILL_MS + 240_000), 1);
    const rec = h.st.events.filter((e) => e.eventType === EXIT_FILL_RECONCILED_EVENT);
    assert.equal(rec.length, 1);
    assert.equal(rec[0].payload.exit_price, 99_000);
    assert.equal(rec[0].payload.exit_fill_status, 'REAL_FILL');
    assert.equal(rec[0].payload.exit_fill_at, new Date(FILL_MS + 10).toISOString());
    assert.equal(rec[0].payload.reconciled_from_event_id, exit.id);
    assert.equal(rec[0].payload.reconciled_from_status, EXIT_FILL_UNREADABLE);
    assert.deepEqual(h.st.participantUpdates, [{ exitPrice: 99_000, pnlUsd: -10, pnlMarginPct: (h.st.participantUpdates[0] as any).pnlMarginPct }]);
    assert.equal(h.st.fences.size, 0);
    assert.deepEqual(h.countability(), { nonCountable: false, nonCountableReason: null });
  });
}

test('HM R5 C1 / RK 09:10: multi-partial stop fills => the exact-qty VWAP, first/last fill times stamped', async () => {
  const h = harness();
  h.st.mode = 'partials';
  assert.equal(await PATHS.reconcileManualClose(h), true);
  const [exit] = h.exits();
  const vwap = (0.006 * 99_000 + 0.004 * 98_500) / 0.01; // 98_800
  assert.ok(Math.abs(exit.payload.exit_price - vwap) < 1e-6);
  assert.ok(Math.abs(exit.payload.exit_fill_vwap - vwap) < 1e-6);
  assert.equal(exit.payload.exchange_exit_filled_qty, 0.01);
  assert.deepEqual(exit.payload.exchange_exit_fill_ids, ['11', '12']);
  assert.equal(exit.payload.exchange_exit_fill_first_at, new Date(FILL_MS + 10).toISOString());
  assert.equal(exit.payload.exit_fill_at, new Date(FILL_MS + 30).toISOString());
  assert.equal(exit.payload.pnl_usd, -12);
  assert.equal(h.st.fences.size, 0);
});

test('HM R5 C1 / RK 09:10: a fill for a different qty is not the lot exit => unreadable (null), fenced', async () => {
  const h = harness();
  const partialMeta = { ...meta, qty: 0.02 }; // the stop filled 0.01 but the lot is 0.02
  h.svc.readOpenPositionDetailWithRetry = async () => null;
  assert.equal(await h.svc.reconcileManualClose(AGENT, USER, { id: CYCLE, status: SignalCycleStatus.OPEN, showcasePnlUsd: null },
    { id: PID, fillPrice: null, status: SignalCycleStatus.OPEN }, partialMeta, creds), true);
  const [exit] = h.exits();
  assert.equal(exit.payload.exit_price, null);
  assert.match(String(exit.payload.exit_fill_unreadable_reason), /^QTY_MISMATCH/);
  assert.equal(h.st.fences.size, 1);
});

test('HM R5 C1 / RK 09:10: D3 recomputes after the clearing read (reporter re-sends POSITION_CLOSED with the real fill)', async () => {
  const h = harness();
  h.st.mode = 'throw';
  await PATHS.reconcileAttributedLotClose(h);
  h.st.mode = 'partials';
  assert.equal(await h.svc.clearUnreadableExitFills(USER, creds, FILL_MS + 240_000), 1);
  const reports = buildLiveCopyReports({
    cycle: { id: CYCLE, tradeId: 'cont-c1', intentEnvelope: {} },
    correlationId: 'corr', lane: 'gs1', participants: [{ id: PID, userId: USER }], accountFor: () => 'acct', nowMs: FILL_MS + 300_000,
    events: h.st.events.map((e) => ({ ...e, platformReceivedAt: null })),
  });
  const closes = reports.filter((r) => r.type === 'POSITION_CLOSED') as any[];
  assert.equal(closes.length, 2);
  assert.equal(closes[0].close.price, null); // first report: UNREADABLE (AMBER entry block on Fly), never a $0 loss / mark
  assert.equal(closes[0].close.reconciled, false);
  assert.ok(Math.abs(closes[1].close.price - 98_800) < 1e-6); // the latest close report: the real VWAP => D3 recomputes
  assert.equal(closes[1].close.reconciled, true);
  assert.notEqual(closes[0].report_id, closes[1].report_id);
});

test('HM R5 C1 / RK 09:10: source scan: ONE live EXIT writer (recordLiveExitEvent) and every exit path uses the shared helper', () => {
  const src = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const writers = src.match(/recordHireExecutionEvent\(\s*[^,()]+,\s*[^,()]+,\s*[^,()]+,\s*'EXIT'/g) ?? [];
  assert.equal(writers.length, 1);
  assert.match(src, /const res = await this\.cycles\.recordHireExecutionEvent\(userId, agentId, cycleId, 'EXIT', safe\);/);
  const body = (name: string) => {
    const at = src.indexOf(`private async ${name}(`);
    assert.ok(at >= 0, name);
    return src.slice(at, src.indexOf('\n  private ', at + 20));
  };
  for (const name of [
    'reconcileAttributedLotClose', 'reconcileManualClose', 'reconcileImmediateExchangeFlat', 'closeVirtualLot',
    'reconcileExitAfterBitfinexClose', 'reconcilePendingPartialFillStop', 'executeShowcaseMirrorCloseOwned',
  ]) {
    const b = body(name);
    assert.match(b, /readLiveExitOrderFill\(/, `${name}: shared exit-fill helper`);
    assert.doesNotMatch(b, /exit_price:\s*(mark|opts\.mark|exitPrice \|\| fillPrice|markPrice)/, `${name}: no mark as exit price`);
  }
  // the operator emergency flatten already holds the stop's own trades: same pure exact-qty helper, no mark
  const at = src.indexOf('  async emergencyFlattenOpenCopyLots(');
  const em = src.slice(at, src.indexOf('\n  async ', at + 20));
  assert.match(em, /liveOrderExitFill\(\{\s*kind: 'stop'/);
  assert.match(em, /exit_price: incidentFill\.ok \? incidentFill\.price : null/);
  // every recordLiveExitEvent caller is one of the paths above
  const callers = [...src.matchAll(/recordLiveExitEvent\(/g)].map((m) => {
    const head = src.lastIndexOf('\n  private async ', m.index) > src.lastIndexOf('\n  async ', m.index)
      ? src.lastIndexOf('\n  private async ', m.index) : src.lastIndexOf('\n  async ', m.index);
    return src.slice(head, head + 90).split('(')[0].trim().split(' ').pop();
  });
  assert.deepEqual([...new Set(callers)].sort(), [
    'closeVirtualLot', 'emergencyFlattenOpenCopyLots', 'executeShowcaseMirrorCloseOwned', 'reconcileAttributedLotClose',
    'reconcileExitAfterBitfinexClose', 'reconcileImmediateExchangeFlat', 'reconcileManualClose',
    'reconcilePendingPartialFillStop', 'recordLiveExitEvent',
  ]);
});
