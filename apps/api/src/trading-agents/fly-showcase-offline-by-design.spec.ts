/**
 * HM condition (Fly deleted): dashboard/trading-status must be offline-by-design —
 * no error loop, no retry storm, no cached armed/live fallback.
 *
 * Mutation proof: flip `FLY_SHOWCASE_RUNTIME_RETIRED` to false, drop
 * `offline, by design` from the label, or remove `live_armed: false` /
 * `refuseRetiredFlyRuntime` / reporter loop guard → this file fails.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  CANONICAL_SHOWCASE_BOT_URL,
  FLY_SHOWCASE_RUNTIME_RETIRED,
  SHOWCASE_OFFLINE_BY_DESIGN_LABEL,
  buildShowcaseOfflineByDesignStatus,
  isDeadFlyHostUrl,
  isFlyShowcaseRuntimeRetired,
  shouldDispatchRelayExecutorWake,
} from './canonical-showcase-runtime';

const priorForce = process.env.FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE;
delete process.env.FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE;

test.after(() => {
  if (priorForce == null) delete process.env.FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE;
  else process.env.FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE = priorForce;
});

const here = __dirname;
const read = (name: string) => readFileSync(join(here, name), 'utf8');
const readAdmin = (name: string) =>
  readFileSync(join(here, '..', 'admin-control', name), 'utf8');

test('FLY_SHOWCASE_RUNTIME_RETIRED is hard-true (Fly app deleted)', () => {
  assert.equal(FLY_SHOWCASE_RUNTIME_RETIRED, true);
  assert.equal(isFlyShowcaseRuntimeRetired(), true);
  assert.match(SHOWCASE_OFFLINE_BY_DESIGN_LABEL, /offline, by design/i);
  assert.match(SHOWCASE_OFFLINE_BY_DESIGN_LABEL, /moving to laptop/i);
});

test('offline-by-design status shape is stable and never armed/live', () => {
  const status = buildShowcaseOfflineByDesignStatus();
  assert.equal(status.status, 'offline');
  assert.equal(status.label, SHOWCASE_OFFLINE_BY_DESIGN_LABEL);
  assert.equal(status.reason, 'FLY_APP_DELETED');
  assert.equal(status.offline_by_design, true);
  assert.equal(status.moving_to_laptop, true);
  assert.equal(status.live_armed, false);
  assert.equal(status.bitfinex_live_enabled, false);
  assert.equal(status.botConnected, false);
  assert.equal(status.flyReachable, false);
  assert.equal(status.retry, false);
  assert.equal(status.error_loop, false);
});

test('dead Fly host detector matches doxed-btc-bot.fly.dev', () => {
  assert.equal(isDeadFlyHostUrl(CANONICAL_SHOWCASE_BOT_URL), true);
  assert.equal(isDeadFlyHostUrl('https://doxed-btc-bot.fly.dev/api/state'), true);
  assert.equal(isDeadFlyHostUrl('https://bot.doxxedcrypto.digital'), false);
  assert.equal(isDeadFlyHostUrl('http://relay-executor.internal'), false);
});

test('RELAY_EXECUTOR_WAKE_URL is dropped while Fly runtime is retired', () => {
  const env = {
    RELAY_EXECUTOR_WAKE_URL: 'https://doxed-btc-bot.fly.dev',
  } as NodeJS.ProcessEnv;
  assert.equal(shouldDispatchRelayExecutorWake(env), false);
  // Even with FORCE_ACTIVE, never wake a deleted Fly host.
  assert.equal(
    shouldDispatchRelayExecutorWake({
      ...env,
      FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE: '1',
      RELAY_EXECUTOR_WAKE_URL: 'https://doxed-btc-bot.fly.dev',
    }),
    false,
  );
  // Non-Fly wake only when explicitly force-active (legacy unit tests).
  assert.equal(
    shouldDispatchRelayExecutorWake({
      FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE: '1',
      RELAY_EXECUTOR_WAKE_URL: 'http://relay-executor.internal',
    }),
    true,
  );
});

test('bot-bridge refuses retired Fly and clears armed/live cache path', () => {
  const src = read('bot-bridge.service.ts');
  assert.match(src, /refuseRetiredFlyRuntime/);
  assert.match(src, /isFlyShowcaseRuntimeRetired/);
  assert.match(src, /Fail closed: never return a stale armed\/live cache after Fly is gone/);
  assert.match(src, /if \(this\.refuseRetiredFlyRuntime\(\)\) return null;/);
  assert.match(src, /async fetchStateForExecution[\s\S]*refuseRetiredFlyRuntime/);
  assert.match(src, /async fetchPublicShowcaseState[\s\S]*refuseRetiredFlyRuntime/);
  assert.match(src, /getCachedExecutionState[\s\S]*refuseRetiredFlyRuntime/);
  assert.match(src, /async isFlyHealthReachable[\s\S]*refuseRetiredFlyRuntime/);
  assert.doesNotMatch(
    src.split('refuseRetiredFlyRuntime(): boolean')[1]?.slice(0, 800) ?? '',
    /live_armed:\s*true/,
  );
});

test('trading-status / bot health / dashboard stamp offline-by-design', () => {
  const src = read('trading-agents.service.ts');
  assert.match(src, /buildShowcaseOfflineByDesignStatus/);
  assert.match(src, /async getBotBridgeStatus\(\)[\s\S]*isFlyShowcaseRuntimeRetired/);
  assert.match(src, /async getBotHealth\([\s\S]*isFlyShowcaseRuntimeRetired/);
  assert.match(src, /offline_by_design/);
  assert.match(src, /live_armed: offline\.live_armed/);
  assert.match(src, /OFFLINE_BY_DESIGN/);
  assert.match(src, /flyReachable: offlineByDesign\s*\?\s*false/);
});

test('admin-control public agent status is offline-by-design without bridge probe', () => {
  const src = readAdmin('admin-control.service.ts');
  assert.match(src, /async getPublicAgentStatus[\s\S]*isFlyShowcaseRuntimeRetired/);
  assert.match(src, /buildShowcaseOfflineByDesignStatus/);
  assert.match(src, /offline_by_design: offline\.offline_by_design/);
  assert.match(src, /live_armed: offline\.live_armed/);
  assert.match(src, /retry: offline\.retry/);
  assert.match(src, /error_loop: offline\.error_loop/);
});

test('live-copy Fly reporter does not start the 5s loop when retired', () => {
  const src = read('live-copy-fly-reporter.ts');
  assert.match(src, /onModuleInit\(\): void \{[\s\S]*isFlyShowcaseRuntimeRetired\(\)/);
  assert.match(src, /Fly reporter loop not started/);
  assert.match(src, /async tick\([\s\S]*isFlyShowcaseRuntimeRetired\(\)\) return;/);
});

test('L27 arm-gate and health probe fail closed without dead-host fetch', () => {
  const arm = read('live-arm-l27-gate.ts');
  assert.match(arm, /isFlyShowcaseRuntimeRetired\(env\)\) return DENY_L27_GATE_UNREADABLE/);
  const health = read('public-bot-health-probe.ts');
  assert.match(health, /isFlyShowcaseRuntimeRetired\(\)/);
  assert.match(health, /SHOWCASE_OFFLINE_BY_DESIGN_LABEL/);
});

test('wake path and fly-control refuse dead Fly host', () => {
  const exec = read('signal-subscriber-execution.service.ts');
  assert.match(exec, /shouldDispatchRelayExecutorWake/);
  assert.match(exec, /startExecutorConnectionKeepalive[\s\S]*shouldDispatchRelayExecutorWake\(\)/);
  assert.match(exec, /dispatchDirectExecutorWake[\s\S]*shouldDispatchRelayExecutorWake\(\)/);
  const fly = read('fly-control.service.ts');
  assert.match(fly, /isFlyShowcaseRuntimeRetired\(\)/);
  assert.match(fly, /SHOWCASE_OFFLINE_BY_DESIGN_LABEL/);
});

test('legacy emergency-wake helpers.spec uses FORCE_ACTIVE (not production wake)', () => {
  // Mutation proof: drop FORCE_ACTIVE from the emergency reconcile helpers test → CI fails
  // the same way tip fe5153f8e166 did (wake retired throw).
  const helpers = read('signal-subscriber-execution.helpers.spec.ts');
  assert.match(
    helpers,
    /web process dispatches emergency reconciliation[\s\S]*FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE\s*=\s*'1'/,
  );
  assert.match(
    helpers,
    /FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE[\s\S]*requestExecutorEmergencyReconcile/,
  );
});
