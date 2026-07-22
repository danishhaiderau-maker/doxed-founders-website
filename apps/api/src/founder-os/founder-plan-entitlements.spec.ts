import assert from 'node:assert/strict';
import { describe, it } from 'node:test';
import {
  FounderPlanEntitlementsService,
  FOUNDER_BUILDER_MONTHLY_PRICE_CENTS,
  FOUNDER_BUILDER_WEEKLY_WEIGHTED_UNITS,
} from './founder-plan-entitlements.service';

const now = new Date('2026-07-22T12:00:00.000Z');
const activeStart = new Date('2026-07-20T00:00:00.000Z');
const activeEnd = new Date('2026-08-20T00:00:00.000Z');

function service(options?: { direct?: unknown; membership?: unknown }) {
  return new FounderPlanEntitlementsService({
    founderPlanSubscription: { findUnique: async () => options?.direct ?? null },
    founderPlanTeamMember: { findFirst: async () => options?.membership ?? null },
  } as never);
}

describe('Founder plan entitlements', () => {
  it('defaults to the recurring Founder Free allowance', async () => {
    const result = await service().resolve('user-1', now);
    assert.equal(result.plan, 'free');
    assert.equal(result.quotaOwnerKey, 'user:user-1');
    assert.equal(result.weeklyWeightedUnitCap, 200_000);
    assert.equal(result.requiresXVerification, true);
    assert.equal(result.coordination, false);
  });

  it('resolves an active Builder subscription', async () => {
    const result = await service({
      direct: {
        tier: 'BUILDER', status: 'ACTIVE',
        currentPeriodStart: activeStart, currentPeriodEnd: activeEnd,
      },
    }).resolve('user-1', now);
    assert.equal(result.plan, 'builder');
    assert.equal(result.weeklyWeightedUnitCap, FOUNDER_BUILDER_WEEKLY_WEIGHTED_UNITS);
    assert.equal(result.priceCentsMonthly, FOUNDER_BUILDER_MONTHLY_PRICE_CENTS);
    assert.equal(result.coordination, true);
    assert.equal(result.remoteControl, true);
    assert.equal(result.requiresXVerification, false);
  });

  it('shares one quota owner across active Team members', async () => {
    const result = await service({
      membership: {
        role: 'ADMIN',
        team: {
          id: 'team-1', name: 'Launch Lab', weeklyWeightedUnitCap: 12_000_000,
          subscription: {
            tier: 'TEAM', status: 'ACTIVE',
            currentPeriodStart: activeStart, currentPeriodEnd: activeEnd,
          },
        },
      },
    }).resolve('user-2', now);
    assert.equal(result.plan, 'team');
    assert.equal(result.quotaOwnerKey, 'team:team-1');
    assert.equal(result.weeklyWeightedUnitCap, 12_000_000);
    assert.equal(result.teamRole, 'admin');
    assert.equal(result.rolesAndAudit, true);
    assert.equal(result.priceCentsMonthly, null);
  });

  it('fails back to Free after a paid period expires', async () => {
    const result = await service({
      direct: {
        tier: 'BUILDER', status: 'ACTIVE',
        currentPeriodStart: new Date('2026-06-01T00:00:00.000Z'),
        currentPeriodEnd: new Date('2026-07-01T00:00:00.000Z'),
      },
    }).resolve('user-1', now);
    assert.equal(result.plan, 'free');
  });

  it('keeps a past-due Builder in a bounded three-day grace period', async () => {
    const result = await service({
      direct: {
        tier: 'BUILDER', status: 'PAST_DUE',
        currentPeriodStart: new Date('2026-06-20T00:00:00.000Z'),
        currentPeriodEnd: new Date('2026-07-21T00:00:00.000Z'),
      },
    }).resolve('user-1', now);
    assert.equal(result.plan, 'builder');
  });
});
