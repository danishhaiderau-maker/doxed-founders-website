import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { FounderNodeService } from './founder-node.service.js';

type PairingRow = {
  id: string;
  userId: string;
  code: string;
  usedAt: Date | null;
  expiresAt: Date;
};

type NodeRow = {
  id: string;
  userId: string;
  nodeId: string;
  label: string;
  secretHash: string;
  platform?: string | null;
  appVersion?: string | null;
  installId?: string | null;
};

function makeService(opts?: { existingNodeOwner?: string }) {
  const pairing: PairingRow = {
    id: 'pair-1',
    userId: 'founder-1',
    code: 'ABCD2345',
    usedAt: null,
    expiresAt: new Date(Date.now() + 60_000),
  };
  const nodes: NodeRow[] = opts?.existingNodeOwner
    ? [
        {
          id: 'node-row-1',
          userId: opts.existingNodeOwner,
          nodeId: 'node_0123456789abcdef',
          label: 'Existing node',
          secretHash: 'existing',
        },
      ]
    : [];

  const prisma = {
    founderNodePairingCode: {
      findUnique: async ({ where }: { where: { code: string } }) =>
        where.code === pairing.code ? pairing : null,
      updateMany: async ({
        where,
        data,
      }: {
        where: {
          id: string;
          usedAt: null;
          expiresAt: { gt: Date };
        };
        data: { usedAt: Date };
      }) => {
        const matches =
          where.id === pairing.id &&
          pairing.usedAt === null &&
          pairing.expiresAt > where.expiresAt.gt;
        if (matches) pairing.usedAt = data.usedAt;
        return { count: matches ? 1 : 0 };
      },
    },
    founderNode: {
      findUnique: async ({ where }: { where: { nodeId: string } }) =>
        nodes.find((node) => node.nodeId === where.nodeId) ?? null,
      upsert: async ({
        where,
        create,
        update,
      }: {
        where: { nodeId: string };
        create: NodeRow;
        update: Partial<NodeRow>;
      }) => {
        const existing = nodes.find((node) => node.nodeId === where.nodeId);
        if (existing) {
          Object.assign(existing, update);
          return existing;
        }
        const created = { ...create, id: 'node-row-created' };
        nodes.push(created);
        return created;
      },
    },
    founderBuilderSettings: {
      upsert: async () => ({}),
    },
    $transaction: async <T>(callback: (tx: unknown) => Promise<T>) =>
      callback(prisma),
  };

  const service = new FounderNodeService(
    prisma as never,
    {} as never,
    {} as never,
    {} as never,
  );
  return { service, pairing, nodes };
}

const request = {
  code: 'abcd2345',
  nodeId: 'node_0123456789abcdef',
  label: `  ${'Founder desktop '.repeat(8)}  `,
  platform: `windows${'x'.repeat(80)}`,
  appVersion: `0.9.4${'x'.repeat(80)}`,
  installId: `install-${'x'.repeat(180)}`,
  ipcSecret: 'a'.repeat(64),
};

describe('Founder Node legacy pairing compatibility path', () => {
  it('consumes a code once and bounds untrusted device metadata', async () => {
    const { service, pairing, nodes } = makeService();

    const paired = await service.pair(request);

    assert.equal(paired.nodeId, request.nodeId);
    assert.ok(pairing.usedAt instanceof Date);
    assert.equal(nodes.length, 1);
    assert.equal(nodes[0]!.label.length, 80);
    assert.equal(nodes[0]!.platform?.length, 40);
    assert.equal(nodes[0]!.appVersion?.length, 40);
    assert.equal(nodes[0]!.installId?.length, 128);

    await assert.rejects(service.pair(request), /Invalid or expired pairing code/);
  });

  it('refuses to transfer an existing node identity across founders', async () => {
    const { service, pairing, nodes } = makeService({
      existingNodeOwner: 'founder-2',
    });

    await assert.rejects(service.pair(request), /Node identity is not available/);
    assert.equal(pairing.usedAt, null, 'rejected ownership must not consume the code');
    assert.equal(nodes[0]!.userId, 'founder-2');
  });
});
