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

test('hire terminal event atomically claims participant before appending its audit row', async () => {
  const calls: string[] = [];
  const service = terminalEventService(terminalEventPrisma(1, calls));

  const result = await service.recordHireExecutionEvent(
    'user-1', 'agent-1', 'cycle-1', 'EXIT', { venue: 'bitfinex', pnl_usd: 1, pnl_margin_pct: 1 },
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
    'user-1', 'agent-1', 'cycle-1', 'EXIT', { venue: 'bitfinex', pnl_usd: 1, pnl_margin_pct: 1 },
  );

  assert.deepEqual(result, { ok: true, participantId: 'participant-1', duplicateTerminal: true });
  assert.deepEqual(calls, ['cycle-read', 'participant-read', 'participant-terminal-claim']);
});
