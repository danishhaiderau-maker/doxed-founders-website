import { Inject, Injectable, Logger, OnModuleDestroy, OnModuleInit, forwardRef } from '@nestjs/common';
import { FounderEventType } from '@prisma/client';
import { buildSuggestedUpdateFromCommits } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { GitHubApiService } from '../github/github-api.service';
import { EventsService } from '../events/events.service';
import { FounderOsMemoryService } from '../github/founder-os-memory.service';
import {
  publicBuildDayNumberForFounder,
  repairFounderBuildStreakIfInflated,
} from './founder-build-streak.helper';
import { FounderCopilotService } from '../events/founder-copilot.service';

const USER_STALE_MS = 5 * 60 * 1000;
const BACKGROUND_INTERVAL_MS = 15 * 60 * 1000;

export type GitHubSyncResult = {
  synced: boolean;
  unchanged?: boolean;
  commits: { sha: string; message: string; date: string }[];
  suggestion?: {
    id: string;
    headline: string;
    body: string;
    devSummary: string;
    traderSummary: string;
  };
  lastSyncedAt?: string;
  reason?: string;
};

@Injectable()
export class GithubAutoSyncService implements OnModuleInit, OnModuleDestroy {
  private readonly log = new Logger(GithubAutoSyncService.name);
  private interval: ReturnType<typeof setInterval> | null = null;

  constructor(
    private readonly prisma: PrismaService,
    private readonly github: GitHubApiService,
    private readonly events: EventsService,
    private readonly memory: FounderOsMemoryService,
    @Inject(forwardRef(() => FounderCopilotService))
    private readonly copilot: FounderCopilotService,
  ) {}

  onModuleInit() {
    if (process.env.DISABLE_GITHUB_AUTO_SYNC === '1') return;
    this.interval = setInterval(() => {
      void this.syncAllConnectedRepos();
    }, BACKGROUND_INTERVAL_MS);
  }

  onModuleDestroy() {
    if (this.interval) clearInterval(this.interval);
  }

  async syncAllConnectedRepos() {
    const connections = await this.prisma.gitHubConnection.findMany({
      select: { userId: true },
    });
    for (const conn of connections) {
      try {
        await this.syncForUser(conn.userId, { background: true });
      } catch (err) {
        this.log.debug(
          `Background sync skipped for ${conn.userId}: ${err instanceof Error ? err.message : err}`,
        );
      }
    }
  }

  async syncForUser(
    userId: string,
    options?: { force?: boolean; background?: boolean },
  ): Promise<GitHubSyncResult> {
    const conn = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { take: 1 } },
    });
    if (!founder) {
      return { synced: false, commits: [], reason: 'no_founder' };
    }

    const repo = conn?.repoFullName ?? founder.githubRepoFullName;
    if (!repo) {
      return { synced: false, commits: [], reason: 'no_repo' };
    }

    const now = Date.now();
    if (
      !options?.force &&
      conn?.lastSyncedAt &&
      now - conn.lastSyncedAt.getTime() < USER_STALE_MS &&
      !options?.background
    ) {
      const commits = await this.github.listCommits(userId, repo, 8);
      return {
        synced: true,
        unchanged: true,
        commits,
        lastSyncedAt: conn.lastSyncedAt.toISOString(),
        reason: 'recently_synced',
      };
    }

    const latest = await this.github.fetchLatestCommit(userId, repo);
    if (!latest) {
      return { synced: false, commits: [], reason: 'github_unreachable' };
    }

    const commits = await this.github.listCommits(userId, repo, 8);
    const latestFullSha = latest.fullSha;

    if (!options?.force && conn?.lastCommitSha && conn.lastCommitSha === latestFullSha) {
      await this.prisma.gitHubConnection.update({
        where: { userId },
        data: { lastSyncedAt: new Date() },
      });
      return {
        synced: true,
        unchanged: true,
        commits,
        lastSyncedAt: new Date().toISOString(),
      };
    }

    await repairFounderBuildStreakIfInflated(this.prisma, founder);
    const founderFresh = await this.prisma.founder.findUnique({ where: { id: founder.id } });
    const dayNumber = publicBuildDayNumberForFounder(founderFresh ?? founder);
    const suggested = buildSuggestedUpdateFromCommits(commits, dayNumber);

    const record = await this.prisma.suggestedBuildUpdate.create({
      data: {
        founderId: founder.id,
        projectId: founder.projects[0]?.id,
        headline: suggested.headline,
        body: suggested.body,
        devSummary: suggested.devSummary,
        traderSummary: suggested.traderSummary,
        commitShas: commits.map((c) => c.sha),
        source: 'github',
      },
    });

    await this.prisma.gitHubConnection.upsert({
      where: { userId },
      create: {
        userId,
        githubUsername: repo.split('/')[0]!,
        repoFullName: repo,
        lastSyncedAt: new Date(),
        lastCommitSha: latestFullSha,
      },
      update: { lastSyncedAt: new Date(), lastCommitSha: latestFullSha },
    });

    void this.memory.syncProjectMemoryToRepo(userId, repo).catch(() => undefined);
    void this.copilot.reconcileMissionGraphFromGithub(userId).catch(() => undefined);

    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    await this.events.emit({
      founderId: founder.id,
      projectId: founder.projects[0]?.id,
      userId,
      type: FounderEventType.GITHUB_COMMIT,
      source: 'github',
      title: suggested.headline,
      payload: {
        suggestionId: record.id,
        commitCount: commits.length,
        autoPublish: settings?.autoPublishOnEvent ?? false,
        autoSync: true,
      },
      dedupeKey: `github:${founder.id}:${latestFullSha}`,
    });

    return {
      synced: true,
      commits,
      suggestion: {
        id: record.id,
        headline: record.headline,
        body: record.body,
        devSummary: record.devSummary,
        traderSummary: record.traderSummary,
      },
      lastSyncedAt: new Date().toISOString(),
    };
  }

  /** GitHub App / webhook push — instant sync when repo receives commits. */
  async handlePushWebhook(payload: {
    repository?: { full_name?: string };
    ref?: string;
  }): Promise<{ synced: boolean; reason?: string }> {
    const repo = payload.repository?.full_name?.trim();
    if (!repo) return { synced: false, reason: 'no_repo' };
    if (payload.ref && !payload.ref.endsWith('/main') && !payload.ref.endsWith('/master')) {
      return { synced: false, reason: 'ignored_ref' };
    }

    const connections = await this.prisma.gitHubConnection.findMany({
      where: { repoFullName: repo },
      select: { userId: true },
    });
    if (connections.length === 0) {
      const founderMatch = await this.prisma.founder.findFirst({
        where: { githubRepoFullName: repo },
        select: { userId: true },
      });
      if (!founderMatch?.userId) return { synced: false, reason: 'no_connection' };
      await this.syncForUser(founderMatch.userId, { force: true });
      return { synced: true };
    }

    for (const conn of connections) {
      await this.syncForUser(conn.userId, { force: true });
    }
    return { synced: true };
  }
}
