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
  goal?: string;
  mode?: 'FOCUS' | 'TEAM';
  scope?: unknown;
  expectedOutput?: unknown;
  dependencies?: unknown;
  branch?: string;
  provider?: string;
  permissions?: unknown;
  budgetWeightedUnits?: number;
};

export type DecomposeFounderTaskInput = {
  specialists: Array<Omit<StartFounderTaskInput, 'workspaceKey' | 'mode'>>;
};

export type VerifyFounderMergeInput = {
  commit: string;
  changedFiles: string[];
  checks: Array<{ name: string; passed: boolean; detail?: string }>;
  summary?: string;
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
    const goal = bounded(input.goal ?? input.title, 'goal', 4_000);
    const mode = input.mode === 'TEAM' ? 'TEAM' : 'FOCUS';
    const expiresAt = leaseExpiry(now);
    const task = await this.prisma.founderCoordinationTask.upsert({
      where: { ownerUserId_clientTaskId: { ownerUserId: userId, clientTaskId } },
      create: {
        clientTaskId,
        ownerUserId: userId,
        teamId: entitlement.teamId,
        workspaceKey,
        title,
        goal,
        mode,
        scope: jsonOrNull(input.scope),
        expectedOutput: jsonOrNull(input.expectedOutput),
        dependencies: jsonOrNull(input.dependencies),
        branch: optionalBounded(input.branch, 200),
        provider: optionalBounded(input.provider, 120),
        permissions: jsonOrNull(input.permissions),
        budgetWeightedUnits: optionalBudget(input.budgetWeightedUnits),
        status: 'RUNNING',
        heartbeatAt: now,
        expiresAt,
      },
      update: {
        teamId: entitlement.teamId,
        workspaceKey,
        title,
        goal,
        mode,
        scope: jsonOrNull(input.scope),
        expectedOutput: jsonOrNull(input.expectedOutput),
        dependencies: jsonOrNull(input.dependencies),
        branch: optionalBounded(input.branch, 200),
        provider: optionalBounded(input.provider, 120),
        permissions: jsonOrNull(input.permissions),
        budgetWeightedUnits: optionalBudget(input.budgetWeightedUnits),
        status: 'RUNNING',
        heartbeatAt: now,
        expiresAt,
        completedAt: null,
      },
      include: { claims: true },
    });
    await this.audit(userId, entitlement.teamId, task.id, 'TASK_STARTED', {
      workspaceKey,
      mode,
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
        OR: [
          {
            status: { in: ['ACTIVE', 'RUNNING', 'WAITING', 'BLOCKED', 'VERIFYING'] },
            expiresAt: { gt: now },
          },
          {
            status: 'COMPLETE',
            completedAt: { gte: new Date(now.getTime() - 24 * 60 * 60_000) },
          },
        ],
        ...(entitlement.teamId
          ? { teamId: entitlement.teamId }
          : { ownerUserId: userId, teamId: null }),
      },
      include: {
        claims: { where: { expiresAt: { gt: now } }, orderBy: { path: 'asc' } },
        parent: { select: { id: true, title: true } },
        children: { select: { id: true, title: true, status: true } },
      },
      orderBy: { heartbeatAt: 'desc' },
      take: 100,
    });
  }

  async heartbeat(
    userId: string,
    taskId: string,
    status: 'RUNNING' | 'WAITING' | 'BLOCKED' | 'VERIFYING' = 'RUNNING',
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

  async decomposeTask(
    userId: string,
    taskId: string,
    input: DecomposeFounderTaskInput,
    now = new Date(),
  ) {
    const { task, entitlement } = await this.ownedTask(userId, taskId);
    if (task.mode !== 'TEAM') {
      throw new BadRequestException('Only a Team-mode goal can create specialist tasks.');
    }
    if (task.status === 'COMPLETE' || task.status === 'CANCELED') {
      throw new ConflictException('A finished goal cannot create new specialist tasks.');
    }
    if (!Array.isArray(input.specialists) || input.specialists.length < 2 || input.specialists.length > 5) {
      throw new BadRequestException('Team mode requires between 2 and 5 bounded specialist tasks.');
    }
    const ids = new Set<string>();
    const specialists = input.specialists.map((specialist) => {
      const clientTaskId = bounded(specialist.clientTaskId, 'clientTaskId', 160);
      if (ids.has(clientTaskId)) throw new BadRequestException('Specialist clientTaskId values must be unique.');
      ids.add(clientTaskId);
      return {
        clientTaskId,
        title: bounded(specialist.title, 'title', 200),
        goal: bounded(specialist.goal ?? specialist.title, 'goal', 4_000),
        scope: jsonOrNull(specialist.scope),
        expectedOutput: jsonOrNull(specialist.expectedOutput),
        dependencies: jsonOrNull(specialist.dependencies),
        branch: optionalBounded(specialist.branch, 200),
        provider: optionalBounded(specialist.provider, 120),
        permissions: jsonOrNull(specialist.permissions),
        budgetWeightedUnits: optionalBudget(specialist.budgetWeightedUnits),
      };
    });
    const requestedBudget = specialists.reduce(
      (total, specialist) => total + (specialist.budgetWeightedUnits ?? 0),
      0,
    );
    if (task.budgetWeightedUnits != null && requestedBudget > task.budgetWeightedUnits) {
      throw new BadRequestException('Specialist budgets exceed the parent goal budget.');
    }
    const expiresAt = leaseExpiry(now);
    return this.prisma.$transaction(async (tx) => {
      const children = [];
      for (const specialist of specialists) {
        children.push(await tx.founderCoordinationTask.upsert({
          where: {
            ownerUserId_clientTaskId: {
              ownerUserId: userId,
              clientTaskId: specialist.clientTaskId,
            },
          },
          create: {
            ...specialist,
            ownerUserId: userId,
            teamId: entitlement.teamId,
            workspaceKey: task.workspaceKey,
            parentTaskId: task.id,
            mode: 'FOCUS',
            status: 'RUNNING',
            heartbeatAt: now,
            expiresAt,
          },
          update: {
            ...specialist,
            teamId: entitlement.teamId,
            workspaceKey: task.workspaceKey,
            parentTaskId: task.id,
            mode: 'FOCUS',
            status: 'RUNNING',
            heartbeatAt: now,
            expiresAt,
            completedAt: null,
            resultCommit: null,
            verification: Prisma.JsonNull,
          },
          include: { claims: true },
        }));
      }
      await tx.founderCoordinationTask.update({
        where: { id: task.id },
        data: { status: 'RUNNING', heartbeatAt: now, expiresAt },
      });
      await tx.founderCoordinationAudit.create({
        data: {
          actorUserId: userId,
          teamId: entitlement.teamId,
          taskId: task.id,
          action: 'TASK_DECOMPOSED',
          details: { childTaskIds: children.map((child) => child.id), count: children.length },
        },
      });
      return children;
    });
  }

  async verifyMerge(
    userId: string,
    taskId: string,
    input: VerifyFounderMergeInput,
    now = new Date(),
  ) {
    const { task, entitlement } = await this.ownedTask(userId, taskId);
    const commit = bounded(input.commit, 'commit', 64).toLowerCase();
    if (!/^[0-9a-f]{7,64}$/.test(commit)) {
      throw new BadRequestException('commit must be a Git commit hash.');
    }
    if (!Array.isArray(input.checks) || input.checks.length === 0 || input.checks.length > 100) {
      throw new BadRequestException('At least one bounded verification check is required.');
    }
    const checks = input.checks.map((check) => ({
      name: bounded(check.name, 'check.name', 200),
      passed: check.passed === true,
      detail: optionalBounded(check.detail, 500),
    }));
    if (checks.some((check) => !check.passed)) {
      throw new ConflictException('Every verification check must pass before merge completion.');
    }
    const changedFiles = [...new Set((input.changedFiles ?? []).map(normalizedClaimPath))].slice(0, 500);
    const children = await this.prisma.founderCoordinationTask.findMany({
      where: { parentTaskId: task.id },
      select: { id: true, title: true, status: true },
    });
    const incomplete = children.filter((child) => child.status !== 'COMPLETE');
    if (incomplete.length > 0) {
      throw new ConflictException({
        error: 'specialists_incomplete',
        tasks: incomplete,
      });
    }
    const verification = {
      version: 1,
      verifiedAt: now.toISOString(),
      summary: optionalBounded(input.summary, 1_000),
      checks,
      changedFiles,
      specialistTaskIds: children.map((child) => child.id),
    };
    const [updated] = await this.prisma.$transaction([
      this.prisma.founderCoordinationTask.update({
        where: { id: task.id },
        data: {
          status: 'COMPLETE',
          resultCommit: commit,
          verification,
          completedAt: now,
          heartbeatAt: now,
          expiresAt: now,
        },
      }),
      this.prisma.founderCoordinationPathClaim.deleteMany({ where: { taskId: task.id } }),
      this.prisma.founderCoordinationAudit.create({
        data: {
          actorUserId: userId,
          teamId: entitlement.teamId,
          taskId: task.id,
          action: 'MERGE_VERIFIED',
          details: { commit, checks: checks.length, changedFiles: changedFiles.length },
        },
      }),
    ]);
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
    if (status === 'COMPLETE' && task.mode === 'TEAM') {
      throw new ConflictException('Team goals require a verified merge receipt before completion.');
    }
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
