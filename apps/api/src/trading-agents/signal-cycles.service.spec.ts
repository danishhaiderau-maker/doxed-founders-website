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
