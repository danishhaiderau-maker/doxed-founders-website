import assert from 'node:assert/strict';
import test from 'node:test';
import { IdeaValidatorService } from './idea-validator.service';

function makeService(prisma: Record<string, unknown>) {
  return new IdeaValidatorService(prisma as never, {} as never, {} as never);
}

test('durable worker reclaims stale research and claims a queued idea once', async () => {
  const updates: Array<Record<string, unknown>> = [];
  const prisma = {
    ideaCheck: {
      updateMany: async (args: Record<string, unknown>) => {
        updates.push(args);
        return { count: updates.length === 2 ? 1 : 0 };
      },
      findMany: async () => [
        {
          id: 'idea-1',
          userId: 'founder-1',
          ideaText: 'Research a durable job queue',
        },
      ],
    },
  };
  const service = makeService(prisma);
  let executed: { userId: string; nodeId: string; rowId: string; ideaText: string } | null = null;
  (service as unknown as {
    runResearch: (
      auth: { userId: string; nodeId: string },
      rowId: string,
      ideaText: string,
    ) => Promise<void>;
  }).runResearch = async (auth, rowId, ideaText) => {
    executed = { ...auth, rowId, ideaText };
  };

  assert.deepEqual(await service.processPending(1), { processed: 1 });
  assert.equal(updates.length, 2);
  assert.deepEqual((updates[0]!.where as { status: string }).status, 'RUNNING');
  assert.deepEqual((updates[0]!.data as { status: string }).status, 'PENDING');
  assert.deepEqual((updates[1]!.where as { id: string; status: string }), {
    id: 'idea-1',
    status: 'PENDING',
  });
  assert.deepEqual(executed, {
    userId: 'founder-1',
    nodeId: 'idea-validator-worker',
    rowId: 'idea-1',
    ideaText: 'Research a durable job queue',
  });
});
