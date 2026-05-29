import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { BountyStatus, NotificationType, SuggestedUpdateStatus, UserBadgeKind } from '@prisma/client';
import {
  COMMUNITY_REWARD_POOL_DEFAULT,
  CONNECTED_APP_PROVIDERS,
  EARLY_SCOUT_FOLLOWER_THRESHOLD,
  EARLY_SCOUT_POINTS,
  FOUNDER_LAUNCH_CREDITS,
  HELPFUL_MARK_POINTS,
  HELPFUL_MARK_POOL_CREDITS,
  POINTS,
  buildSuggestedUpdateFromCommits,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';
import { NotificationsService } from '../notifications/notifications.service';

@Injectable()
export class FounderOsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly points: PointsService,
    private readonly notifications: NotificationsService,
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

    return {
      founderCredits: founder?.founderCredits ?? 0,
      communityRewardPool: founder?.projects[0]?.communityRewardPool ?? 0,
      primaryProject: founder?.projects[0] ?? null,
      connectedApps,
      pendingSuggestions: pendingSuggestions.map((s) => ({
        id: s.id,
        headline: s.headline,
        body: s.body,
        devSummary: s.devSummary,
        traderSummary: s.traderSummary,
        createdAt: s.createdAt.toISOString(),
      })),
      openBounties: bounties,
    };
  }

  async getConnectedApps(userId: string, founder?: { githubUrl?: string | null; githubUsername?: string | null; githubRepoFullName?: string | null; userId?: string | null } | null) {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { twitterHandle: true, oauthAccounts: { where: { provider: 'twitter' } } },
    });
    const gh = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    const stored = await this.prisma.connectedAppStatus.findMany({ where: { userId } });
    const map = new Map(stored.map((s) => [s.provider, s]));

    return CONNECTED_APP_PROVIDERS.map((p) => {
      let connected = map.get(p.key)?.connected ?? false;
      if (p.key === 'github') connected = connected || Boolean(gh || founder?.githubUrl || founder?.githubRepoFullName);
      if (p.key === 'x') connected = connected || Boolean(user?.twitterHandle || user?.oauthAccounts.length);
      if (p.key === 'cursor') connected = map.get('cursor')?.connected ?? false;
      return {
        provider: p.key,
        label: p.label,
        connected,
        reputationBoost: p.reputationBoost,
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
    return { success: true, repoFullName: normalized };
  }

  async syncGitHubCommits(userId: string) {
    const conn = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { take: 1 } },
    });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const repo = conn?.repoFullName ?? founder.githubRepoFullName;
    if (!repo) throw new BadRequestException('Connect a GitHub repo first (owner/repo)');

    const res = await fetch(`https://api.github.com/repos/${repo}/commits?per_page=8`, {
      headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'DoxxedCrypto-FounderOS' },
    });
    if (!res.ok) throw new BadRequestException('Could not fetch GitHub commits — check repo is public');

    const data = (await res.json()) as {
      sha: string;
      commit: { message: string; author: { date: string } };
    }[];

    const commits = data.map((c) => ({
      sha: c.sha.slice(0, 7),
      message: c.commit.message,
      date: c.commit.author.date,
    }));

    const dayNumber = (founder.buildStreakDays || 0) + 1;
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
      },
    });

    await this.prisma.gitHubConnection.upsert({
      where: { userId },
      create: {
        userId,
        githubUsername: repo.split('/')[0]!,
        repoFullName: repo,
        lastSyncedAt: new Date(),
        lastCommitSha: data[0]?.sha,
      },
      update: { lastSyncedAt: new Date(), lastCommitSha: data[0]?.sha },
    });

    return {
      commits,
      suggestion: {
        id: record.id,
        headline: record.headline,
        body: record.body,
        devSummary: record.devSummary,
        traderSummary: record.traderSummary,
      },
    };
  }

  async publishSuggestedUpdate(userId: string, suggestionId: string) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const suggestion = await this.prisma.suggestedBuildUpdate.findFirst({
      where: { id: suggestionId, founderId: founder.id, status: SuggestedUpdateStatus.PENDING },
    });
    if (!suggestion) throw new NotFoundException('Suggestion not found');

    const post = await this.prisma.founderBuildPost.create({
      data: {
        founderId: founder.id,
        projectId: suggestion.projectId,
        headline: suggestion.headline,
        body: `${suggestion.body}\n\n---\n**Trader view:**\n${suggestion.traderSummary}`,
      },
    });

    await this.prisma.suggestedBuildUpdate.update({
      where: { id: suggestionId },
      data: { status: SuggestedUpdateStatus.PUBLISHED, buildPostId: post.id },
    });

    await this.points.award(userId, POINTS.FOUNDER_BUILD_POST);
    return { success: true, buildPostId: post.id };
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
