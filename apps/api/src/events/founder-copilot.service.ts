import {
  BadRequestException,
  ForbiddenException,
  Inject,
  Injectable,
  forwardRef,
} from '@nestjs/common';
import { FounderEventType, SuggestedUpdateStatus } from '@prisma/client';
import {
  buildCommunityUpdateFromSummary,
  buildWeeklySummary,
  detectHandsFreeAction,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { BuildQueueService } from '../build-queue/build-queue.service';
import { BuilderService } from '../builder/builder.service';
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
    @Inject(forwardRef(() => BuildQueueService))
    private readonly buildQueue: BuildQueueService,
    @Inject(forwardRef(() => FounderOsService))
    private readonly founderOs: FounderOsService,
  ) {}

  async ask(userId: string, prompt: string) {
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
      'You are Founder Copilot — concise, founder-friendly weekly ops assistant.',
      `${prompt}\n\nContext:\n${summary.body}`,
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
