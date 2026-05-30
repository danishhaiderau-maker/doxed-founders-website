import { Injectable, Logger } from '@nestjs/common';
import {
  BuildQueueStatus,
  RoadmapStatus,
} from '@prisma/client';
import {
  FOUNDER_OS_MEMORY_FILES,
  buildDecisionsMarkdown,
  buildLaunchChecklistMarkdown,
  buildProjectContextMarkdown,
  buildRoadmapMarkdown,
  buildTasksJsonFile,
  formatRelativeTime,
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
      this.logger.log(`Skip GitHub memory bootstrap for ${repo} — no PAT`);
      return { bootstrapped: false, reason: 'no_token' as const };
    }

    const context = await this.github.getRepoFile(userId, repo, FOUNDER_OS_MEMORY_FILES.projectContext);
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
    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
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

    const currentGoal =
      settings?.currentGoalFocus?.trim() ||
      openQueue[0]?.title ||
      project?.roadmapItems.find((r) => r.status === RoadmapStatus.IN_PROGRESS)?.title ||
      'Define your next milestone';

    const progressPercent = Math.min(
      100,
      Math.round(
        (project?.roadmapItems.filter((r) => r.status === RoadmapStatus.DONE).length ?? 0) /
          Math.max(project?.roadmapItems.length ?? 1, 1) *
          100,
      ),
    );

    const projectContext = buildProjectContextMarkdown({
      projectName: project?.name ?? founder.name,
      currentGoal,
      progressPercent,
      lastCommit: commits[0]?.message ?? null,
      lastActivity: lastEvent ? formatRelativeTime(lastEvent.createdAt) : null,
    });

    const roadmap = buildRoadmapMarkdown(
      (project?.roadmapItems ?? []).map((r) => ({
        title: r.title,
        status: r.status,
      })),
    );

    const tasksJson = buildTasksJsonFile({
      currentGoal,
      tasks: openQueue.map((t) => ({
        id: t.id,
        title: t.title,
        status: t.status,
        kind: t.kind,
        done: false,
      })),
    });

    const hasToken = await this.github.hasToken(userId);
    if (!hasToken) {
      return { synced: false as const, reason: 'no_token' as const };
    }

    await this.github.upsertRepoFile(
      userId,
      resolved,
      FOUNDER_OS_MEMORY_FILES.projectContext,
      projectContext,
      'chore(founder-os): sync project context',
    );
    await this.github.upsertRepoFile(
      userId,
      resolved,
      FOUNDER_OS_MEMORY_FILES.roadmap,
      roadmap,
      'chore(founder-os): sync roadmap',
    );
    await this.github.upsertRepoFile(
      userId,
      resolved,
      FOUNDER_OS_MEMORY_FILES.tasks,
      `${JSON.stringify(tasksJson, null, 2)}\n`,
      'chore(founder-os): sync tasks',
    );

    const decisions = await this.github.getRepoFile(userId, resolved, FOUNDER_OS_MEMORY_FILES.decisions);
    if (!decisions) {
      await this.github.upsertRepoFile(
        userId,
        resolved,
        FOUNDER_OS_MEMORY_FILES.decisions,
        buildDecisionsMarkdown(),
        'chore(founder-os): init decisions log',
      );
    }

    const checklist = await this.github.getRepoFile(userId, resolved, FOUNDER_OS_MEMORY_FILES.launchChecklist);
    if (!checklist) {
      await this.github.upsertRepoFile(
        userId,
        resolved,
        FOUNDER_OS_MEMORY_FILES.launchChecklist,
        buildLaunchChecklistMarkdown(),
        'chore(founder-os): init launch checklist',
      );
    }

    this.logger.log(`Synced Founder OS memory to ${resolved}`);
    return { synced: true as const, repo: resolved };
  }

  async readRepoMemory(userId: string, repo?: string | null) {
    const resolved = repo ?? (await this.github.resolveRepo(userId, null, null));
    if (!resolved) return null;

    const [projectContext, roadmap, tasksRaw] = await Promise.all([
      this.github.getRepoFile(userId, resolved, FOUNDER_OS_MEMORY_FILES.projectContext),
      this.github.getRepoFile(userId, resolved, FOUNDER_OS_MEMORY_FILES.roadmap),
      this.github.getRepoFile(userId, resolved, FOUNDER_OS_MEMORY_FILES.tasks),
    ]);

    if (!projectContext && !roadmap && !tasksRaw) return null;

    const tasksFile = tasksRaw ? parseTasksJson(tasksRaw) : null;

    return {
      repoFullName: resolved,
      projectContext,
      roadmap,
      tasksFile,
      currentGoalFromRepo: tasksFile?.currentGoal ?? null,
      openTasksFromRepo: tasksFile?.tasks.filter((t) => !t.done).slice(0, 8) ?? [],
    };
  }
}
