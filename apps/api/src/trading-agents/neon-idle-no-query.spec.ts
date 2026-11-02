/**
 * RK R51 + HM R2: when Fly showcase runtime is retired, Neon-chatting background
 * timers must not register — proving the idle backend does not query Neon on a
 * timer cadence (20s alerts, wake poll, 5m signal-cycle idle recovery).
 *
 * Mutation proof: drop shouldRegisterNeonBackgroundTimers from any registration
 * site below, or flip the gate to always-true → this file fails.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import {
  FLY_SHOWCASE_RUNTIME_RETIRED,
  isFlyShowcaseRuntimeRetired,
  shouldRegisterNeonBackgroundTimers,
} from './canonical-showcase-runtime';

const priorForce = process.env.FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE;
delete process.env.FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE;

test.after(() => {
  if (priorForce == null) delete process.env.FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE;
  else process.env.FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE = priorForce;
});

const here = __dirname;
const read = (name: string) => readFileSync(join(here, name), 'utf8');

test('idle gate refuses Neon background timers while Fly runtime is retired', () => {
  assert.equal(FLY_SHOWCASE_RUNTIME_RETIRED, true);
  assert.equal(isFlyShowcaseRuntimeRetired(), true);
  assert.equal(shouldRegisterNeonBackgroundTimers(), false);
  assert.equal(
    shouldRegisterNeonBackgroundTimers({ FLY_SHOWCASE_RUNTIME_FORCE_ACTIVE: '1' }),
    true,
  );
});

test('R51: Neon timers gated once at registration sites (not scattered callback stops)', () => {
  const trading = read('trading-agents.service.ts');
  const exec = read('signal-subscriber-execution.service.ts');
  const cycles = read('signal-cycles.service.ts');
  const sync = read('showcase-session-sync.service.ts');
  const reporter = read('live-copy-fly-reporter.ts');

  // Single shared gate symbol at each registration site.
  for (const [label, src] of [
    ['trading-agents 20s', trading],
    ['executor wake/reconcile', exec],
    ['signal-cycles 5m', cycles],
    ['showcase-session-sync', sync],
    ['live-copy reporter', reporter],
  ] as const) {
    assert.match(
      src,
      /shouldRegisterNeonBackgroundTimers/,
      `${label} must call shouldRegisterNeonBackgroundTimers at registration`,
    );
  }

  const sliceInit = (src: string, startNeedle: string, endNeedle: string) => {
    const start = src.indexOf(startNeedle);
    assert.ok(start >= 0, `missing ${startNeedle}`);
    const end = src.indexOf(endNeedle, start + startNeedle.length);
    assert.ok(end > start, `missing end ${endNeedle}`);
    return src.slice(start, end);
  };

  // 20s position-alert interval is behind the gate inside onModuleInit.
  const tradingInit = sliceInit(
    trading,
    'async onModuleInit()',
    'private async syncConservativeBtcAgentRecord',
  );
  assert.match(tradingInit, /shouldRegisterNeonBackgroundTimers\(\)/);
  assert.match(tradingInit, /pollBotPositionAlerts/);
  assert.ok(
    tradingInit.indexOf('shouldRegisterNeonBackgroundTimers')
      < tradingInit.indexOf('setInterval(() => void this.pollBotPositionAlerts()'),
    '20s timer must be gated before setInterval registration',
  );

  // Wake / reconcile registration gated at executor onModuleInit.
  const execInit = sliceInit(exec, '  onModuleInit() {', '  private async persistLiveTickHeartbeats');
  assert.match(execInit, /shouldRegisterNeonBackgroundTimers\(\)/);
  assert.match(execInit, /Neon background timers not registered/);
  assert.ok(
    execInit.indexOf('shouldRegisterNeonBackgroundTimers')
      < execInit.indexOf('schedulePersistedWakePoll(0)'),
    'wake poll must not register when gate is false',
  );

  // 5m idle recovery gated at signal-cycles onModuleInit.
  const cyclesInit = sliceInit(cycles, '  onModuleInit() {', '  onModuleDestroy()');
  assert.match(cyclesInit, /shouldRegisterNeonBackgroundTimers\(\)/);
  assert.match(cyclesInit, /Neon wake\/5m idle recovery timers not registered/);
  assert.ok(
    cyclesInit.indexOf('shouldRegisterNeonBackgroundTimers')
      < cyclesInit.indexOf('scheduleBackstop('),
    '5m backstop must not register when gate is false',
  );
});

test('emergency reconcile fails closed without throw storm when wake retired', async () => {
  // Source contract: soft return, not throw, when shouldDispatchRelayExecutorWake is false.
  const exec = read('signal-subscriber-execution.service.ts');
  assert.match(
    exec,
    /requestExecutorEmergencyReconcile[\s\S]*shouldDispatchRelayExecutorWake\(\)[\s\S]*return \{ flattened: 0, requestId: 'offline-by-design' \}/,
  );
  assert.doesNotMatch(
    exec,
    /shouldDispatchRelayExecutorWake\(\) \{\s*throw new Error\('Relay executor wake retired/,
  );
});

test('behavioral: idle gate registers zero Neon timers (no prisma cadence)', () => {
  // Same decision every registration site makes — when false, no timer/Neon work starts.
  assert.equal(shouldRegisterNeonBackgroundTimers(), false);
  const registered: string[] = [];
  const registerIfAllowed = (name: string, start: () => void) => {
    if (!shouldRegisterNeonBackgroundTimers()) return;
    registered.push(name);
    start();
  };
  let neonQueries = 0;
  registerIfAllowed('20s-position-alerts', () => {
    neonQueries += 1;
  });
  registerIfAllowed('wake-poll', () => {
    neonQueries += 1;
  });
  registerIfAllowed('5m-signal-cycle-idle', () => {
    neonQueries += 1;
  });
  assert.deepEqual(registered, []);
  assert.equal(neonQueries, 0);
});
