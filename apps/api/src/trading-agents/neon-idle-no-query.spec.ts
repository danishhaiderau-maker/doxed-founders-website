/**
 * RK R51 + HM R2 B2: when Fly showcase runtime is retired, Neon-chatting
 * background timers must not register — prove by counting real setInterval
 * registrations and prisma calls (not source greps).
 *
 * Covers B1 registration sites: paper-limit (60s), epoch-settlement (5m),
 * metrics-sync (2m), plus signal-cycles (5m idle recovery).
 *
 * Mutation proof: drop shouldRegisterNeonBackgroundTimers from those
 * onModuleInit sites → setInterval and/or prisma counts become non-zero.
 */
import assert from 'node:assert/strict';
import test from 'node:test';
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

function prismaTrap() {
  const calls: string[] = [];
  const prisma = new Proxy(
    {},
    {
      get(_t, prop) {
        if (prop === 'then') return undefined;
        if (typeof prop === 'symbol') return undefined;
        const model = String(prop);
        return new Proxy(
          {},
          {
            get(_t2, prop2) {
              if (prop2 === 'then') return undefined;
              const method = String(prop2);
              return async () => {
                calls.push(`${model}.${method}`);
                throw new Error(`Neon query while idle: ${model}.${method}`);
              };
            },
          },
        );
      },
    },
  );
  return { prisma: prisma as never, calls };
}

test('idle gate refuses Neon background timers while Fly runtime is retired', () => {
  assert.equal(FLY_SHOWCASE_RUNTIME_RETIRED, true);
  assert.equal(isFlyShowcaseRuntimeRetired(), true);
  assert.equal(shouldRegisterNeonBackgroundTimers(), false);
});

test('behavioral: retired onModuleInit registers zero timers and zero prisma calls', async () => {
  assert.equal(shouldRegisterNeonBackgroundTimers(), false);

  const registeredMs: number[] = [];
  const created: ReturnType<typeof setInterval>[] = [];
  const realSetInterval = global.setInterval;
  // Nest `tsc` has no DOM lib — avoid TimerHandler (breaks Railway nest build).
  global.setInterval = ((fn: Parameters<typeof setInterval>[0], ms?: number, ...args: unknown[]) => {
    registeredMs.push(Number(ms));
    const handle = realSetInterval(fn as never, ms as never, ...(args as never[]));
    created.push(handle);
    return handle;
  }) as typeof setInterval;

  try {
    const paperTrap = prismaTrap();
    const { PaperLimitOrderService } = await import('../paper-trading/paper-limit-order.service');
    new PaperLimitOrderService(paperTrap.prisma, {} as never).onModuleInit();

    const metricsTrap = prismaTrap();
    const { MetricsSyncService } = await import('../projects/metrics-sync.service');
    new MetricsSyncService(metricsTrap.prisma, {} as never).onModuleInit();

    const epochTrap = prismaTrap();
    const { EpochSettlementService } = await import(
      '../founder-economics/epoch-settlement.service'
    );
    const epoch = new EpochSettlementService(epochTrap.prisma, {} as never);
    epoch.onModuleInit();

    const cyclesTrap = prismaTrap();
    const { SignalCyclesService } = await import('./signal-cycles.service');
    const cycles = new SignalCyclesService(
      cyclesTrap.prisma,
      { isEnabled: () => true } as never,
      {} as never,
      {} as never,
    );
    cycles.onModuleInit();

    assert.deepEqual(
      registeredMs,
      [],
      `unexpected setInterval registrations while idle: ${JSON.stringify(registeredMs)}`,
    );
    assert.deepEqual(paperTrap.calls, [], `paper prisma: ${JSON.stringify(paperTrap.calls)}`);
    assert.deepEqual(metricsTrap.calls, [], `metrics prisma: ${JSON.stringify(metricsTrap.calls)}`);
    assert.deepEqual(epochTrap.calls, [], `epoch prisma: ${JSON.stringify(epochTrap.calls)}`);
    assert.deepEqual(cyclesTrap.calls, [], `cycles prisma: ${JSON.stringify(cyclesTrap.calls)}`);
    assert.equal(
      (cycles as unknown as { backstopEnabled?: boolean }).backstopEnabled,
      false,
    );
    assert.equal(
      (epoch as unknown as { settlementTimer?: unknown }).settlementTimer,
      null,
    );
  } finally {
    for (const h of created) clearInterval(h);
    global.setInterval = realSetInterval;
  }
});
