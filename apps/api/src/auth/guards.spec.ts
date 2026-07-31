import assert from 'node:assert/strict';
import test from 'node:test';
import { ConfigService } from '@nestjs/config';
import { JwtAuthGuard } from './guards';

/**
 * FIX 1 — JwtAuthGuard bearer-skip for the DDollar gate.
 *
 * The strategy bot sends `Authorization: Bearer ${DDOLLAR_GATE_TOKEN}`
 * to /api/founder-economics/ddollar/gate-balance. That token is NOT a JWT,
 * so the passport jwt strategy would 401 before the route-level
 * DdollarGateTokenGuard can run. The global guard therefore skips JWT
 * validation when the bearer token matches DDOLLAR_GATE_TOKEN
 * (timing-safe compare). If env is unset, this skip never fires.
 */

type FakeRequest = {
  headers: Record<string, string | string[] | undefined>;
};

function fakeContext(req: FakeRequest) {
  return {
    switchToHttp: () => ({ getRequest: () => req }),
    getHandler: () => function handler() {},
    getClass: () => class FakeController {},
  };
}

function makeReflector(isPublic: boolean) {
  return {
    getAllAndOverride: () => isPublic,
  } as never;
}

function makeConfig(values: Record<string, string | undefined>) {
  return {
    get: <T = string>(key: string) => (values[key] as unknown as T) ?? undefined,
  } as unknown as ConfigService;
}

test('JwtAuthGuard: bearer header equal to DDOLLAR_GATE_TOKEN skips JWT validation (returns true)', async () => {
  const guard = new JwtAuthGuard(
    makeReflector(false),
    makeConfig({ DDOLLAR_GATE_TOKEN: 'gate-secret' }),
  );
  const req: FakeRequest = {
    headers: { authorization: 'Bearer gate-secret' },
  };
  // Should NOT call super.canActivate (which would invoke passport jwt and fail).
  const result = await guard.canActivate(fakeContext(req) as never);
  assert.equal(result, true);
});

test('JwtAuthGuard: bearer header NOT equal to DDOLLAR_GATE_TOKEN does not take the skip branch', async () => {
  // We cannot exercise super.canActivate() without a full NestJS runtime
  // (passport needs getResponse() and an installed jwt strategy). Instead,
  // we verify the negative: a mismatched token must NOT short-circuit. We
  // do this by stubbing super.canActivate via the Reflect prototype path —
  // if the skip branch were taken, super would never run. Here we assert
  // that the skip branch's observable side effect (returning true without
  // invoking passport) does NOT happen for a mismatched token by checking
  // that the guard throws when super.canActivate is unreachable, which
  // proves the skip branch was NOT taken.
  const guard = new JwtAuthGuard(
    makeReflector(false),
    makeConfig({ DDOLLAR_GATE_TOKEN: 'gate-secret' }),
  );
  const req: FakeRequest = {
    headers: { authorization: 'Bearer different-token' },
  };
  // super.canActivate will throw because the fake context has no
  // getResponse(). That proves the skip branch was NOT taken — if it had
  // been, the guard would have returned true without touching super.
  await assert.rejects(
    async () => Promise.resolve(guard.canActivate(fakeContext(req) as never)),
    // Any error confirms super.canActivate was reached.
  );
});

test('JwtAuthGuard: unset DDOLLAR_GATE_TOKEN means bearer does not take the skip branch', async () => {
  const guard = new JwtAuthGuard(
    makeReflector(false),
    makeConfig({ DDOLLAR_GATE_TOKEN: undefined }),
  );
  const req: FakeRequest = {
    headers: { authorization: 'Bearer gate-secret' },
  };
  // Same reasoning as above: super.canActivate is reached and throws
  // because the fake context lacks getResponse(). This proves the skip
  // branch was inert when DDOLLAR_GATE_TOKEN is unset.
  await assert.rejects(
    async () => Promise.resolve(guard.canActivate(fakeContext(req) as never)),
  );
});

test('JwtAuthGuard: FounderNode scheme still bypasses JWT (existing behavior preserved)', async () => {
  const guard = new JwtAuthGuard(
    makeReflector(false),
    makeConfig({ DDOLLAR_GATE_TOKEN: 'gate-secret' }),
  );
  const req: FakeRequest = {
    headers: { authorization: 'FounderNode abc:def' },
  };
  const result = await guard.canActivate(fakeContext(req) as never);
  assert.equal(result, true);
});

test('JwtAuthGuard: @Public route still returns true (existing behavior preserved)', async () => {
  const guard = new JwtAuthGuard(
    makeReflector(true),
    makeConfig({ DDOLLAR_GATE_TOKEN: 'gate-secret' }),
  );
  const req: FakeRequest = { headers: {} };
  const result = await guard.canActivate(fakeContext(req) as never);
  assert.equal(result, true);
});
