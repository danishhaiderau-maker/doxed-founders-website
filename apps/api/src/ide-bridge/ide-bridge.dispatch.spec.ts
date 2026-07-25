import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { NotFoundException } from '@nestjs/common';
import { IdeBridgeService } from './ide-bridge.service';
import { DesktopBridgeService } from '../desktop-bridge/desktop-bridge.service';

type Query = Record<string, unknown>;

function serviceWith(options?: {
  ownerNodeId?: string;
  claimed?: boolean;
  statusResult?: string;
}) {
  const calls = {
    creates: [] as Query[],
    findMany: [] as Query[],
    findFirst: [] as Query[],
    updateMany: [] as Query[],
  };
  let availableToClaim = options?.claimed !== false;
  const row: {
    id: string;
    userId: string;
    nodeId: string;
    claimedByNodeId: string | null;
    sessionId: string;
    prompt: string;
    ideProvider: string;
    status: string;
    result: string | null;
    dispatchedAt: Date | null;
    createdAt: Date;
  } = {
    id: 'dispatch-1',
    userId: 'user-1',
    nodeId: 'node-a',
    claimedByNodeId: null,
    sessionId: 'founder-ide:node-a',
    prompt: 'hello',
    ideProvider: 'founder-ide',
    status: 'PENDING',
    result: options?.statusResult ?? null,
    dispatchedAt: options?.statusResult ? new Date() : null,
    createdAt: new Date(),
  };
  if (options?.statusResult) row.status = 'DISPATCHED';

  const model = {
    async create(args: Query) {
      calls.creates.push(args);
      return { id: row.id, status: 'PENDING' };
    },
    async findMany(args: Query) {
      calls.findMany.push(args);
      const where = (args.where ?? {}) as Query;
      if (where.status === 'PENDING' && where.sessionId) return [];
      if (where.status === 'PENDING') {
        return where.nodeId === 'node-a' ? [row] : [];
      }
      return [];
    },
    async findFirst(args: Query) {
      calls.findFirst.push(args);
      const where = (args.where ?? {}) as Query;
      if (where.id !== row.id || where.userId !== row.userId) return null;
      if (where.nodeId && where.nodeId !== row.nodeId) return null;
      if (where.status === 'PENDING') {
        return availableToClaim ? row : null;
      }
      return row;
    },
    async updateMany(args: Query) {
      calls.updateMany.push(args);
      const where = (args.where ?? {}) as Query;
      if (
        where.id === row.id &&
        where.userId === row.userId &&
        where.nodeId === row.nodeId &&
        where.status === 'PENDING' &&
        availableToClaim
      ) {
        availableToClaim = false;
        row.status = 'DISPATCHING';
        row.claimedByNodeId = row.nodeId;
        return { count: 1 };
      }
      if (
        where.id === row.id &&
        where.userId === row.userId &&
        where.nodeId === row.nodeId &&
        where.claimedByNodeId === row.nodeId &&
        where.status === 'DISPATCHING' &&
        row.status === 'DISPATCHING'
      ) {
        row.status = 'DISPATCHED';
        return { count: 1 };
      }
      return { count: 0 };
    },
  };
  const desktopBridge = {
    async findSessionOwnerNodeId() {
      return options?.ownerNodeId ?? 'node-a';
    },
  };
  const service = new IdeBridgeService(
    { pendingIdeDispatch: model } as never,
    desktopBridge as never,
    {} as never,
    {} as never,
    {} as never,
  );
  return { service, calls, row };
}

describe('IDE bridge node-scoped dispatch', () => {
  it('selects the computer with the newest matching session heartbeat', async () => {
    const memoryGraph = {
      _sessionsByNode: {
        'node-old': [
          {
            id: 'shared-session',
            title: 'Old',
            ideProvider: 'cursor',
            restorable: true,
            lastActiveAt: '2026-07-24T10:00:00.000Z',
            messages: [{ role: 'user', text: 'more history', at: null }],
          },
        ],
        'node-new': [
          {
            id: 'shared-session',
            title: 'New',
            ideProvider: 'cursor',
            restorable: true,
            lastActiveAt: '2026-07-24T10:05:00.000Z',
          },
        ],
      },
    };
    const desktop = new DesktopBridgeService({
      founderBuilderSettings: {
        async findUnique() {
          return { memoryGraph };
        },
      },
    } as never);
    assert.equal(
      await desktop.findSessionOwnerNodeId('user-1', 'shared-session'),
      'node-new',
    );
  });

  it('derives the target node from the authenticated session snapshot', async () => {
    const { service, calls } = serviceWith();
    await service.createDispatch(
      'user-1',
      'founder-ide:node-a',
      'Review this change',
      'founder-ide',
    );
    const data = (calls.creates[0]?.data ?? {}) as Query;
    assert.equal(data.nodeId, 'node-a');
    assert.equal(data.prompt, 'Review this change');
  });

  it('keeps structured Founder IDE actions valid JSON', async () => {
    const { service, calls } = serviceWith();
    const prompt = JSON.stringify({
      founderIdeAction: {
        type: 'commandRequest',
        command: 'npm test',
      },
    });
    await service.createDispatch(
      'user-1',
      'founder-ide:node-a',
      prompt,
      'founder-ide',
    );
    const data = (calls.creates[0]?.data ?? {}) as Query;
    assert.deepEqual(JSON.parse(String(data.prompt)), JSON.parse(prompt));
  });

  it('retains visible relay attribution for Cursor prompts', async () => {
    const { service, calls } = serviceWith();
    await service.createDispatch(
      'user-1',
      'cursor-session',
      'Review this change',
      'cursor',
    );
    const data = (calls.creates[0]?.data ?? {}) as Query;
    assert.match(String(data.prompt), /^\[Founder OS/);
  });

  it('rejects a session that no paired node advertised', async () => {
    const { service } = serviceWith({ ownerNodeId: '' });
    await assert.rejects(
      service.createDispatch(
        'user-1',
        'forged-session',
        'hello',
        'founder-ide',
      ),
      NotFoundException,
    );
  });

  it('returns pending work only to the selected node', async () => {
    const { service } = serviceWith();
    assert.equal(
      (await service.getPendingDispatches('user-1', 'node-a')).length,
      1,
    );
    assert.equal(
      (await service.getPendingDispatches('user-1', 'node-b')).length,
      0,
    );
  });

  it('allows exactly one atomic claim and records the claiming node', async () => {
    const { service, calls } = serviceWith();
    assert.ok(await service.claimDispatch('user-1', 'node-a', 'dispatch-1'));
    assert.equal(
      await service.claimDispatch('user-1', 'node-a', 'dispatch-1'),
      null,
    );
    const claim = calls.updateMany[0]!;
    assert.equal((claim.data as Query).claimedByNodeId, 'node-a');
  });

  it('rejects cross-node completion and preserves full command output', async () => {
    const { service, calls } = serviceWith();
    await service.claimDispatch('user-1', 'node-a', 'dispatch-1');
    await assert.rejects(
      service.markDispatched(
        'user-1',
        'node-b',
        'dispatch-1',
        '{"kind":"command","exitCode":0}',
      ),
      NotFoundException,
    );

    const longResult = JSON.stringify({
      kind: 'command',
      exitCode: 0,
      stdout: 'x'.repeat(20_000),
    });
    await service.markDispatched(
      'user-1',
      'node-a',
      'dispatch-1',
      longResult,
    );
    const completion = calls.updateMany.at(-1)!;
    assert.equal((completion.data as Query).result, longResult);
  });

  it('reports authenticated Founder IDE JSON completion as delivered', async () => {
    const { service } = serviceWith({
      statusResult: JSON.stringify({ kind: 'chat', delivered: true }),
    });
    const status = await service.getDispatchStatus('user-1', 'dispatch-1');
    assert.equal(status.delivered, true);
    assert.equal(status.failed, false);
  });
});
