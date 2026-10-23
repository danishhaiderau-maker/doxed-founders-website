import { strict as assert } from 'node:assert';
import { test } from 'node:test';
import { dislocEntryOrderMode } from './signal-subscriber-execution.service';

test('LIVE-FIX-1510: DISLOC entries default to IOC at market', () => {
  assert.equal(dislocEntryOrderMode('dsl-1791842085-86', {}), 'IOC_MARKET');
  assert.equal(dislocEntryOrderMode('dsl-1', { DISLOC_ENTRY_ORDER_MODE: 'ioc_cap' }), 'IOC_CAP');
  assert.equal(dislocEntryOrderMode('dsl-1', { DISLOC_ENTRY_ORDER_MODE: 'LIMIT' }), 'LIMIT');
  assert.equal(dislocEntryOrderMode('dsl-1', { DISLOC_ENTRY_ORDER_MODE: 'bogus' }), 'IOC_MARKET');
});

test('LIVE-FIX-1510: non-DISLOC trades keep the legacy limit path', () => {
  assert.equal(dislocEntryOrderMode('gs1-ab2302016ee3', {}), null);
  assert.equal(dislocEntryOrderMode(undefined, {}), null);
});

// ---------------------------------------------------------------------------
// Behaviour: placeEntry with mocked prisma/activeTrading. The durable
// entry_submission_fence_v1 write must happen BEFORE the exchange write in both
// DISLOC modes, and the exchange write carries the same fixed client order id
// that the fence recorded.
// ---------------------------------------------------------------------------
import { createHash, createHmac } from 'node:crypto';
import { TradingAgentInstanceStatus } from '@prisma/client';
import { SignalSubscriberExecutionService } from './signal-subscriber-execution.service';

const DSL_TRADE_ID = 'dsl-1791842085-86';
const DSL_CYCLE_ID = 'cycle-disloc-fence';
const DSL_PARTICIPANT_ID = 'participant-disloc-fence';
const EXPECTED_CID =
  parseInt(createHash('sha256').update(`${DSL_CYCLE_ID}|${DSL_PARTICIPANT_ID}|${DSL_TRADE_ID}`).digest('hex').slice(0, 8), 16)
  & 0x7fffffff;

function signDislocApproval(tradeId: string, secret: string) {
  const approval = {
    schema: 'fly_live_copy_approval_v1', correlation_id: tradeId, trade_id: tradeId,
    event: 'ORDER_PLACED', research_lane: 'FAMILY_DISLOC_BFX_DISLOCATION_1M', relay_eligible: true,
    eligibility_source: 'OPERATOR', entry_allowed: true, output_on: true, tile_live_on: true,
    created_at_ts: Date.now() / 1000, max_margin_usd: 0.25, leverage: 100, order_type: 'LIMIT',
    hard_stop_bp: 35, exchange_stop_bp: 35, liquidation_bp: 50,
  };
  const signedBody = JSON.stringify(approval);
  const key = createHmac('sha256', secret).update('fly-live-copy-approval-v1').digest();
  return { ...approval, signed_body: signedBody, signature: createHmac('sha256', key).update(signedBody).digest('hex') };
}

type Call =
  | { kind: 'fence'; payload: Record<string, unknown> }
  | { kind: 'submitMarketEntry' | 'submitLimitOrder'; order: Record<string, unknown> };

async function runDislocEntry(mode: 'IOC_MARKET' | 'LIMIT'): Promise<Call[]> {
  const prevMode = process.env.DISLOC_ENTRY_ORDER_MODE;
  process.env.DISLOC_ENTRY_ORDER_MODE = mode;
  const calls: Call[] = [];
  try {
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.venueMarginEvidence = {
      value: {
        symbol: 'tBTCF0:USTF0', minQtyBtc: 0.00004, maxQtyBtc: 100,
        initialMarginFraction: 0.01, maintenanceMarginFraction: 0.005,
        priceSignificantDigits: 5, amountDecimals: 8,
        observedAt: '2026-09-29T23:21:00.000Z', source: 'BITFINEX_PUBLIC_FUTURES_CONFIG',
      },
      atMs: Date.now(),
    };
    const now = Date.now();
    const createdAt = new Date(now);
    const dashboardState = {
      relayExecutionMode: 'LIVE',
      relayPolicyVersion: 'two_lane_explicit_v6',
      realTradingConfirmedAt: new Date(now - 2_000).toISOString(),
      relayArmedAt: new Date(now - 1_000).toISOString(),
    };
    const instance = {
      id: 'instance-disloc-fence', userId: 'user-disloc-fence', agentId: 'agent-disloc-fence',
      status: TradingAgentInstanceStatus.ACTIVE, dashboardState,
    };
    const envelope = {
      schema: 'dcf-signal-intent/v1', cycleId: DSL_CYCLE_ID, signalId: DSL_TRADE_ID, trade_id: DSL_TRADE_ID,
      version: 'test', action: 'ENTER', direction: 'SHORT',
      entry: {
        type: 'LIMIT', mode: 'EXACT_LIMIT', offset_pct: 0, exact_limit_price: 63_614.55,
        exact_qty_btc: 25 / 63_614.55, reference: 'SHOWCASE_EXACT_LIMIT', ttl_sec: 1_800,
      },
      risk: { stop_loss_margin_pct: -18, take_profit_ladder: [], leverage_hint: 100, max_margin_usd: 0.25 },
      context: {
        regime: 'UNKNOWN', edge: 0, ai_win_prob: 0, entry_mode_source: 'test',
        entry_limit_policy: 'micro_sr_structural_limit_v1', research_venue: 'bitfinex', disclaimer: 'test',
        signed_showcase_event: true, showcase_event: 'ORDER_PLACED',
        showcase_event_at: new Date(now - 200).toISOString(),
        platform_received_at: new Date(now - 100).toISOString(),
        fly_live_approval: signDislocApproval(DSL_TRADE_ID, 'test-secret'),
      },
    };
    service.logger = { log() {}, warn() {}, error() {} };
    service.cycleAudit = { stage() {} };
    service.config = { get: (k: string) => (k === 'SHOWCASE_WEBHOOK_SECRET' ? 'test-secret' : undefined) };
    service.activeTrading = {
      getOpenPositionDetail: async () => null,
      getBestBidAsk: async () => null,
      submitMarketEntry: async (_creds: unknown, order: Record<string, unknown>) => {
        calls.push({ kind: 'submitMarketEntry', order });
        return 9001;
      },
      submitLimitOrder: async (_creds: unknown, order: Record<string, unknown>) => {
        calls.push({ kind: 'submitLimitOrder', order });
        return 9002;
      },
    };
    service.prisma = {
      $transaction: async (run: (tx: any) => Promise<unknown>) => run({
        $executeRawUnsafe: async () => [{ pg_advisory_xact_lock: null }],
        signalCycleParticipant: {
          create: async () => ({ id: DSL_PARTICIPANT_ID }),
          findMany: async () => [{
            id: DSL_PARTICIPANT_ID, cycleId: DSL_CYCLE_ID,
            cycle: { tradeId: DSL_TRADE_ID, createdAt, intentEnvelope: envelope },
          }],
        },
      }),
      signalCycleParticipant: {
        create: async () => ({ id: DSL_PARTICIPANT_ID }),
        delete: async () => ({}),
        findUnique: async () => null,
        findMany: async () => [],
      },
      tradingAgentInstance: {
        findUnique: async () => ({ status: TradingAgentInstanceStatus.ACTIVE, dashboardState }),
        update: async () => ({}),
      },
      signalCycle: { findUnique: async () => ({ createdAt }) },
      signalCycleEvent: {
        create: async ({ data }: { data: { eventType: string; payload: Record<string, unknown> } }) => {
          if (data.eventType === 'ENTRY_SUBMISSION_UNKNOWN' && data.payload?.schema === 'entry_submission_fence_v1') {
            calls.push({ kind: 'fence', payload: data.payload });
          }
          return {};
        },
      },
    };
    service.cycles = { recordHireExecutionEvent: async () => {} };
    service.applyLimitChase = async () => {};
    // Anything after the exchange ACK is out of scope here; tolerate downstream mock gaps.
    await service.placeEntry(
      instance.agentId, instance, DSL_CYCLE_ID, envelope,
      { apiKey: 'redacted', apiSecret: 'redacted' }, 0.25, DSL_TRADE_ID, 'bitfinex',
      { availableUsd: 100, markPrice: 63_620, exchangeBookProvenEmpty: true, provenEmptyAtMs: Date.now() },
    ).catch(() => undefined);
  } finally {
    if (prevMode === undefined) delete process.env.DISLOC_ENTRY_ORDER_MODE;
    else process.env.DISLOC_ENTRY_ORDER_MODE = prevMode;
  }
  return calls;
}

function assertFenceThenSubmit(calls: Call[], submitKind: 'submitMarketEntry' | 'submitLimitOrder') {
  const fenceIdx = calls.findIndex((c) => c.kind === 'fence' && c.payload.phase === 'SUBMITTING');
  const submitIdx = calls.findIndex((c) => c.kind === submitKind);
  const otherKind = submitKind === 'submitMarketEntry' ? 'submitLimitOrder' : 'submitMarketEntry';
  assert.ok(fenceIdx >= 0, 'SUBMITTING fence written');
  assert.ok(submitIdx >= 0, `${submitKind} called`);
  assert.ok(fenceIdx < submitIdx, `fence write precedes ${submitKind}`);
  assert.equal(calls.filter((c) => c.kind === submitKind).length, 1, `exactly one ${submitKind}`);
  assert.equal(calls.filter((c) => c.kind === otherKind).length, 0, `no ${otherKind}`);
  const fence = calls[fenceIdx] as Extract<Call, { kind: 'fence' }>;
  const submit = calls[submitIdx] as Extract<Call, { kind: 'submitMarketEntry' | 'submitLimitOrder' }>;
  assert.equal(fence.payload.client_order_id, EXPECTED_CID, 'fence records the deterministic CID');
  assert.equal(submit.order.clientOrderId, EXPECTED_CID, 'exchange write uses the fence CID');
  assert.equal(fence.payload.trade_id, DSL_TRADE_ID);
  assert.equal(fence.payload.participant_id, DSL_PARTICIPANT_ID);
  return EXPECTED_CID;
}

test('LIVE-FIX-1510 behaviour: IOC_MARKET writes the entry_submission fence before submitMarketEntry, same CID', async () => {
  const calls = await runDislocEntry('IOC_MARKET');
  assertFenceThenSubmit(calls, 'submitMarketEntry');
});

test('LIVE-FIX-1510 behaviour: LIMIT writes the entry_submission fence before submitLimitOrder, same CID', async () => {
  const calls = await runDislocEntry('LIMIT');
  assertFenceThenSubmit(calls, 'submitLimitOrder');
});

test('LIVE-FIX-1510 behaviour: both DISLOC modes use the same fixed client order id as the fence', async () => {
  const market = await runDislocEntry('IOC_MARKET');
  const limit = await runDislocEntry('LIMIT');
  const cid = (calls: Call[]) => (calls.find((c) => c.kind !== 'fence') as { order: Record<string, unknown> }).order.clientOrderId;
  const fenceCid = (calls: Call[]) => (calls.find((c) => c.kind === 'fence') as { payload: Record<string, unknown> }).payload.client_order_id;
  assert.equal(cid(market), cid(limit));
  assert.equal(fenceCid(market), fenceCid(limit));
  assert.equal(cid(market), fenceCid(market));
  assert.equal(typeof cid(market), 'number');
});
