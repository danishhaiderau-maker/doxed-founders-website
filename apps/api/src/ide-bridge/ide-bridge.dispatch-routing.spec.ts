import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { IdeBridgeService } from './ide-bridge.service.js';

type ModelMethod = (args: Record<string, unknown>) => Promise<unknown>;

function makeService(methods: Partial<Record<string, ModelMethod>>) {
  const model = {
    create: async () => ({ id: 'dispatch-1', status: 'PENDING' }),
    findMany: async () => [],
    findFirst: async () => null,
    update: async () => ({}),
    updateMany: async () => ({ count: 0 }),
    ...methods,
  };
  const service = new IdeBridgeService(
    { pendingIdeDispatch: model } as never,
    {} as never,
    {} as never,
    {} as never,
    {} as never,
  );
  return { service, model };
}

function assertFounderIdeNodeScope(
  where: Record<string, unknown>,
  nodeId: string,
): void {
  assert.deepEqual(where.OR, [
    {
      ideProvider: {
        notIn: ['founder-ide', 'founder_ide', 'void', 'vscode'],
      },
    },
    {
      ideProvider: {
        in: ['founder-ide', 'founder_ide', 'void', 'vscode'],
      },
      sessionId: `founder-ide:${nodeId}`,
    },
  ]);
}

describe('IdeBridgeService Founder IDE node routing', () => {
  it('returns Founder IDE dispatches only to the selected laptop', async () => {
    let captured: Record<string, unknown> = {};
    const { service } = makeService({
      findMany: async (args) => {
        captured = args;
        return [];
      },
    });

    await service.getPendingDispatches('user-1', 'fn_target');

    const where = (captured.where ?? {}) as Record<string, unknown>;
    assert.equal(where.userId, 'user-1');
    assert.equal(where.status, 'PENDING');
    assertFounderIdeNodeScope(where, 'fn_target');
  });

  it('applies the same laptop scope before atomically claiming a dispatch', async () => {
    let captured: Record<string, unknown> = {};
    const { service } = makeService({
      findFirst: async (args) => {
        captured = args;
        return null;
      },
    });

    const result = await service.claimDispatch(
      'user-1',
      'dispatch-1',
      'fn_target',
    );

    assert.equal(result, null);
    const where = (captured.where ?? {}) as Record<string, unknown>;
    assert.equal(where.id, 'dispatch-1');
    assertFounderIdeNodeScope(where, 'fn_target');
  });

  it('prevents a sibling node from completing another laptop dispatch', async () => {
    let captured: Record<string, unknown> = {};
    const { service } = makeService({
      updateMany: async (args) => {
        captured = args;
        return { count: 1 };
      },
    });

    await service.markDispatched(
      'dispatch-1',
      '{"kind":"chat","delivered":true}',
      'user-1',
      'fn_target',
    );

    const where = (captured.where ?? {}) as Record<string, unknown>;
    assert.equal(where.id, 'dispatch-1');
    assert.equal(where.userId, 'user-1');
    assertFounderIdeNodeScope(where, 'fn_target');
  });

  it('reports a rejected chat delivery as failed', async () => {
    const { service } = makeService({
      findFirst: async () => ({
        id: 'dispatch-1',
        status: 'DISPATCHED',
        result: JSON.stringify({
          kind: 'chat',
          delivered: false,
          error: 'Chat view unavailable',
        }),
        dispatchedAt: new Date('2026-07-19T00:00:01.000Z'),
        createdAt: new Date('2026-07-19T00:00:00.000Z'),
        sessionId: 'founder-ide:fn_target',
      }),
    });

    const status = await service.getDispatchStatus('user-1', 'dispatch-1');

    assert.equal(status.delivered, false);
    assert.equal(status.failed, true);
  });
});
