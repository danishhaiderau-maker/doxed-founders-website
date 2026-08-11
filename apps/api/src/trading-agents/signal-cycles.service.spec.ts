import assert from 'node:assert/strict';
import test from 'node:test';
import { SignalCycleStatus } from '@prisma/client';
import { SignalCyclesService } from './signal-cycles.service';

test('hire EXIT overlaps independent cycle and participant reads before persistence', async () => {
  let resolveCycle!: (value: Record<string, unknown>) => void;
  let resolveParticipant!: (value: Record<string, unknown>) => void;
  const cycleRead = new Promise<Record<string, unknown>>((resolve) => {
    resolveCycle = resolve;
  });
  const participantRead = new Promise<Record<string, unknown>>((resolve) => {
    resolveParticipant = resolve;
  });
  const calls: string[] = [];
  const prisma = {
    signalCycle: {
      findFirst: () => {
        calls.push('cycle-read');
        return cycleRead;
      },
      update: async () => {
        calls.push('cycle-update');
      },
    },
    signalCycleParticipant: {
      findUnique: () => {
        calls.push('participant-read');
        return participantRead;
      },
      create: async () => {
        throw new Error('existing EXIT participant must not be recreated');
      },
      update: async () => {
        calls.push('participant-update');
      },
    },
    signalCycleEvent: {
      create: async () => {
        calls.push('event-create');
      },
    },
  };
  const service = new SignalCyclesService(
    prisma as never,
    {} as never,
    {} as never,
    {} as never,
  );

  const pending = service.recordHireExecutionEvent(
    'user-1',
    'agent-1',
    'cycle-1',
    'EXIT',
    { venue: 'bitfinex', pnl_usd: 1, pnl_margin_pct: 1 },
  );

  // Neither read has resolved, so observing both calls proves they were
  // launched together rather than separated by an awaited network round trip.
  await Promise.resolve();
  assert.deepEqual(calls, ['cycle-read', 'participant-read']);

  resolveCycle({ id: 'cycle-1', status: SignalCycleStatus.CLOSED });
  resolveParticipant({ id: 'participant-1', venue: 'bitfinex' });
  await pending;

  assert.deepEqual(calls, [
    'cycle-read',
    'participant-read',
    'event-create',
    'participant-update',
  ]);
});
