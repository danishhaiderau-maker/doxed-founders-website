import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { BountyStatus, FounderEventType, NotificationType, Prisma, SuggestedUpdateStatus, UserBadgeKind } from '@prisma/client';
import {
  COMMUNITY_REWARD_POOL_DEFAULT,
  CURSOR_BUILD_SESSION_CREDITS,
  INTEGRATION_PROVIDERS,
  EARLY_SCOUT_FOLLOWER_THRESHOLD,
  EARLY_SCOUT_POINTS,
  FOUNDER_LAUNCH_CREDITS,
  HELPFUL_MARK_POINTS,
  HELPFUL_MARK_POOL_CREDITS,
  POINTS,
  PublishChannelResult,
  PublishDestinations,
  buildCommunityAnnouncement,
  buildDeploySuggestion,
  buildFeedPostBody,
  buildSuggestionFromBuildPrompt,
  buildXUpdateTweet,
  getRepoStarterTemplate,
  REPO_STARTER_TEMPLATES,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';
import { NotificationsService } from '../notifications/notifications.service';
import { UserXPostingService } from '../x-social/user-x-posting.service';
import { FounderOsIntegrationService } from './founder-os-integration.service';
import { EventsService } from '../events/events.service';
import { FounderOsMemoryService } from '../github/founder-os-memory.service';
import { GitHubApiService } from '../github/github-api.service';
import { GithubAutoSyncService } from './github-auto-sync.service';

@Injectable()
export class FounderOsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly points: PointsService,
    private readonly notifications: NotificationsService,
    private readonly userX: UserXPostingService,
    private readonly integrations: FounderOsIntegrationService,
    private readonly events: EventsService,
    private readonly memory: FounderOsMemoryService,
    private readonly github: GitHubApiService,
    private readonly githubAutoSync: GithubAutoSyncService,
  ) {}

  async grantLaunchCredits(userId: string, founderId: string, projectId: string, projectName: string) {
    const founder = await this.prisma.founder.update({
      where: { id: founderId },
      data: { founderCredits: { increment: FOUNDER_LAUNCH_CREDITS } },
    });

    await this.prisma.founderCreditLedger.create({
      data: {
        userId,
        founderId,
        projectId,
        delta: FOUNDER_LAUNCH_CREDITS,
        balanceAfter: founder.founderCredits,
        reason: 'FOUNDER_PROJECT_LAUNCH',
      },
    });

    await this.prisma.project.update({
      where: { id: projectId },
      data: { communityRewardPool: COMMUNITY_REWARD_POOL_DEFAULT },
    });

    await this.notifications.notifyUser(userId, {
      type: NotificationType.POINTS_EARNED,
      title: `${FOUNDER_LAUNCH_CREDITS.toLocaleString()} Founder Credits`,
      body: `"${projectName}" is live. Use credits for bounties, demand tests, and community rewards. ${COMMUNITY_REWARD_POOL_DEFAULT.toLocaleString()} community pool allocated.`,
      link: '/founder-den',
    });
  }

  async getDashboard(userId: string) {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: {
        projects: {
          where: { approved: true },
          select: {
            id: true,
            slug: true,
            name: true,
            communityRewardPool: true,
            githubRepoFullName: true,
          },
        },
      },
    });

    const connectedApps = await this.getConnectedApps(userId, founder);
    const pendingSuggestions = founder
      ? await this.prisma.suggestedBuildUpdate.findMany({
          where: { founderId: founder.id, status: SuggestedUpdateStatus.PENDING },
          orderBy: { createdAt: 'desc' },
          take: 5,
        })
      : [];

    const bounties = founder?.projects[0]
      ? await this.prisma.founderBounty.findMany({
          where: { projectId: founder.projects[0].id, status: BountyStatus.OPEN },
          orderBy: { createdAt: 'desc' },
          take: 10,
        })
      : [];

    const recentBuildSessions = founder
      ? await this.prisma.cursorBuildSession.findMany({
          where: { founderId: founder.id },
          orderBy: { createdAt: 'desc' },
          take: 3,
          select: { id: true, title: true, creditsSpent: true, createdAt: true },
        })
      : [];

    return {
      founderCredits: founder?.founderCredits ?? 0,
      communityRewardPool: founder?.projects[0]?.communityRewardPool ?? 0,
      primaryProject: founder?.projects[0] ?? null,
      connectedApps,
      integrationProviders: this.integrations.getProviderConfigs(),
      pendingSuggestions: pendingSuggestions.map((s) => ({
        id: s.id,
        headline: s.headline,
        body: s.body,
        devSummary: s.devSummary,
        traderSummary: s.traderSummary,
        source: s.source,
        createdAt: s.createdAt.toISOString(),
      })),
      openBounties: bounties,
      recentBuildSessions: recentBuildSessions.map((s) => ({
        ...s,
        createdAt: s.createdAt.toISOString(),
      })),
    };
  }

  getIntegrationProviders() {
    return this.integrations.getProviderConfigs();
  }

  connectIntegration(
    userId: string,
    input: { provider: string; token?: string; repoFullName?: string; projectName?: string },
  ) {
    return this.integrations.connectIntegration(userId, input);
  }

  disconnectIntegration(userId: string, provider: string) {
    return this.integrations.disconnectIntegration(userId, provider);
  }

  async getConnectedApps(userId: string, founder?: { githubUrl?: string | null; githubUsername?: string | null; githubRepoFullName?: string | null; userId?: string | null } | null) {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { twitterHandle: true, oauthAccounts: { where: { provider: 'twitter' } } },
    });
    const gh = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    const stored = await this.prisma.connectedAppStatus.findMany({ where: { userId } });
    const creds = await this.prisma.integrationCredential.findMany({ where: { userId } });
    const map = new Map(stored.map((s) => [s.provider, s]));
    const credMap = new Map(creds.map((c) => [c.provider, c]));

    return INTEGRATION_PROVIDERS.map((p) => {
      let connected = map.get(p.key)?.connected ?? false;
      const meta = (map.get(p.key)?.metadata ?? credMap.get(p.key)?.metadata) as Record<string, unknown> | undefined;
      if (p.key === 'github') connected = connected || Boolean(gh || founder?.githubUrl || founder?.githubRepoFullName);
      if (p.key === 'x') connected = connected || Boolean(user?.twitterHandle || user?.oauthAccounts.length);
      if (['vercel', 'railway', 'neon', 'digitalocean', 'supabase'].includes(p.key)) {
        connected = connected || Boolean(credMap.get(p.key)?.verifiedAt);
      }
      if (p.key === 'cursor') connected = connected || Boolean(credMap.get('cursor')?.verifiedAt);
      return {
        provider: p.key,
        label: p.label,
        connected,
        reputationBoost: p.reputationBoost,
        billTip: p.billTip,
        accountName: (meta?.accountName as string | undefined) ?? null,
        webhookUrl: (meta?.webhookUrl as string | undefined) ?? null,
      };
    });
  }

  async connectGitHubRepo(userId: string, repoFullName: string) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const normalized = repoFullName.trim().replace(/^https:\/\/github\.com\//, '').replace(/\/$/, '');
    if (!/^[\w.-]+\/[\w.-]+$/.test(normalized)) {
      throw new BadRequestException('Use format owner/repo');
    }

    const [owner] = normalized.split('/');

    await this.prisma.gitHubConnection.upsert({
      where: { userId },
      create: { userId, githubUsername: owner, repoFullName: normalized },
      update: { githubUsername: owner, repoFullName: normalized },
    });

    await this.prisma.founder.update({
      where: { id: founder.id },
      data: { githubRepoFullName: normalized, githubUsername: owner },
    });

    const primaryProject = await this.prisma.project.findFirst({
      where: { founderId: founder.id },
      select: { id: true },
    });
    if (primaryProject) {
      await this.prisma.project.update({
        where: { id: primaryProject.id },
        data: { githubRepoFullName: normalized },
      });
    }

    await this.upsertConnectedApp(userId, 'github', true);

    void this.memory.bootstrapRepoMemory(userId, normalized).catch(() => undefined);
    void this.memory.syncProjectMemoryToRepo(userId, normalized).catch(() => undefined);
    void this.githubAutoSync.syncForUser(userId, { force: true }).catch(() => undefined);

    return { success: true, repoFullName: normalized };
  }

  listRepoStarterTemplates() {
    return REPO_STARTER_TEMPLATES.map(({ key, label, description, tags, defaultRepoName }) => ({
      key,
      label,
      description,
      tags,
      defaultRepoName,
    }));
  }

  async scaffoldGitHubRepo(userId: string, input: { templateKey: string; repoName: string }) {
    const template = getRepoStarterTemplate(input.templateKey);
    if (!template) throw new BadRequestException('Unknown starter template');

    const repoName = input.repoName.trim().replace(/[^a-zA-Z0-9._-]/g, '-').slice(0, 80);
    if (!repoName) throw new BadRequestException('Repo name required');

    const { repoFullName, htmlUrl } = await this.github.createUserRepo(userId, repoName, {
      description: template.description,
    });

    for (const file of template.files) {
      await this.github.upsertRepoFile(
        userId,
        repoFullName,
        file.path,
        file.content,
        `Scaffold ${file.path} from Founder OS`,
      );
    }

    await this.connectGitHubRepo(userId, repoFullName);
    return { success: true, repoFullName, htmlUrl, template: template.key };
  }

  async listGitHubRepos(userId: string) {
    const repos = await this.github.listUserRepos(userId);
    return { repos };
  }

  async syncProjectMemory(userId: string) {
    return this.memory.syncProjectMemoryToRepo(userId);
  }

  autoSyncGitHubCommits(userId: string) {
    return this.githubAutoSync.syncForUser(userId);
  }

  async syncGitHubCommits(userId: string) {
    return this.githubAutoSync.syncForUser(userId, { force: true });
  }

  async getOnboardingStatus(userId: string) {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { where: { approved: true }, take: 1 } },
    });
    const gh = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const creds = await this.prisma.integrationCredential.findMany({ where: { userId } });
    const founderNode = await this.prisma.founderNode.findFirst({
      where: { userId },
      orderBy: { lastSeenAt: 'desc' },
    });

    const llmConnected = creds.some(
      (c) =>
        c.verifiedAt &&
        ['openai', 'anthropic', 'gemini', 'deepseek', 'openrouter', 'phala'].includes(c.provider),
    );
    const cursorConnected = creds.some((c) => c.provider === 'cursor' && c.verifiedAt && c.token);
    const githubConnected = Boolean(
      (gh?.repoFullName ?? founder?.githubRepoFullName) &&
        !String(gh?.repoFullName ?? founder?.githubRepoFullName).endsWith('/pending-setup'),
    );
    const goalSet = Boolean(settings?.currentGoalFocus?.trim());
    const founderActive = Boolean(founder);
    const nodeOnline =
      Boolean(founderNode?.lastSeenAt) &&
      Date.now() - (founderNode!.lastSeenAt?.getTime() ?? 0) < 180_000;

    const steps = [
      {
        id: 'founder',
        label: 'Activate founder profile',
        complete: founderActive,
        href: '/founder-den?tab=analytics',
      },
      {
        id: 'github',
        label: 'Connect GitHub repo',
        complete: githubConnected,
        detail: gh?.repoFullName ?? founder?.githubRepoFullName ?? null,
        href: '/founder-den?tab=build',
      },
      {
        id: 'ai_stack',
        label: 'Connect AI Stack (LLM or Cursor)',
        complete: llmConnected || cursorConnected,
        detail: settings?.defaultProvider !== 'RULE_BASED' ? settings?.defaultProvider : null,
        href: '/settings/builder',
      },
      {
        id: 'goal',
        label: 'Set your current goal',
        complete: goalSet,
        detail: settings?.currentGoalFocus?.slice(0, 80) ?? null,
        href: '/settings/builder',
      },
      {
        id: 'founder_node',
        label: 'Pair Founder Node (Windows vault)',
        complete: nodeOnline,
        optional: true,
        href: '/founder-node',
      },
    ];

    const requiredComplete = steps.filter((s) => !('optional' in s && s.optional)).every((s) => s.complete);

    return {
      steps,
      requiredComplete,
      allComplete: steps.every((s) => s.complete),
      githubLastSyncedAt: gh?.lastSyncedAt?.toISOString() ?? null,
      projectName: founder?.projects[0]?.name ?? null,
    };
  }

  async runCursorBuildRoom(userId: string, input: { title: string; prompt: string }) {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { take: 1 } },
    });
    if (!founder) throw new ForbiddenException('Founder profile required');
    if (founder.founderCredits < CURSOR_BUILD_SESSION_CREDITS) {
      throw new BadRequestException(`Need ${CURSOR_BUILD_SESSION_CREDITS} Founder Credits for a build room session`);
    }

    const dayNumber = (founder.buildStreakDays || 0) + 1;
    const suggested = buildSuggestionFromBuildPrompt(input.prompt, dayNumber);

    const updated = await this.prisma.founder.update({
      where: { id: founder.id },
      data: { founderCredits: { decrement: CURSOR_BUILD_SESSION_CREDITS } },
    });

    await this.prisma.founderCreditLedger.create({
      data: {
        userId,
        founderId: founder.id,
        projectId: founder.projects[0]?.id,
        delta: -CURSOR_BUILD_SESSION_CREDITS,
        balanceAfter: updated.founderCredits,
        reason: 'CURSOR_BUILD_ROOM',
      },
    });

    const suggestion = await this.prisma.suggestedBuildUpdate.create({
      data: {
        founderId: founder.id,
        projectId: founder.projects[0]?.id,
        headline: suggested.headline,
        body: suggested.body,
        devSummary: suggested.devSummary,
        traderSummary: suggested.traderSummary,
        commitShas: [],
        source: 'cursor',
      },
    });

    const session = await this.prisma.cursorBuildSession.create({
      data: {
        userId,
        founderId: founder.id,
        title: input.title.trim() || 'Build session',
        userPrompt: input.prompt.trim(),
        suggestionId: suggestion.id,
        creditsSpent: CURSOR_BUILD_SESSION_CREDITS,
      },
    });

    await this.integrations.connectIntegration(userId, { provider: 'cursor' });

    await this.events.emit({
      founderId: founder.id,
      projectId: founder.projects[0]?.id,
      userId,
      type: FounderEventType.CURSOR_BUILD_SESSION,
      source: 'cursor',
      title: suggested.headline,
      payload: { sessionId: session.id, suggestionId: suggestion.id },
    });

    return {
      sessionId: session.id,
      creditsSpent: CURSOR_BUILD_SESSION_CREDITS,
      suggestion: {
        id: suggestion.id,
        headline: suggestion.headline,
        body: suggestion.body,
        devSummary: suggestion.devSummary,
        traderSummary: suggestion.traderSummary,
      },
    };
  }

  async dismissSuggestion(userId: string, suggestionId: string) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const updated = await this.prisma.suggestedBuildUpdate.updateMany({
      where: { id: suggestionId, founderId: founder.id, status: SuggestedUpdateStatus.PENDING },
      data: { status: SuggestedUpdateStatus.DISMISSED },
    });
    if (updated.count === 0) throw new NotFoundException('Suggestion not found');
    return { success: true };
  }

  async publishSuggestedUpdate(
    userId: string,
    suggestionId: string,
    destinations: Partial<PublishDestinations> = {},
  ) {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { take: 1 } },
    });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const suggestion = await this.prisma.suggestedBuildUpdate.findFirst({
      where: { id: suggestionId, founderId: founder.id, status: SuggestedUpdateStatus.PENDING },
    });
    if (!suggestion) throw new NotFoundException('Suggestion not found');

    const dest: PublishDestinations = {
      buildFeed: destinations.buildFeed ?? true,
      x: destinations.x ?? true,
      community: destinations.community ?? true,
    };

    const project = suggestion.projectId
      ? await this.prisma.project.findUnique({ where: { id: suggestion.projectId } })
      : founder.projects[0];

    const results: PublishChannelResult = {};
    let buildPostId: string | undefined;
    let communityThreadId: string | undefined;
    let xTweetUrl: string | undefined;

    if (dest.buildFeed) {
      try {
        const dayNumber = (founder.buildStreakDays || 0) + 1;
        const post = await this.prisma.founderBuildPost.create({
          data: {
            founderId: founder.id,
            projectId: suggestion.projectId ?? project?.id,
            dayNumber,
            headline: suggestion.headline,
            body: buildFeedPostBody({
              body: suggestion.body,
              devSummary: suggestion.devSummary,
              traderSummary: suggestion.traderSummary,
            }),
          },
        });
        buildPostId = post.id;
        await this.updateBuildStreak(founder.id);
        await this.points.award(userId, POINTS.FOUNDER_BUILD_POST, 'FOUNDER_BUILD_POST');
        results.buildFeed = { ok: true, buildPostId: post.id };
      } catch (err) {
        results.buildFeed = {
          ok: false,
          error: err instanceof Error ? err.message : 'Build feed failed',
        };
      }
    }

    if (dest.community && project?.id) {
      try {
        const ann = buildCommunityAnnouncement({
          headline: suggestion.headline,
          body: suggestion.body,
          traderSummary: suggestion.traderSummary,
        });
        const thread = await this.prisma.communityThread.create({
          data: {
            projectId: project.id,
            channel: 'DEVELOPMENT',
            title: ann.title,
            body: ann.body,
            authorId: userId,
            pinned: false,
          },
        });
        communityThreadId = thread.id;
        await this.points.award(userId, POINTS.FOUNDER_COMMUNITY_POST, 'FOUNDER_COMMUNITY_POST');
        results.community = { ok: true, threadId: thread.id };
      } catch (err) {
        results.community = {
          ok: false,
          error: err instanceof Error ? err.message : 'Community post failed',
        };
      }
    } else if (dest.community) {
      results.community = { ok: false, skipped: true, error: 'No project linked' };
    }

    if (dest.x) {
      const xStatus = await this.userX.canUserPost(userId);
      if (!xStatus.canPost) {
        results.x = { ok: false, skipped: true, error: xStatus.connected ? 'Reconnect X for posting' : 'X not connected' };
      } else {
        try {
          const tweetText = buildXUpdateTweet({
            headline: suggestion.headline,
            traderSummary: suggestion.traderSummary,
            projectName: project?.name,
          });
          const tweet = await this.userX.postTweet(userId, tweetText);
          if (tweet.ok) {
            xTweetUrl = tweet.tweetUrl;
            results.x = { ok: true, tweetUrl: tweet.tweetUrl };
          } else {
            results.x = { ok: false, error: tweet.reason };
          }
        } catch (err) {
          results.x = {
            ok: false,
            error: err instanceof Error ? err.message : 'X post failed',
          };
        }
      }
    }

    const anyOk = Object.values(results).some((r) => r?.ok);
    if (!anyOk) {
      throw new BadRequestException(
        `Publish failed on all destinations: ${JSON.stringify(results)}`,
      );
    }

    await this.prisma.suggestedBuildUpdate.update({
      where: { id: suggestionId },
      data: {
        status: SuggestedUpdateStatus.PUBLISHED,
        buildPostId,
        communityThreadId,
        xTweetUrl,
        publishLog: results as Prisma.InputJsonValue,
      },
    });

    await this.events.emit({
      founderId: founder.id,
      projectId: suggestion.projectId ?? project?.id,
      userId,
      type: FounderEventType.BUILD_PUBLISHED,
      source: 'founder-os',
      title: `Published: ${suggestion.headline.slice(0, 72)}`,
      payload: { suggestionId, destinations: results },
    });

    return {
      success: true,
      buildPostId,
      communityThreadId,
      xTweetUrl,
      destinations: results,
    };
  }

  async handleDeployWebhook(
    webhookSecret: string,
    payload: { provider?: string; projectName?: string; environment?: string },
  ) {
    const cred = await this.integrations.findByWebhookSecret(webhookSecret);
    if (!cred?.user?.founder) throw new NotFoundException('Webhook not found');

    const suggested = buildDeploySuggestion({
      provider: payload.provider ?? cred.provider,
      projectName: payload.projectName ?? (cred.metadata as { projectName?: string })?.projectName,
      environment: payload.environment,
    });

    const founder = cred.user.founder;
    const record = await this.prisma.suggestedBuildUpdate.create({
      data: {
        founderId: founder.id,
        projectId: founder.projects[0]?.id,
        headline: suggested.headline,
        body: suggested.body,
        devSummary: suggested.devSummary,
        traderSummary: suggested.traderSummary,
        commitShas: [],
        source: `deploy:${cred.provider}`,
      },
    });

    if (cred.userId) {
      await this.notifications.notifyUser(cred.userId, {
        type: NotificationType.FOUNDER_EVENT,
        title: 'Deploy detected',
        body: `New suggested update ready — review and publish everywhere.`,
        link: '/founder-den?tab=build',
      });
    }

    const settings = cred.userId
      ? await this.prisma.founderBuilderSettings.findUnique({ where: { userId: cred.userId } })
      : null;

    await this.events.emit({
      founderId: founder.id,
      projectId: founder.projects[0]?.id,
      userId: cred.userId ?? undefined,
      type: FounderEventType.DEPLOY_SUCCESS,
      source: cred.provider,
      title: suggested.headline,
      payload: {
        suggestionId: record.id,
        provider: payload.provider ?? cred.provider,
        autoPublish: settings?.autoPublishOnEvent ?? false,
      },
      dedupeKey: `deploy:${record.id}`,
    });

    return { success: true, suggestionId: record.id };
  }

  private async updateBuildStreak(founderId: string) {
    const founder = await this.prisma.founder.findUnique({ where: { id: founderId } });
    if (!founder) return 0;

    const now = new Date();
    const last = founder.lastBuildPostAt;
    let streak = founder.buildStreakDays;
    if (!last) {
      streak = 1;
    } else {
      const daysSince = Math.floor((now.getTime() - last.getTime()) / 86400000);
      streak = daysSince <= 7 ? streak + 1 : 1;
    }

    await this.prisma.founder.update({
      where: { id: founderId },
      data: {
        buildStreakDays: streak,
        lastBuildPostAt: now,
        publicBuildingSince: founder.publicBuildingSince ?? now,
      },
    });
    return streak;
  }

  async markCommentHelpful(founderUserId: string, projectId: string, commentId: string) {
    const founder = await this.prisma.founder.findUnique({ where: { userId: founderUserId } });
    if (!founder) throw new ForbiddenException('Founders only');

    const owns = await this.prisma.project.count({ where: { id: projectId, founderId: founder.id } });
    if (!owns) throw new ForbiddenException('Not your project');

    const comment = await this.prisma.communityComment.findUnique({
      where: { id: commentId },
      include: { thread: true },
    });
    if (!comment || comment.thread.projectId !== projectId) throw new NotFoundException('Comment not found');
    if (comment.userId === founderUserId) throw new BadRequestException('Cannot mark your own comment');

    const existing = await this.prisma.helpfulMark.findUnique({ where: { commentId } });
    if (existing) throw new BadRequestException('Already marked helpful');

    const project = await this.prisma.project.findUnique({ where: { id: projectId } });
    const poolCredits = Math.min(HELPFUL_MARK_POOL_CREDITS, project?.communityRewardPool ?? 0);

    await this.prisma.$transaction(async (tx) => {
      await tx.helpfulMark.create({
        data: {
          projectId,
          commentId,
          markedByUserId: founderUserId,
          recipientUserId: comment.userId,
          pointsAwarded: HELPFUL_MARK_POINTS,
          poolCreditsUsed: poolCredits,
        },
      });

      if (poolCredits > 0) {
        await tx.project.update({
          where: { id: projectId },
          data: { communityRewardPool: { decrement: poolCredits } },
        });
      }

      await tx.userBadge.upsert({
        where: {
          userId_kind_projectId: {
            userId: comment.userId,
            kind: UserBadgeKind.HELPFUL_CONTRIBUTOR,
            projectId,
          },
        },
        create: {
          userId: comment.userId,
          kind: UserBadgeKind.HELPFUL_CONTRIBUTOR,
          projectId,
          label: 'Helpful contributor',
        },
        update: {},
      });
    });

    await this.points.awardOnce(comment.userId, `HELPFUL:${commentId}`, HELPFUL_MARK_POINTS);

    await this.notifications.notifyUser(comment.userId, {
      type: NotificationType.POINTS_EARNED,
      title: 'Marked helpful by founder',
      body: `+${HELPFUL_MARK_POINTS} reputation points for a useful contribution.`,
      link: `/project/${project?.slug ?? ''}`,
    });

    return { success: true, pointsAwarded: HELPFUL_MARK_POINTS, poolCreditsUsed: poolCredits };
  }

  async createBounty(
    userId: string,
    projectId: string,
    input: { title: string; description: string; rewardCredits: number; rewardPoints?: number },
  ) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const owns = await this.prisma.project.count({ where: { id: projectId, founderId: founder.id } });
    if (!owns) throw new ForbiddenException('Not your project');

    if (input.rewardCredits < 100) throw new BadRequestException('Minimum bounty is 100 credits');
    if (founder.founderCredits < input.rewardCredits) {
      throw new BadRequestException('Insufficient Founder Credits');
    }

    const updated = await this.prisma.founder.update({
      where: { id: founder.id },
      data: { founderCredits: { decrement: input.rewardCredits } },
    });

    await this.prisma.founderCreditLedger.create({
      data: {
        userId,
        founderId: founder.id,
        projectId,
        delta: -input.rewardCredits,
        balanceAfter: updated.founderCredits,
        reason: `BOUNTY_CREATE:${input.title.slice(0, 40)}`,
      },
    });

    const bounty = await this.prisma.founderBounty.create({
      data: {
        projectId,
        title: input.title.trim(),
        description: input.description.trim(),
        rewardCredits: input.rewardCredits,
        rewardPoints: input.rewardPoints ?? 0,
      },
    });

    return bounty;
  }

  async awardBounty(founderUserId: string, bountyId: string, awardeeUserId: string) {
    const bounty = await this.prisma.founderBounty.findUnique({
      where: { id: bountyId },
      include: { project: { include: { founder: true } } },
    });
    if (!bounty || bounty.status !== BountyStatus.OPEN) throw new NotFoundException('Bounty not open');
    if (bounty.project.founder?.userId !== founderUserId) throw new ForbiddenException('Not your bounty');

    await this.prisma.founderBounty.update({
      where: { id: bountyId },
      data: { status: BountyStatus.AWARDED, awardeeUserId },
    });

    if (bounty.rewardPoints > 0) {
      await this.points.awardOnce(awardeeUserId, `BOUNTY:${bountyId}`, bounty.rewardPoints);
    }

    await this.notifications.notifyUser(awardeeUserId, {
      type: NotificationType.POINTS_EARNED,
      title: 'Bounty completed',
      body: `You earned bounty "${bounty.title}" (+${bounty.rewardPoints} pts).`,
      link: `/project/${bounty.project.slug}`,
    });

    return { success: true };
  }

  async recordEarlyScout(userId: string, projectId: string, allocationUsd: number, followerCount: number) {
    if (followerCount > EARLY_SCOUT_FOLLOWER_THRESHOLD) return;

    try {
      await this.prisma.earlyScoutRecord.create({
        data: {
          userId,
          projectId,
          followerCountAtScout: followerCount,
          allocationUsd,
        },
      });

      await this.prisma.userBadge.upsert({
        where: {
          userId_kind_projectId: {
            userId,
            kind: UserBadgeKind.EARLY_SCOUT,
            projectId,
          },
        },
        create: {
          userId,
          kind: UserBadgeKind.EARLY_SCOUT,
          projectId,
          label: 'Early Scout',
        },
        update: {},
      });

      await this.points.awardOnce(userId, `EARLY_SCOUT:${projectId}`, EARLY_SCOUT_POINTS);
    } catch {
      /* already scouted */
    }
  }

  private async upsertConnectedApp(userId: string, provider: string, connected: boolean) {
    await this.prisma.connectedAppStatus.upsert({
      where: { userId_provider: { userId, provider } },
      create: { userId, provider, connected, label: provider },
      update: { connected },
    });
  }
}
