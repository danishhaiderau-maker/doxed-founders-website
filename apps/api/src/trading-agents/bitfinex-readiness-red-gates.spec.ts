import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import test from 'node:test';
import { isMirrorableLaneTradeId, isPartialExitLaneTradeId, PARTIAL_EXIT_TILE_ID_PREFIXES } from '@dcf/utils';
import {
  DEFAULT_MIN_LIQUIDATION_TO_STOP_MULTIPLE,
  evaluatePreTradeLiquidationSafety,
  resolveMinLiquidationToStopMultiple,
} from './bitfinex-pre-trade-safety';
import {
  buildRelayExecutorHealth,
  describeSignedCopyMarginCap,
  readPersistedRelayExecutorHealth,
  resolveEffectiveStopLossMarginPct,
} from './signal-subscriber-execution.service';

const LIVE_VENUE = { initialMarginFraction: 0.01, maintenanceMarginFraction: 0.005 };

test('gate 2: clamped -13% stop at 100x clears posted-margin liquidation by the 3x default', () => {
  const stop = resolveEffectiveStopLossMarginPct(undefined, { mirrorMode: true });
  assert.equal(stop, -13);
  const result = evaluatePreTradeLiquidationSafety({
    leverage: 100, orderLeverage: 100, stopLossMarginPct: stop, venue: LIVE_VENUE,
  });
  assert.equal(result.ok, true);
  if (!result.ok) return;
  assert.ok(Math.abs(result.stopDistanceFraction - 0.0013) < 1e-12);
  assert.ok(Math.abs(result.liquidationDistanceFraction - 0.005) < 1e-12);
  assert.ok(result.multiple > 3.84 && result.multiple < 3.85);
  assert.equal(result.minMultiple, DEFAULT_MIN_LIQUIDATION_TO_STOP_MULTIPLE);
});

test('gate 2: fails closed when liquidation is too close, evidence is missing, or leverage is unverified', () => {
  const tooClose = evaluatePreTradeLiquidationSafety({
    leverage: 100, orderLeverage: 100, stopLossMarginPct: -30, venue: LIVE_VENUE,
  });
  assert.equal(tooClose.ok, false);
  assert.equal(!tooClose.ok && tooClose.reason, 'LIQUIDATION_TOO_CLOSE_TO_STOP');

  const stricter = evaluatePreTradeLiquidationSafety({
    leverage: 100, orderLeverage: 100, stopLossMarginPct: -13, venue: LIVE_VENUE, minMultiple: 4,
  });
  assert.equal(!stricter.ok && stricter.reason, 'LIQUIDATION_TOO_CLOSE_TO_STOP');

  const noVenue = evaluatePreTradeLiquidationSafety({
    leverage: 100, orderLeverage: 100, stopLossMarginPct: -13, venue: null,
  });
  assert.equal(!noVenue.ok && noVenue.reason, 'VENUE_MARGIN_EVIDENCE_MISSING');

  const levDrift = evaluatePreTradeLiquidationSafety({
    leverage: 150, orderLeverage: 100, stopLossMarginPct: -13, venue: LIVE_VENUE,
  });
  assert.equal(!levDrift.ok && levDrift.reason, 'MARGIN_MODE_LEVERAGE_UNVERIFIED');

  const aboveVenueMax = evaluatePreTradeLiquidationSafety({
    leverage: 100, orderLeverage: 100, stopLossMarginPct: -13,
    venue: { initialMarginFraction: 0.02, maintenanceMarginFraction: 0.005 },
  });
  assert.equal(!aboveVenueMax.ok && aboveVenueMax.reason, 'LEVERAGE_EXCEEDS_VENUE_MAXIMUM');

  const maintenanceSwallowsMargin = evaluatePreTradeLiquidationSafety({
    leverage: 100, orderLeverage: 100, stopLossMarginPct: -13,
    venue: { initialMarginFraction: 0.01, maintenanceMarginFraction: 0.01 },
  });
  assert.equal(!maintenanceSwallowsMargin.ok && maintenanceSwallowsMargin.reason, 'LIQUIDATION_INSIDE_MAINTENANCE');
});

test('gate 2: env can only raise the liquidation/stop multiple, never lower it', () => {
  assert.equal(resolveMinLiquidationToStopMultiple(undefined), 3);
  assert.equal(resolveMinLiquidationToStopMultiple('1'), 3);
  assert.equal(resolveMinLiquidationToStopMultiple('garbage'), 3);
  assert.equal(resolveMinLiquidationToStopMultiple('5'), 5);
  const lowered = evaluatePreTradeLiquidationSafety({
    leverage: 100, orderLeverage: 100, stopLossMarginPct: -30, venue: LIVE_VENUE, minMultiple: 1,
  });
  assert.equal(lowered.ok, false);
});

test('gate 2: every new-exposure entry path runs the pre-trade liquidation gate', () => {
  const source = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const calls = source.match(/await this\.checkPreTradeLiquidationSafety\(/g) ?? [];
  assert.equal(calls.length, 3);
});

test('gate 4: partial-exit tiles are refused by the relay allowlist while reductions are off', () => {
  // The registry's partial-exit tiles are the B1/B2/B3 regime tiles (gb1/gb2/gb3),
  // whose ladder take-profits reduce a position in parts. They stay relay-ineligible
  // until exchange-side reductions are proven, so the relay must refuse them.
  assert.deepEqual([...PARTIAL_EXIT_TILE_ID_PREFIXES], ['gb1', 'gb2', 'gb3']);
  assert.equal(isPartialExitLaneTradeId('gb1-deadbeef1234'), true);
  assert.equal(isPartialExitLaneTradeId('gb2-deadbeef1234'), true);
  assert.equal(isPartialExitLaneTradeId('gb3-deadbeef1234'), true);
  assert.equal(isPartialExitLaneTradeId('gs1-deadbeef1234'), false);
  assert.equal(isMirrorableLaneTradeId('gb1-deadbeef1234'), false);
  assert.equal(isMirrorableLaneTradeId('gb1-deadbeef1234', { partialReductionsEnabled: false }), false);
  assert.equal(isMirrorableLaneTradeId('gb1-deadbeef1234', { partialReductionsEnabled: true }), false);
});

test('gate 4: the executor routes every allowlist check through the partial-exit gate', () => {
  const source = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  const bare = source.match(/isMirrorableLaneTradeId\(/g) ?? [];
  assert.equal(bare.length, 1, 'only relayMayCopyTradeId may call isMirrorableLaneTradeId');
  assert.match(
    source,
    /isPartialExitLaneTradeId\(tradeId\) && !partialReductionsEnabled[\s\S]{0,300}return false;/,
  );
  assert.match(source, /SUBSCRIBER_POSITION_REDUCTION_ENABLED'\) === 'true'/);
});

const baseHealth = {
  nowMs: 10_000_000,
  running: false,
  tickStartedAtMs: 0,
  lastTickDurationMs: 800,
  currentInstanceId: null,
  currentStage: null,
  timeoutMs: 60_000,
  healthMaxAgeMs: 15_000,
  timeoutCount: 0,
};

test('gate 5: a paused executor reports PAUSED_HEALTHY instead of freezing at STARTING', () => {
  const paused = buildRelayExecutorHealth({
    ...baseHealth, lastTickCompletedAtMs: 10_000_000 - 3_600_000, pollActivity: 'PAUSED',
  });
  assert.equal(paused.status, 'PAUSED_HEALTHY');
  assert.equal(paused.healthy, true);

  const boot = buildRelayExecutorHealth({ ...baseHealth, lastTickCompletedAtMs: 0, pollActivity: 'PAUSED' });
  assert.equal(boot.status, 'STARTING');
  assert.equal(boot.healthy, false);

  const staleActive = buildRelayExecutorHealth({
    ...baseHealth, lastTickCompletedAtMs: 10_000_000 - 60_000, pollActivity: 'ACTIVE',
  });
  assert.equal(staleActive.status, 'IDLE');
  assert.equal(staleActive.healthy, false);
});

test('gate 5: persisted PAUSED_HEALTHY is visible for display but arming keeps strict freshness', () => {
  const now = Date.parse('2026-09-30T00:10:00.000Z');
  const dash = {
    relayExecutor: {
      healthy: true,
      status: 'PAUSED_HEALTHY',
      running: false,
      observedAt: '2026-09-30T00:08:30.000Z',
      serviceRole: 'executor-worker',
      executionEnabled: true,
    },
  };
  const display = readPersistedRelayExecutorHealth(dash, now);
  assert.equal(display.status, 'PAUSED_HEALTHY');
  assert.equal(display.healthy, true);
  assert.equal(display.heartbeatAgeMs, 90_000);

  const arm = readPersistedRelayExecutorHealth(dash, now, undefined, 0);
  assert.equal(arm.healthy, false);

  const expired = readPersistedRelayExecutorHealth(
    { relayExecutor: { ...dash.relayExecutor, observedAt: '2026-09-30T00:06:00.000Z' } },
    now,
  );
  assert.equal(expired.healthy, false);

  const source = readFileSync(join(__dirname, 'trading-agent-instances.service.ts'), 'utf8');
  assert.match(source, /readPersistedRelayExecutorHealth\(\s*freshInstance\?\.dashboardState,\s*Date\.now\(\),\s*undefined,\s*0,\s*\)/);
});

test('gate 5: the paused heartbeat replaces only relayExecutor atomically', () => {
  const source = readFileSync(join(__dirname, 'signal-subscriber-execution.service.ts'), 'utf8');
  assert.match(source, /jsonb_set\(COALESCE\("dashboardState", '\{\}'::jsonb\), '\{relayExecutor\}'/);
  assert.match(source, /setInterval\(\(\) => void this\.persistPausedExecutorHeartbeat\(\), PAUSED_EXECUTOR_HEARTBEAT_MS\)/);
});

test('gate 8: startup log states the real per-leg cap, not the platform ceiling', () => {
  const text = describeSignedCopyMarginCap(20);
  assert.match(text, /margin input <= \$0\.25 at 100x/);
  assert.match(text, /platform setting \$20/);
  assert.match(text, /never rounded up/);
  assert.match(text, /not max loss/);
  assert.doesNotMatch(text, /max \$20\/trade/);
  assert.match(describeSignedCopyMarginCap(0.2), /<= \$0\.20 at 100x/);
});
