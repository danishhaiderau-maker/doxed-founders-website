import assert from 'node:assert/strict';
import { createHmac } from 'node:crypto';
import { test } from 'node:test';
import { evaluatePreTradeLiquidationSafety } from './bitfinex-pre-trade-safety';
import { approvalBackedStopLossMarginPct } from './live-copy-approval';
import {
  resolveEffectiveStopLossMarginPct,
  SignalSubscriberExecutionService,
} from './signal-subscriber-execution.service';
import { isPrismaWriteConflict, retryOnPrismaWriteConflict } from './trading-agent-instances.service';

const SECRET = 'stop-policy-secret';
const VENUE = { initialMarginFraction: 0.01, maintenanceMarginFraction: 0.005 };

function signTestFlyApproval(tradeId: string, secret: string, over: Record<string, unknown> = {}) {
  const approval = {
    schema: 'fly_live_copy_approval_v1', correlation_id: tradeId, trade_id: tradeId,
    event: 'ORDER_PLACED', research_lane: 'FAMILY_GS01_XV_PREMIUM_ATR_TP', relay_eligible: true,
    eligibility_source: 'OPERATOR', entry_allowed: true, output_on: true, tile_live_on: true,
    created_at_ts: Date.now() / 1000, max_margin_usd: 0.25, leverage: 100, order_type: 'LIMIT',
    hard_stop_bp: 35, exchange_stop_bp: 35, liquidation_bp: 50, ...over,
  };
  const signedBody = JSON.stringify(approval);
  const key = createHmac('sha256', secret).update('fly-live-copy-approval-v1').digest();
  return { ...approval, signed_body: signedBody, signature: createHmac('sha256', key).update(signedBody).digest('hex') };
}

function intentWith(approval: unknown, signalId = 'gs1-stop-1') {
  return { signalId, risk: { stop_loss_margin_pct: -60 }, context: { fly_live_approval: approval } };
}

test('approval path: exchange stop = signed backup stop (35 bp -> -35% margin at 100x)', () => {
  const approval = signTestFlyApproval('gs1-stop-1', SECRET, { exchange_stop_bp: 35 });
  assert.equal(approvalBackedStopLossMarginPct(intentWith(approval), SECRET, 100), -35);
});

test('approval path refuses forged, wrong-trade, leverage-drift and over-cap approvals', () => {
  const good = signTestFlyApproval('gs1-stop-1', SECRET, { exchange_stop_bp: 35 });
  assert.equal(approvalBackedStopLossMarginPct(intentWith(good), 'wrong-secret', 100), null);
  assert.equal(approvalBackedStopLossMarginPct(intentWith(good, 'gs1-other'), SECRET, 100), null);
  assert.equal(approvalBackedStopLossMarginPct(intentWith(good), SECRET, 50), null);
  const overCap = signTestFlyApproval('gs1-stop-1', SECRET, { exchange_stop_bp: 40 });
  assert.equal(approvalBackedStopLossMarginPct(intentWith(overCap), SECRET, 100), null);
  assert.equal(approvalBackedStopLossMarginPct(intentWith(null), SECRET, 100), null);
});

test('legacy path keeps the frozen -13% clamp', () => {
  assert.equal(approvalBackedStopLossMarginPct(intentWith(null), SECRET, 100), null);
  assert.equal(resolveEffectiveStopLossMarginPct(-60, { mirrorMode: true }), -13);
});

test('approval gate: 35 bp stop passes liq-15 buffer, 3x multiple would refuse it', () => {
  const legacy = evaluatePreTradeLiquidationSafety({
    leverage: 100, orderLeverage: 100, stopLossMarginPct: -35, venue: VENUE,
  });
  assert.equal(!legacy.ok && legacy.reason, 'LIQUIDATION_TOO_CLOSE_TO_STOP');
  const approved = evaluatePreTradeLiquidationSafety({
    leverage: 100, orderLeverage: 100, stopLossMarginPct: -35, venue: VENUE, minLiquidationBufferBp: 15,
  });
  assert.equal(approved.ok, true);
  const tooClose = evaluatePreTradeLiquidationSafety({
    leverage: 100, orderLeverage: 100, stopLossMarginPct: -36, venue: VENUE, minLiquidationBufferBp: 15,
  });
  assert.equal(!tooClose.ok && tooClose.reason, 'LIQUIDATION_TOO_CLOSE_TO_STOP');
  // Buffer can never be weakened below 15 bp.
  const weakened = evaluatePreTradeLiquidationSafety({
    leverage: 100, orderLeverage: 100, stopLossMarginPct: -40, venue: VENUE, minLiquidationBufferBp: 5,
  });
  assert.equal(weakened.ok, false);
  // Legacy 13 bp path unchanged.
  assert.equal(evaluatePreTradeLiquidationSafety({
    leverage: 100, orderLeverage: 100, stopLossMarginPct: -13, venue: VENUE,
  }).ok, true);
});

test('flat-audit refresh retries only Prisma P2034, bounded at 3 attempts', async () => {
  const p2034 = Object.assign(new Error('Transaction failed due to a write conflict or a deadlock'), { code: 'P2034' });
  assert.equal(isPrismaWriteConflict(p2034), true);
  assert.equal(isPrismaWriteConflict(new Error('P2002 unique')), false);
  let calls = 0;
  const ok = await retryOnPrismaWriteConflict(async () => {
    calls += 1;
    if (calls < 3) throw p2034;
    return 'done';
  }, 3, async () => {});
  assert.equal(ok, 'done');
  assert.equal(calls, 3);
  calls = 0;
  await assert.rejects(retryOnPrismaWriteConflict(async () => { calls += 1; throw p2034; }, 3, async () => {}));
  assert.equal(calls, 3);
  calls = 0;
  await assert.rejects(retryOnPrismaWriteConflict(async () => { calls += 1; throw new Error('other'); }, 3, async () => {}));
  assert.equal(calls, 1);
});

test('executor: every stop site resolves the approval backup for approved copies, legacy clamp otherwise', () => {
  const proto = SignalSubscriberExecutionService.prototype as unknown as Record<string, Function>;
  const fake = {
    config: { get: (k: string) => (k === 'SHOWCASE_WEBHOOK_SECRET' ? SECRET : undefined) },
    approvalBackedStopPct: proto.approvalBackedStopPct,
  };
  const resolve = (intent: unknown, opts?: Record<string, unknown>) =>
    proto.liveCopyStopLossMarginPct.call(fake, intent, opts) as number;
  const approved = intentWith(signTestFlyApproval('gs1-stop-1', SECRET, { exchange_stop_bp: 35 }));
  assert.equal(resolve(approved, { mirrorMode: true }), -35);
  // Simulation never uses the exchange backup.
  assert.equal(resolve(approved, { mirrorMode: true, simActive: true }), resolveEffectiveStopLossMarginPct(-60, { mirrorMode: true, simActive: true }));
  assert.equal(resolve(intentWith(null), { mirrorMode: true }), -13);
  assert.equal(resolve(intentWith(signTestFlyApproval('gs1-stop-1', 'forged'))), -13);
});
