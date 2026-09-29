import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import { DesktopBridgeService } from './desktop-bridge.service';

type Session = {
  id: string;
  composerId: string;
  title: string;
  lastActiveAt: string;
  targetNodeId?: string;
};

type Workspace = {
  id: string;
  title: string;
  lastActiveAt: string;
  targetNodeId?: string;
};

function makeService(
  memoryGraphs: Record<string, unknown>,
  retryFailures = 0,
  onSerializationConflict?: (store: Map<string, { memoryGraph: unknown }>) => void,
) {
  const rows = new Map(
    Object.entries(memoryGraphs).map(([userId, memoryGraph]) => [userId, { memoryGraph }]),
  );
  let transactionCalls = 0;
  const prisma = {
    founderBuilderSettings: {
      findUnique: async ({ where }: { where: { userId: string } }) => {
        return rows.get(where.userId) ?? null;
      },
      upsert: async ({
        where,
        create,
        update,
      }: {
        where: { userId: string };
        create: { userId: string; memoryGraph: unknown };
        update: { memoryGraph: unknown };
      }) => {
        const row = rows.get(where.userId) ?? create;
        const next = { ...row, ...update };
        rows.set(where.userId, next);
        return next;
      },
    },
  };
  Object.assign(prisma, {
    $transaction: async <T>(operation: (transaction: typeof prisma) => Promise<T>) => {
      transactionCalls += 1;
      if (transactionCalls <= retryFailures) {
        onSerializationConflict?.(rows);
        throw Object.assign(new Error('serialization conflict'), { code: 'P2034' });
      }
      return operation(prisma);
    },
  });
  return Object.assign(new DesktopBridgeService(prisma as never), {
    transactionCalls: () => transactionCalls,
  });
}

function graph(nodes: Record<string, Session[]>) {
  return { _sessionsByNode: nodes };
}

const NOW = '2026-09-21T00:00:00.000Z';
const session = (id: string, composerId = id, targetNodeId?: string): Session => ({
  id,
  composerId,
  title: 'Cursor chat',
  lastActiveAt: NOW,
  ...(targetNodeId ? { targetNodeId } : {}),
});

describe('DesktopBridgeService.findDispatchTarget', () => {
  it('never resolves a session saved for another user', async () => {
    const service = makeService({
      owner: graph({ 'node-owner': [session('owner-session')] }),
      stranger: graph({ 'node-stranger': [session('stranger-session')] }),
    });

    assert.equal(
      await service.findDispatchTarget('stranger', 'owner-session'),
      undefined,
    );
  });

  it('returns undefined for unknown and cross-node ambiguous session aliases', async () => {
    const service = makeService({
      user: graph({
        'node-a': [session('session-a', 'shared-composer')],
        'node-b': [session('session-b', 'shared-composer')],
      }),
    });

    assert.equal(await service.findDispatchTarget('user', 'missing'), undefined);
    assert.equal(
      await service.findDispatchTarget('user', 'shared-composer'),
      undefined,
    );
  });

  it('uses an explicit node to disambiguate an alias', async () => {
    const service = makeService({
      user: graph({
        'node-a': [session('session-a', 'shared-composer')],
        'node-b': [session('session-b', 'shared-composer')],
      }),
    });

    const target = await service.findDispatchTarget(
      'user',
      'shared-composer',
      'node-b',
    );
    assert.equal(target?.nodeId, 'node-b');
    assert.equal(target?.session.id, 'session-b');
    assert.equal(
      await service.findDispatchTarget('user', 'shared-composer', 'missing-node'),
      undefined,
    );
  });

  it('binds listSessions targetNodeId from the owning bucket, not session data', async () => {
    const service = makeService({
      user: graph({
        'trusted-node': [session('session-a', 'composer-a', 'spoofed-node')],
      }),
    });

    const sessions = await service.listSessions('user');
    assert.equal((sessions[0] as Session | undefined)?.targetNodeId, 'trusted-node');
  });

  it('binds listWorkspaces targetNodeId from the owning bucket, not workspace data', async () => {
    const service = makeService({
      user: {
        _workspacesByNode: {
          'trusted-node': [
            { id: 'workspace-a', title: 'Repository', lastActiveAt: NOW, targetNodeId: 'spoofed-node' },
          ],
        },
      },
    });

    const workspaces = await service.listWorkspaces('user');
    assert.equal((workspaces[0] as Workspace | undefined)?.targetNodeId, 'trusted-node');
  });

  it('clears only the explicitly supplied node bucket when sessions or workspaces are empty', async () => {
    const service = makeService({
      user: {
        _sessionsByNode: {
          'node-a': [session('stale-a')],
          'node-b': [session('kept-b')],
        },
        _workspacesByNode: {
          'node-a': [{ id: 'stale-workspace', title: 'Stale repository', lastActiveAt: NOW }],
          'node-b': [{ id: 'kept-workspace', title: 'Kept repository', lastActiveAt: NOW }],
        },
      },
    });

    await service.saveBridgePayload('user', 'node-a', '', { sessions: [], workspaces: [] });

    assert.deepEqual(
      (await service.listSessions('user')).map((item) => item.id),
      ['kept-b'],
    );
    assert.deepEqual(
      (await service.listWorkspaces('user')).map((item) => item.id),
      ['kept-workspace'],
    );
  });

  it('retains prior buckets when sessions and workspaces are omitted', async () => {
    const service = makeService({
      user: {
        _sessionsByNode: { 'node-a': [session('retained-session')] },
        _workspacesByNode: {
          'node-a': [{ id: 'retained-workspace', title: 'Repository', lastActiveAt: NOW }],
        },
      },
    });

    await service.saveBridgePayload('user', 'node-a', '', {});

    assert.deepEqual(
      (await service.listSessions('user')).map((item) => item.id),
      ['retained-session'],
    );
    assert.deepEqual(
      (await service.listWorkspaces('user')).map((item) => item.id),
      ['retained-workspace'],
    );
  });

  it('uses the transaction callback and retries only bounded serialization conflicts', async () => {
    const service = makeService({ user: graph({}) }, 2);
    await service.saveSessions('user', 'node-a', [session('saved-after-retry')] as never);
    assert.equal(service.transactionCalls(), 3);
    assert.deepEqual(
      (await service.listSessions('user')).map((item) => item.id),
      ['saved-after-retry'],
    );

    const exhausted = makeService({ user: graph({}) }, 3);
    await assert.rejects(
      exhausted.saveSessions('user', 'node-a', [session('never-saved')] as never),
      (error: unknown) => (error as { code?: string }).code === 'P2034',
    );
    assert.equal(exhausted.transactionCalls(), 3);
  });

  it('preserves an interleaved node write when a retried clear re-reads the graph', async () => {
    const service = makeService(
      {
        user: graph({
          'node-a': [session('stale-session')],
          'node-b': [session('old-node-b')],
        }),
      },
      1,
      (store) => {
        store.set('user', {
          memoryGraph: graph({
            'node-a': [session('stale-session')],
            'node-b': [session('interleaved-node-b')],
          }),
        });
      },
    );

    await service.saveSessions('user', 'node-a', [] as never);
    assert.deepEqual(
      (await service.listSessions('user')).map((item) => item.id),
      ['interleaved-node-b'],
    );
    assert.equal(service.transactionCalls(), 2);
  });

  it('matches a normal encoded composer alias and safely handles malformed encoding', async () => {
    const service = makeService({
      user: graph({
        'node-a': [
          session('session-a', 'composer%2Fwith%20space'),
          session('session-b', '%E0%A4%A'),
        ],
      }),
    });

    const target = await service.findDispatchTarget('user', 'composer/with space');
    assert.equal(target?.nodeId, 'node-a');
    assert.equal(target?.session.id, 'session-a');
    assert.equal((await service.findDispatchTarget('user', '%E0%A4%A'))?.session.id, 'session-b');
  });
});
