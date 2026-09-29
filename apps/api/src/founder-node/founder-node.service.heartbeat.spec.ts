import assert from 'node:assert/strict';
import test from 'node:test';
import type { FounderNodeHeartbeat } from '@dcf/founder-vault';
import { FounderNodeService } from './founder-node.service';

type UpdateArgs = { where: Record<string, unknown>; data: Record<string, unknown> };

type HarnessOptions = {
  snapshotFailure?: Error;
  transactionFailures?: number;
};

function serviceHarness(options: HarnessOptions = {}) {
  const nodeUpdates: UpdateArgs[] = [];
  const snapshotCalls: unknown[][] = [];
  const transactionOptions: Array<Record<string, unknown> | undefined> = [];
  const vaultHeartbeats: Array<[string, string]> = [];
  let transactionAttempts = 0;
  const pairUpserts: Array<{
    where: Record<string, unknown>;
    create: Record<string, unknown>;
    update: Record<string, unknown>;
  }> = [];
  const prisma = {
    $transaction: async <T>(
      operation: (transaction: {
        founderNode: { update: (args: UpdateArgs) => Promise<Record<string, unknown>> };
      }) => Promise<T>,
      transactionOption?: Record<string, unknown>,
    ) => {
      transactionAttempts += 1;
      transactionOptions.push(transactionOption);
      if (transactionAttempts <= (options.transactionFailures ?? 0)) {
        throw Object.assign(new Error('write conflict'), { code: 'P2034' });
      }
      const stagedNodeUpdates: UpdateArgs[] = [];
      const transaction = {
        founderNode: {
          update: async (args: UpdateArgs) => {
            stagedNodeUpdates.push(args);
            return {
              id: 'db-node',
              userId: 'user-a',
              nodeId: 'node-a',
              label: 'Laptop',
              platform: 'win32',
            };
          },
        },
      };
      const result = await operation(transaction);
      nodeUpdates.push(...stagedNodeUpdates);
      return result;
    },
    founderNode: {
      update: async (args: UpdateArgs) => {
        nodeUpdates.push(args);
        return { id: 'db-node', userId: 'user-a', nodeId: 'node-a', label: 'Laptop', platform: 'win32' };
      },
      findUnique: async () => ({
        id: 'db-node',
        userId: 'user-a',
        nodeId: 'node-a',
        label: 'Laptop',
        platform: 'win32',
      }),
      upsert: async (args: {
        where: Record<string, unknown>;
        create: Record<string, unknown>;
        update: Record<string, unknown>;
      }) => {
        pairUpserts.push(args);
        return { id: 'db-node', userId: 'new-owner', nodeId: 'node-a' };
      },
    },
    founderNodePairingCode: {
      findUnique: async () => ({
        id: 'pair-code',
        userId: 'new-owner',
        usedAt: null,
        expiresAt: new Date('2100-01-01T00:00:00.000Z'),
      }),
      update: async () => ({}),
    },
    founderBuilderSettings: {
      upsert: async () => ({}),
    },
    founderNodeVaultRelay: {
      upsert: async () => ({}),
    },
  };
  const copilot = {
    saveDeviceMemorySync: async () => ({ success: true }),
  };
  const vaultSync = {
    onNodeHeartbeat: (userId: string, nodeId: string) => {
      vaultHeartbeats.push([userId, nodeId]);
    },
    recordRelayMerge: async () => undefined,
  };
  const desktopBridge = {
    saveBridgePayload: async (...args: unknown[]) => {
      snapshotCalls.push(args);
      if (options.snapshotFailure) throw options.snapshotFailure;
      return undefined;
    },
  };
  return {
    nodeUpdates,
    pairUpserts,
    snapshotCalls,
    transactionOptions,
    vaultHeartbeats,
    get transactionAttempts() {
      return transactionAttempts;
    },
    service: new FounderNodeService(
      prisma as never,
      copilot as never,
      vaultSync as never,
      desktopBridge as never,
    ),
  };
}

const principal = { userId: 'user-a', nodeId: 'node-a' };

function heartbeat(patch: Partial<FounderNodeHeartbeat> = {}): FounderNodeHeartbeat {
  return {
    nodeId: 'node-a',
    label: 'Laptop',
    platform: 'win32',
    appVersion: '1.0.124',
    vaultHealthy: true,
    ...patch,
  };
}

test('heartbeat persists sanitized capability with a server timestamp and rounded Int metrics', async () => {
  const {
    nodeUpdates,
    service,
    snapshotCalls,
    transactionOptions,
    vaultHeartbeats,
  } = serviceHarness();
  const before = Date.now();
  await service.heartbeat(
    'db-node',
    heartbeat({
      sessions: [],
      ramGb: 63.8,
      storageGb: 999.4,
      storageFreeGb: 512.6,
      ide: {
        provider: 'founder-ide-next',
        capabilityVersion: 1,
        capabilities: ['remote-build-v1', 'unapproved-terminal'],
      },
    }),
    principal,
  );
  const after = Date.now();
  const data = nodeUpdates[0].data;

  assert.deepEqual(nodeUpdates[0].where, {
    id: 'db-node',
    userId: 'user-a',
    nodeId: 'node-a',
  });
  assert.equal(data.ramGb, 64);
  assert.equal(data.storageGb, 999);
  assert.equal(data.storageFreeGb, 513);
  assert.equal(data.ideProvider, 'founder-ide-next');
  assert.equal(data.ideCapabilityVersion, 1);
  assert.deepEqual(data.ideCapabilities, ['remote-build-v1']);
  assert.ok(data.lastSeenAt instanceof Date);
  assert.ok(data.ideCapabilitiesAt instanceof Date);
  assert.equal(data.ideCapabilitiesAt.getTime(), data.lastSeenAt.getTime());
  assert.ok(data.ideCapabilitiesAt.getTime() >= before);
  assert.ok(data.ideCapabilitiesAt.getTime() <= after);
  assert.equal(transactionOptions[0]?.isolationLevel, 'Serializable');
  assert.equal(typeof (snapshotCalls[0][4] as { founderNode?: unknown }).founderNode, 'object');
  assert.deepEqual(vaultHeartbeats, [['user-a', 'node-a']]);
});

test('invalid heartbeat capability and non-finite metrics clear safely without throwing', async () => {
  const { nodeUpdates, service } = serviceHarness();
  await service.heartbeat(
    'db-node',
    heartbeat({
      sessions: [],
      ramGb: Number.NaN,
      storageGb: Number.POSITIVE_INFINITY,
      storageFreeGb: -1,
      ide: {
        provider: 'founder-ide-next',
        capabilityVersion: 2,
        capabilities: ['remote-build-v1'],
      },
    }),
    principal,
  );
  const data = nodeUpdates[0].data;

  assert.equal(data.ramGb, null);
  assert.equal(data.storageGb, null);
  assert.equal(data.storageFreeGb, null);
  assert.equal(data.ideProvider, null);
  assert.equal(data.ideCapabilityVersion, null);
  assert.deepEqual(data.ideCapabilities, []);
  assert.equal(data.ideCapabilitiesAt, null);
});

test('missing or malformed session snapshots clear remote eligibility even with a valid IDE claim', async () => {
  const { nodeUpdates, service, snapshotCalls } = serviceHarness();
  const validIde = {
    provider: 'founder-ide-next' as const,
    capabilityVersion: 1 as const,
    capabilities: ['remote-build-v1'],
  };

  await service.heartbeat('db-node', heartbeat({ sessions: [], ide: validIde }), principal);
  await service.heartbeat('db-node', heartbeat({ ide: validIde }), principal);
  await service.heartbeat(
    'db-node',
    heartbeat({ sessions: {} as never, ide: validIde }),
    principal,
  );

  assert.equal(nodeUpdates[0].data.ideProvider, 'founder-ide-next');
  for (const update of nodeUpdates.slice(1)) {
    assert.equal(update.data.ideProvider, null);
    assert.equal(update.data.ideCapabilityVersion, null);
    assert.deepEqual(update.data.ideCapabilities, []);
    assert.equal(update.data.ideCapabilitiesAt, null);
  }
  assert.equal((snapshotCalls[1][3] as { sessions?: unknown }).sessions, undefined);
  assert.equal((snapshotCalls[2][3] as { sessions?: unknown }).sessions, undefined);
});

test('snapshot persistence failure rolls back capability freshness and suppresses post-commit callbacks', async () => {
  const snapshotFailure = new Error('snapshot failed');
  const harness = serviceHarness({ snapshotFailure });

  await assert.rejects(
    harness.service.heartbeat(
      'db-node',
      heartbeat({
        sessions: [],
        ide: {
          provider: 'founder-ide-next',
          capabilityVersion: 1,
          capabilities: ['remote-build-v1'],
        },
      }),
      principal,
    ),
    snapshotFailure,
  );

  assert.equal(harness.transactionAttempts, 1);
  assert.equal(harness.nodeUpdates.length, 0);
  assert.equal(harness.snapshotCalls.length, 1);
  assert.equal(
    typeof (harness.snapshotCalls[0][4] as { founderNode?: unknown }).founderNode,
    'object',
  );
  assert.deepEqual(harness.vaultHeartbeats, []);
});

test('serializable heartbeat transactions retry P2034 conflicts at most three times', async () => {
  const recovered = serviceHarness({ transactionFailures: 2 });
  await recovered.service.heartbeat('db-node', heartbeat({ sessions: [] }), principal);

  assert.equal(recovered.transactionAttempts, 3);
  assert.equal(recovered.nodeUpdates.length, 1);
  assert.deepEqual(
    recovered.transactionOptions.map((option) => option?.isolationLevel),
    ['Serializable', 'Serializable', 'Serializable'],
  );
  assert.deepEqual(recovered.vaultHeartbeats, [['user-a', 'node-a']]);

  const exhausted = serviceHarness({ transactionFailures: 3 });
  await assert.rejects(
    exhausted.service.heartbeat('db-node', heartbeat({ sessions: [] }), principal),
    (error: unknown) =>
      error instanceof Error &&
      (error as Error & { code?: string }).code === 'P2034',
  );
  assert.equal(exhausted.transactionAttempts, 3);
  assert.equal(exhausted.nodeUpdates.length, 0);
  assert.deepEqual(exhausted.vaultHeartbeats, []);
});

test('pair upsert resets capability fields on both create and re-pair update paths', async () => {
  const { pairUpserts, service } = serviceHarness();
  await service.pair({ code: 'ABCDEFGH', nodeId: 'node-a', label: 'Re-paired laptop' });
  const upsert = pairUpserts[0];
  const reset = {
    ideProvider: null,
    ideCapabilityVersion: null,
    ideCapabilities: [],
    ideCapabilitiesAt: null,
  };

  assert.equal(upsert.update.userId, 'new-owner');
  assert.deepEqual(
    {
      ideProvider: upsert.update.ideProvider,
      ideCapabilityVersion: upsert.update.ideCapabilityVersion,
      ideCapabilities: upsert.update.ideCapabilities,
      ideCapabilitiesAt: upsert.update.ideCapabilitiesAt,
    },
    reset,
  );
  assert.deepEqual(
    {
      ideProvider: upsert.create.ideProvider,
      ideCapabilityVersion: upsert.create.ideCapabilityVersion,
      ideCapabilities: upsert.create.ideCapabilities,
      ideCapabilitiesAt: upsert.create.ideCapabilitiesAt,
    },
    reset,
  );
});

test('generic node sync refreshes lastSeenAt without refreshing capability timestamp', async () => {
  const { nodeUpdates, service } = serviceHarness();
  await service.syncFromNode('user-a', 'db-node', {} as never);
  const data = nodeUpdates[0].data;

  assert.ok(data.lastSeenAt instanceof Date);
  assert.equal(Object.hasOwn(data, 'ideCapabilitiesAt'), false);
  assert.equal(Object.hasOwn(data, 'ideProvider'), false);
  assert.equal(Object.hasOwn(data, 'ideCapabilities'), false);
});
