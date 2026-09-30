import assert from 'node:assert/strict';
import test from 'node:test';
import { RELAY_ELIGIBLE_TILE_ID_PREFIXES, RELAY_ELIGIBLE_TILE_LANES } from '@dcf/utils';
import { SignalSubscriberExecutionService, isCycleFreshForRelayArm } from './signal-subscriber-execution.service';

const armedState = (arm: number) => ({
  relayExecutionMode: 'LIVE',
  relayPolicyVersion: 'two_lane_explicit_v6',
  realTradingConfirmedAt: new Date(arm).toISOString(),
  relayArmedAt: new Date(arm).toISOString(),
});

test('fresh-arm boundary: only cycles created strictly after the arm are eligible', () => {
  const arm = Date.parse('2026-09-30T00:00:00.000Z');
  assert.equal(isCycleFreshForRelayArm(armedState(arm), new Date(arm - 1)), false);
  assert.equal(isCycleFreshForRelayArm(armedState(arm), new Date(arm)), false);
  assert.equal(isCycleFreshForRelayArm(armedState(arm), new Date(arm + 1)), true);
  assert.equal(isCycleFreshForRelayArm({ relayExecutionMode: 'PAUSED' }, new Date(arm + 1)), false);
  assert.equal(isCycleFreshForRelayArm(null, new Date(arm + 1)), false);
});

// Every registered tile is relay-ineligible, so even a signed, fresh,
// post-arm entry for any lane prefix must be refused before any placement.
for (const offset of [-1, 0, 1]) test(`production registry refuses signed entry at arm delta ${offset}`, async () => {
  assert.deepEqual([...RELAY_ELIGIBLE_TILE_LANES], []);
  assert.deepEqual([...RELAY_ELIGIBLE_TILE_ID_PREFIXES], []);
  const previous = { kill: process.env.INTENT_MIRROR_KILL_SWITCH, dry: process.env.INTENT_MIRROR_DRY_RUN };
  process.env.INTENT_MIRROR_KILL_SWITCH = '0';
  process.env.INTENT_MIRROR_DRY_RUN = '0';
  try {
    const now = Date.now(), oldArm = now - 2000, freshArm = now - 1000;
    const instance = { id: 'instance', userId: 'user', status: 'ACTIVE', exchangeProvider: 'BITFINEX', dashboardState: armedState(oldArm) };
    const cycles = ['cont-aabbccddeeff', 'fc3-aabbccddeeff', 'aabbccddeeff'].map((tradeId, i) => ({
      id: `cycle-${i}`, tradeId, createdAt: new Date(freshArm + offset),
      intentEnvelope: {
        schema: 'dcf-signal-intent/v1', signalId: tradeId, trade_id: tradeId, action: 'ENTER', direction: 'LONG',
        entry: { mode: 'EXACT_LIMIT', reference: 'SHOWCASE_EXACT_LIMIT', exact_limit_price: 64000, exact_qty_btc: 0.00039 },
        context: { signed_showcase_event: true, showcase_event: 'ORDER_PLACED', platform_received_at: new Date(now).toISOString(), entry_limit_policy: 'micro_sr_structural_limit_v1' },
      },
    }));
    let placements = 0;
    const reasons: string[] = [];
    const service = Object.create(SignalSubscriberExecutionService.prototype) as any;
    service.logger = { warn: (s: string) => reasons.push(s), log: () => {} };
    service.prisma = {
      signalCycle: { findMany: async () => cycles },
      platformSettings: { findUnique: async () => null },
      signalCycleParticipant: { findMany: async () => [] },
      tradingAgentInstance: { findUnique: async () => ({ ...instance, dashboardState: armedState(freshArm) }) },
    };
    service.exchanges = { getUserCredentials: async () => ({}) };
    service.activeTrading = { listActiveOrders: async () => [], getOpenPositionDetail: async () => null, getDerivativesAvailableUsd: async () => 100, getMarkPrice: async () => 64000 };
    service.botBridge = { getCachedExecutionState: () => null };
    service.evaluateEntryEligibility = async () => ({ canEnter: true });
    service.placeEntry = async () => { placements++; return true; };
    const result = await service.tryFreshSignedFlatEntry('agent', instance);
    assert.equal(result, false, reasons.join(';'));
    assert.equal(placements, 0);
    assert.ok(reasons.some((s) => s.includes('no-fresh-signed-intent')), reasons.join(';'));
  } finally {
    for (const [key, value] of [['INTENT_MIRROR_KILL_SWITCH', previous.kill], ['INTENT_MIRROR_DRY_RUN', previous.dry]] as const) {
      if (value == null) delete process.env[key]; else process.env[key] = value;
    }
  }
});
