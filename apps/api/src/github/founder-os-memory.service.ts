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

type MemoryFile = { path: string; content: string };

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
    const commits = await this.github.listCommits(userId, resolved, 20);
    const latestProductCommit = commits.find(
      (commit) => !commit.message.startsWith('chore(founder-os): sync'),
    );

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
      lastCommit: latestProductCommit?.message ?? null,
      lastActivity: lastEvent ? formatRelativeTime(lastEvent.createdAt) : null,
    });

    const roadmap = buildRoadmapMarkdown(
      (project?.roadmapItems ?? []).map((r) => ({
        title: r.title,
        status: r.status,
      })),
    );

    let tasksJson = buildTasksJsonFile({
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

    const [existingProjectContext, existingRoadmap, existingTasksRaw] = await Promise.all([
      this.github.getRepoFile(userId, resolved, FOUNDER_OS_MEMORY_FILES.projectContext),
      this.github.getRepoFile(userId, resolved, FOUNDER_OS_MEMORY_FILES.roadmap),
      this.github.getRepoFile(userId, resolved, FOUNDER_OS_MEMORY_FILES.tasks),
    ]);

    const existingTasks = existingTasksRaw ? parseTasksJson(existingTasksRaw) : null;
    if (existingTasks && this.sameTasksPayload(existingTasks, tasksJson)) {
      tasksJson = { ...tasksJson, updatedAt: existingTasks.updatedAt };
    }

    const desiredFiles: MemoryFile[] = [
      { path: FOUNDER_OS_MEMORY_FILES.projectContext, content: projectContext },
      { path: FOUNDER_OS_MEMORY_FILES.roadmap, content: roadmap },
      { path: FOUNDER_OS_MEMORY_FILES.tasks, content: `${JSON.stringify(tasksJson, null, 2)}\n` },
    ];
    const existingByPath = new Map<string, string | null>([
      [FOUNDER_OS_MEMORY_FILES.projectContext, existingProjectContext],
      [FOUNDER_OS_MEMORY_FILES.roadmap, existingRoadmap],
      [FOUNDER_OS_MEMORY_FILES.tasks, existingTasksRaw],
    ]);
    const changedFiles = desiredFiles.filter((file) =>
      this.memoryFileChanged(file, existingByPath.get(file.path) ?? null),
    );

    if (changedFiles.length > 0) {
      await this.github.upsertRepoFiles(
        userId,
        resolved,
        changedFiles,
        'chore(founder-os): sync memory (context + roadmap + tasks)',
      );
    }

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

    if (changedFiles.length === 0) {
      this.logger.log(`Founder OS memory unchanged for ${resolved}`);
      return { synced: false as const, reason: 'unchanged' as const, repo: resolved };
    }

    this.logger.log(`Synced ${changedFiles.length} Founder OS memory file(s) to ${resolved}`);
    return { synced: true as const, repo: resolved, changed: changedFiles.length };
  }

  private sameTasksPayload(
    a: NonNullable<ReturnType<typeof parseTasksJson>>,
    b: ReturnType<typeof buildTasksJsonFile>,
  ): boolean {
    return (
      a.currentGoal === b.currentGoal &&
      JSON.stringify(a.tasks) === JSON.stringify(b.tasks)
    );
  }

  private memoryFileChanged(file: MemoryFile, existing: string | null): boolean {
    if (existing == null) return true;
    if (file.path === FOUNDER_OS_MEMORY_FILES.projectContext) {
      return this.normalizeProjectContext(existing) !== this.normalizeProjectContext(file.content);
    }
    return existing.trimEnd() !== file.content.trimEnd();
  }

  private normalizeProjectContext(content: string): string {
    return content
      .replace(/## Last Activity\n\n[\s\S]*?(?=\n## |\n*$)/, '## Last Activity\n\n_ignored for idempotency_')
      .trimEnd();
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
