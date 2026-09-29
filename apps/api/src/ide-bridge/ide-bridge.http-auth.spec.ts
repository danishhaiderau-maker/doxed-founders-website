import assert from 'node:assert/strict';
import test from 'node:test';
import { INestApplication, Module, ValidationPipe } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { APP_GUARD, Reflector } from '@nestjs/core';
import { JwtModule, JwtService } from '@nestjs/jwt';
import { NestFactory } from '@nestjs/core';
import { PassportModule } from '@nestjs/passport';
import * as bcrypt from 'bcrypt';
import { AuthService } from '../auth/auth.service';
import type { AuthUser, JwtPayload } from '../auth/auth.types';
import { JwtAuthGuard } from '../auth/guards';
import { JwtStrategy } from '../auth/jwt.strategy';
import { BuilderService } from '../builder/builder.service';
import { ConnectedWorkspaceService } from '../connected-workspace/connected-workspace.service';
import { DesktopBridgeService } from '../desktop-bridge/desktop-bridge.service';
import { FounderAgentRunService } from '../founder-agent-run/founder-agent-run.service';
import { FounderNodeController } from '../founder-node/founder-node.controller';
import { FounderNodeGuard } from '../founder-node/founder-node.guard';
import { FounderNodeInferenceService } from '../founder-node/founder-node-inference.service';
import { FounderNodeService } from '../founder-node/founder-node.service';
import { FounderNodeSyncService } from '../founder-node/founder-node-sync.service';
import { FounderNodeVaultSyncService } from '../founder-node/founder-node-vault-sync.service';
import { WorkspaceSessionService } from '../workspace-session/workspace-session.service';
import { IdeBridgeController } from './ide-bridge.controller';
import { IdeBridgeService } from './ide-bridge.service';
import {
  FOUNDER_REMOTE_CAPABILITY,
  FOUNDER_REMOTE_CAPABILITY_VERSION,
  FOUNDER_REMOTE_PENDING,
  FOUNDER_REMOTE_PROVIDER,
  founderRemoteEnvelopeHash,
} from './founder-remote-dispatch';

const TEST_JWT_SECRET = 'synthetic-http-auth-secret-at-least-32-characters';

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
  cancelRequestedAt: Date | null;
  cancelReason: string | null;
};

type NodeRow = {
  id: string;
  userId: string;
  nodeId: string;
  secretHash: string;
  status: string;
  lastSeenAt: Date;
  ideCapabilitiesAt: Date;
  ideProvider: string;
  ideCapabilityVersion: number;
  ideCapabilities: string[];
};

function matches(row: Record<string, unknown>, where: Record<string, unknown>): boolean {
  return Object.entries(where).every(([key, expected]) => {
    const actual = row[key];
    if (expected && typeof expected === 'object' && !Array.isArray(expected)) {
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

function remoteRow(data: Record<string, unknown>): DispatchRow {
  const row = {
    id: data.id,
    userId: data.userId,
    sessionId: data.sessionId,
    prompt: data.prompt,
    ideProvider: data.ideProvider,
    status: data.status ?? FOUNDER_REMOTE_PENDING,
    result: data.result ?? null,
    error: data.error ?? null,
    dispatchedAt: data.dispatchedAt ?? null,
    createdAt: data.createdAt ?? new Date(),
    targetNodeId: data.targetNodeId,
    capabilityVersion: data.capabilityVersion,
    targetCapability: data.targetCapability,
    envelopeHash: data.envelopeHash ?? null,
    claimTokenHash: data.claimTokenHash ?? null,
    claimedAt: data.claimedAt ?? null,
    expiresAt: data.expiresAt ?? null,
    cancelRequestedAt: data.cancelRequestedAt ?? null,
    cancelReason: data.cancelReason ?? null,
  } as DispatchRow;
  if (!row.envelopeHash && row.targetNodeId && row.capabilityVersion && row.targetCapability) {
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

function createHarness() {
  const users = new Map<string, AuthUser>([
    ['owner', {
      id: 'owner', email: 'owner@example.test', name: 'Owner', role: 'USER',
      reputationPoints: 0, contributorLevel: 0,
    }],
    ['other-user', {
      id: 'other-user', email: 'other@example.test', name: 'Other', role: 'USER',
      reputationPoints: 0, contributorLevel: 0,
    }],
  ]);
  const now = new Date();
  const nodes: NodeRow[] = [
    {
      id: 'db-node-a', userId: 'owner', nodeId: 'node-a',
      secretHash: bcrypt.hashSync('owner-node-secret', 4), status: 'online',
      lastSeenAt: now, ideCapabilitiesAt: now, ideProvider: FOUNDER_REMOTE_PROVIDER,
      ideCapabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION,
      ideCapabilities: [FOUNDER_REMOTE_CAPABILITY],
    },
    {
      id: 'db-node-c', userId: 'owner', nodeId: 'node-c',
      secretHash: bcrypt.hashSync('other-owner-node-secret', 4), status: 'online',
      lastSeenAt: now, ideCapabilitiesAt: now, ideProvider: FOUNDER_REMOTE_PROVIDER,
      ideCapabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION,
      ideCapabilities: [FOUNDER_REMOTE_CAPABILITY],
    },
    {
      id: 'db-node-b', userId: 'other-user', nodeId: 'node-b',
      secretHash: bcrypt.hashSync('other-user-node-secret', 4), status: 'online',
      lastSeenAt: now, ideCapabilitiesAt: now, ideProvider: FOUNDER_REMOTE_PROVIDER,
      ideCapabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION,
      ideCapabilities: [FOUNDER_REMOTE_CAPABILITY],
    },
  ];
  const rows: DispatchRow[] = [];
  const memoryGraphs = new Map<string, { memoryGraph: unknown }>([
    ['owner', {
      memoryGraph: {
        _sessionsByNode: {
          'node-a': [{
            id: 'session-a', composerId: 'composer-a', title: 'Founder IDE chat',
            lastActiveAt: now.toISOString(), ideProvider: FOUNDER_REMOTE_PROVIDER,
            restorable: true,
          }],
        },
      },
    }],
  ]);
  const prisma = {
    founderBuilderSettings: {
      findUnique: async ({ where }: { where: { userId: string } }) =>
        memoryGraphs.get(where.userId) ?? null,
    },
    founderNode: {
      findUnique: async ({ where }: { where: { nodeId?: string } }) =>
        nodes.find((node) => node.nodeId === where.nodeId) ?? null,
      findFirst: async ({ where }: { where: Record<string, unknown> }) =>
        nodes.find((node) => matches(node as unknown as Record<string, unknown>, where)) ?? null,
    },
    pendingIdeDispatch: {
      create: async ({ data }: { data: Record<string, unknown> }) => {
        const row = remoteRow(data);
        rows.push(row);
        return row;
      },
      findFirst: async ({ where }: { where: Record<string, unknown> }) =>
        rows.find((row) => matches(row as unknown as Record<string, unknown>, where)) ?? null,
      findMany: async ({ where, orderBy, take }: {
        where: Record<string, unknown>;
        orderBy?: { createdAt?: 'asc' | 'desc' };
        take?: number;
      }) => {
        const found = rows.filter((row) =>
          matches(row as unknown as Record<string, unknown>, where));
        if (orderBy?.createdAt) {
          found.sort((left, right) => orderBy.createdAt === 'asc'
            ? left.createdAt.getTime() - right.createdAt.getTime()
            : right.createdAt.getTime() - left.createdAt.getTime());
        }
        return take === undefined ? found : found.slice(0, take);
      },
      updateMany: async ({ where, data }: {
        where: Record<string, unknown>;
        data: Partial<DispatchRow>;
      }) => {
        const found = rows.filter((row) =>
          matches(row as unknown as Record<string, unknown>, where));
        found.forEach((row) => Object.assign(row, data));
        return { count: found.length };
      },
    },
  };
  const desktopBridge = new DesktopBridgeService(prisma as never);
  const ideBridge = new IdeBridgeService(
    prisma as never,
    desktopBridge,
    {} as FounderAgentRunService,
    {} as BuilderService,
    {} as ConnectedWorkspaceService,
  );
  const founderNodes = new FounderNodeService(
    prisma as never,
    {} as never,
    {} as never,
    desktopBridge,
  );
  return { founderNodes, ideBridge, rows, users };
}

const harness = createHarness();

@Module({
  imports: [
    PassportModule.register({ defaultStrategy: 'jwt' }),
    JwtModule.register({ secret: TEST_JWT_SECRET }),
  ],
  controllers: [IdeBridgeController, FounderNodeController],
  providers: [
    {
      provide: JwtStrategy,
      inject: [AuthService],
      useFactory: (auth: AuthService) => new JwtStrategy(auth),
    },
    {
      provide: FounderNodeGuard,
      inject: [FounderNodeService],
      useFactory: (nodes: FounderNodeService) => new FounderNodeGuard(nodes),
    },
    {
      provide: APP_GUARD,
      inject: [Reflector, ConfigService],
      useFactory: (reflector: Reflector, config: ConfigService) =>
        new JwtAuthGuard(reflector, config),
    },
    { provide: ConfigService, useValue: { get: () => undefined } },
    {
      provide: AuthService,
      useValue: {
        validatePayload: async (payload: JwtPayload) => harness.users.get(payload.sub) ?? null,
      },
    },
    { provide: IdeBridgeService, useValue: harness.ideBridge },
    { provide: FounderNodeService, useValue: harness.founderNodes },
    { provide: FounderNodeInferenceService, useValue: {} },
    { provide: FounderNodeSyncService, useValue: {} },
    { provide: FounderNodeVaultSyncService, useValue: {} },
    { provide: WorkspaceSessionService, useValue: {} },
  ],
})
class RemoteDispatchHttpAuthModule {}

type HttpResult = { status: number; body: Record<string, unknown> | null };

async function jsonRequest(
  baseUrl: string,
  path: string,
  options: {
    method?: string;
    authorization?: string;
    body?: unknown;
    rawBody?: string;
  } = {},
): Promise<HttpResult> {
  const headers: Record<string, string> = {};
  if (options.authorization) headers.authorization = options.authorization;
  const hasBody = options.body !== undefined || options.rawBody !== undefined;
  if (hasBody) headers['content-type'] = 'application/json';
  const response = await fetch(`${baseUrl}${path}`, {
    method: options.method ?? 'GET',
    headers,
    ...(hasBody ? {
      body: options.rawBody ?? JSON.stringify(options.body),
    } : {}),
  });
  const text = await response.text();
  return {
    status: response.status,
    body: text ? JSON.parse(text) as Record<string, unknown> : null,
  };
}

function restoreEnv(name: string, prior: string | undefined): void {
  if (prior === undefined) delete process.env[name];
  else process.env[name] = prior;
}

test('production controllers enforce JWT and Founder Node ownership over real HTTP', async (t) => {
  const priorJwtSecret = process.env.JWT_SECRET;
  process.env.JWT_SECRET = TEST_JWT_SECRET;
  let app: INestApplication | undefined;
  try {
    app = await NestFactory.create(RemoteDispatchHttpAuthModule, { logger: false });
    // tsx/esbuild executes decorators but does not emit constructor design metadata.
    // Bind the already-created production controller/guard instances explicitly;
    // the HTTP routing, decorators, guards, Passport strategy, and controller code
    // under test remain the production implementations.
    Object.assign(app.get(IdeBridgeController), { ideBridge: harness.ideBridge });
    Object.assign(app.get(FounderNodeController), { ideBridge: harness.ideBridge });
    Object.assign(app.get(FounderNodeGuard), { nodes: harness.founderNodes });
    app.setGlobalPrefix('api');
    app.useGlobalPipes(new ValidationPipe({ whitelist: true, transform: true, forbidNonWhitelisted: true }));
    await app.listen(0, '127.0.0.1');
  } finally {
    restoreEnv('JWT_SECRET', priorJwtSecret);
  }

  const address = app.getHttpServer().address();
  assert.ok(address && typeof address === 'object');
  const baseUrl = `http://127.0.0.1:${address.port}`;
  const jwt = app.get(JwtService);
  const ownerJwt = jwt.sign({ sub: 'owner', email: 'owner@example.test', role: 'USER' });
  const otherJwt = jwt.sign({ sub: 'other-user', email: 'other@example.test', role: 'USER' });
  const ownerBearer = `Bearer ${ownerJwt}`;
  const otherBearer = `Bearer ${otherJwt}`;
  const ownerNode = 'FounderNode node-a:owner-node-secret';
  const wrongOwnerNode = 'FounderNode node-c:other-owner-node-secret';
  const otherUserNode = 'FounderNode node-b:other-user-node-secret';
  const dispatchBody = {
    prompt: 'Build the authenticated dashboard',
    ideProvider: FOUNDER_REMOTE_PROVIDER,
    targetNodeId: 'node-a',
  };
  let dispatchId = '';
  let claimToken = '';

  try {
    await t.test('rejects missing credentials and invalid Founder Node secrets', async () => {
      const web = await jsonRequest(baseUrl, '/api/ide-bridge/sessions/session-a/dispatch', {
        method: 'POST', body: dispatchBody,
      });
      assert.equal(web.status, 401, JSON.stringify(web.body));

      const node = await jsonRequest(baseUrl, '/api/founder-node/pending-dispatches');
      assert.equal(node.status, 401);

      const invalidNode = await jsonRequest(baseUrl, '/api/founder-node/pending-dispatches', {
        authorization: 'FounderNode node-a:not-the-secret',
      });
      assert.equal(invalidNode.status, 401);
      assert.equal(invalidNode.body?.message, 'Invalid Founder Node token');
    });

    await t.test('binds web reads to the JWT user and node claims to the authenticated node', async () => {
      const created = await jsonRequest(baseUrl, '/api/ide-bridge/sessions/session-a/dispatch', {
        method: 'POST', authorization: ownerBearer, body: dispatchBody,
      });
      assert.equal(created.status, 201, JSON.stringify(created.body));
      assert.equal(typeof created.body?.id, 'string');
      dispatchId = String(created.body?.id);

      const wrongUserRead = await jsonRequest(baseUrl, `/api/ide-bridge/dispatch/${dispatchId}`, {
        authorization: otherBearer,
      });
      assert.equal(wrongUserRead.status, 404);
      assert.equal(wrongUserRead.body?.message, 'Dispatch not found');
      assert.equal('result' in (wrongUserRead.body ?? {}), false);

      const claimBody = {
        targetNodeId: 'node-c', ideProvider: FOUNDER_REMOTE_PROVIDER,
        capabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION,
        targetCapability: FOUNDER_REMOTE_CAPABILITY,
      };
      const wrongNode = await jsonRequest(baseUrl, `/api/founder-node/dispatch/${dispatchId}/claim`, {
        method: 'POST', authorization: wrongOwnerNode, body: claimBody,
      });
      assert.equal(wrongNode.status, 409);
      assert.equal(wrongNode.body?.code, 'CLAIM_CONFLICT');
      assert.equal('claimToken' in (wrongNode.body ?? {}), false);

      const otherUserClaim = await jsonRequest(baseUrl, `/api/founder-node/dispatch/${dispatchId}/claim`, {
        method: 'POST', authorization: otherUserNode,
        body: { ...claimBody, targetNodeId: 'node-b' },
      });
      assert.equal(otherUserClaim.status, 409);
      assert.equal(otherUserClaim.body?.code, 'CLAIM_CONFLICT');
    });

    await t.test('requires a token-bound claim before accepting terminal completion', async () => {
      const claim = await jsonRequest(baseUrl, `/api/founder-node/dispatch/${dispatchId}/claim`, {
        method: 'POST', authorization: ownerNode,
        body: {
          targetNodeId: 'node-a', ideProvider: FOUNDER_REMOTE_PROVIDER,
          capabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION,
          targetCapability: FOUNDER_REMOTE_CAPABILITY,
        },
      });
      assert.equal(claim.status, 201);
      assert.equal(typeof claim.body?.claimToken, 'string');
      claimToken = String(claim.body?.claimToken);

      const replacement = claimToken.startsWith('A') ? 'B' : 'A';
      const invalidCompletion = await jsonRequest(
        baseUrl,
        `/api/founder-node/dispatch/${dispatchId}/complete`,
        {
          method: 'POST', authorization: ownerNode,
          body: { claimToken: `${replacement}${claimToken.slice(1)}`, result: 'forged' },
        },
      );
      assert.equal(invalidCompletion.status, 409);
      assert.equal(invalidCompletion.body?.code, 'INVALID_CLAIM');

      const completed = await jsonRequest(baseUrl, `/api/founder-node/dispatch/${dispatchId}/complete`, {
        method: 'POST', authorization: ownerNode,
        body: { claimToken, result: 'Authenticated completion' },
      });
      assert.equal(completed.status, 201);
      assert.equal(completed.body?.executionStatus, 'complete');
      assert.equal(completed.body?.result, 'Authenticated completion');
      assert.equal(completed.body?.delivered, false);
    });

    await t.test('owner cancel reaches a claimed run only through the guarded cancel-status poll', async () => {
      const created = await jsonRequest(baseUrl, '/api/ide-bridge/sessions/session-a/dispatch', {
        method: 'POST', authorization: ownerBearer,
        body: { ...dispatchBody, prompt: 'Build the cancellable dashboard' },
      });
      assert.equal(created.status, 201, JSON.stringify(created.body));
      const cancelId = String(created.body?.id);
      const claim = await jsonRequest(baseUrl, `/api/founder-node/dispatch/${cancelId}/claim`, {
        method: 'POST', authorization: ownerNode,
        body: {
          targetNodeId: 'node-a', ideProvider: FOUNDER_REMOTE_PROVIDER,
          capabilityVersion: FOUNDER_REMOTE_CAPABILITY_VERSION,
          targetCapability: FOUNDER_REMOTE_CAPABILITY,
        },
      });
      assert.equal(claim.status, 201);
      const cancelClaimToken = String(claim.body?.claimToken);
      const statusPath = `/api/founder-node/dispatch/${cancelId}/cancel-status`;

      const unauthenticated = await jsonRequest(baseUrl, statusPath, {
        method: 'POST', body: { claimToken: cancelClaimToken },
      });
      assert.equal(unauthenticated.status, 401);

      const before = await jsonRequest(baseUrl, statusPath, {
        method: 'POST', authorization: ownerNode, body: { claimToken: cancelClaimToken },
      });
      assert.equal(before.status, 201, JSON.stringify(before.body));
      assert.equal(before.body?.cancellationRequested, false);

      const otherUserCancel = await jsonRequest(baseUrl, `/api/ide-bridge/dispatch/${cancelId}/cancel`, {
        method: 'POST', authorization: otherBearer, body: { reason: 'not mine' },
      });
      assert.equal(otherUserCancel.status, 404);

      const cancelled = await jsonRequest(baseUrl, `/api/ide-bridge/dispatch/${cancelId}/cancel`, {
        method: 'POST', authorization: ownerBearer, body: { reason: 'Owner stop' },
      });
      assert.equal(cancelled.status, 201, JSON.stringify(cancelled.body));
      assert.equal(cancelled.body?.executionStatus, 'cancellation_requested');

      const after = await jsonRequest(baseUrl, statusPath, {
        method: 'POST', authorization: ownerNode, body: { claimToken: cancelClaimToken },
      });
      assert.equal(after.body?.cancellationRequested, true);
      assert.equal(after.body?.reason, 'Owner stop');

      const terminal = await jsonRequest(baseUrl, `/api/founder-node/dispatch/${cancelId}/complete`, {
        method: 'POST', authorization: ownerNode,
        body: { claimToken: cancelClaimToken, error: 'Aborted locally' },
      });
      assert.equal(terminal.status, 201);
      assert.equal(terminal.body?.executionStatus, 'failed');
      assert.equal(terminal.body?.error, 'Cancelled by the Founder owner: Owner stop');
    });

    await t.test('rejects malformed remote bodies at the HTTP boundary', async () => {
      const malformedClaim = await jsonRequest(baseUrl, `/api/founder-node/dispatch/${dispatchId}/claim`, {
        method: 'POST', authorization: ownerNode,
        body: { targetNodeId: 'node-a' },
      });
      assert.equal(malformedClaim.status, 400);
      assert.equal(malformedClaim.body?.code, 'INVALID_CLAIM');

      const missingPrompt = await jsonRequest(baseUrl, '/api/ide-bridge/sessions/session-a/dispatch', {
        method: 'POST', authorization: ownerBearer,
        body: { ideProvider: FOUNDER_REMOTE_PROVIDER, targetNodeId: 'node-a' },
      });
      assert.equal(missingPrompt.status, 400);
      assert.equal(missingPrompt.body?.code, 'INVALID_PROMPT');

      const invalidJson = await jsonRequest(baseUrl, '/api/ide-bridge/sessions/session-a/dispatch', {
        method: 'POST', authorization: ownerBearer, rawBody: '{',
      });
      assert.equal(invalidJson.status, 400);
    });
  } finally {
    await app.close();
  }
});
