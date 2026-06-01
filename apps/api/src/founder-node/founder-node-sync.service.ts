import { BadRequestException, Injectable } from '@nestjs/common';
import {
  FounderNodeSyncJobKind,
  FounderNodeSyncJobStatus,
  MemoryStorageMode,
  Prisma,
} from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';

const ONLINE_WINDOW_MS = 3 * 60 * 1000;
const JOB_TTL_MS = 5 * 60 * 1000;
const STALE_PROCESSING_MS = 90_000;
const POLL_MS = 400;
const DEFAULT_JOB_TIMEOUT_MS = 45_000;
const BLOCKING_JOB_TIMEOUT_MS = 180_000;

@Injectable()
export class FounderNodeSyncService {
  constructor(private readonly prisma: PrismaService) {}

  async getV2Status(userId: string) {
    const nodes = await this.prisma.founderNode.findMany({
      where: { userId },
      orderBy: { lastSeenAt: 'desc' },
    });
    const onlineNode = nodes.find(
      (n) => n.lastSeenAt != null && Date.now() - n.lastSeenAt.getTime() < ONLINE_WINDOW_MS,
    );
    const pendingJobs = await this.prisma.founderNodeSyncJob.count({
      where: {
        userId,
        status: { in: [FounderNodeSyncJobStatus.PENDING, FounderNodeSyncJobStatus.PROCESSING] },
      },
    });

    const activeNode = onlineNode ?? nodes[0] ?? null;

    return {
      paired: nodes.length > 0,
      online: Boolean(onlineNode),
      nodeId: activeNode?.nodeId ?? null,
      nodeLabel: onlineNode?.label ?? nodes[0]?.label ?? null,
      appVersion: onlineNode?.appVersion ?? nodes[0]?.appVersion ?? null,
      vectorChunks: onlineNode?.vectorChunks ?? nodes[0]?.vectorChunks ?? null,
      vectorIndexedAt:
        onlineNode?.vectorIndexedAt?.toISOString() ??
        nodes[0]?.vectorIndexedAt?.toISOString() ??
        null,
      lastPullSyncAt:
        onlineNode?.lastPullSyncAt?.toISOString() ??
        nodes[0]?.lastPullSyncAt?.toISOString() ??
        null,
      pendingJobs,
      bidirectionalSync: Boolean(onlineNode),
    };
  }

  async enqueuePushGoal(userId: string, goal: string) {
    const trimmed = goal.trim();
    if (!trimmed) throw new BadRequestException('Goal required');
    return this.enqueueJob(userId, FounderNodeSyncJobKind.PUSH_GOAL, { goal: trimmed });
  }

  async enqueuePushTask(userId: string, title: string, taskId?: string) {
    const trimmed = title.trim();
    if (!trimmed) throw new BadRequestException('Task title required');
    return this.enqueueJob(userId, FounderNodeSyncJobKind.PUSH_TASK, {
      title: trimmed,
      ...(taskId ? { taskId } : {}),
    });
  }

  async searchVault(userId: string, query: string, topK = 5) {
    const trimmed = query.trim();
    if (!trimmed) throw new BadRequestException('Search query required');
    const job = await this.enqueueJob(userId, FounderNodeSyncJobKind.VAULT_SEARCH, {
      query: trimmed,
      topK,
    });
    const result = await this.waitForJob(job.id, BLOCKING_JOB_TIMEOUT_MS);
    if (!result.ok) throw new BadRequestException(result.error);
    return result.result;
  }

  async runAgent(userId: string, agent: string, payload: Record<string, unknown> = {}) {
    const job = await this.enqueueJob(userId, FounderNodeSyncJobKind.RUN_AGENT, {
      agent,
      ...payload,
    });
    const result = await this.waitForJob(job.id, BLOCKING_JOB_TIMEOUT_MS);
    if (!result.ok) throw new BadRequestException(result.error);
    return result.result;
  }

  async maybeEnqueueGoalPush(userId: string, goal: string | null | undefined) {
    if (!goal?.trim()) return null;
    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    if (settings?.memoryStorageMode !== MemoryStorageMode.FOUNDER_NODE) return null;
    const node = await this.findOnlineNode(userId);
    if (!node) return null;
    return this.enqueueJob(
      userId,
      FounderNodeSyncJobKind.PUSH_GOAL,
      { goal: goal.trim() },
      node.nodeId,
    );
  }

  async claimPending(nodeId: string) {
    const now = new Date();
    const staleBefore = new Date(Date.now() - STALE_PROCESSING_MS);

    const stale = await this.prisma.founderNodeSyncJob.findFirst({
      where: {
        nodeId,
        status: FounderNodeSyncJobStatus.PROCESSING,
        updatedAt: { lt: staleBefore },
        expiresAt: { gt: now },
      },
      orderBy: { createdAt: 'asc' },
    });
    if (stale) {
      await this.prisma.founderNodeSyncJob.update({
        where: { id: stale.id },
        data: {
          status: FounderNodeSyncJobStatus.PENDING,
          error: 'Reclaimed after stale processing — retrying',
        },
      });
    }

    const job = await this.prisma.founderNodeSyncJob.findFirst({
      where: {
        nodeId,
        status: FounderNodeSyncJobStatus.PENDING,
        expiresAt: { gt: now },
      },
      orderBy: { createdAt: 'asc' },
    });
    if (!job) return null;

    await this.prisma.founderNodeSyncJob.update({
      where: { id: job.id },
      data: { status: FounderNodeSyncJobStatus.PROCESSING, error: null },
    });

    return {
      id: job.id,
      kind: job.kind,
      payload: job.payload,
    };
  }

  async completeJob(
    nodeId: string,
    jobId: string,
    input: { result?: Record<string, unknown>; error?: string },
  ) {
    const job = await this.prisma.founderNodeSyncJob.findFirst({
      where: { id: jobId, nodeId },
    });
    if (!job) return { success: false };

    if (input.error?.trim()) {
      await this.prisma.founderNodeSyncJob.update({
        where: { id: jobId },
        data: {
          status: FounderNodeSyncJobStatus.FAILED,
          error: input.error.trim().slice(0, 500),
        },
      });
      return { success: true };
    }

    const result = input.result ?? { ok: true };
    await this.prisma.founderNodeSyncJob.update({
      where: { id: jobId },
      data: {
        status: FounderNodeSyncJobStatus.DONE,
        result: result as Prisma.InputJsonValue,
      },
    });

    const node = await this.prisma.founderNode.findUnique({ where: { nodeId } });
    if (node) {
      const chunks =
        typeof (result as { chunks?: unknown }).chunks === 'number'
          ? (result as { chunks: number }).chunks
          : undefined;
      await this.prisma.founderNode.update({
        where: { id: node.id },
        data: {
          lastPullSyncAt: new Date(),
          ...(typeof chunks === 'number'
            ? { vectorChunks: chunks, vectorIndexedAt: new Date() }
            : {}),
        },
      });
    }

    return { success: true };
  }

  private async enqueueJob(
    userId: string,
    kind: FounderNodeSyncJobKind,
    payload: Record<string, unknown>,
    preferredNodeId?: string,
  ) {
    const node = preferredNodeId
      ? await this.prisma.founderNode.findFirst({ where: { userId, nodeId: preferredNodeId } })
      : await this.findOnlineNode(userId);

    if (!node) {
      throw new BadRequestException('Founder Node is offline — open the tray app on your desktop');
    }

    return this.prisma.founderNodeSyncJob.create({
      data: {
        userId,
        nodeId: node.nodeId,
        kind,
        payload: payload as Prisma.InputJsonValue,
        expiresAt: new Date(Date.now() + JOB_TTL_MS),
      },
    });
  }

  private async waitForJob(jobId: string, timeoutMs = DEFAULT_JOB_TIMEOUT_MS) {
    const started = Date.now();
    while (Date.now() - started < timeoutMs) {
      const job = await this.prisma.founderNodeSyncJob.findUnique({ where: { id: jobId } });
      if (!job) return { ok: false as const, error: 'Sync job not found' };
      if (job.status === FounderNodeSyncJobStatus.DONE && job.result) {
        return { ok: true as const, result: job.result as Record<string, unknown> };
      }
      if (job.status === FounderNodeSyncJobStatus.FAILED) {
        return { ok: false as const, error: job.error ?? 'Founder Node sync job failed' };
      }
      if (job.status === FounderNodeSyncJobStatus.EXPIRED) {
        return { ok: false as const, error: 'Sync job expired — is Founder Node running?' };
      }
      await new Promise((r) => setTimeout(r, POLL_MS));
    }

    await this.prisma.founderNodeSyncJob.update({
      where: { id: jobId },
      data: {
        status: FounderNodeSyncJobStatus.EXPIRED,
        error: 'Timed out waiting for Founder Node',
      },
    });
    return {
      ok: false as const,
      error:
        'Timed out waiting for Founder Node — open the tray app, update to Founder Node v0.5.0+, then retry Rebuild vector index',
    };
  }

  private async findOnlineNode(userId: string) {
    const nodes = await this.prisma.founderNode.findMany({
      where: { userId },
      orderBy: { lastSeenAt: 'desc' },
    });
    return (
      nodes.find(
        (n) => n.lastSeenAt != null && Date.now() - n.lastSeenAt.getTime() < ONLINE_WINDOW_MS,
      ) ?? null
    );
  }
}
