import assert from 'node:assert/strict';
import { existsSync } from 'node:fs';
import { pathToFileURL } from 'node:url';
import { join } from 'node:path';
import test from 'node:test';
import { DesktopBridgeService } from '../desktop-bridge/desktop-bridge.service';
import { FounderNodeController } from '../founder-node/founder-node.controller';
import { IdeBridgeController } from './ide-bridge.controller';
import { IdeBridgeService } from './ide-bridge.service';
import {
  FOUNDER_REMOTE_CAPABILITY,
  FOUNDER_REMOTE_CAPABILITY_VERSION,
  FOUNDER_REMOTE_PENDING,
  FOUNDER_REMOTE_PROVIDER,
  founderRemoteEnvelopeHash,
} from './founder-remote-dispatch';

type DispatchRow = {
  id: string;
  userId: string;
  sessionId: string;
  prompt: string;
  ideProvider: string;
  status: string;
  result: string | null;
  error: string | null;
  dispatchedAt: Date | null;
  createdAt: Date;
  targetNodeId: string | null;
  capabilityVersion: number | null;
  targetCapability: string | null;
  envelopeHash: string | null;
  claimTokenHash: string | null;
  claimedAt: Date | null;
  expiresAt: Date | null;
};

type NodeRow = {
  userId: string;
  nodeId: string;
  status: string;
  lastSeenAt: Date;
  ideCapabilitiesAt: Date;
  ideProvider: string;
  ideCapabilityVersion: number;
  ideCapabilities: string[];
};

function matches(row: Record<string, unknown>, where: Record<string, unknown>): boolean {
  return Object.entries(where).every(([key, expected]) => {
    if (key === 'OR') return true;
    const actual = row[key];
    if (expected && typeof expected === 'object') {
      if ('gt' in expected) {
        return actual instanceof Date && actual.getTime() > (expected as { gt: Date }).gt.getTime();
      }
      if ('gte' in expected) {
        return actual instanceof Date && actual.getTime() >= (expected as { gte: Date }).gte.getTime();
      }
      if ('in' in expected) return (expected as { in: unknown[] }).in.includes(actual);
    }
    return actual === expected;
  });
}

function httpCode(error: unknown): string | undefined {
  const body = typeof (error as { getResponse?: unknown })?.getResponse === 'function'
    ? (error as { getResponse(): unknown }).getResponse()
    : undefined;
  return body && typeof body === 'object' ? (body as { code?: string }).code : undefined;
}

function remoteRow(patch: Partial<DispatchRow> = {}): DispatchRow {
  const base: DispatchRow = {
    id: 'legacy-remote',
    userId: 'owner',
    sessionId: 'session-a',
    prompt: 'remote request',
    ideProvider: FOUNDER_REMOTE_PROVIDER,
    status: FOUNDER_REMOTE_PENDING,
    result: null,
    error: null,
    dispatchedAt: null,
    createdAt: new Date(),
    targetNodeId: 'node-a',
    capabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION,
    targetCapability: FOUNDER_REMOTE_CAPABILITY,
    envelopeHash: null,
    claimTokenHash: null,
    claimedAt: null,
    expiresAt: new Date(Date.now() + 60_000),
  };
  const row = { ...base, ...patch };
  if (row.targetNodeId && row.capabilityVersion && row.targetCapability) {
    row.envelopeHash = founderRemoteEnvelopeHash({
      id: row.id,
      sessionId: row.sessionId,
      ideProvider: row.ideProvider,
      targetNodeId: row.targetNodeId,
      capabilityVersion: row.capabilityVersion,
      targetCapability: row.targetCapability,
      prompt: row.prompt,
    });
  }
  return row;
}

function harness() {
  const memoryGraphs = new Map<string, { memoryGraph: unknown }>();
  const rows: DispatchRow[] = [];
  const nodes: NodeRow[] = [
    {
      userId: 'owner', nodeId: 'node-a', status: 'online', lastSeenAt: new Date(), ideCapabilitiesAt: new Date(),
      ideProvider: FOUNDER_REMOTE_PROVIDER, ideCapabilityVersion: 1,
      ideCapabilities: [FOUNDER_REMOTE_CAPABILITY],
    },
    {
      userId: 'other-user', nodeId: 'node-b', status: 'online', lastSeenAt: new Date(), ideCapabilitiesAt: new Date(),
      ideProvider: FOUNDER_REMOTE_PROVIDER, ideCapabilityVersion: 1,
      ideCapabilities: [FOUNDER_REMOTE_CAPABILITY],
    },
    {
      userId: 'owner', nodeId: 'legacy-node', status: 'online', lastSeenAt: new Date(), ideCapabilitiesAt: new Date(),
      ideProvider: 'cursor', ideCapabilityVersion: 0, ideCapabilities: [],
    },
  ];
  let sequence = 0;
  const prisma = {
    founderBuilderSettings: {
      findUnique: async ({ where }: { where: { userId: string } }) => memoryGraphs.get(where.userId) ?? null,
      upsert: async ({ where, create, update }: {
        where: { userId: string }; create: { userId: string; memoryGraph: unknown }; update: { memoryGraph: unknown };
      }) => {
        const next = { ...(memoryGraphs.get(where.userId) ?? create), ...update };
        memoryGraphs.set(where.userId, next);
        return next;
      },
    },
    founderNode: {
      findFirst: async ({ where }: { where: Record<string, unknown> }) =>
        nodes.find((node) => matches(node as unknown as Record<string, unknown>, where)) ?? null,
    },
    pendingIdeDispatch: {
      create: async ({ data }: { data: Record<string, unknown> }) => {
        const row = {
          ...remoteRow(),
          ...data,
          id: typeof data.id === 'string' ? data.id : `dispatch-${++sequence}`,
          createdAt: data.createdAt instanceof Date ? data.createdAt : new Date(),
        } as DispatchRow;
        rows.push(row);
        return row;
      },
      findFirst: async ({ where }: { where: Record<string, unknown> }) =>
        rows.find((row) => matches(row as unknown as Record<string, unknown>, where)) ?? null,
      findMany: async ({ where, take, orderBy }: {
        where: Record<string, unknown>; take?: number; orderBy?: { createdAt?: 'asc' | 'desc' };
      }) => {
        const found = rows.filter((row) => matches(row as unknown as Record<string, unknown>, where));
        if (orderBy?.createdAt) found.sort((a, b) =>
          orderBy.createdAt === 'asc' ? a.createdAt.getTime() - b.createdAt.getTime() : b.createdAt.getTime() - a.createdAt.getTime());
        return take === undefined ? found : found.slice(0, take);
      },
      updateMany: async ({ where, data }: { where: Record<string, unknown>; data: Partial<DispatchRow> }) => {
        const found = rows.filter((row) => matches(row as unknown as Record<string, unknown>, where));
        found.forEach((row) => Object.assign(row, data));
        return { count: found.length };
      },
    },
  };
  Object.assign(prisma, {
    $transaction: async <T>(operation: (transaction: typeof prisma) => Promise<T>) => operation(prisma),
  });
  const desktop = new DesktopBridgeService(prisma as never);
  const ide = new IdeBridgeService(
    prisma as never,
    desktop,
    {} as never,
    {} as never,
    {} as never,
  );
  const founderController = new FounderNodeController(
    {} as never, {} as never, {} as never, {} as never, ide, {} as never,
  );
  const ideController = new IdeBridgeController(ide);
  const ownerNodeRequest = { founderNode: { userId: 'owner', nodeId: 'node-a' } };
  const otherNodeRequest = { founderNode: { userId: 'other-user', nodeId: 'node-b' } };
  const clientFetch = async (url: string, init?: { method?: string; body?: string; headers?: Record<string, string> }) => {
    const path = new URL(url).pathname;
    const body = init?.body ? JSON.parse(init.body) : undefined;
    const auth = init?.headers?.Authorization;
    const req = auth === 'FounderNode node-a:node-token' ? ownerNodeRequest : otherNodeRequest;
    try {
      let value: unknown;
      if (path === '/api/founder-node/pending-dispatches' && init?.method === 'GET') {
        value = await founderController.pendingDispatches(req as never);
      } else if (init?.method === 'POST' && /^\/api\/founder-node\/dispatch\/[^/]+\/claim$/.test(path)) {
        value = await founderController.claimDispatch(req as never, decodeURIComponent(path.split('/')[4]!), body);
      } else if (init?.method === 'POST' && /^\/api\/founder-node\/dispatch\/[^/]+\/complete$/.test(path)) {
        value = await founderController.completeDispatch(req as never, decodeURIComponent(path.split('/')[4]!), body);
      } else {
        return { ok: false, status: 404, text: async () => JSON.stringify({ message: 'not found' }) };
      }
      return { ok: true, status: 200, text: async () => JSON.stringify(value) };
    } catch (error) {
      return { ok: false, status: 409, text: async () => JSON.stringify({ code: httpCode(error) }) };
    }
  };

  return { desktop, ide, ideController, founderController, rows, nodes, ownerNodeRequest, otherNodeRequest, clientFetch };
}

async function seedTarget(h: ReturnType<typeof harness>) {
  await h.desktop.saveSessions('owner', 'node-a', [{
    id: 'session-a', composerId: 'composer-a', title: 'Founder IDE chat', lastActiveAt: new Date().toISOString(),
    ideProvider: FOUNDER_REMOTE_PROVIDER, restorable: true,
  }]);
}

test('frozen desktop client polls without executing, then approves exactly once and completes through both controllers', {
  skip: !process.env.FOUNDER_DESKTOP_REPO || !existsSync(join(process.env.FOUNDER_DESKTOP_REPO, 'server', 'founder-node-remote.mjs')),
}, async () => {
  const h = harness();
  await seedTarget(h);
  const created = await h.ideController.dispatchToIde(
    { id: 'owner' } as never, 'session-a',
    { prompt: 'Build the dashboard', ideProvider: FOUNDER_REMOTE_PROVIDER, targetNodeId: 'node-a' },
  );
  const remotePath = join(process.env.FOUNDER_DESKTOP_REPO!, 'server', 'founder-node-remote.mjs');
  const { FounderNodeRemoteClient } = await import(pathToFileURL(remotePath).href) as {
    FounderNodeRemoteClient: new (options: Record<string, unknown>) => {
      poll(): Promise<Array<{ id: string }>>; approve(id: string): Promise<{ status: string; result?: string }>;
    };
  };
  let executions = 0;
  const client = new FounderNodeRemoteClient({
    apiBaseUrl: 'http://127.0.0.1:3999', nodeId: 'node-a', nodeToken: 'node-token', fetchImpl: h.clientFetch,
    execute: async () => { executions += 1; return { message: 'Build complete', evidence: ['unit tests'] }; },
  });

  const pending = await client.poll();
  assert.deepEqual(pending.map((item) => item.id), [created.id]);
  assert.equal(executions, 0, 'polling must never execute a remote request');
  const [first, second] = await Promise.all([client.approve(created.id), client.approve(created.id)]);
  assert.equal(executions, 1, 'only approval may execute, and it executes once');
  assert.equal(first.status, 'complete');
  assert.equal(second.status, 'complete');
  const status = await h.ideController.getDispatchStatus({ id: 'owner' } as never, created.id);
  assert.equal((status as { executionStatus?: string }).executionStatus, 'complete');
  assert.equal(status.result, 'Build complete\nEvidence: unit tests');
  assert.equal(status.delivered, false);
});

test('declining through the frozen desktop client never invokes execution', {
  skip: !process.env.FOUNDER_DESKTOP_REPO || !existsSync(join(process.env.FOUNDER_DESKTOP_REPO, 'server', 'founder-node-remote.mjs')),
}, async () => {
  const h = harness();
  await seedTarget(h);
  const created = await h.ideController.dispatchToIde(
    { id: 'owner' } as never, 'session-a',
    { prompt: 'Do not run', ideProvider: FOUNDER_REMOTE_PROVIDER, targetNodeId: 'node-a' },
  );
  const remotePath = join(process.env.FOUNDER_DESKTOP_REPO!, 'server', 'founder-node-remote.mjs');
  const { FounderNodeRemoteClient } = await import(pathToFileURL(remotePath).href) as {
    FounderNodeRemoteClient: new (options: Record<string, unknown>) => {
      poll(): Promise<Array<{ id: string }>>; decline(id: string, reason: string): Promise<{ status: string }>;
    };
  };
  let executions = 0;
  const client = new FounderNodeRemoteClient({
    apiBaseUrl: 'http://127.0.0.1:3999', nodeId: 'node-a', nodeToken: 'node-token', fetchImpl: h.clientFetch,
    execute: async () => { executions += 1; return 'must not happen'; },
  });
  await client.poll();
  assert.equal((await client.decline(created.id, 'No thanks')).status, 'declined');
  assert.equal(executions, 0);
  assert.equal(
    (await h.ideController.getDispatchStatus({ id: 'owner' } as never, created.id) as { executionStatus?: string }).executionStatus,
    'failed',
  );
});

test('controller claims and completes only for the authenticated owner/node, with remote claim-body validation', async () => {
  const h = harness();
  await seedTarget(h);
  const created = await h.ideController.dispatchToIde(
    { id: 'owner' } as never, 'session-a',
    { prompt: 'Controller completion', ideProvider: FOUNDER_REMOTE_PROVIDER, targetNodeId: 'node-a' },
  );
  const validBody = {
    targetNodeId: 'node-a', ideProvider: FOUNDER_REMOTE_PROVIDER,
    capabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION, targetCapability: FOUNDER_REMOTE_CAPABILITY,
  };
  await assert.rejects(
    h.founderController.claimDispatch(h.ownerNodeRequest as never, created.id, { ...validBody, targetNodeId: 'node-b' }),
    (error: unknown) => httpCode(error) === 'INVALID_CLAIM',
  );
  await assert.rejects(
    h.founderController.claimDispatch(h.otherNodeRequest as never, created.id, validBody),
    (error: unknown) => httpCode(error) === 'INVALID_CLAIM',
  );
  const claim = await h.founderController.claimDispatch(
    h.ownerNodeRequest as never, created.id, validBody,
  ) as unknown as { claimToken: string };
  await assert.rejects(
    h.founderController.completeDispatch(h.otherNodeRequest as never, created.id, { claimToken: claim.claimToken, result: 'wrong' }),
    (error: unknown) => httpCode(error) === 'INVALID_CLAIM',
  );
  const completed = await h.founderController.completeDispatch(
    h.ownerNodeRequest as never, created.id, { claimToken: claim.claimToken, result: 'authenticated completion' },
  ) as { executionStatus: string; result: string };
  assert.equal(completed.executionStatus, 'complete');
  assert.equal(completed.result, 'authenticated completion');
});

test('expired remote rows and stale registered nodes are blocked, and legacy polling cannot consume REMOTE rows', async () => {
  const h = harness();
  await seedTarget(h);
  const created = await h.ideController.dispatchToIde(
    { id: 'owner' } as never, 'session-a',
    { prompt: 'Expire me', ideProvider: FOUNDER_REMOTE_PROVIDER, targetNodeId: 'node-a' },
  );
  const expiring = h.rows.find((row) => row.id === created.id)!;
  expiring.expiresAt = new Date(Date.now() - 1);
  assert.deepEqual(await h.founderController.pendingDispatches(h.ownerNodeRequest as never), []);

  h.rows.push(remoteRow({ id: 'remote-on-legacy', targetNodeId: 'legacy-node' }));
  assert.deepEqual(await h.ide.getPendingDispatches('owner', 'legacy-node'), []);

  h.nodes.find((node) => node.nodeId === 'node-a')!.ideCapabilitiesAt = new Date(Date.now() - 181_000);
  await assert.rejects(
    h.ideController.dispatchToIde(
      { id: 'owner' } as never, 'session-a',
      { prompt: 'Blocked stale node', ideProvider: FOUNDER_REMOTE_PROVIDER, targetNodeId: 'node-a' },
    ),
    (error: unknown) => (error as { getStatus?: () => number }).getStatus?.() === 409,
  );
});
