import { Injectable } from '@nestjs/common';
import { Prisma } from '@prisma/client';
import type { FounderAgentRunRecord, FounderAgentRunWorker } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

const ACTIVE_RUN_KEY = '_activeAgentRun';

@Injectable()
export class FounderAgentRunService {
  constructor(private readonly prisma: PrismaService) {}

  async getActive(userId: string): Promise<FounderAgentRunRecord | null> {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    const graph = settings?.memoryGraph;
    if (!graph || typeof graph !== 'object' || Array.isArray(graph)) return null;
    const raw = (graph as Record<string, unknown>)[ACTIVE_RUN_KEY];
    if (!raw || typeof raw !== 'object') return null;
    return raw as FounderAgentRunRecord;
  }

  async start(
    userId: string,
    input: {
      worker: FounderAgentRunWorker;
      status: string;
      task: string;
      repository?: string | null;
      agentId?: string | null;
      runId?: string | null;
      conversationId?: string | null;
    },
  ) {
    const now = new Date().toISOString();
    const record: FounderAgentRunRecord = {
      worker: input.worker,
      status: input.status,
      task: input.task.slice(0, 1200),
      repository: input.repository ?? null,
      agentId: input.agentId ?? null,
      runId: input.runId ?? null,
      conversationId: input.conversationId ?? null,
      prUrl: null,
      branch: null,
      terminal: false,
      startedAt: now,
      updatedAt: now,
    };
    await this.save(userId, record);
    return record;
  }

  async patch(
    userId: string,
    patch: Partial<
      Pick<
        FounderAgentRunRecord,
        'status' | 'prUrl' | 'branch' | 'terminal' | 'agentId' | 'runId' | 'conversationId'
      >
    >,
  ) {
    const current = (await this.getActive(userId)) ?? null;
    if (!current) return null;
    const record: FounderAgentRunRecord = {
      ...current,
      ...patch,
      updatedAt: new Date().toISOString(),
    };
    await this.save(userId, record);
    return record;
  }

  async clear(userId: string) {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    const base =
      settings?.memoryGraph && typeof settings.memoryGraph === 'object' && !Array.isArray(settings.memoryGraph)
        ? { ...(settings.memoryGraph as Record<string, unknown>) }
        : {};
    delete base[ACTIVE_RUN_KEY];
    await this.prisma.founderBuilderSettings.updateMany({
      where: { userId },
      data: { memoryGraph: base as Prisma.InputJsonValue },
    });
  }

  private async save(userId: string, record: FounderAgentRunRecord) {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { memoryGraph: true },
    });
    const base =
      settings?.memoryGraph && typeof settings.memoryGraph === 'object' && !Array.isArray(settings.memoryGraph)
        ? { ...(settings.memoryGraph as Record<string, unknown>) }
        : {};
    base[ACTIVE_RUN_KEY] = record;
    await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: {
        userId,
        memoryGraph: base as Prisma.InputJsonValue,
      },
      update: {
        memoryGraph: base as Prisma.InputJsonValue,
      },
    });
  }
}
