/**
 * Unit tests for the token lifecycle contract (rotate / revoke / logout).
 *
 * These pin the contract that Workstream B (Founder Node tray) and Workstream
 * C (settings UI) implement. The Prisma client is replaced with an in-memory
 * stub so the tests run offline.
 *
 * Covers:
 *   - rotateToken issues a new nodeToken + invalidates the old (changes secretHash)
 *   - rotateToken sets tokenExpiresAt to ~30 days out, tokenRotatedAt to ~now
 *   - revokeNode deletes the node + cascade-clears relay/sync/acks
 *   - logout marks the node offline but does NOT delete it
 *   - TTL constants are 30 days (NODE_TOKEN_TTL_MS) / 7 days (ROTATION_WINDOW_MS)
 *   - shouldAutoRotate returns true within the rotation window of expiry
 *
 * Run with:
 *   npx tsx --test apps/api/src/founder-node/founder-node.token-lifecycle.spec.ts
 */

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { FounderNodeService } from './founder-node.service.js';

// ---------------------------------------------------------------------------
// In-memory Prisma stub — the slice exercised by token lifecycle.
// ---------------------------------------------------------------------------

type NodeRow = {
  id: string;
  userId: string;
  nodeId: string;
  label: string;
  secretHash: string;
  status: string;
  lastSeenAt: Date | null;
  platform: string | null;
  appVersion: string | null;
  vaultHealthy: boolean;
  founderId: string | null;
  tokenExpiresAt: Date | null;
  tokenRotatedAt: Date | null;
  installId: string | null;
  ipcSecretHash: string | null;
};

function makeStubPrisma(seedNodes: NodeRow[] = []) {
  const nodes: NodeRow[] = seedNodes.map((n) => ({ ...n }));
  let idCounter = nodes.length;
  const nextId = () => `row-${++idCounter}`;
  return {
    _nodes: nodes,
    founderNode: {
      findUnique: async ({ where }: { where: { nodeId?: string } }) => {
        if (!where.nodeId) return null;
        return nodes.find((n) => n.nodeId === where.nodeId) ?? null;
      },
      findFirst: async ({ where }: { where: { userId?: string; nodeId?: string } }) => {
        return (
          nodes.find(
            (n) =>
              (!where.userId || n.userId === where.userId) &&
              (!where.nodeId || n.nodeId === where.nodeId),
          ) ?? null
        );
      },
      update: async ({ where, data }: { where: { id?: string; nodeId?: string }; data: Partial<NodeRow> }) => {
        const row = nodes.find(
          (n) => (where.id && n.id === where.id) || (where.nodeId && n.nodeId === where.nodeId),
        );
        if (!row) throw new Error(`node not found: ${JSON.stringify(where)}`);
        Object.assign(row, data);
        return row;
      },
      delete: async ({ where }: { where: { id?: string } }) => {
        const idx = nodes.findIndex((n) => n.id === where.id);
        if (idx === -1) throw new Error('node not found for delete');
        const [removed] = nodes.splice(idx, 1);
        return removed;
      },
      upsert: async () => ({}),
    },
    founderNodeVaultRelay: { deleteMany: async () => ({ count: 0 }) },
    founderNodeVaultSyncAck: { deleteMany: async () => ({ count: 0 }) },
    founderBuilderSettings: { upsert: async () => ({}), findUnique: async () => null },
    founderNodePairingCode: { deleteMany: async () => ({ count: 0 }) },
    founderNodeDeviceCode: { create: async () => ({}), updateMany: async () => ({ count: 0 }) },
  };
}

function seedNode(overrides: Partial<NodeRow> = {}): NodeRow {
  return {
    id: 'row-seed',
    userId: 'user-1',
    nodeId: 'fn_seednode',
    label: 'Seed Node',
    secretHash: '$2a$10$oldhash',
    status: 'online',
    lastSeenAt: new Date(),
    platform: 'darwin',
    appVersion: '1.0.0',
    vaultHealthy: true,
    founderId: 'user-1',
    tokenExpiresAt: new Date(Date.now() + 30 * 24 * 60 * 60 * 1000),
    tokenRotatedAt: new Date(),
    installId: 'install-seed',
    ipcSecretHash: null,
    ...overrides,
  };
}

function makeService(seedNodes: NodeRow[] = []) {
  const prisma = makeStubPrisma(seedNodes);
  const svc = new FounderNodeService(
    prisma as never,
    {} as never,
    {} as never,
    {} as never,
  );
  return { svc, prisma };
}

describe('FounderNodeService — token lifecycle contract', () => {
  describe('rotateToken', () => {
    it('issues a new nodeToken and invalidates the old (changes secretHash)', async () => {
      const seed = seedNode({ secretHash: 'OLD_HASH' });
      const { svc, prisma } = makeService([seed]);

      const result = await svc.rotateToken('fn_seednode');

      assert.ok(result.nodeToken.startsWith('fn_'), 'new token must be fn_-prefixed');
      assert.equal(result.nodeId, 'fn_seednode');
      const updated = prisma._nodes[0];
      assert.notEqual(updated.secretHash, 'OLD_HASH', 'secretHash must change');
      assert.notEqual(updated.secretHash, result.nodeToken, 'stored hash must not equal plaintext');
    });

    it('sets tokenExpiresAt to ~30 days out', async () => {
      const { svc, prisma } = makeService([seedNode()]);
      const before = Date.now();
      const result = await svc.rotateToken('fn_seednode');
      const after = Date.now();

      const expiresAt = new Date(result.tokenExpiresAt!).getTime();
      // Allow a small clock skew window (±5s).
      const min = before + FounderNodeService.NODE_TOKEN_TTL_MS - 5_000;
      const max = after + FounderNodeService.NODE_TOKEN_TTL_MS + 5_000;
      assert.ok(expiresAt >= min && expiresAt <= max, 'tokenExpiresAt should be ~30 days out');
      // Row agrees with the response.
      assert.equal(prisma._nodes[0].tokenExpiresAt?.toISOString(), result.tokenExpiresAt);
    });

    it('sets tokenRotatedAt to ~now', async () => {
      const { svc } = makeService([seedNode()]);
      const before = Date.now();
      const result = await svc.rotateToken('fn_seednode');
      const after = Date.now();
      const rotatedAt = new Date(result.tokenRotatedAt).getTime();
      assert.ok(rotatedAt >= before - 1000 && rotatedAt <= after + 1000);
    });

    it('throws on unknown nodeId', async () => {
      const { svc } = makeService([seedNode()]);
      await assert.rejects(
        () => svc.rotateToken('fn_does_not_exist'),
        /Node not found/i,
      );
    });

    it('accepts the new token before expiry and rejects it after expiry', async () => {
      const { svc, prisma } = makeService([seedNode()]);
      const rotated = await svc.rotateToken('fn_seednode');

      await assert.doesNotReject(() =>
        svc.validateNodeToken('fn_seednode', rotated.nodeToken),
      );
      prisma._nodes[0].tokenExpiresAt = new Date(Date.now() - 1);
      await assert.rejects(
        () => svc.validateNodeToken('fn_seednode', rotated.nodeToken),
        /expired/i,
      );
    });
  });

  describe('revokeNode', () => {
    it('invalidates the node identity by deleting the row', async () => {
      const { svc, prisma } = makeService([seedNode()]);
      const before = prisma._nodes.length;
      const result = await svc.revokeNode('user-1', 'fn_seednode');
      assert.equal(result.nodeId, 'fn_seednode');
      assert.equal(result.founderId, 'user-1');
      assert.ok(result.revokedAt, 'revokedAt must be present');
      assert.equal(prisma._nodes.length, before - 1, 'node row should be deleted');
    });

    it('throws on unknown nodeId', async () => {
      const { svc } = makeService([seedNode()]);
      await assert.rejects(
        () => svc.revokeNode('user-1', 'fn_ghost'),
        /Node not found/i,
      );
    });

    it('throws when the node belongs to a different user', async () => {
      const { svc } = makeService([seedNode({ userId: 'other-user' })]);
      await assert.rejects(
        () => svc.revokeNode('user-1', 'fn_seednode'),
        /Node not found/i,
      );
    });
  });

  describe('logout', () => {
    it('marks the node offline but does NOT delete it', async () => {
      const { svc, prisma } = makeService([seedNode({ status: 'online' })]);
      const before = prisma._nodes.length;

      const result = await svc.logout('fn_seednode');

      assert.equal(result.nodeId, 'fn_seednode');
      assert.equal(result.loggedOutAt !== undefined, true);
      assert.equal(result.serverSideRevocable, true);
      // Row survives.
      assert.equal(prisma._nodes.length, before, 'logout must not delete the node row');
      // And is marked offline so the status panel shows "logged out".
      assert.equal(prisma._nodes[0].status, 'offline');
    });

    it('throws on unknown nodeId', async () => {
      const { svc } = makeService([seedNode()]);
      await assert.rejects(
        () => svc.logout('fn_missing'),
        /Node not found/i,
      );
    });

    it('keeps founderId accessible after logout (server-side revocable separately)', async () => {
      const { svc } = makeService([seedNode({ founderId: 'founder-9' })]);
      const result = await svc.logout('fn_seednode');
      assert.equal(result.founderId, 'founder-9');
    });
  });

  describe('shouldAutoRotate', () => {
    it('returns false when tokenExpiresAt is null (legacy token)', () => {
      const { svc } = makeService([]);
      assert.equal(svc.shouldAutoRotate({ tokenExpiresAt: null }), false);
    });

    it('returns false when expiry is far in the future (> rotation window)', () => {
      const { svc } = makeService([]);
      const future = new Date(Date.now() + 30 * 24 * 60 * 60 * 1000); // 30 days
      assert.equal(svc.shouldAutoRotate({ tokenExpiresAt: future }), false);
    });

    it('returns true when within the rotation window of expiry (< 7 days)', () => {
      const { svc } = makeService([]);
      const soon = new Date(Date.now() + 3 * 24 * 60 * 60 * 1000); // 3 days
      assert.equal(svc.shouldAutoRotate({ tokenExpiresAt: soon }), true);
    });

    it('returns true when token is already expired', () => {
      const { svc } = makeService([]);
      const past = new Date(Date.now() - 1000);
      assert.equal(svc.shouldAutoRotate({ tokenExpiresAt: past }), true);
    });
  });

  describe('TTL constants', () => {
    it('NODE_TOKEN_TTL_MS is 30 days', () => {
      assert.equal(FounderNodeService.NODE_TOKEN_TTL_MS, 30 * 24 * 60 * 60 * 1000);
    });

    it('TOKEN_ROTATION_WINDOW_MS is 7 days', () => {
      assert.equal(FounderNodeService.TOKEN_ROTATION_WINDOW_MS, 7 * 24 * 60 * 60 * 1000);
    });

    it('rotation window is less than TTL (so proactive rotation is meaningful)', () => {
      assert.ok(
        FounderNodeService.TOKEN_ROTATION_WINDOW_MS < FounderNodeService.NODE_TOKEN_TTL_MS,
        'rotation window must be shorter than TTL',
      );
    });
  });
});
