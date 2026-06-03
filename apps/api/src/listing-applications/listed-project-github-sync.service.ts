import { Injectable, Logger, OnModuleDestroy, OnModuleInit } from '@nestjs/common';
import { FounderEventType } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { EventsService } from '../events/events.service';

const SYNC_INTERVAL_MS = 30 * 60 * 1000;
const LOOKBACK_MS = 7 * 24 * 60 * 60 * 1000;

type PublicCommit = {
  sha: string;
  commit: { message: string; author?: { date?: string } };
};

@Injectable()
export class ListedProjectGithubSyncService implements OnModuleInit, OnModuleDestroy {
  private readonly logger = new Logger(ListedProjectGithubSyncService.name);
  private timer: ReturnType<typeof setInterval> | null = null;
  private running = false;

  constructor(
    private readonly prisma: PrismaService,
    private readonly events: EventsService,
  ) {}

  onModuleInit() {
    setTimeout(() => void this.syncAll(), 60_000);
    this.timer = setInterval(() => void this.syncAll(), SYNC_INTERVAL_MS);
  }

  onModuleDestroy() {
    if (this.timer) clearInterval(this.timer);
  }

  async syncAll(): Promise<{ projects: number; events: number }> {
    if (this.running) return { projects: 0, events: 0 };
    this.running = true;
    let eventCount = 0;
    try {
      const projects = await this.prisma.project.findMany({
        where: {
          approved: true,
          trackingActive: true,
          githubRepoFullName: { not: null },
          founderId: { not: null },
        },
        select: {
          id: true,
          githubRepoFullName: true,
          founderId: true,
          founder: { select: { userId: true } },
        },
        take: 80,
      });

      for (const project of projects) {
        const repo = project.githubRepoFullName?.trim();
        if (!repo || !project.founderId || !repo.includes('/')) continue;
        try {
          eventCount += await this.syncProjectRepo(project.id, project.founderId, repo, project.founder?.userId);
        } catch (err) {
          this.logger.warn(
            `GitHub sync skipped for ${repo}: ${err instanceof Error ? err.message : err}`,
          );
        }
      }

      if (eventCount > 0) {
        this.logger.log(`Listed project GitHub sync: ${eventCount} new commit event(s)`);
      }
      return { projects: projects.length, events: eventCount };
    } finally {
      this.running = false;
    }
  }

  private async syncProjectRepo(
    projectId: string,
    founderId: string,
    repoFullName: string,
    userId: string | null | undefined,
  ): Promise<number> {
    const since = new Date(Date.now() - LOOKBACK_MS).toISOString();
    const url = `https://api.github.com/repos/${repoFullName}/commits?per_page=15&since=${encodeURIComponent(since)}`;
    const res = await fetch(url, {
      headers: {
        Accept: 'application/vnd.github+json',
        'User-Agent': 'DoxxedCrypto-ListedProjectSync',
      },
    });
    if (!res.ok) return 0;

    const commits = (await res.json()) as PublicCommit[];
    let created = 0;
    for (const c of commits) {
      const title = (c.commit.message ?? 'Commit').split('\n')[0]!.slice(0, 120);
      const result = await this.events.emit({
        founderId,
        projectId,
        userId: userId ?? undefined,
        type: FounderEventType.GITHUB_COMMIT,
        source: 'listed-project-github',
        title,
        payload: { sha: c.sha, repoFullName, publicPoll: true },
        dedupeKey: `listed-github:${projectId}:${c.sha}`,
      });
      if (!result.duplicate) created += 1;
    }
    return created;
  }
}
