import assert from 'node:assert/strict';
import test from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import { SignalCyclesService } from './signal-cycles.service';

function terminalEventPrisma(claimCount: number, calls: string[]) {
  const prisma: any = {
    signalCycle: {
      findFirst: async () => {
        calls.push('cycle-read');
        return { id: 'cycle-1', status: SignalCycleStatus.OPEN };
      },
      updateMany: async () => {
        calls.push('cycle-terminal-update');
        return { count: 1 };
      },
    },
    signalCycleParticipant: {
      findUnique: async () => {
        calls.push('participant-read');
        return { id: 'participant-1', venue: 'bitfinex' };
      },
      create: async () => assert.fail('existing participant must not be recreated'),
      updateMany: async () => {
        calls.push('participant-terminal-claim');
        return { count: claimCount };
      },
    },
    signalCycleEvent: {
      create: async () => {
        calls.push('event-create');
      },
    },
  };
  prisma.$transaction = async (work: (tx: any) => Promise<unknown>) => work(prisma);
  return prisma;
}

function terminalEventService(prisma: any) {
  return new SignalCyclesService(prisma, {} as never, {} as never, {} as never);
}

function schedulingService() {
  const service = new SignalCyclesService(
    {
      tradingAgent: { findUnique: async () => null },
      signalCycle: { findFirst: async () => null },
    } as never,
    { isEnabled: () => true } as never,
    {} as never,
    {} as never,
  ) as any;
  const delays: number[] = [];
  service.backstopEnabled = true;
  service.scheduleTimeout = (_callback: () => void, delayMs: number) => {
    delays.push(delayMs);
    return { unref() {} };
  };
  return { service, delays };
}

test('signal-cycle scheduler performs one bounded startup recovery wake', () => {
  const { service, delays } = schedulingService();
  service.backstopEnabled = false;
  service.onModuleInit();
  assert.deepEqual(delays, [1_000]);
  service.onModuleDestroy();
});

test('idle signal-cycle recovery schedules one five-minute probe instead of recurring 2s reads', async () => {
  const { service, delays } = schedulingService();
  let intentReads = 0;
  let closureReads = 0;
  service.pollBotForIntents = async () => {
    intentReads += 1;
    return false;
  };
  service.syncShowcaseCycleClosures = async () => {
    closureReads += 1;
    return false;
  };

  await service.runBackstop();

  assert.equal(intentReads, 1);
  assert.equal(closureReads, 1);
  assert.deepEqual(delays, [5 * 60_000]);
});

test('active signal cycle preserves the two-second reconciliation cadence', async () => {
  const { service, delays } = schedulingService();
  service.pollBotForIntents = async () => false;
  service.syncShowcaseCycleClosures = async () => true;

  await service.runBackstop();

  assert.deepEqual(delays, [2_000]);
});

test('signed ORDER_PLACED wake bypasses idle delay and restores active cadence', async () => {
  const { service, delays } = schedulingService();
  let directWakeReads = 0;
  service.pollBotForIntents = async () => {
    directWakeReads += 1;
    return false;
  };
  service.syncShowcaseCycleClosures = async () => {
    assert.fail('ORDER_PLACED wake intentionally skips the closure query');
  };

  await service.wakeFromShowcase({ intents: true, closures: false });

  assert.equal(directWakeReads, 1);
  assert.deepEqual(delays, [2_000]);
});

test('newer direct wake cadence cannot be overwritten by an older deferred snapshot', async () => {
  const { service, delays } = schedulingService();
  let releaseClosure!: (active: boolean) => void;
  const closureStarted = new Promise<void>((resolve) => {
    service.syncShowcaseCycleClosures = () => {
      resolve();
      return new Promise<boolean>((done) => { releaseClosure = done; });
    };
  });
  service.pollBotForIntents = async () => false;

  const staleBackstop = service.runBackstop();
  await closureStarted;
  await service.wakeFromShowcase({ intents: true, closures: false });
  releaseClosure(false);
  await staleBackstop;

  assert.equal(service.activeCycleBackstop, true);
  assert.deepEqual(delays, [2_000]);
});

test('closure reconciliation is single-file and a forced wake runs after an in-flight backstop', async () => {
  let releaseFirst!: () => void;
  let firstStarted!: () => void;
  const started = new Promise<void>((resolve) => { firstStarted = resolve; });
  const firstGate = new Promise<void>((resolve) => { releaseFirst = resolve; });
  let flyCalls = 0;
  let inFlight = 0;
  let maxInFlight = 0;
  const service = new SignalCyclesService(
    {
      tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
      signalCycle: { findMany: async () => [] },
    } as never,
    {
      isEnabled: () => true,
      fetchStateForExecution: async () => {
        flyCalls += 1;
        inFlight += 1;
        maxInFlight = Math.max(maxInFlight, inFlight);
        if (flyCalls === 1) {
          firstStarted();
          await firstGate;
        }
        inFlight -= 1;
        return {};
      },
    } as never,
    {} as never,
    {} as never,
  );
  // Keep one active row so both serialized calls reach the Fly snapshot.
  (service as any).prisma.signalCycle.findMany = async () => [{
    id: 'cycle-1', tradeId: 'cont-1', status: SignalCycleStatus.OPEN, expiresAt: null,
  }];

  const first = service.syncShowcaseCycleClosures(false);
  await started;
  const second = service.syncShowcaseCycleClosures(true);
  await new Promise((resolve) => setImmediate(resolve));
  assert.equal(flyCalls, 1);
  releaseFirst();
  await Promise.all([first, second]);

  assert.equal(flyCalls, 2);
  assert.equal(maxInFlight, 1);
});

test('closure query paginates every active cycle with a narrow stable projection', async () => {
  const rows = Array.from({ length: 205 }, (_, index) => ({
    id: `cycle-${String(index).padStart(3, '0')}`,
    tradeId: `cont-${index}`,
    status: SignalCycleStatus.INTENT,
    expiresAt: new Date(Date.now() - 60_000),
  }));
  const queries: any[] = [];
  let updates = 0;
  const service = new SignalCyclesService(
    {
      tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
      signalCycle: {
        findMany: async (query: any) => {
          queries.push(query);
          const start = query.cursor
            ? rows.findIndex((row) => row.id === query.cursor.id) + query.skip
            : 0;
          return rows.slice(start, start + query.take);
        },
        update: async () => { updates += 1; },
      },
    } as never,
    {
      isEnabled: () => true,
      fetchStateForExecution: async () => ({
        positions: [], trades: [], fidelity_trades: [], trades_map: {},
      }),
    } as never,
    {} as never,
    {} as never,
  );

  assert.equal(await service.syncShowcaseCycleClosures(), true);
  assert.equal(queries.length, 2);
  assert.equal(updates, 205);
  assert.deepEqual(queries[0].orderBy, { id: 'asc' });
  assert.deepEqual(queries[0].select, {
    id: true, tradeId: true, status: true, expiresAt: true,
  });
  assert.equal('intentEnvelope' in queries[0].select, false);
  assert.deepEqual(queries[1].cursor, { id: 'cycle-199' });
  assert.equal(queries[1].skip, 1);
});

test('isolated relay executor never starts the SignalCycles Neon scheduler', () => {
  const prior = process.env.RELAY_EXECUTOR_WORKER;
  process.env.RELAY_EXECUTOR_WORKER = 'true';
  try {
    const { service, delays } = schedulingService();
    service.backstopEnabled = false;
    service.onModuleInit();
    assert.equal(service.backstopEnabled, false);
    assert.deepEqual(delays, []);
  } finally {
    if (prior == null) delete process.env.RELAY_EXECUTOR_WORKER;
    else process.env.RELAY_EXECUTOR_WORKER = prior;
  }
});

test('closure recovery reports idle without fetching Fly when Neon has no active cycles', async () => {
  let flyReads = 0;
  const service = new SignalCyclesService(
    {
      tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
      signalCycle: { findMany: async () => [] },
    } as never,
    {
      isEnabled: () => true,
      fetchStateForExecution: async () => {
        flyReads += 1;
        return {};
      },
    } as never,
    {} as never,
    {} as never,
  );

  assert.equal(await service.syncShowcaseCycleClosures(), false);
  assert.equal(flyReads, 0);
});

const authenticatedObservedExit = {
  venue: 'bitfinex',
  pnl_usd: 1,
  pnl_margin_pct: 1,
  terminal_authority_kind: 'EXCHANGE_OBSERVED_TERMINAL',
  terminal_authority_evidence: {
    schema: 'exchange_observed_terminal_v1',
    authenticated_exchange_read: true,
    submitted_close: false,
  },
  final_reconciliation: {
    schema: 'relay_final_reconciliation_v1',
    position_reconciled: true,
    complete: true,
  },
};

test('hire terminal event atomically claims participant before appending its audit row', async () => {
  const calls: string[] = [];
  const service = terminalEventService(terminalEventPrisma(1, calls));

  const result = await service.recordHireExecutionEvent(
    'user-1', 'agent-1', 'cycle-1', 'EXIT', authenticatedObservedExit,
  );

  assert.deepEqual(result, { ok: true, participantId: 'participant-1', duplicateTerminal: false });
  assert.deepEqual(calls, [
    'cycle-read',
    'participant-read',
    'participant-terminal-claim',
    'event-create',
    'cycle-terminal-update',
  ]);
});

test('hire duplicate terminal event is ignored after another closer owns the participant', async () => {
  const calls: string[] = [];
  const service = terminalEventService(terminalEventPrisma(0, calls));

  const result = await service.recordHireExecutionEvent(
    'user-1', 'agent-1', 'cycle-1', 'EXIT', authenticatedObservedExit,
  );

  assert.deepEqual(result, { ok: true, participantId: 'participant-1', duplicateTerminal: true });
  assert.deepEqual(calls, ['cycle-read', 'participant-read', 'participant-terminal-claim']);
});

test('hire EXIT rejects ledger-only terminal writes before reading or mutating the database', async () => {
  const calls: string[] = [];
  const service = terminalEventService(terminalEventPrisma(1, calls));
  await assert.rejects(
    service.recordHireExecutionEvent(
      'user-1', 'agent-1', 'cycle-1', 'EXIT',
      { venue: 'bitfinex', pnl_usd: 0, exit_reason: 'GHOST_LOT_REPAIRED' },
    ),
    /authenticated final reconciliation/,
  );
  assert.deepEqual(calls, []);
});

test('source-open position prevents fallback TTL from falsely expiring its INTENT cycle', async () => {
  let updates = 0;
  const cycle = {
    id: 'cycle-source-open', tradeId: 'cont-source-open', status: SignalCycleStatus.INTENT,
    expiresAt: new Date(Date.now() - 1_000), intentEnvelope: {},
  };
  const service = Object.create(SignalCyclesService.prototype) as any;
  service.botBridge = {
    isEnabled: () => true,
    fetchStateForExecution: async () => ({
      positions: [{ trade_id: 'cont-source-open', entry: 64_100 }],
      trades: [], fidelity_trades: [], trades_map: {},
    }),
  };
  service.prisma = {
    tradingAgent: { findUnique: async () => ({ id: 'agent-1' }) },
    signalCycle: {
      findMany: async () => [cycle],
      update: async () => { updates += 1; },
    },
  };
  await service.syncShowcaseCycleClosures(true);
  assert.equal(updates, 0);
});
