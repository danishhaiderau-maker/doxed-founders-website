import {
  BadRequestException,
  ForbiddenException,
  Inject,
  Injectable,
  forwardRef,
} from '@nestjs/common';
import { BuildQueueStatus, FounderEventType, RoadmapStatus, SimulatedRaiseStatus, SuggestedUpdateStatus } from '@prisma/client';
import {
  buildCommunityUpdateFromSummary,
  buildDailyStandup,
  buildResumeCursorPrompt,
  buildWeeklySummary,
  computeProjectProgress,
  detectHandsFreeAction,
  formatRelativeTime,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { BuildQueueService } from '../build-queue/build-queue.service';
import { BuilderService } from '../builder/builder.service';
import { GitHubApiService } from '../github/github-api.service';
import { FounderOsService } from '../founder-os/founder-os.service';
import { EventsService } from './events.service';
import { FounderMetricsService } from './founder-metrics.service';

@Injectable()
export class FounderCopilotService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly events: EventsService,
    private readonly metrics: FounderMetricsService,
    private readonly builder: BuilderService,
    private readonly github: GitHubApiService,
    @Inject(forwardRef(() => BuildQueueService))
    private readonly buildQueue: BuildQueueService,
    @Inject(forwardRef(() => FounderOsService))
    private readonly founderOs: FounderOsService,
  ) {}

  async getProjectMemory(userId: string) {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: {
        user: { select: { name: true } },
        projects: {
          where: { approved: true },
          take: 1,
          include: {
            roadmapItems: { orderBy: { sortOrder: 'asc' } },
            simulatedRaises: {
              where: { status: SimulatedRaiseStatus.ACTIVE },
              include: { allocations: true },
              take: 1,
            },
            _count: { select: { followers: true } },
          },
        },
      },
    });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const project = founder.projects[0];
    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });

    const openQueue = await this.prisma.buildQueueItem.findMany({
      where: {
        founderId: founder.id,
        status: { notIn: [BuildQueueStatus.DISMISSED, BuildQueueStatus.DONE] },
      },
      orderBy: [{ sortOrder: 'asc' }, { createdAt: 'desc' }],
      take: 30,
    });

    const ideas = openQueue.filter((i) => i.kind === 'IDEA');
    const tasks = openQueue.filter((i) => i.kind === 'TASK');
    const doneTasks = await this.prisma.buildQueueItem.count({
      where: { founderId: founder.id, kind: 'TASK', status: BuildQueueStatus.DONE },
    });

    const lastEvent = await this.prisma.founderEvent.findFirst({
      where: { founderId: founder.id },
      orderBy: { createdAt: 'desc' },
    });

    const repo = project
      ? await this.github.resolveRepo(userId, founder.githubRepoFullName, project.githubRepoFullName)
      : null;
    const commits = repo ? await this.github.listCommits(userId, repo, 3) : [];
    const lastCommit = commits[0]?.message ?? null;

    const readiness = project
      ? await this.metrics.refreshLaunchReadiness(project.id)
      : { score: 0, previous: 0 };

    const progressPercent = computeProjectProgress({
      launchReadiness: readiness.score,
      openTasks: tasks.length,
      doneTasks,
    });

    const currentGoal =
      settings?.currentGoalFocus?.trim() ||
      ideas[0]?.title ||
      project?.roadmapItems.find((r) => r.status === RoadmapStatus.IN_PROGRESS)?.title ||
      project?.roadmapItems[0]?.title ||
      'Define your next milestone in Founder Copilot';

    const suggestedNext =
      tasks[0]?.title ||
      openQueue.find((i) => i.kind === 'GITHUB_ISSUE')?.title ||
      `Start: ${currentGoal}`;

    const connected = await this.prisma.connectedAppStatus.findMany({ where: { userId } });
    const deployments: { provider: string; label: string; healthy: boolean }[] = connected
      .filter((c) => ['vercel', 'railway', 'neon', 'supabase', 'digitalocean'].includes(c.provider) && c.connected)
      .map((c) => ({
        provider: c.provider,
        label: c.label ?? c.provider,
        healthy: true,
      }));

    const activeRaise = project?.simulatedRaises[0];
    const raiseAllocated =
      activeRaise?.allocations.reduce((s, a) => s + Number(a.amountUsd), 0) ?? 0;

    const featureRequests = project
      ? await this.prisma.communityThread.count({
          where: { projectId: project.id, channel: 'FEATURE_REQUESTS' },
        })
      : 0;

    const cursorCopy = buildResumeCursorPrompt({
      projectName: project?.name ?? founder.name,
      currentGoal,
      suggestedNext,
      openTasks: tasks.map((t) => t.title),
      lastCommit: lastCommit ?? undefined,
    });

    return {
      welcomeMessage: `Welcome back${founder.user?.name ? `, ${founder.user.name.split(' ')[0]}` : ''}.`,
      project: project
        ? { id: project.id, name: project.name, slug: project.slug, lifecycleStage: project.lifecycleStage }
        : null,
      currentGoal,
      progressPercent,
      launchReadiness: readiness.score,
      buildStreakDays: founder.buildStreakDays,
      lastActivityAt: lastEvent?.createdAt.toISOString() ?? null,
      lastActivityLabel: formatRelativeTime(lastEvent?.createdAt),
      lastCommit,
      repoFullName: repo,
      currentBranch: repo ? 'main' : null,
      openTasks: tasks.slice(0, 8).map((t) => ({
        id: t.id,
        title: t.title,
        kind: t.kind,
        status: t.status,
        done: false,
      })),
      suggestedNextStep: suggestedNext,
      deployments,
      raiseStatus: activeRaise
        ? {
            goalUsd: Number(activeRaise.goalUsd),
            allocatedUsd: raiseAllocated,
            participantCount: activeRaise.allocations.length,
            status: activeRaise.status,
          }
        : null,
      community: {
        followers: project?._count.followers ?? 0,
        featureRequests,
      },
      defaultAiProvider: settings?.defaultProvider ?? 'RULE_BASED',
      cursorCopy,
    };
  }

  async getDailyStandup(userId: string) {
    const memory = await this.getProjectMemory(userId);
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { user: { select: { name: true } } },
    });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const dayAgo = new Date(Date.now() - 86400000);
    const [yCommits, yDeploys, yEvents] = await Promise.all([
      this.prisma.founderEvent.count({
        where: { founderId: founder.id, type: FounderEventType.GITHUB_COMMIT, createdAt: { gte: dayAgo } },
      }),
      this.prisma.founderEvent.count({
        where: { founderId: founder.id, type: FounderEventType.DEPLOY_SUCCESS, createdAt: { gte: dayAgo } },
      }),
      this.prisma.founderEvent.findMany({
        where: { founderId: founder.id, createdAt: { gte: dayAgo } },
        orderBy: { createdAt: 'desc' },
        take: 5,
      }),
    ]);

    const openTaskTitles = memory.openTasks.map((t) => t.title);
    const estimatedDays = Math.max(1, Math.ceil((100 - memory.progressPercent) / 25));

    const standup = buildDailyStandup({
      founderName: founder.user?.name ?? 'Founder',
      projectName: memory.project?.name ?? founder.name,
      yesterdayCommits: yCommits,
      yesterdayDeploys: yDeploys,
      yesterdayHighlights: yEvents.map((e) => e.title),
      openTasks: openTaskTitles,
      suggestedNext: memory.suggestedNextStep,
      progressPercent: memory.progressPercent,
      estimatedDays,
    });

    return { standup, memory };
  }

  async resumeWork(userId: string) {
    const memory = await this.getProjectMemory(userId);

    if (memory.repoFullName) {
      try {
        await this.founderOs.syncGitHubCommits(userId);
      } catch {
        /* optional sync */
      }
    }

    const prompt = memory.suggestedNextStep;
    await this.events.emit({
      founderId: (await this.prisma.founder.findUnique({ where: { userId } }))!.id,
      projectId: memory.project?.id,
      userId,
      type: FounderEventType.COPILOT_COMMAND,
      source: 'copilot',
      title: 'Resume work',
      payload: { action: 'resume', suggestedNext: prompt },
    });

    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    let message = `Resuming: ${prompt}`;
    let cursorCloudDispatch:
      | Awaited<ReturnType<BuilderService['dispatchCursorBuildTask']>>
      | { error: string }
      | null = null;
    let openHandsDispatch:
      | Awaited<ReturnType<BuilderService['dispatchOpenHandsBuildTask']>>
      | { error: string }
      | null = null;

    if (settings?.defaultProvider === 'CURSOR') {
      try {
        cursorCloudDispatch = await this.builder.dispatchCursorBuildTask(userId, {
          spec: memory.currentGoal,
          cursorPrompt: prompt,
          repository: memory.repoFullName ?? undefined,
        });
        message =
          cursorCloudDispatch.mode === 'follow_up'
            ? `Cursor agent resumed — follow-up run started on ${memory.repoFullName ?? 'your repo'}.`
            : `Cursor cloud agent started — building on ${memory.repoFullName ?? 'GitHub'}.`;
      } catch (err) {
        cursorCloudDispatch = {
          error: err instanceof Error ? err.message : 'Cursor dispatch failed',
        };
      }
    } else if (settings?.defaultProvider === 'OPENHANDS') {
      try {
        openHandsDispatch = await this.builder.dispatchOpenHandsBuildTask(userId, {
          spec: memory.currentGoal,
          cursorPrompt: prompt,
          repository: memory.repoFullName ?? undefined,
        });
        message = `OpenHands task dispatched — ${prompt}`;
      } catch (err) {
        openHandsDispatch = {
          error: err instanceof Error ? err.message : 'OpenHands dispatch failed',
        };
      }
    }

    const agentUrl =
      cursorCloudDispatch && 'agentUrl' in cursorCloudDispatch
        ? cursorCloudDispatch.agentUrl
        : openHandsDispatch && 'conversationUrl' in openHandsDispatch
          ? openHandsDispatch.conversationUrl
          : null;

    return {
      message,
      memory,
      cursorCopy: memory.cursorCopy,
      cursorCloudDispatch,
      openHandsDispatch,
      dispatchHint: agentUrl
        ? `Remote agent running — open ${agentUrl}`
        : 'Copy the prompt into your connected builder (Cursor, Claude Code, etc.) or type "Finish it" to queue via hands-free.',
    };
  }

  async ask(userId: string, prompt: string) {
    const memory = await this.getProjectMemory(userId);
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: {
        projects: { where: { approved: true }, take: 1 },
        buildPosts: { orderBy: { publishedAt: 'desc' }, take: 5 },
      },
    });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const project = founder.projects[0];
    const weekAgo = new Date(Date.now() - 7 * 86400000);

    const [commitCount, deployCount, followerCount, featureRequests] = await Promise.all([
      this.prisma.founderEvent.count({
        where: { founderId: founder.id, type: FounderEventType.GITHUB_COMMIT, createdAt: { gte: weekAgo } },
      }),
      this.prisma.founderEvent.count({
        where: { founderId: founder.id, type: FounderEventType.DEPLOY_SUCCESS, createdAt: { gte: weekAgo } },
      }),
      project
        ? this.prisma.projectFollow.count({ where: { projectId: project.id } })
        : Promise.resolve(0),
      project
        ? this.prisma.communityThread.count({
            where: { projectId: project.id, channel: 'FEATURE_REQUESTS' },
          })
        : Promise.resolve(0),
    ]);

    const readiness = project
      ? await this.metrics.refreshLaunchReadiness(project.id)
      : { score: 0, previous: 0 };

    const summary = buildWeeklySummary({
      projectName: project?.name ?? founder.name,
      commitCount,
      deployCount,
      followerCount,
      featureRequests,
      launchReadiness: readiness.score,
      launchReadinessDelta: readiness.score - readiness.previous,
      buildStreak: founder.buildStreakDays,
      recentHeadlines: founder.buildPosts.map((p) => p.headline),
    });

    const aiAnswer = await this.builder.tryAiCompletion(
      userId,
      'You are Founder Copilot — persistent project memory for crypto founders. Answer from context; never ask what they are building.',
      `${prompt}\n\nProject memory:\nGoal: ${memory.currentGoal}\nProgress: ${memory.progressPercent}%\nLast commit: ${memory.lastCommit ?? 'none'}\nSuggested next: ${memory.suggestedNextStep}\nOpen tasks: ${memory.openTasks.map((t) => t.title).join(', ') || 'none'}\n\nWeekly summary:\n${summary.body}`,
    );

    await this.events.emit({
      founderId: founder.id,
      projectId: project?.id,
      userId,
      type: FounderEventType.COPILOT_COMMAND,
      source: 'copilot',
      title: prompt.slice(0, 80),
      payload: { intent: 'ask' },
    });

    return {
      answer: aiAnswer ?? summary.body,
      summary,
      stats: {
        commits: commitCount,
        deploys: deployCount,
        followers: followerCount,
        featureRequests,
        launchReadiness: readiness.score,
        buildStreak: founder.buildStreakDays,
      },
    };
  }

  async handsFree(userId: string, prompt: string) {
    const text = prompt.trim();
    if (!text) throw new BadRequestException('Tell Founder OS what you want');

    const action = detectHandsFreeAction(text);
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { where: { approved: true }, take: 1 } },
    });
    if (!founder) throw new ForbiddenException('Founder profile required');

    switch (action) {
      case 'weekly_summary':
      case 'launch_report': {
        const result = await this.ask(userId, text);
        return { action, ...result };
      }
      case 'community_update': {
        const result = await this.ask(userId, 'Create community update for this week');
        const body = buildCommunityUpdateFromSummary(result.summary);
        return { action, answer: body, summary: result.summary, stats: result.stats };
      }
      case 'publish_progress': {
        const pending = await this.prisma.suggestedBuildUpdate.findFirst({
          where: { founderId: founder.id, status: SuggestedUpdateStatus.PENDING },
          orderBy: { createdAt: 'desc' },
        });
        if (!pending) {
          return {
            action,
            answer: 'No pending suggested update — sync GitHub or run Quick Build first.',
          };
        }
        const published = await this.founderOs.publishSuggestedUpdate(userId, pending.id, {
          buildFeed: true,
          x: true,
          community: true,
        });
        await this.events.emit({
          founderId: founder.id,
          projectId: pending.projectId ?? founder.projects[0]?.id,
          userId,
          type: FounderEventType.BUILD_PUBLISHED,
          source: 'founder-os',
          title: `Published: ${pending.headline.slice(0, 60)}`,
          payload: { suggestionId: pending.id },
        });
        return { action, answer: 'Published everywhere.', published };
      }
      case 'create_github_issues': {
        const result = await this.buildQueue.publishGitHubIssues(userId);
        await this.events.emit({
          founderId: founder.id,
          projectId: founder.projects[0]?.id,
          userId,
          type: FounderEventType.GITHUB_ISSUE_CREATED,
          source: 'github',
          title: `Created ${result.created} GitHub issue(s)`,
          payload: { created: result.created },
        });
        return { action, answer: `Created ${result.created} GitHub issue(s) on ${result.repoFullName}.` };
      }
      case 'roadmap': {
        const result = await this.buildQueue.runCommand(userId, { intent: 'roadmap', prompt: text });
        return { action, answer: result.result.body, creditsSpent: result.creditsSpent };
      }
      case 'resume_work': {
        const result = await this.resumeWork(userId);
        const queued = await this.buildQueue.quickBuild(userId, {
          prompt: result.memory.suggestedNextStep,
          source: 'QUICK_BUILD',
        });
        return {
          action,
          answer: `${result.message} Task queued for your connected builder.`,
          memory: result.memory,
          cursorCopy: result.cursorCopy,
          queued,
        };
      }
      case 'quick_build':
      default: {
        const result = await this.buildQueue.quickBuild(userId, { prompt: text, source: 'QUICK_BUILD' });
        return {
          action: 'quick_build',
          answer: `Queued: ${result.parsed.ideaTitle} — ${result.parsed.tasks.length} tasks ready.`,
          ...result,
        };
      }
    }
  }
}
