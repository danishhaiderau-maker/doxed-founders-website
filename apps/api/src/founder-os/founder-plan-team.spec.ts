import assert from 'node:assert/strict';
import { ForbiddenException } from '@nestjs/common';
import { describe, it } from 'node:test';
import { FounderPlanTeamService } from './founder-plan-team.service';
import type { FounderPlanEntitlement } from './founder-plan-entitlements.service';

function entitlement(role: 'owner' | 'admin' | 'member'): FounderPlanEntitlement {
  return {
    plan: 'team', quotaOwnerKey: 'team:team-1', weeklyWeightedUnitCap: 20_000_000,
    currentPeriodStart: null, currentPeriodEnd: null, priceCentsMonthly: null,
    teamId: 'team-1', teamName: 'Team One', teamRole: role, coordination: true,
    remoteControl: true, rolesAndAudit: true, requiresXVerification: false,
  };
}

function entitlements(role: 'owner' | 'admin' | 'member') {
  return { resolve: async () => entitlement(role) };
}

describe('Founder Team role enforcement', () => {
  it('allows an admin to add a member and records an audit event', async () => {
    const audits: unknown[] = [];
    const prisma = {
      user: { findUnique: async () => ({ id: 'user-2' }) },
      founderPlanTeamMember: {
        upsert: async ({ create }: any) => ({ id: 'member-2', ...create }),
      },
      founderCoordinationAudit: {
        create: async (value: unknown) => { audits.push(value); return {}; },
      },
    };
    const service = new FounderPlanTeamService(prisma as never, entitlements('admin') as never);
    const member = await service.addMember('user-1', 'new@example.com', 'MEMBER');
    assert.equal(member.role, 'MEMBER');
    assert.equal((audits[0] as any).data.action, 'TEAM_MEMBER_ADDED');
  });

  it('prevents an admin from promoting another admin', async () => {
    const service = new FounderPlanTeamService({} as never, entitlements('admin') as never);
    await assert.rejects(
      service.addMember('user-1', 'new@example.com', 'ADMIN'),
      ForbiddenException,
    );
  });

  it('keeps role changes owner-only', async () => {
    const service = new FounderPlanTeamService({} as never, entitlements('admin') as never);
    await assert.rejects(
      service.changeRole('user-1', 'member-2', 'ADMIN'),
      ForbiddenException,
    );
  });

  it('prevents an admin from removing an owner or admin', async () => {
    const prisma = {
      founderPlanTeamMember: {
        findUnique: async () => ({ id: 'member-2', teamId: 'team-1', userId: 'user-2', role: 'ADMIN' }),
      },
    };
    const service = new FounderPlanTeamService(prisma as never, entitlements('admin') as never);
    await assert.rejects(service.removeMember('user-1', 'member-2'), ForbiddenException);
  });
});
