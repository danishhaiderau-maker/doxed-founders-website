import { Injectable } from '@nestjs/common';
import { BuildQueueStatus, RoadmapStatus } from '@prisma/client';
import {
  buildMemoryPrefix,
  extractVaultRelaySummary,
  mergeFounderMemoryGraph,
  parseFounderMemoryGraph,
  type DeviceMemoryMetadataPayload,
  type DeviceMemoryPayload,
  type FounderMemoryGraph,
  type FounderMemoryGraphPatch,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { GitHubApiService } from '../github/github-api.service';

@Injectable()
export class FounderMemoryGraphService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly github: GitHubApiService,
  ) {}

  async resolveForUser(userId: string): Promise<FounderMemoryGraph> {
    const hints = await this.collectHints(userId);
    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const stored = parseFounderMemoryGraph(settings?.memoryGraph);
    return mergeFounderMemoryGraph(stored, hints);
  }

  getPrefix(graph: FounderMemoryGraph): string {
    return buildMemoryPrefix(graph);
  }

  async getPrefixForUser(userId: string): Promise<string> {
    return this.getPrefix(await this.resolveForUser(userId));
  }

  async patchForUser(
    userId: string,
    patch: FounderMemoryGraphPatch,
  ): Promise<FounderMemoryGraph> {
    const merged = mergeFounderMemoryGraph(
      parseFounderMemoryGraph(
        (await this.prisma.founderBuilderSettings.findUnique({ where: { userId } }))?.memoryGraph,
      ),
      await this.collectHints(userId),
      patch,
    );

    await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: {
        userId,
        memoryGraph: merged as object,
        currentGoalFocus: merged.active_goal,
      },
      update: {
        memoryGraph: merged as object,
        currentGoalFocus: merged.active_goal,
      },
    });

    return merged;
  }

  private async collectHints(userId: string) {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: {
        projects: {
          where: { approved: true },
          take: 1,
          include: { roadmapItems: { orderBy: { sortOrder: 'asc' } } },
        },
      },
    });

    const project = founder?.projects[0];
    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });

    const openQueue = founder
      ? await this.prisma.buildQueueItem.findMany({
          where: {
            founderId: founder.id,
            status: { notIn: [BuildQueueStatus.DISMISSED, BuildQueueStatus.DONE] },
          },
          orderBy: [{ sortOrder: 'asc' }, { createdAt: 'desc' }],
          take: 10,
        })
      : [];

    const tasks = openQueue.filter((i) => i.kind === 'TASK');
    const ideas = openQueue.filter((i) => i.kind === 'IDEA');

    const repo = project
      ? await this.github.resolveRepo(userId, founder?.githubRepoFullName, project.githubRepoFullName)
      : null;

    const currentBranch = repo ? await this.github.getDefaultBranch(userId, repo) : null;

    let currentPr: string | null = null;
    const prItem = openQueue.find((i) => {
      const meta = i.metadata as Record<string, unknown> | null;
      return typeof meta?.prUrl === 'string' || typeof meta?.pullRequestUrl === 'string';
    });
    if (prItem?.metadata && typeof prItem.metadata === 'object') {
      const meta = prItem.metadata as Record<string, unknown>;
      currentPr =
        (typeof meta.prUrl === 'string' && meta.prUrl) ||
        (typeof meta.pullRequestUrl === 'string' && meta.pullRequestUrl) ||
        null;
    }

    const deviceSyncRow = await this.prisma.projectMemoryDeviceSync.findUnique({
      where: { userId },
    });
    const connectedNodes =
      settings?.memoryStorageMode === 'FOUNDER_NODE'
        ? (
            await this.prisma.founderNode.findMany({
              where: { userId },
              orderBy: { lastSeenAt: 'desc' },
            })
          ).map((n) => ({
            nodeId: n.nodeId,
            label: n.label,
            status:
              n.lastSeenAt && Date.now() - n.lastSeenAt.getTime() < 180_000
                ? ('online' as const)
                : ('offline' as const),
            lastSeenAt: n.lastSeenAt?.toISOString() ?? null,
            ramGb: n.ramGb,
            storageGb: n.storageGb,
            storageFreeGb: n.storageFreeGb,
            vaultHealthy: n.vaultHealthy,
            platform: n.platform,
          }))
        : undefined;

    const vaultRelay = extractVaultRelaySummary({
      memoryStorageMode: settings?.memoryStorageMode ?? 'PLATFORM',
      deviceSync: deviceSyncRow
        ? {
            updatedAt: deviceSyncRow.updatedAt.toISOString(),
            deviceLabel: deviceSyncRow.deviceLabel,
            payload: deviceSyncRow.payload as DeviceMemoryPayload | DeviceMemoryMetadataPayload,
          }
        : null,
      connectedNodes,
    });

    const goalFromVault = vaultRelay?.currentGoal?.trim();
    const activeGoal =
      settings?.currentGoalFocus?.trim() ||
      goalFromVault ||
      ideas[0]?.title ||
      project?.roadmapItems.find((r) => r.status === RoadmapStatus.IN_PROGRESS)?.title ||
      project?.roadmapItems[0]?.title ||
      'Define your next milestone';

    const currentTask = tasks[0]?.title ?? ideas[0]?.title ?? null;
    const nextAction =
      tasks[1]?.title ??
      openQueue.find((i) => i.kind === 'GITHUB_ISSUE')?.title ??
      (currentTask ? `Continue: ${currentTask}` : `Start: ${activeGoal}`);

    return {
      projectName: project?.name ?? founder?.name ?? 'My startup',
      activeGoal,
      currentTask,
      nextAction,
      currentBranch,
      currentPr,
    };
  }
}
