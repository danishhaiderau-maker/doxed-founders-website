import assert from 'node:assert/strict';
import test from 'node:test';
import { LamOrchestratorService } from './lam-orchestrator.service';

function makeService(prisma: Record<string, unknown>) {
  return new LamOrchestratorService(
    {} as never,
    {} as never,
    {} as never,
    {} as never,
    prisma as never,
  );
}

test('durable worker claims a pending row once and resumes it with the original owner', async () => {
  const calls: Array<Record<string, unknown>> = [];
  const prisma = {
    lamTask: {
      findMany: async () => [{ id: 'task-1', userId: 'founder-1', goal: 'Research competitors' }],
      updateMany: async (args: Record<string, unknown>) => {
        calls.push(args);
        return { count: 1 };
      },
    },
  };
  const service = makeService(prisma);
  (service as unknown as { requeueStaleClaims: () => Promise<void> }).requeueStaleClaims = async () => {};
  let resumed: { userId: string; taskId: string; goal: string } | null = null;
  (service as unknown as { runTask: (auth: { userId: string }, taskId: string, goal: string) => Promise<void> }).runTask = async (auth, taskId, goal) => {
    resumed = { userId: auth.userId, taskId, goal };
  };

  assert.equal(await service.processQueuedTasks(1), 1);
  assert.deepEqual(resumed, {
    userId: 'founder-1',
    taskId: 'task-1',
    goal: 'Research competitors',
  });
  assert.equal(calls.length, 1);
  assert.equal((calls[0]!.data as { status: string }).status, 'RUNNING');
});

test('browser writes and computer control require a founder confirmation', () => {
  const service = makeService({});
  const needsConfirmation = (service as unknown as {
    requiresConfirmation: (step: unknown) => boolean;
  }).requiresConfirmation;

  assert.equal(needsConfirmation({ adapter: 'browser', payload: { action: 'extract' } }), false);
  assert.equal(needsConfirmation({ adapter: 'browser', payload: { action: 'fillForm' } }), true);
  assert.equal(needsConfirmation({ adapter: 'computer-use', payload: { action: 'screenshot' } }), true);
});
