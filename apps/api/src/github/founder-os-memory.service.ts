import { Injectable, Logger } from '@nestjs/common';
import {
  BuildQueueStatus,
  RoadmapStatus,
} from '@prisma/client';
import {
  FOUNDER_OS_MEMORY_FILES,
  buildDecisionsMarkdown,
  buildGoalContractJsonFile,
  buildLaunchChecklistMarkdown,
  buildProjectContextMarkdown,
  buildRoadmapMarkdown,
  buildTasksJsonFile,
  parseGoalContractJson,
  parseTasksJson,
} from '@dcf/utils';
import { GitHubApiService } from '../github/github-api.service';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class FounderOsMemoryService {
  private readonly logger = new Logger(FounderOsMemoryService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly github: GitHubApiService,
  ) {}

  async bootstrapRepoMemory(userId: string, repo: string) {
    const hasToken = await this.github.hasToken(userId);
    if (!hasToken) {
      this.logger.log(`Skip GitHub memory bootstrap for ${repo} - no PAT`);
      return { bootstrapped: false, reason: 'no_token' as const };
    }

    const context = await this.github.getRepoFile(
      userId,
      repo,
      FOUNDER_OS_MEMORY_FILES.projectContext,
    );
    if (context) {
      return { bootstrapped: false, reason: 'already_exists' as const };
    }

    await this.syncProjectMemoryToRepo(userId, repo);
    return { bootstrapped: true };
  }

  async syncProjectMemoryToRepo(userId: string, repo?: string | null) {
    const resolved = repo ?? (await this.github.resolveRepo(userId, null, null));
    if (!resolved) return { synced: false as const, reason: 'no_repo' as const };

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
    if (!founder) return { synced: false as const, reason: 'no_founder' as const };

    const project = founder.projects[0];
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
    });
    const openQueue = await this.prisma.buildQueueItem.findMany({
      where: {
        founderId: founder.id,
        status: { notIn: [BuildQueueStatus.DISMISSED, BuildQueueStatus.DONE] },
        kind: 'TASK',
      },
      orderBy: [{ sortOrder: 'asc' }, { createdAt: 'desc' }],
      take: 20,
    });
    const lastEvent = await this.prisma.founderEvent.findFirst({
      where: { founderId: founder.id },
      orderBy: { createdAt: 'desc' },
    });
    const commits = await this.github.listCommits(userId, resolved, 1);

    const fallbackGoal =
      settings?.currentGoalFocus?.trim()
      || openQueue[0]?.title
      || project?.roadmapItems.find(
        (item) => item.status === RoadmapStatus.IN_PROGRESS,
      )?.title
      || 'Define your next milestone';
    const sourceUpdatedAt = latestSourceTimestamp([
      founder.updatedAt,
      project?.updatedAt,
      settings?.updatedAt,
      lastEvent?.createdAt,
      ...openQueue.map((task) => task.updatedAt),
      ...(project?.roadmapItems ?? []).map((item) => item.updatedAt),
    ]);
    const goalContract = buildGoalContractJsonFile({
      memoryGraph: settings?.memoryGraph,
      fallbackGoal,
      updatedAt: sourceUpdatedAt,
    });
    const currentGoal = goalContract.currentGoal;
    const progressPercent = Math.min(
      100,
      Math.round(
        (
          project?.roadmapItems.filter(
            (item) => item.status === RoadmapStatus.DONE,
          ).length ?? 0
        )
          / Math.max(project?.roadmapItems.length ?? 1, 1)
          * 100,
      ),
    );

    const projectContext = buildProjectContextMarkdown({
      projectName: project?.name ?? founder.name,
      currentGoal,
      progressPercent,
      lastCommit: commits[0]?.message ?? null,
      lastActivity: lastEvent?.createdAt.toISOString() ?? null,
    });
    const roadmap = buildRoadmapMarkdown(
      (project?.roadmapItems ?? []).map((item) => ({
        title: item.title,
        status: item.status,
      })),
    );
    const tasksJson = buildTasksJsonFile({
      currentGoal,
      updatedAt: sourceUpdatedAt,
      tasks: openQueue.map((task) => ({
        id: task.id,
        title: task.title,
        status: task.status,
        kind: task.kind,
        done: false,
      })),
    });

    const hasToken = await this.github.hasToken(userId);
    if (!hasToken) {
      return { synced: false as const, reason: 'no_token' as const };
    }

    const [decisions, checklist] = await Promise.all([
      this.github.getRepoFile(
        userId,
        resolved,
        FOUNDER_OS_MEMORY_FILES.decisions,
      ),
      this.github.getRepoFile(
        userId,
        resolved,
        FOUNDER_OS_MEMORY_FILES.launchChecklist,
      ),
    ]);
    const memoryFiles = [
      {
        path: FOUNDER_OS_MEMORY_FILES.projectContext,
        content: projectContext,
      },
      {
        path: FOUNDER_OS_MEMORY_FILES.goalContract,
        content: `${JSON.stringify(goalContract, null, 2)}\n`,
      },
      { path: FOUNDER_OS_MEMORY_FILES.roadmap, content: roadmap },
      {
        path: FOUNDER_OS_MEMORY_FILES.tasks,
        content: `${JSON.stringify(tasksJson, null, 2)}\n`,
      },
      ...(!decisions
        ? [{
            path: FOUNDER_OS_MEMORY_FILES.decisions,
            content: buildDecisionsMarkdown(),
          }]
        : []),
      ...(!checklist
        ? [{
            path: FOUNDER_OS_MEMORY_FILES.launchChecklist,
            content: buildLaunchChecklistMarkdown(),
          }]
        : []),
    ];

    const batch = await this.github.upsertRepoFilesBatch(
      userId,
      resolved,
      memoryFiles,
      'chore(founder-os): sync goal and project memory',
    );
    if (batch.updated === 0 && batch.skipped === memoryFiles.length) {
      return { synced: true as const, repo: resolved, unchanged: true as const };
    }

    this.logger.log(`Synced Founder OS memory to ${resolved}`);
    return { synced: true as const, repo: resolved };
  }

  async readRepoMemory(userId: string, repo?: string | null) {
    const resolved = repo ?? (await this.github.resolveRepo(userId, null, null));
    if (!resolved) return null;

    const [projectContext, goalRaw, roadmap, tasksRaw] = await Promise.all([
      this.github.getRepoFile(
        userId,
        resolved,
        FOUNDER_OS_MEMORY_FILES.projectContext,
      ),
      this.github.getRepoFile(
        userId,
        resolved,
        FOUNDER_OS_MEMORY_FILES.goalContract,
      ),
      this.github.getRepoFile(
        userId,
        resolved,
        FOUNDER_OS_MEMORY_FILES.roadmap,
      ),
      this.github.getRepoFile(
        userId,
        resolved,
        FOUNDER_OS_MEMORY_FILES.tasks,
      ),
    ]);

    if (!projectContext && !goalRaw && !roadmap && !tasksRaw) return null;

    const goalContract = goalRaw ? parseGoalContractJson(goalRaw) : null;
    const tasksFile = tasksRaw ? parseTasksJson(tasksRaw) : null;

    return {
      repoFullName: resolved,
      projectContext,
      goalContract,
      roadmap,
      tasksFile,
      currentGoalFromRepo:
        goalContract?.currentGoal ?? tasksFile?.currentGoal ?? null,
      openTasksFromRepo:
        tasksFile?.tasks.filter((task) => !task.done).slice(0, 8) ?? [],
    };
  }
}

function latestSourceTimestamp(values: Array<Date | null | undefined>): string {
  return values
    .filter((value): value is Date => value instanceof Date)
    .map((value) => value.toISOString())
    .sort()
    .at(-1) ?? new Date(0).toISOString();
}
