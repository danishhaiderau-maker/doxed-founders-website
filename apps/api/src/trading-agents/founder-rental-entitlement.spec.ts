import assert from 'node:assert/strict';
import test from 'node:test';
import { FounderPresenceLevel, UserRole } from '@prisma/client';
import {
  freeRentalExpiresAt,
  recordFounderRentalGrant,
  resolveFounderRentalEntitlement,
} from './founder-rental-entitlement';

function prismaMock(overrides: {
  operatorId?: string;
  role?: UserRole | null;
  presenceLevel?: FounderPresenceLevel | null;
} = {}) {
  const user = overrides.role
    ? { role: overrides.role }
    : null;
  const founder = overrides.presenceLevel
    ? { presenceLevel: overrides.presenceLevel }
    : null;
  return {
    user: { findUnique: async () => user },
    founder: { findUnique: async () => founder },
    pointLedger: {
      create: async (args: { data: unknown }) => ({ id: 'ledger-1', ...(args.data as object) }),
    },
  } as unknown as Parameters<typeof resolveFounderRentalEntitlement>[0];
}

function withOperatorId<T>(value: string | undefined, fn: () => T): T {
  const prev = process.env.DDOLLAR_GATE_OPERATOR_USER_ID;
  if (value === undefined) delete process.env.DDOLLAR_GATE_OPERATOR_USER_ID;
  else process.env.DDOLLAR_GATE_OPERATOR_USER_ID = value;
  try {
    return fn();
  } finally {
    if (prev === undefined) delete process.env.DDOLLAR_GATE_OPERATOR_USER_ID;
    else process.env.DDOLLAR_GATE_OPERATOR_USER_ID = prev;
  }
}

test('freeRentalExpiresAt returns null (permanent, no expiry)', () => {
  assert.equal(freeRentalExpiresAt(), null);
});

test('operator is entitled for free rental', async () => {
  await withOperatorId('owner-123', async () => {
    const res = await resolveFounderRentalEntitlement(prismaMock(), 'owner-123');
    assert.equal(res.entitled, true);
    assert.equal(res.reason, 'operator');
  });
});

test('admin role is entitled for free rental', async () => {
  await withOperatorId(undefined, async () => {
    const res = await resolveFounderRentalEntitlement(
      prismaMock({ role: UserRole.ADMIN }),
      'user-1',
    );
    assert.equal(res.entitled, true);
    assert.equal(res.reason, 'admin');
  });
});

test('verified founder presence is entitled for free rental', async () => {
  await withOperatorId(undefined, async () => {
    const res = await resolveFounderRentalEntitlement(
      prismaMock({ presenceLevel: FounderPresenceLevel.PROVEN_FOUNDER }),
      'user-1',
    );
    assert.equal(res.entitled, true);
    assert.equal(res.reason, 'verified_founder');
  });
});

test('unverified founder is NOT entitled', async () => {
  await withOperatorId(undefined, async () => {
    const res = await resolveFounderRentalEntitlement(
      prismaMock({ presenceLevel: FounderPresenceLevel.UNVERIFIED }),
      'user-1',
    );
    assert.equal(res.entitled, false);
    assert.equal(res.reason, null);
  });
});

test('ordinary user is NOT entitled (normal rental path unchanged)', async () => {
  await withOperatorId(undefined, async () => {
    const res = await resolveFounderRentalEntitlement(
      prismaMock({ role: UserRole.USER }),
      'user-1',
    );
    assert.equal(res.entitled, false);
    assert.equal(res.reason, null);
  });
});

test('recordFounderRentalGrant writes a zero-amount auditable ledger entry', async () => {
  const captured: { userId: string; amount: number; actionKey: string; label: string }[] = [];
  const prisma = {
    pointLedger: {
      create: async (args: {
        data: { userId: string; amount: number; actionKey: string; label: string };
      }) => {
        captured.push(args.data);
        return { id: 'ledger-1' };
      },
    },
  } as unknown as Parameters<typeof recordFounderRentalGrant>[0];

  await recordFounderRentalGrant(prisma, {
    userId: 'user-1',
    agentSlug: 'conservative-btc',
    agentName: 'Conservative BTC Agent',
    reason: 'verified_founder',
  });

  assert.equal(captured.length, 1, 'expected exactly one ledger entry to be written');
  assert.equal(captured[0].amount, 0, 'grant must never move DDollar');
  assert.equal(captured[0].actionKey, 'FOUNDER_RENTAL_GRANT');
  assert.equal(captured[0].userId, 'user-1');
  assert.match(captured[0].label, /verified_founder/);
  assert.match(captured[0].label, /no DDollar deducted/);
});
