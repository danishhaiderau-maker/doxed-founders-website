import {
  BadRequestException,
  ConflictException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { Prisma, type FounderCoordinationTaskStatus } from '@prisma/client';
import { randomUUID } from 'node:crypto';
import { PrismaService } from '../prisma/prisma.service';
import {
  FounderPlanEntitlementsService,
  type FounderPlanEntitlement,
} from './founder-plan-entitlements.service';

export const FOUNDER_COORDINATION_LEASE_MS = 3 * 60_000;

export type StartFounderTaskInput = {
  clientTaskId: string;
  workspaceKey: string;
  title: string;
  scope?: unknown;
  branch?: string;
  provider?: string;
  permissions?: unknown;
  budgetWeightedUnits?: number;
};

export type ClaimFounderPathInput = {
  path: string;
};

@Injectable()
export class FounderAgentCoordinationService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly entitlements: FounderPlanEntitlementsService,
  ) {}

  async startTask(userId: string, input: StartFounderTaskInput, now = new Date()) {
    const entitlement = await this.requireCoordination(userId);
    const clientTaskId = bounded(input.clientTaskId, 'clientTaskId', 160);
    const workspaceKey = workspaceKeyOf(input.workspaceKey);
    const title = bounded(input.title, 'title', 200);
    const expiresAt = leaseExpiry(now);
    const task = await this.prisma.founderCoordinationTask.upsert({
      where: { ownerUserId_clientTaskId: { ownerUserId: userId, clientTaskId } },
      create: {
        clientTaskId,
        ownerUserId: userId,
        teamId: entitlement.teamId,
        workspaceKey,
        title,
        scope: jsonOrNull(input.scope),
        branch: optionalBounded(input.branch, 200),
        provider: optionalBounded(input.provider, 120),
        permissions: jsonOrNull(input.permissions),
        budgetWeightedUnits: optionalBudget(input.budgetWeightedUnits),
        status: 'ACTIVE',
        heartbeatAt: now,
        expiresAt,
      },
      update: {
        teamId: entitlement.teamId,
        workspaceKey,
        title,
        scope: jsonOrNull(input.scope),
        branch: optionalBounded(input.branch, 200),
        provider: optionalBounded(input.provider, 120),
        permissions: jsonOrNull(input.permissions),
        budgetWeightedUnits: optionalBudget(input.budgetWeightedUnits),
        status: 'ACTIVE',
        heartbeatAt: now,
        expiresAt,
        completedAt: null,
      },
      include: { claims: true },
    });
    await this.audit(userId, entitlement.teamId, task.id, 'TASK_STARTED', {
      workspaceKey,
      branch: task.branch,
    });
    return task;
  }

  async listTasks(userId: string, rawWorkspaceKey: string, now = new Date()) {
    const entitlement = await this.requireCoordination(userId);
    const workspaceKey = workspaceKeyOf(rawWorkspaceKey);
    return this.prisma.founderCoordinationTask.findMany({
      where: {
        workspaceKey,
        status: { in: ['ACTIVE', 'WAITING'] },
        expiresAt: { gt: now },
        ...(entitlement.teamId
          ? { teamId: entitlement.teamId }
          : { ownerUserId: userId, teamId: null }),
      },
      include: {
        claims: { where: { expiresAt: { gt: now } }, orderBy: { path: 'asc' } },
      },
      orderBy: { heartbeatAt: 'desc' },
      take: 100,
    });
  }

  async heartbeat(
    userId: string,
    taskId: string,
    status: 'ACTIVE' | 'WAITING' = 'ACTIVE',
    now = new Date(),
  ) {
    const { task, entitlement } = await this.ownedTask(userId, taskId);
    const expiresAt = leaseExpiry(now);
    const [updated] = await this.prisma.$transaction([
      this.prisma.founderCoordinationTask.update({
        where: { id: task.id },
        data: { status, heartbeatAt: now, expiresAt },
        include: { claims: true },
      }),
      this.prisma.founderCoordinationPathClaim.updateMany({
        where: { taskId: task.id },
        data: { heartbeatAt: now, expiresAt },
      }),
    ]);
    await this.audit(userId, entitlement.teamId, task.id, 'TASK_HEARTBEAT', { status });
    return updated;
  }

  async claimPath(userId: string, taskId: string, input: ClaimFounderPathInput, now = new Date()) {
    const { task, entitlement } = await this.ownedTask(userId, taskId);
    const claimedPath = normalizedClaimPath(input.path);
    const expiresAt = leaseExpiry(now);
    try {
      const result = await this.prisma.$transaction(async (tx) => {
        const existing = await tx.founderCoordinationPathClaim.findUnique({
          where: { workspaceKey_path: { workspaceKey: task.workspaceKey, path: claimedPath } },
          include: { task: { select: { ownerUserId: true, title: true } } },
        });
        if (existing && existing.taskId !== task.id && existing.expiresAt > now) {
          await tx.founderCoordinationAudit.create({
            data: {
              actorUserId: userId,
              teamId: entitlement.teamId,
              taskId: task.id,
              action: 'CLAIM_REJECTED',
              details: { path: claimedPath, ownerTaskId: existing.taskId },
            },
          });
          return {
            conflict: {
              ownerTaskId: existing.taskId,
              ownerTitle: existing.task.title,
              path: claimedPath,
            },
          };
        }
        if (existing?.taskId === task.id) {
          const claim = await tx.founderCoordinationPathClaim.update({
            where: { id: existing.id },
            data: { heartbeatAt: now, expiresAt },
          });
          return { claim };
        }
        const generation = (existing?.generation ?? 0) + 1;
        if (existing) {
          await tx.founderCoordinationPathClaim.delete({ where: { id: existing.id } });
        }
        const claim = await tx.founderCoordinationPathClaim.create({
          data: {
            taskId: task.id,
            workspaceKey: task.workspaceKey,
            path: claimedPath,
            fencingToken: `${generation}-${randomUUID()}`,
            generation,
            heartbeatAt: now,
            expiresAt,
          },
        });
        await tx.founderCoordinationAudit.create({
          data: {
            actorUserId: userId,
            teamId: entitlement.teamId,
            taskId: task.id,
            action: 'CLAIM_ACQUIRED',
            details: { path: claimedPath, generation },
          },
        });
        return { claim };
      });
      if ('conflict' in result && result.conflict) {
        throw new ConflictException({
          error: 'path_claimed',
          ...result.conflict,
        });
      }
      return result.claim;
    } catch (error) {
      if (error instanceof ConflictException) throw error;
      if (error instanceof Prisma.PrismaClientKnownRequestError && error.code === 'P2002') {
        throw new ConflictException({ error: 'path_claimed', path: claimedPath });
      }
      throw error;
    }
  }

  async releasePath(userId: string, taskId: string, rawPath: string) {
    const { task, entitlement } = await this.ownedTask(userId, taskId);
    const claimedPath = normalizedClaimPath(rawPath);
    const claim = await this.prisma.founderCoordinationPathClaim.findUnique({
      where: { workspaceKey_path: { workspaceKey: task.workspaceKey, path: claimedPath } },
    });
    if (!claim || claim.taskId !== task.id) throw new NotFoundException('Path claim not found.');
    await this.prisma.$transaction([
      this.prisma.founderCoordinationPathClaim.delete({ where: { id: claim.id } }),
      this.prisma.founderCoordinationAudit.create({
        data: {
          actorUserId: userId,
          teamId: entitlement.teamId,
          taskId: task.id,
          action: 'CLAIM_RELEASED',
          details: { path: claimedPath, generation: claim.generation },
        },
      }),
    ]);
    return { released: true, path: claimedPath };
  }

  async finishTask(
    userId: string,
    taskId: string,
    status: Extract<FounderCoordinationTaskStatus, 'COMPLETE' | 'CANCELED'>,
    now = new Date(),
  ) {
    const { task, entitlement } = await this.ownedTask(userId, taskId);
    const [updated] = await this.prisma.$transaction([
      this.prisma.founderCoordinationTask.update({
        where: { id: task.id },
        data: { status, completedAt: now, expiresAt: now, heartbeatAt: now },
      }),
      this.prisma.founderCoordinationPathClaim.deleteMany({ where: { taskId: task.id } }),
      this.prisma.founderCoordinationAudit.create({
        data: {
          actorUserId: userId,
          teamId: entitlement.teamId,
          taskId: task.id,
          action: status === 'COMPLETE' ? 'TASK_COMPLETED' : 'TASK_CANCELED',
        },
      }),
    ]);
    return updated;
  }

  async auditTrail(userId: string, rawWorkspaceKey?: string) {
    const entitlement = await this.requireCoordination(userId);
    if (entitlement.teamId && entitlement.teamRole === 'member') {
      throw new ForbiddenException('Team audit is available to owners and admins.');
    }
    const workspaceKey = rawWorkspaceKey ? workspaceKeyOf(rawWorkspaceKey) : undefined;
    return this.prisma.founderCoordinationAudit.findMany({
      where: {
        ...(entitlement.teamId ? { teamId: entitlement.teamId } : { actorUserId: userId }),
        ...(workspaceKey ? { task: { is: { workspaceKey } } } : {}),
      },
      orderBy: { createdAt: 'desc' },
      take: 250,
    });
  }

  private async ownedTask(userId: string, taskId: string) {
    const entitlement = await this.requireCoordination(userId);
    const task = await this.prisma.founderCoordinationTask.findUnique({ where: { id: taskId } });
    if (!task) throw new NotFoundException('Founder task not found.');
    if (task.ownerUserId !== userId) {
      throw new ForbiddenException('Only the task owner may mutate its heartbeat or path claims.');
    }
    if ((entitlement.teamId ?? null) !== (task.teamId ?? null)) {
      throw new ForbiddenException('Task does not belong to the active plan workspace.');
    }
    return { task, entitlement };
  }

  private async requireCoordination(userId: string): Promise<FounderPlanEntitlement> {
    const entitlement = await this.entitlements.resolve(userId);
    if (!entitlement.coordination) {
      throw new ForbiddenException('Multi-agent coordination requires Founder Builder or Team.');
    }
    return entitlement;
  }

  private audit(
    actorUserId: string,
    teamId: string | null,
    taskId: string,
    action: string,
    details?: Prisma.InputJsonValue,
  ) {
    return this.prisma.founderCoordinationAudit.create({
      data: { actorUserId, teamId, taskId, action, details },
    });
  }
}

function leaseExpiry(now: Date): Date {
  return new Date(now.getTime() + FOUNDER_COORDINATION_LEASE_MS);
}

function bounded(value: unknown, label: string, max: number): string {
  if (typeof value !== 'string' || !value.trim()) throw new BadRequestException(`${label} is required.`);
  return value.trim().slice(0, max);
}

function optionalBounded(value: unknown, max: number): string | null {
  return typeof value === 'string' && value.trim() ? value.trim().slice(0, max) : null;
}

function workspaceKeyOf(value: unknown): string {
  const key = bounded(value, 'workspaceKey', 160);
  if (!/^[a-zA-Z0-9:._-]+$/.test(key)) {
    throw new BadRequestException('workspaceKey must be an opaque hash or stable repository identifier.');
  }
  return key.toLowerCase();
}

function normalizedClaimPath(value: unknown): string {
  const claimed = bounded(value, 'path', 600).replaceAll('\\', '/').replace(/^\.\//, '');
  if (claimed.startsWith('/') || claimed.split('/').includes('..')) {
    throw new BadRequestException('Path claims must be workspace-relative.');
  }
  return claimed.toLowerCase();
}

function optionalBudget(value: unknown): number | null {
  if (value == null) return null;
  if (!Number.isInteger(value) || Number(value) < 0 || Number(value) > 100_000_000) {
    throw new BadRequestException('budgetWeightedUnits must be an integer between 0 and 100,000,000.');
  }
  return Number(value);
}

function jsonOrNull(value: unknown): Prisma.InputJsonValue | typeof Prisma.JsonNull {
  return value == null ? Prisma.JsonNull : value as Prisma.InputJsonValue;
}
