import assert from 'node:assert/strict';
import test from 'node:test';
import { LearningEngineService } from './learning-engine.service';

function makeService(prisma: Record<string, unknown>) {
  return new LearningEngineService(prisma as never, {} as never);
}

test('durable rollup commits its watermark only while it owns the lease', async () => {
  const updates: Array<Record<string, unknown>> = [];
  const prisma = {
    learningEngineState: {
      updateMany: async (args: Record<string, unknown>) => {
        updates.push(args);
        return { count: 1 };
      },
      create: async () => ({ id: 'global' }),
      findUnique: async () => ({
        id: 'global',
        lastRollupAt: null,
        lastProcessedCount: 0,
        lastUpdatedCount: 0,
      }),
    },
    routingDecision: { findMany: async () => [] },
  };
  const service = makeService(prisma);

  assert.deepEqual(await service.rollup(), { processed: 0, updated: 0 });
  assert.equal(updates.length, 2);
  assert.equal((updates[0]!.data as { leaseOwner: string }).leaseOwner.length > 0, true);
  assert.equal((updates[1]!.data as { leaseOwner: null }).leaseOwner, null);
  assert.equal((updates[1]!.data as { leaseExpiresAt: null }).leaseExpiresAt, null);
});

test('a replica that loses the create race skips the duplicate rollup', async () => {
  const prisma = {
    learningEngineState: {
      updateMany: async () => ({ count: 0 }),
      create: async () => {
        throw Object.assign(new Error('unique key'), { code: 'P2002' });
      },
      findUnique: async () => {
        throw new Error('must not read a window without the lease');
      },
    },
    routingDecision: {
      findMany: async () => {
        throw new Error('must not process a window without the lease');
      },
    },
  };
  const service = makeService(prisma);
  assert.deepEqual(await service.rollup(), { processed: 0, updated: 0 });
});
