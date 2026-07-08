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

  /**
   * In-memory cooldown map: userId → epoch ms of last successful sync.
   *
   * Why this exists: the sync writes `formatRelativeTime(lastEvent.createdAt)`
   * strings ("5 minutes ago") into `project-context.md`. Those strings change
   * every few minutes even when nothing material changed, so a sync running
   * every 15 minutes produces a stream of `chore(founder-os): sync memory`
   * commits that don't reflect real product state. Each commit cancels the
   * in-progress Vercel build, which stuck production 9+ hours behind at one
   * point.
   *
   * The 24h throttle is the server-side source of truth — regardless of who
   * calls the endpoint (web button, founder node loop, or external script),
   * at most one commit per founder per day actually lands. If the API
   * process restarts, the map resets and one extra sync slips through —
   * that's acceptable. Persisting this to Prisma would require a migration
   * for one column; not worth it.
   */
  private readonly lastSyncAt = new Map<string, number>();
  private static readonly SYNC_COOLDOWN_MS = 24 * 60 * 60 * 1000; // 24 hours

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
    // 24h cooldown — see `lastSyncAt` doc above. Real changes to roadmap /
    // tasks / goal still land within 24h, which is plenty for an AI coding
    // agent reading `project-context.md`. The previous every-15-min cadence
    // was breaking the Vercel pipeline (each commit canceled the prior build).
    const last = this.lastSyncAt.get(userId);
    const now = Date.now();
    if (last && now - last < FounderOsMemoryService.SYNC_COOLDOWN_MS) {
      const minutesLeft = Math.ceil(
        (FounderOsMemoryService.SYNC_COOLDOWN_MS - (now - last)) / 60_000,
      );
      this.logger.debug(
        `Skipping memory sync for ${userId} — cooldown (${minutesLeft} min left)`,
      );
      return {
        synced: false as const,
        reason: 'cooldown' as const,
        retryAfterMinutes: minutesLeft,
      };
    }

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

    const tasksContent = `${JSON.stringify(tasksJson, null, 2)}\n`;
    const memoryFiles = [
      { path: FOUNDER_OS_MEMORY_FILES.projectContext, content: projectContext },
      { path: FOUNDER_OS_MEMORY_FILES.roadmap, content: roadmap },
      { path: FOUNDER_OS_MEMORY_FILES.tasks, content: tasksContent },
    ];

    const batch = await this.github.upsertRepoFilesBatch(
      userId,
      resolved,
      memoryFiles,
      'chore(founder-os): sync memory (context + roadmap + tasks)',
    );
    // Record cooldown timestamp regardless of whether files changed — even
    // an "unchanged" sync counts as a successful poll, so we don't retry
    // every 15 minutes for 24 hours just because the content happened to
    // match this time.
    this.lastSyncAt.set(userId, Date.now());
    if (batch.updated === 0 && batch.skipped === memoryFiles.length) {
      return { synced: true as const, repo: resolved, unchanged: true as const };
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
