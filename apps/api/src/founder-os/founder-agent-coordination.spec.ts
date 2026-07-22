import assert from 'node:assert/strict';
import { ConflictException, ForbiddenException } from '@nestjs/common';
import { describe, it } from 'node:test';
import { FounderAgentCoordinationService } from './founder-agent-coordination.service';
import type { FounderPlanEntitlement } from './founder-plan-entitlements.service';

const builder: FounderPlanEntitlement = {
  plan: 'builder', quotaOwnerKey: 'user:user-1', weeklyWeightedUnitCap: 5_000_000,
  currentPeriodStart: null, currentPeriodEnd: null, priceCentsMonthly: 3_500,
  teamId: null, teamName: null, teamRole: null, coordination: true,
  remoteControl: true, rolesAndAudit: false, requiresXVerification: false,
};

const team = (role: 'owner' | 'admin' | 'member'): FounderPlanEntitlement => ({
  ...builder,
  plan: 'team', quotaOwnerKey: 'team:team-1', teamId: 'team-1', teamName: 'Team One',
  teamRole: role, rolesAndAudit: true, priceCentsMonthly: null,
});

function entitlements(value: FounderPlanEntitlement) {
  return { resolve: async () => value };
}

describe('Founder agent coordination service', () => {
  it('denies managed multi-agent coordination on Free', async () => {
    const service = new FounderAgentCoordinationService({} as never, entitlements({
      ...builder,
      plan: 'free', coordination: false, remoteControl: false,
      weeklyWeightedUnitCap: 200_000, priceCentsMonthly: 0, requiresXVerification: true,
    }) as never);
    await assert.rejects(
      service.startTask('user-1', {
        clientTaskId: 'local-1', workspaceKey: 'repo:abc', title: 'Build settings',
      }),
      ForbiddenException,
    );
  });

  it('starts a Builder task with a bounded lease and audit event', async () => {
    const calls: Array<{ kind: string; value: unknown }> = [];
    const prisma = {
      founderCoordinationTask: {
        upsert: async (value: unknown) => {
          calls.push({ kind: 'task', value });
          return { id: 'task-1', branch: 'feature/a', claims: [] };
        },
      },
      founderCoordinationAudit: {
        create: async (value: unknown) => {
          calls.push({ kind: 'audit', value });
          return { id: 'audit-1' };
        },
      },
    };
    const service = new FounderAgentCoordinationService(prisma as never, entitlements(builder) as never);
    const result = await service.startTask('user-1', {
      clientTaskId: 'local-1', workspaceKey: 'Repo:ABC', title: 'Build settings', branch: 'feature/a',
    }, new Date('2026-07-22T12:00:00.000Z'));
    assert.equal(result.id, 'task-1');
    assert.equal(calls[0]?.kind, 'task');
    assert.equal(calls[1]?.kind, 'audit');
    assert.equal((calls[0]?.value as any).create.workspaceKey, 'repo:abc');
    assert.equal((calls[0]?.value as any).create.expiresAt.toISOString(), '2026-07-22T12:03:00.000Z');
  });

  it('lists all active tasks in the same Team workspace', async () => {
    let query: any;
    const prisma = {
      founderCoordinationTask: {
        findMany: async (value: unknown) => { query = value; return []; },
      },
    };
    const service = new FounderAgentCoordinationService(prisma as never, entitlements(team('member')) as never);
    await service.listTasks('user-2', 'repo:abc', new Date('2026-07-22T12:00:00.000Z'));
    assert.equal(query.where.teamId, 'team-1');
    assert.equal(query.where.ownerUserId, undefined);
  });

  it('rejects a fresh competing path claim and records the rejection', async () => {
    const audit: unknown[] = [];
    const task = {
      id: 'task-2', ownerUserId: 'user-2', teamId: 'team-1', workspaceKey: 'repo:abc',
    };
    const tx = {
      founderCoordinationPathClaim: {
        findUnique: async () => ({
          id: 'claim-1', taskId: 'task-1', generation: 1,
          expiresAt: new Date('2026-07-22T12:03:00.000Z'),
          task: { ownerUserId: 'user-1', title: 'Edit settings' },
        }),
      },
      founderCoordinationAudit: {
        create: async (value: unknown) => { audit.push(value); return {}; },
      },
    };
    const prisma = {
      founderCoordinationTask: { findUnique: async () => task },
      $transaction: async (work: (client: typeof tx) => unknown) => work(tx),
    };
    const service = new FounderAgentCoordinationService(prisma as never, entitlements(team('member')) as never);
    await assert.rejects(
      service.claimPath('user-2', 'task-2', { path: 'src/settings.ts' }, new Date('2026-07-22T12:00:00.000Z')),
      ConflictException,
    );
    assert.equal(audit.length, 1);
    assert.equal((audit[0] as any).data.action, 'CLAIM_REJECTED');
  });

  it('keeps Team-wide audit restricted to owners and admins', async () => {
    const service = new FounderAgentCoordinationService({} as never, entitlements(team('member')) as never);
    await assert.rejects(service.auditTrail('user-2'), ForbiddenException);
  });

  it('prevents a Team peer from mutating another member task', async () => {
    const prisma = {
      founderCoordinationTask: {
        findUnique: async () => ({ id: 'task-1', ownerUserId: 'user-1', teamId: 'team-1' }),
      },
    };
    const service = new FounderAgentCoordinationService(prisma as never, entitlements(team('admin')) as never);
    await assert.rejects(service.heartbeat('user-2', 'task-1'), ForbiddenException);
  });
});
