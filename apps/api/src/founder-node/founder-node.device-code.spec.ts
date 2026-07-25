/**
 * Unit tests for the device-code (RFC 8628) contract.
 *
 * These pin the wire-level contract that Workstream B implements on the tray
 * side and Workstream C surfaces in the web UI. The Prisma client is replaced
 * with an in-memory stub so the tests run offline (no DATABASE_URL required).
 *
 * Covers:
 *   - createDeviceCode returns the full RFC 8628 shape
 *   - pollDeviceCode returns pending (before authorize), authorized (after),
 *     expired (after TTL), denied (after deny), slow_down (polling too fast)
 *   - authorizeDeviceCode binds the founder's userId + mints founderId/nodeId/
 *     nodeToken
 *   - expired grants are reaped by cleanupExpiredGrants() on poll
 *   - the grant is single-use (nodeToken cleared after first authorized read)
 *
 * Run with:
 *   npx tsx --test apps/api/src/founder-node/founder-node.device-code.spec.ts
 */

import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import * as bcrypt from 'bcrypt';
import { FounderNodeService } from './founder-node.service.js';
import type { FounderNodeDeviceCode } from '@prisma/client';

// ---------------------------------------------------------------------------
// In-memory Prisma stub. Only implements the slice exercised by the device-
// code flow. Method signatures mirror PrismaService so the service can't tell
// the difference.
// ---------------------------------------------------------------------------

type DeviceCodeRow = FounderNodeDeviceCode;

type FounderNodeRow = {
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

function makeStubPrisma() {
  const deviceCodes: DeviceCodeRow[] = [];
  const nodes: FounderNodeRow[] = [];
  let idCounter = 0;
  const nextId = () => `row-${++idCounter}`;

  const prisma = {
    _deviceCodes: deviceCodes,
    _nodes: nodes,

    founderNodeDeviceCode: {
      create: async ({ data }: { data: Partial<DeviceCodeRow> }) => {
        const row = {
          id: nextId(),
          userId: data.userId ?? null,
          deviceCode: data.deviceCode ?? '',
          userCode: data.userCode ?? '',
          verificationUri: data.verificationUri ?? '',
          expiresAt: data.expiresAt ?? new Date(),
          interval: data.interval ?? 5,
          status: data.status ?? 'pending',
          nodeToken: data.nodeToken ?? null,
          nodeId: data.nodeId ?? null,
          founderId: data.founderId ?? null,
          installId: data.installId ?? null,
          ipcSecretHash: data.ipcSecretHash ?? null,
          tokenExpiresAt: data.tokenExpiresAt ?? null,
          lastPolledAt: data.lastPolledAt ?? null,
          createdAt: new Date(),
          updatedAt: new Date(),
        } as DeviceCodeRow;
        deviceCodes.push(row);
        return row;
      },
      findUnique: async ({ where }: { where: { deviceCode?: string; userCode?: string } }) => {
        if (where.deviceCode) {
          return deviceCodes.find((r) => r.deviceCode === where.deviceCode) ?? null;
        }
        if (where.userCode) {
          return deviceCodes.find((r) => r.userCode === where.userCode) ?? null;
        }
        return null;
      },
      update: async ({ where, data }: { where: { id?: string; deviceCode?: string }; data: Partial<DeviceCodeRow> }) => {
        const row = deviceCodes.find(
          (r) => (where.id && r.id === where.id) || (where.deviceCode && r.deviceCode === where.deviceCode),
        );
        if (!row) throw new Error('row not found');
        Object.assign(row, data, { updatedAt: new Date() });
        return row;
      },
      updateMany: async (args: {
        where: {
          id?: string;
          userId?: string | null;
          status?: string | { in: string[] };
          nodeToken?: string;
        };
        data: Partial<DeviceCodeRow>;
      }) => {
        const where = args.where;
        const data = args.data;
        let count = 0;
        for (const r of deviceCodes) {
          const statusClause: string | { in: string[] } | undefined = where.status;
          const matchesStatus =
            statusClause === undefined ||
            (typeof statusClause === 'string' && r.status === statusClause) ||
            (typeof statusClause === 'object' &&
              statusClause !== null &&
              statusClause.in.includes(r.status));
          const matchesUserId = where.userId === undefined || r.userId === where.userId;
          const matchesId = where.id === undefined || r.id === where.id;
          const matchesNodeToken =
            where.nodeToken === undefined || r.nodeToken === where.nodeToken;
          if (matchesStatus && matchesUserId && matchesId && matchesNodeToken) {
            Object.assign(r, data, { updatedAt: new Date() });
            count++;
          }
        }
        return { count };
      },
      deleteMany: async ({ where }: { where: { status?: { in: string[] }; expiresAt?: { lt: Date } } }) => {
        let count = 0;
        const survivors: DeviceCodeRow[] = [];
        for (const r of deviceCodes) {
          const statusMatch = !where.status?.in || where.status.in.includes(r.status);
          const expiresMatch = !where.expiresAt?.lt || r.expiresAt < where.expiresAt.lt;
          if (statusMatch && expiresMatch) {
            count++;
          } else {
            survivors.push(r);
          }
        }
        deviceCodes.length = 0;
        deviceCodes.push(...survivors);
        return { count };
      },
    },

    founderNode: {
      upsert: async ({ where, create, update }: { where: { nodeId: string }; create: Partial<FounderNodeRow>; update: Partial<FounderNodeRow> }) => {
        let row = nodes.find((n) => n.nodeId === where.nodeId);
        if (!row) {
          row = {
            id: nextId(),
            userId: create.userId ?? '',
            nodeId: where.nodeId,
            label: create.label ?? 'Founder Node',
            secretHash: create.secretHash ?? '',
            status: create.status ?? 'online',
            lastSeenAt: create.lastSeenAt ?? new Date(),
            platform: create.platform ?? null,
            appVersion: create.appVersion ?? null,
            vaultHealthy: create.vaultHealthy ?? true,
            founderId: create.founderId ?? null,
            tokenExpiresAt: create.tokenExpiresAt ?? null,
            tokenRotatedAt: create.tokenRotatedAt ?? null,
            installId: create.installId ?? null,
            ipcSecretHash: create.ipcSecretHash ?? null,
          };
          nodes.push(row);
        } else {
          Object.assign(row, update);
        }
        return row;
      },
      update: async ({ where, data }: { where: { id?: string; nodeId?: string }; data: Partial<FounderNodeRow> }) => {
        const row = nodes.find(
          (n) => (where.id && n.id === where.id) || (where.nodeId && n.nodeId === where.nodeId),
        );
        if (!row) throw new Error('node not found');
        Object.assign(row, data);
        return row;
      },
      findUnique: async ({ where }: { where: { nodeId?: string } }) => {
        if (!where.nodeId) return null;
        return nodes.find((n) => n.nodeId === where.nodeId) ?? null;
      },
      findFirst: async () => null,
      delete: async () => {},
    },

    founderNodeVaultRelay: { deleteMany: async () => ({ count: 0 }) },
    founderNodeVaultSyncAck: { deleteMany: async () => ({ count: 0 }) },
    founderNodePairingCode: { deleteMany: async () => ({ count: 0 }) },
    founderBuilderSettings: { upsert: async () => ({}), findUnique: async () => null },
  };
  return {
    ...prisma,
    $transaction: async <T>(callback: (tx: typeof prisma) => Promise<T>) =>
      callback(prisma),
  };
}

// Minimal stubs for the three non-Prisma service dependencies. The device-
// code flow doesn't touch them, so they can be no-ops.
function makeService() {
  const prisma = makeStubPrisma();
  const svc = new FounderNodeService(
    prisma as never,
    {} as never,
    {} as never,
    {} as never,
  );
  return { svc, prisma };
}

describe('FounderNodeService — device-code (RFC 8628) contract', () => {
  let svc: FounderNodeService;
  let prisma: ReturnType<typeof makeStubPrisma>;

  beforeEach(() => {
    const ctx = makeService();
    svc = ctx.svc;
    prisma = ctx.prisma;
  });

  describe('createDeviceCode', () => {
    it('returns the RFC 8628 shape with all required fields', async () => {
      const grant = await svc.createDeviceCode({ installId: 'install-1' });

      // RFC 8628 §3.2 required fields.
      assert.ok(grant.deviceCode, 'deviceCode required');
      assert.ok(grant.userCode, 'userCode required');
      assert.ok(grant.verificationUri, 'verificationUri required');
      assert.ok(grant.expiresAt, 'expiresAt required');
      assert.ok(typeof grant.interval === 'number', 'interval required');

      // verificationUriComplete is the ready-to-open URL.
      assert.ok(grant.verificationUriComplete, 'verificationUriComplete recommended');
      assert.ok(
        grant.verificationUriComplete!.includes(grant.userCode),
        'verificationUriComplete should embed the userCode',
      );
    });

    it('formats userCode as ABCD-1234 (4 chars, dash, 4 chars, no 0/O/1/I)', async () => {
      const grant = await svc.createDeviceCode();
      assert.match(grant.userCode, /^[A-HJ-NP-Z2-9]{4}-[A-HJ-NP-Z2-9]{4}$/);
    });

    it('creates the grant row with userId = null (anonymous per RFC 8628 §3.1)', async () => {
      await svc.createDeviceCode({ installId: 'install-1' });
      const row = prisma._deviceCodes[0];
      assert.equal(row.userId, null, 'anonymous grant must have null userId');
      assert.equal(row.status, 'pending');
      assert.equal(row.installId, 'install-1');
    });

    it('stamps installId onto the row so authorize() can pair with it', async () => {
      await svc.createDeviceCode({ installId: 'install-xyz' });
      assert.equal(prisma._deviceCodes[0].installId, 'install-xyz');
    });

    it('stores only a hash of the IPC secret and binds it to the authorized node', async () => {
      const ipcSecret = 'a'.repeat(64);
      const grant = await svc.createDeviceCode({
        installId: 'install-secure',
        ipcSecret,
      });
      const deviceRow = prisma._deviceCodes[0];
      assert.ok(deviceRow.ipcSecretHash);
      assert.notEqual(deviceRow.ipcSecretHash, ipcSecret);
      assert.equal(await bcrypt.compare(ipcSecret, deviceRow.ipcSecretHash!), true);

      const authorized = await svc.authorizeDeviceCode(
        'founder-secure',
        grant.userCode,
        { label: 'Secure IDE' },
      );
      const node = prisma._nodes.find((candidate) => candidate.nodeId === authorized.nodeId);
      assert.equal(node?.ipcSecretHash, deviceRow.ipcSecretHash);
    });

    it('deviceCode is 64 hex chars (32 random bytes)', async () => {
      const grant = await svc.createDeviceCode();
      assert.match(grant.deviceCode, /^[0-9a-f]{64}$/);
    });

    it('interval is 5 seconds (matches DEVICE_CODE_INTERVAL_S)', async () => {
      const grant = await svc.createDeviceCode();
      assert.equal(grant.interval, 5);
    });
  });

  describe('pollDeviceCode', () => {
    it('returns pending (with interval) before authorize', async () => {
      const grant = await svc.createDeviceCode();
      const result = await svc.pollDeviceCode(grant.deviceCode);
      assert.equal(result.status, 'pending');
      assert.equal((result as { interval: number }).interval, 5);
    });

    it('returns authorized with tokens after authorize', async () => {
      const grant = await svc.createDeviceCode();
      const userId = 'user-1';
      const nodeId = 'fn_' + 'a'.repeat(64);
      await svc.authorizeDeviceCode(userId, grant.userCode, {
        nodeId,
        label: 'MacBook',
      });

      const result = await svc.pollDeviceCode(grant.deviceCode);

      assert.equal(result.status, 'authorized');
      const auth = result as {
        founderId: string;
        nodeId: string;
        nodeToken: string;
        tokenExpiresAt?: string;
      };
      assert.equal(auth.founderId, userId);
      assert.equal(auth.nodeId, nodeId);
      assert.ok(auth.nodeToken.startsWith('fn_'), 'nodeToken should be fn_-prefixed');
      assert.ok(auth.tokenExpiresAt, 'tokenExpiresAt should be present');
    });

    it('returns expired after TTL elapses', async () => {
      const grant = await svc.createDeviceCode();
      // Advance virtual time past the TTL (15 min) + a buffer.
      const future = Date.now() + FounderNodeService.DEVICE_CODE_TTL_MS + 1000;
      const result = await svc.pollDeviceCode(grant.deviceCode, { now: future });
      assert.equal(result.status, 'expired');
      assert.match(
        (result as { error: string }).error,
        /expired/i,
      );
    });

    it('returns denied after deny', async () => {
      const grant = await svc.createDeviceCode();
      await svc.denyDeviceCode('user-1', grant.userCode);
      const result = await svc.pollDeviceCode(grant.deviceCode);
      assert.equal(result.status, 'denied');
      assert.match(
        (result as { error: string }).error,
        /denied/i,
      );
    });

    it('returns slow_down when polled faster than interval', async () => {
      const grant = await svc.createDeviceCode();
      const t0 = 1_000_000;
      // First poll at t0 — sets lastPolledAt.
      const r1 = await svc.pollDeviceCode(grant.deviceCode, { now: t0 });
      assert.equal(r1.status, 'pending');
      // Second poll 1 second later — within the 5s interval → slow_down.
      const r2 = await svc.pollDeviceCode(grant.deviceCode, { now: t0 + 1000 });
      assert.equal(r2.status, 'slow_down');
      // slow_down must carry the bumped interval (interval + 5 per RFC 8628 §3.5).
      assert.equal((r2 as { interval: number }).interval, 10);
    });

    it('does NOT return slow_down when polling at exactly the interval', async () => {
      const grant = await svc.createDeviceCode();
      const t0 = 5_000_000;
      await svc.pollDeviceCode(grant.deviceCode, { now: t0 });
      // Poll 5s later — exactly at the interval, should be pending not slow_down.
      const r2 = await svc.pollDeviceCode(grant.deviceCode, { now: t0 + 5000 });
      assert.equal(r2.status, 'pending');
    });

    it('the authorized grant is single-use — second poll returns expired', async () => {
      const grant = await svc.createDeviceCode();
      await svc.authorizeDeviceCode('user-1', grant.userCode, {
        nodeId: 'fn_node1',
        label: 'Node 1',
      });
      const r1 = await svc.pollDeviceCode(grant.deviceCode);
      assert.equal(r1.status, 'authorized');
      // Second poll: nodeToken has been cleared.
      const r2 = await svc.pollDeviceCode(grant.deviceCode);
      assert.equal(r2.status, 'expired');
    });

    it('allows only one winner when two authorized polls race', async () => {
      const grant = await svc.createDeviceCode();
      await svc.authorizeDeviceCode('user-1', grant.userCode, {
        label: 'Node 1',
      });

      const results = await Promise.all([
        svc.pollDeviceCode(grant.deviceCode),
        svc.pollDeviceCode(grant.deviceCode),
      ]);
      assert.equal(
        results.filter((result: { status: string }) => result.status === 'authorized').length,
        1,
      );
      assert.equal(
        results.filter((result: { status: string }) => result.status === 'expired').length,
        1,
      );
    });

    it('throws on unknown deviceCode', async () => {
      await assert.rejects(
        () => svc.pollDeviceCode('nonexistent-device-code'),
        /Unknown device code/i,
      );
    });
  });

  describe('authorizeDeviceCode', () => {
    it('mints nodeId when the browser flow does not provide one', async () => {
      const grant = await svc.createDeviceCode({ installId: 'install-browser' });
      const result = await svc.authorizeDeviceCode('user-browser', grant.userCode, {
        label: 'Founder IDE on Windows',
      });
      assert.match(result.nodeId, /^fn_[0-9a-f]{64}$/);
      const polled = await svc.pollDeviceCode(grant.deviceCode);
      assert.equal(polled.status, 'authorized');
      assert.equal(
        (polled as { nodeId: string }).nodeId,
        result.nodeId,
      );
    });

    it('binds the founder userId + mints founderId/nodeId/nodeToken', async () => {
      const grant = await svc.createDeviceCode();
      const userId = 'founder-1';
      const nodeId = 'fn_' + 'b'.repeat(64);

      const result = await svc.authorizeDeviceCode(userId, grant.userCode, {
        nodeId,
        label: 'Workstation',
      });

      assert.equal(result.authorized, true);
      assert.equal(result.founderId, userId);

      // The grant row is now bound to this user + carries the node identity.
      const row = prisma._deviceCodes[0];
      assert.equal(row.userId, userId, 'userId must be stamped on authorize');
      assert.equal(row.status, 'authorized');
      assert.equal(row.nodeId, nodeId);
      assert.equal(row.founderId, userId);
      assert.ok(row.nodeToken, 'nodeToken must be stashed for the next poll');
    });

    it('does not let a legacy client reassign another founder nodeId', async () => {
      const nodeId = `fn_${'c'.repeat(64)}`;
      const ownerGrant = await svc.createDeviceCode();
      await svc.authorizeDeviceCode('owner-user', ownerGrant.userCode, {
        nodeId,
        label: 'Owner laptop',
      });
      const attackerGrant = await svc.createDeviceCode();

      await assert.rejects(
        () =>
          svc.authorizeDeviceCode('other-user', attackerGrant.userCode, {
            nodeId,
            label: 'Other laptop',
          }),
        /not available/i,
      );
      assert.equal(prisma._nodes.find((node) => node.nodeId === nodeId)?.userId, 'owner-user');
    });

    it('allows only one founder to authorize a pending grant', async () => {
      const grant = await svc.createDeviceCode();
      const results = await Promise.allSettled([
        svc.authorizeDeviceCode('founder-a', grant.userCode, {
          label: 'Founder A laptop',
        }),
        svc.authorizeDeviceCode('founder-b', grant.userCode, {
          label: 'Founder B laptop',
        }),
      ]);
      assert.equal(
        results.filter((result) => result.status === 'fulfilled').length,
        1,
      );
      assert.equal(
        results.filter((result) => result.status === 'rejected').length,
        1,
      );
    });

    it('rejects an already-authorized grant', async () => {
      const grant = await svc.createDeviceCode();
      await svc.authorizeDeviceCode('user-1', grant.userCode, {
        nodeId: 'fn_n1',
        label: 'N1',
      });
      await assert.rejects(
        () => svc.authorizeDeviceCode('user-1', grant.userCode, {
          nodeId: 'fn_n2',
          label: 'N2',
        }),
        /already authorized/i,
      );
    });

    it('rejects an expired grant', async () => {
      const grant = await svc.createDeviceCode();
      // Force the row into the expired state.
      const row = prisma._deviceCodes[0];
      row.expiresAt = new Date(Date.now() - 1000);
      await assert.rejects(
        () => svc.authorizeDeviceCode('user-1', grant.userCode, {
          nodeId: 'fn_n1',
          label: 'N1',
        }),
        /expired/i,
      );
    });

    it('rejects a denied grant', async () => {
      const grant = await svc.createDeviceCode();
      await svc.denyDeviceCode('user-1', grant.userCode);
      await assert.rejects(
        () => svc.authorizeDeviceCode('user-1', grant.userCode, {
          nodeId: 'fn_n1',
          label: 'N1',
        }),
        /denied/i,
      );
    });
  });

  describe('denyDeviceCode', () => {
    it('marks the grant as denied', async () => {
      const grant = await svc.createDeviceCode();
      const result = await svc.denyDeviceCode('user-1', grant.userCode);
      assert.equal(result.denied, true);
      assert.equal(prisma._deviceCodes[0].status, 'denied');
    });
  });

  describe('cleanupExpiredGrants', () => {
    it('reaps expired/denied grants past 2x TTL', async () => {
      // Create two grants at t0.
      await svc.createDeviceCode();
      await svc.createDeviceCode();
      // Deny one, expire the other.
      prisma._deviceCodes[0].status = 'denied';
      prisma._deviceCodes[0].expiresAt = new Date(1000);
      prisma._deviceCodes[1].status = 'expired';
      prisma._deviceCodes[1].expiresAt = new Date(1000);

      // Cleanup at t0 + 2*TTL + buffer — both should be reaped.
      const now = Date.now() + FounderNodeService.DEVICE_CODE_TTL_MS * 2 + 1000;
      const count = await svc.cleanupExpiredGrants({ now });
      assert.ok(count >= 2, `expected at least 2 reaped, got ${count}`);
      assert.equal(prisma._deviceCodes.length, 0, 'all terminal grants should be reaped');
    });

    it('does NOT reap pending grants (even if old — they expire naturally first)', async () => {
      await svc.createDeviceCode();
      prisma._deviceCodes[0].status = 'pending';
      prisma._deviceCodes[0].expiresAt = new Date(1000);
      const now = Date.now() + FounderNodeService.DEVICE_CODE_TTL_MS * 2 + 1000;
      const count = await svc.cleanupExpiredGrants({ now });
      assert.equal(count, 0);
      // Pending grant survives — poll() will mark it expired lazily, then the
      // NEXT cleanup will reap it. Two-step keeps cleanup cheap.
      assert.equal(prisma._deviceCodes.length, 1);
    });

    it('is called on each poll (lazy cleanup)', async () => {
      // Seed an old expired grant manually.
      await svc.createDeviceCode();
      prisma._deviceCodes[0].status = 'expired';
      prisma._deviceCodes[0].expiresAt = new Date(1);

      // Create a fresh grant + poll it — the fresh poll triggers cleanup.
      const fresh = await svc.createDeviceCode();
      const before = prisma._deviceCodes.length;
      await svc.pollDeviceCode(fresh.deviceCode, { now: Date.now() + FounderNodeService.DEVICE_CODE_TTL_MS * 3 });
      // The old grant should have been reaped during this poll.
      assert.ok(
        prisma._deviceCodes.length < before,
        'expected old grant reaped during poll',
      );
    });
  });

  describe('TTL constants', () => {
    it('DEVICE_CODE_TTL_MS is 15 minutes', () => {
      assert.equal(FounderNodeService.DEVICE_CODE_TTL_MS, 15 * 60 * 1000);
    });

    it('DEVICE_CODE_INTERVAL_S is 5 seconds', () => {
      assert.equal(FounderNodeService.DEVICE_CODE_INTERVAL_S, 5);
    });
  });
});
