/**
 * Unit tests for FounderNodeService.getRuntimeStatus (Phase 3 / Workstream C).
 *
 * Pins the truthful-runtime-status contract: every field is derived from a
 * documented source, the method never throws on missing data, and staleness
 * rules match the contract at packages/founder-vault/src/status-schema.ts.
 *
 * The Prisma client + DesktopBridgeService are replaced with in-memory stubs
 * so the tests run offline. Run with:
 *   npx tsx --test apps/api/src/founder-node/founder-node.runtime-status.spec.ts
 */

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import type { FounderStackRuntimeStatus } from '@dcf/founder-vault';
import { FOUNDER_STACK_UPDATE_STATES, EXECUTION_CONSENT_STATES } from '@dcf/founder-vault';
import { FounderNodeService } from './founder-node.service.js';

// ─── Stubs ──────────────────────────────────────────────────────────────────

type NodeRow = {
  id: string;
  userId: string;
  nodeId: string;
  label: string;
  status: string;
  lastSeenAt: Date | null;
  appVersion: string | null;
  founderId: string | null;
  tokenExpiresAt: Date | null;
  tokenRotatedAt: Date | null;
  installId: string | null;
  gatewayReachable?: boolean;
};

type Workspace = {
  id: string;
  title: string;
  repository?: string;
  ideProvider: string;
  lastActiveAt: string;
};

type Bridge = { updatedAt: string };

function makeStubPrisma(seedNodes: NodeRow[] = []) {
  const nodes: NodeRow[] = seedNodes.map((n) => ({ ...n }));
  return {
    founderNode: {
      findMany: async ({ where }: { where: { userId?: string; status?: { not?: string } } }) => {
        return nodes.filter((n) => {
          if (where.userId && n.userId !== where.userId) return false;
          if (where.status?.not && n.status === where.status.not) return false;
          return true;
        });
      },
    },
  };
}

function makeStubDesktopBridge(bridges: Bridge[] = [], workspaces: Workspace[] = []) {
  return {
    listForUser: async () => bridges,
    listWorkspaces: async () => workspaces,
  };
}

function makeService(
  seedNodes: NodeRow[] = [],
  bridges: Bridge[] = [],
  workspaces: Workspace[] = [],
) {
  const prisma = makeStubPrisma(seedNodes);
  const desktopBridge = makeStubDesktopBridge(bridges, workspaces);
  const svc = new FounderNodeService(
    prisma as never,
    {} as never,
    {} as never,
    desktopBridge as never,
  );
  return { svc, prisma, desktopBridge };
}

// ─── Required-field pinning ─────────────────────────────────────────────────

const REQUIRED_FIELDS: ReadonlyArray<keyof FounderStackRuntimeStatus> = [
  'installedVersion',
  'latestVersion',
  'founderNodeOnline',
  'ideHandshakeActive',
  'gatewayReachable',
  'paired',
  'workspace',
  'lastHeartbeat',
  'updateState',
  'executionConsentState',
];

// ─── Tests ──────────────────────────────────────────────────────────────────

describe('FounderNodeService.getRuntimeStatus — contract shape', () => {
  it('returns all 10 required fields with no extras', async () => {
    const { svc } = makeService();
    const result = await svc.getRuntimeStatus('user-1', '0.9.1');
    const keys = Object.keys(result).sort();
    const expected = [...REQUIRED_FIELDS].map((k) => k as string).sort();
    assert.deepEqual(keys, expected);
  });

  it('never throws when no data exists (safe defaults)', async () => {
    const { svc } = makeService();
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.installedVersion, '');
    assert.equal(result.latestVersion, '');
    assert.equal(result.founderNodeOnline, false);
    assert.equal(result.ideHandshakeActive, false);
    assert.equal(result.gatewayReachable, false);
    assert.equal(result.paired, false);
    assert.equal(result.workspace, null);
    // lastHeartbeat must be a parseable ISO string (epoch zero when no data).
    assert.equal(typeof result.lastHeartbeat, 'string');
    assert.ok(!Number.isNaN(Date.parse(result.lastHeartbeat)));
    // Hardcoded placeholders per Workstream C spec (until B/E wire live values).
    assert.ok(FOUNDER_STACK_UPDATE_STATES.includes(result.updateState));
    assert.ok(EXECUTION_CONSENT_STATES.includes(result.executionConsentState));
  });
});

describe('FounderNodeService.getRuntimeStatus — field sources', () => {
  it('installedVersion comes from the latest node heartbeat appVersion', async () => {
    const { svc } = makeService([
      {
        id: 'row-1',
        userId: 'user-1',
        nodeId: 'fn_1',
        label: 'node 1',
        status: 'online',
        lastSeenAt: new Date(),
        appVersion: '0.9.0',
        founderId: 'user-1',
        tokenExpiresAt: null,
        tokenRotatedAt: null,
        installId: null,
      },
    ]);
    const result = await svc.getRuntimeStatus('user-1', '0.9.1');
    assert.equal(result.installedVersion, '0.9.0');
  });

  it('latestVersion is plumbed through from the controller manifest cache', async () => {
    const { svc } = makeService();
    const result = await svc.getRuntimeStatus('user-1', '1.2.3');
    assert.equal(result.latestVersion, '1.2.3');
  });

  it('founderNodeOnline is true when lastSeenAt is within ONLINE_WINDOW_MS', async () => {
    const { svc } = makeService([
      {
        id: 'row-1',
        userId: 'user-1',
        nodeId: 'fn_1',
        label: 'node 1',
        status: 'online',
        lastSeenAt: new Date(Date.now() - 60_000), // 1 min ago, within 5 min window
        appVersion: '0.9.0',
        founderId: 'user-1',
        tokenExpiresAt: null,
        tokenRotatedAt: null,
        installId: null,
      },
    ]);
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.founderNodeOnline, true);
  });

  it('founderNodeOnline is false when lastSeenAt is older than ONLINE_WINDOW_MS', async () => {
    const { svc } = makeService([
      {
        id: 'row-1',
        userId: 'user-1',
        nodeId: 'fn_1',
        label: 'node 1',
        status: 'online',
        lastSeenAt: new Date(Date.now() - 10 * 60_000), // 10 min ago
        appVersion: '0.9.0',
        founderId: 'user-1',
        tokenExpiresAt: null,
        tokenRotatedAt: null,
        installId: null,
      },
    ]);
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.founderNodeOnline, false);
  });

  it('ideHandshakeActive is true when a fresh desktop bridge exists', async () => {
    const { svc } = makeService(
      [],
      [{ updatedAt: new Date(Date.now() - 30_000).toISOString() }], // 30s ago
      [],
    );
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.ideHandshakeActive, true);
  });

  it('ideHandshakeActive is false when no fresh bridge', async () => {
    const { svc } = makeService(
      [],
      [{ updatedAt: new Date(Date.now() - 10 * 60_000).toISOString() }], // 10 min ago
      [],
    );
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.ideHandshakeActive, false);
  });

  it('workspace is sourced from the most recent workspace repository', async () => {
    const { svc } = makeService(
      [],
      [],
      [
        {
          id: 'ws-1',
          title: 'my-repo',
          repository: '/home/me/my-repo',
          ideProvider: 'founder-ide',
          lastActiveAt: new Date().toISOString(),
        },
      ],
    );
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.workspace, '/home/me/my-repo');
  });

  it('workspace falls back to title when repository is absent', async () => {
    const { svc } = makeService(
      [],
      [],
      [
        {
          id: 'ws-1',
          title: 'untitled',
          ideProvider: 'founder-ide',
          lastActiveAt: new Date().toISOString(),
        },
      ],
    );
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.workspace, 'untitled');
  });

  it('workspace is null when no workspaces have been reported', async () => {
    const { svc } = makeService();
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.workspace, null);
  });

  it('lastHeartbeat is the ISO string of the latest node lastSeenAt', async () => {
    const ts = new Date(Date.now() - 30_000);
    const { svc } = makeService([
      {
        id: 'row-1',
        userId: 'user-1',
        nodeId: 'fn_1',
        label: 'node 1',
        status: 'online',
        lastSeenAt: ts,
        appVersion: '0.9.0',
        founderId: 'user-1',
        tokenExpiresAt: null,
        tokenRotatedAt: null,
        installId: null,
      },
    ]);
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.lastHeartbeat, ts.toISOString());
  });

  it('paired is true when at least one non-revoked node exists', async () => {
    const { svc } = makeService([
      {
        id: 'row-1',
        userId: 'user-1',
        nodeId: 'fn_1',
        label: 'node 1',
        status: 'online',
        lastSeenAt: new Date(),
        appVersion: '0.9.0',
        founderId: 'user-1',
        tokenExpiresAt: null,
        tokenRotatedAt: null,
        installId: null,
      },
    ]);
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.paired, true);
  });

  it('paired is false when all nodes are revoked', async () => {
    const { svc } = makeService([
      {
        id: 'row-1',
        userId: 'user-1',
        nodeId: 'fn_1',
        label: 'node 1',
        status: 'revoked',
        lastSeenAt: new Date(),
        appVersion: '0.9.0',
        founderId: 'user-1',
        tokenExpiresAt: null,
        tokenRotatedAt: null,
        installId: null,
      },
    ]);
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.paired, false);
  });

  it('gatewayReachable is true when the latest node row reports it', async () => {
    const { svc } = makeService([
      {
        id: 'row-1',
        userId: 'user-1',
        nodeId: 'fn_1',
        label: 'node 1',
        status: 'online',
        lastSeenAt: new Date(),
        appVersion: '0.9.0',
        founderId: 'user-1',
        tokenExpiresAt: null,
        tokenRotatedAt: null,
        installId: null,
        gatewayReachable: true,
      },
    ]);
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.gatewayReachable, true);
  });

  it('gatewayReachable is false by default (Workstream B will wire the probe)', async () => {
    const { svc } = makeService();
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.gatewayReachable, false);
  });

  it('updateState is "idle" (Workstream E will wire the updater state machine)', async () => {
    const { svc } = makeService();
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.updateState, 'idle');
  });

  it('executionConsentState defaults to "expired" (safe default — no consent without explicit grant)', async () => {
    const { svc } = makeService();
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.executionConsentState, 'expired');
  });
});

describe('FounderNodeService.getRuntimeStatus — staleness tolerance', () => {
  it('still returns a result when desktopBridge.listForUser throws', async () => {
    const failingBridge = {
      listForUser: async () => {
        throw new Error('db down');
      },
      listWorkspaces: async () => [],
    };
    const svc = new FounderNodeService(
      makeStubPrisma() as never,
      {} as never,
      {} as never,
      failingBridge as never,
    );
    // The .catch(() => []) on the bridge calls in getRuntimeStatus keeps
    // this from throwing — status panel can always render.
    const result = await svc.getRuntimeStatus('user-1', '');
    assert.equal(result.ideHandshakeActive, false);
    assert.equal(result.workspace, null);
  });
});
