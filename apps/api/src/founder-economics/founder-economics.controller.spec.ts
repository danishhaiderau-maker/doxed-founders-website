import assert from 'node:assert/strict';
import test from 'node:test';
import { FounderEconomicsController } from './founder-economics.controller';
import type { AuthUser } from '../auth/auth.types';

/**
 * DDollar gate shim — FIX 1.
 *
 * The strategy bot's DDOLLAR_GATE_URL expects JSON with one of
 * `ddollar | balance | ddollarBalance | points` as a numeric field. The
 * existing `/api/founder-economics/ddollar/balance` endpoint returns
 * `{ rawDdollar, ... }`, which does not match that contract.
 *
 * The new `ddollar/gate-balance` endpoint returns exactly
 * `{ ddollarBalance: <number> }` translated from the same rawDdollar
 * source (User.reputationPoints via the snapshot). It is authenticated
 * via the standard @CurrentUser session — no separate token, no bypass.
 *
 * Auth itself is enforced by the global JwtAuthGuard (registered in
 * app.module.ts as APP_GUARD). The endpoint does NOT use @Public(), so
 * NestJS rejects unauthenticated requests with 401 before the handler
 * runs. We assert the contract the guard relies on:
 *   - the handler must NOT be marked @Public
 *   - given an AuthUser, the response shape is exactly { ddollarBalance }
 *   - the value is a finite non-negative number derived from rawDdollar
 */
type ExportedSnapshot = {
  founders: Array<{ userId: string; rawDdollar: number }>;
};

function makeController(snapshot: ExportedSnapshot) {
  const ddollarEngine = {
    exportSnapshot: async () => snapshot,
  };
  const stubs = {
    gdpService: { computeGdp: () => ({}) },
    knowledgeGraph: { recentKnowledge: () => [] },
    proof: { founderProofs: () => [], milestoneTiersTable: () => [] },
    settlement: {
      epochHistory: () => [],
      claimableForFounder: async () => [],
      settleCurrentEpoch: async () => ({}),
    },
  };
  return new FounderEconomicsController(
    stubs.gdpService as never,
    ddollarEngine as never,
    stubs.knowledgeGraph as never,
    stubs.proof as never,
    stubs.settlement as never,
  );
}

function reflectPublicMetadata(): boolean {
  // Reflect over the handler to confirm the @Public() decorator was NOT
  // applied. The global JwtAuthGuard treats IS_PUBLIC_KEY as a bypass
  // signal; the shim must rely on the standard session instead.
  // eslint-disable-next-line @typescript-eslint/no-require-imports
  const { IS_PUBLIC_KEY } = require('../auth/public.decorator');
  const controllerProto = FounderEconomicsController.prototype;
  const meta = Reflect.getMetadata(
    IS_PUBLIC_KEY,
    controllerProto.ddollarGateBalance,
  );
  return meta === true;
}

test('ddollar/gate-balance returns exactly { ddollarBalance: <number> }', async () => {
  const controller = makeController({
    founders: [{ userId: 'user-1', rawDdollar: 123.456 }],
  });
  const user: AuthUser = {
    id: 'user-1',
    email: 'founder@example.com',
    name: 'Founder',
    role: 'FOUNDER',
    reputationPoints: 999,
    contributorLevel: 1,
  };
  const result = await controller.ddollarGateBalance(user);
  assert.deepEqual(result, { ddollarBalance: 123.46 });
});

test('ddollar/gate-balance translates rawDdollar from the snapshot when present', async () => {
  const controller = makeController({
    founders: [{ userId: 'user-2', rawDdollar: 500 }],
  });
  const user: AuthUser = {
    id: 'user-2',
    email: 'founder@example.com',
    name: 'Founder',
    role: 'FOUNDER',
    reputationPoints: 0,
    contributorLevel: 1,
  };
  const result = await controller.ddollarGateBalance(user);
  assert.equal(result.ddollarBalance, 500);
  assert.equal(typeof result.ddollarBalance, 'number');
  assert.equal(Number.isFinite(result.ddollarBalance), true);
  assert.equal(result.ddollarBalance >= 0, true);
});

test('ddollar/gate-balance falls back to user.reputationPoints when the founder is not in the snapshot', async () => {
  const controller = makeController({ founders: [] });
  const user: AuthUser = {
    id: 'user-3',
    email: 'founder@example.com',
    name: 'Founder',
    role: 'FOUNDER',
    reputationPoints: 42,
    contributorLevel: 1,
  };
  const result = await controller.ddollarGateBalance(user);
  assert.equal(result.ddollarBalance, 42);
});

test('ddollar/gate-balance never leaks rawDdollar, userId, or any other field', async () => {
  const controller = makeController({
    founders: [{ userId: 'user-4', rawDdollar: 7 }],
  });
  const user: AuthUser = {
    id: 'user-4',
    email: 'leak@example.com',
    name: 'Founder',
    role: 'FOUNDER',
    reputationPoints: 7,
    contributorLevel: 1,
  };
  const result = await controller.ddollarGateBalance(user);
  assert.deepEqual(Object.keys(result).sort(), ['ddollarBalance']);
});

test('ddollar/gate-balance requires the standard authenticated session (not @Public)', () => {
  // The global JwtAuthGuard (app.module.ts APP_GUARD) rejects requests
  // without a valid session unless the handler is marked @Public. This
  // shim must NOT be public — the bot authenticates via the session
  // cookie issued to the operator account.
  assert.equal(
    reflectPublicMetadata(),
    false,
    'ddollar/gate-balance must not be marked @Public — auth is required',
  );
});
