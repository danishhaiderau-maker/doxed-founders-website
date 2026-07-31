import assert from 'node:assert/strict';
import test from 'node:test';
import { UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { DdollarGateTokenGuard } from './ddollar-gate-token.guard';
import type { AuthUser } from '../auth/auth.types';

/**
 * FIX 1 — DdollarGateTokenGuard unit tests.
 *
 * The guard allows the strategy bot to authenticate to
 * /api/founder-economics/ddollar/gate-balance via
 * `Authorization: Bearer ${DDOLLAR_GATE_TOKEN}` and have request.user
 * materialized from the configured DDOLLAR_GATE_OPERATOR_USER_ID.
 *
 * Fail-closed contract:
 *   - missing env (token or operator id) → throws UnauthorizedException
 *   - wrong token → throws UnauthorizedException
 *   - valid token but unknown operator user → throws UnauthorizedException
 *   - missing bearer header → returns true (defer to global JwtAuthGuard
 *     for the session path — never authenticates on its own)
 *   - valid token + known operator user → request.user set, returns true
 */

type FakeRequest = {
  headers: Record<string, string | string[] | undefined>;
  user?: AuthUser;
};

function fakeContext(req: FakeRequest) {
  return {
    switchToHttp: () => ({ getRequest: () => req }),
  };
}

function makeConfig(values: Record<string, string | undefined>) {
  return {
    get: <T = string>(key: string) => (values[key] as unknown as T) ?? undefined,
  } as unknown as ConfigService;
}

function makePrisma(user: Record<string, unknown> | null) {
  return {
    user: {
      findUnique: async () => user,
    },
  } as never;
}

const operatorUser = {
  id: 'op-1',
  email: 'op@example.com',
  name: 'Operator',
  role: 'FOUNDER',
  reputationPoints: 250.5,
  contributorLevel: 3,
};

test('DdollarGateTokenGuard: valid bearer token materializes the operator user', async () => {
  const config = makeConfig({
    DDOLLAR_GATE_TOKEN: 'secret-token',
    DDOLLAR_GATE_OPERATOR_USER_ID: 'op-1',
  });
  const prisma = makePrisma(operatorUser);
  const guard = new DdollarGateTokenGuard(config, prisma);
  const req: FakeRequest = {
    headers: { authorization: 'Bearer secret-token' },
  };
  const result = await guard.canActivate(fakeContext(req) as never);
  assert.equal(result, true);
  assert.equal(req.user?.id, 'op-1');
  assert.equal(req.user?.email, 'op@example.com');
  assert.equal(req.user?.reputationPoints, 250.5);
});

test('DdollarGateTokenGuard: missing bearer header defers to the session (returns true, no user set)', async () => {
  // The guard must NOT authenticate on its own when there is no bearer
  // header. It returns true so the route-level @CurrentUser resolution
  // either succeeds (session cookie via the global JwtAuthGuard) or fails
  // (401). Without this, the global guard's skip-JWT-on-bearer-match
  // contract would leak an authenticated response for an unauthenticated
  // request.
  const config = makeConfig({
    DDOLLAR_GATE_TOKEN: 'secret-token',
    DDOLLAR_GATE_OPERATOR_USER_ID: 'op-1',
  });
  const prisma = makePrisma(operatorUser);
  const guard = new DdollarGateTokenGuard(config, prisma);
  const req: FakeRequest = { headers: {} };
  const result = await guard.canActivate(fakeContext(req) as never);
  assert.equal(result, true);
  assert.equal(req.user, undefined);
});

test('DdollarGateTokenGuard: wrong token throws UnauthorizedException', async () => {
  const config = makeConfig({
    DDOLLAR_GATE_TOKEN: 'secret-token',
    DDOLLAR_GATE_OPERATOR_USER_ID: 'op-1',
  });
  const prisma = makePrisma(operatorUser);
  const guard = new DdollarGateTokenGuard(config, prisma);
  const req: FakeRequest = {
    headers: { authorization: 'Bearer wrong-token' },
  };
  await assert.rejects(
    () => guard.canActivate(fakeContext(req) as never),
    (err: unknown) => err instanceof UnauthorizedException,
  );
});

test('DdollarGateTokenGuard: a truncated token is rejected (timing-safe length mismatch)', async () => {
  const config = makeConfig({
    DDOLLAR_GATE_TOKEN: 'secret-token',
    DDOLLAR_GATE_OPERATOR_USER_ID: 'op-1',
  });
  const prisma = makePrisma(operatorUser);
  const guard = new DdollarGateTokenGuard(config, prisma);
  // Truncated prefix — different length, must be rejected.
  const req: FakeRequest = {
    headers: { authorization: 'Bearer secret' },
  };
  await assert.rejects(
    () => guard.canActivate(fakeContext(req) as never),
    (err: unknown) => err instanceof UnauthorizedException,
  );
});

test('DdollarGateTokenGuard: missing DDOLLAR_GATE_TOKEN env throws UnauthorizedException', async () => {
  const config = makeConfig({
    DDOLLAR_GATE_TOKEN: undefined,
    DDOLLAR_GATE_OPERATOR_USER_ID: 'op-1',
  });
  const prisma = makePrisma(operatorUser);
  const guard = new DdollarGateTokenGuard(config, prisma);
  const req: FakeRequest = {
    headers: { authorization: 'Bearer anything' },
  };
  await assert.rejects(
    () => guard.canActivate(fakeContext(req) as never),
    (err: unknown) => err instanceof UnauthorizedException,
  );
});

test('DdollarGateTokenGuard: missing DDOLLAR_GATE_OPERATOR_USER_ID env throws UnauthorizedException', async () => {
  const config = makeConfig({
    DDOLLAR_GATE_TOKEN: 'secret-token',
    DDOLLAR_GATE_OPERATOR_USER_ID: undefined,
  });
  const prisma = makePrisma(operatorUser);
  const guard = new DdollarGateTokenGuard(config, prisma);
  const req: FakeRequest = {
    headers: { authorization: 'Bearer secret-token' },
  };
  await assert.rejects(
    () => guard.canActivate(fakeContext(req) as never),
    (err: unknown) => err instanceof UnauthorizedException,
  );
});

test('DdollarGateTokenGuard: configured operator user not in DB throws UnauthorizedException', async () => {
  const config = makeConfig({
    DDOLLAR_GATE_TOKEN: 'secret-token',
    DDOLLAR_GATE_OPERATOR_USER_ID: 'op-1',
  });
  const prisma = makePrisma(null);
  const guard = new DdollarGateTokenGuard(config, prisma);
  const req: FakeRequest = {
    headers: { authorization: 'Bearer secret-token' },
  };
  await assert.rejects(
    () => guard.canActivate(fakeContext(req) as never),
    (err: unknown) => err instanceof UnauthorizedException,
  );
});

test('DdollarGateTokenGuard: bearer header is case-insensitive on the scheme', async () => {
  const config = makeConfig({
    DDOLLAR_GATE_TOKEN: 'secret-token',
    DDOLLAR_GATE_OPERATOR_USER_ID: 'op-1',
  });
  const prisma = makePrisma(operatorUser);
  const guard = new DdollarGateTokenGuard(config, prisma);
  const req: FakeRequest = {
    headers: { authorization: 'bearer secret-token' },
  };
  const result = await guard.canActivate(fakeContext(req) as never);
  assert.equal(result, true);
  assert.equal(req.user?.id, 'op-1');
});
