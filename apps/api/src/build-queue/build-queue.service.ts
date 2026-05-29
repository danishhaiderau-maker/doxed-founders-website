import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import {
  BuildQueueItemKind,
  BuildQueueSource,
  BuildQueueStatus,
  NotificationType,
  Prisma,
  RoadmapStatus,
  SuggestedUpdateStatus,
} from '@prisma/client';
import {
  COMMAND_BAR_CREDITS,
  CommandBarIntent,
  buildCursorCopyBlock,
  processCommandBar,
  processQuickBuild,
  runWorkforceAgent,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { NotificationsService } from '../notifications/notifications.service';

function serializeItem(item: {
  id: string;
  kind: BuildQueueItemKind;
  status: BuildQueueStatus;
  source: BuildQueueSource;
  title: string;
  description: string | null;
  spec: string | null;
  githubIssueTitle: string | null;
  githubIssueUrl: string | null;
  cursorPrompt: string | null;
  roadmapItemId: string | null;
  agentRunId: string | null;
  sortOrder: number;
  createdAt: Date;
  updatedAt: Date;
  parentId: string | null;
}) {
  return {
    ...item,
    createdAt: item.createdAt.toISOString(),
    updatedAt: item.updatedAt.toISOString(),
  };
}

@Injectable()
export class BuildQueueService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly notifications: NotificationsService,
  ) {}

  private async requireFounder(userId: string) {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { where: { approved: true }, take: 1 } },
    });
    if (!founder) throw new ForbiddenException('Activate your founder profile first');
    return founder;
  }

  async listItems(userId: string) {
    const founder = await this.requireFounder(userId);
    const items = await this.prisma.buildQueueItem.findMany({
      where: { founderId: founder.id, status: { not: BuildQueueStatus.DISMISSED } },
      orderBy: [{ sortOrder: 'asc' }, { createdAt: 'desc' }],
      take: 100,
    });
    return { items: items.map(serializeItem) };
  }

  async getBuildRoom(userId: string) {
    const founder = await this.requireFounder(userId);
    const project = founder.projects[0];

    const items = await this.prisma.buildQueueItem.findMany({
      where: { founderId: founder.id, status: { not: BuildQueueStatus.DISMISSED } },
      orderBy: [{ createdAt: 'desc' }],
      take: 80,
    });

    const gh = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    let commits: { sha: string; message: string; date: string }[] = [];
    const repo = gh?.repoFullName ?? founder.githubRepoFullName ?? project?.githubRepoFullName;
    if (repo) {
      try {
        const res = await fetch(`https://api.github.com/repos/${repo}/commits?per_page=10`, {
          headers: { Accept: 'application/vnd.github+json', 'User-Agent': 'DoxxedCrypto-FounderOS' },
        });
        if (res.ok) {
          const data = (await res.json()) as {
            sha: string;
            commit: { message: string; author: { date: string } };
          }[];
          commits = data.map((c) => ({
            sha: c.sha.slice(0, 7),
            message: c.commit.message.split('\n')[0] ?? c.commit.message,
            date: c.commit.author.date,
          }));
        }
      } catch {
        /* public repo fetch optional */
      }
    }

    const deploySuggestions = await this.prisma.suggestedBuildUpdate.findMany({
      where: {
        founderId: founder.id,
        source: { in: ['vercel', 'railway', 'deploy', 'github'] },
      },
      orderBy: { createdAt: 'desc' },
      take: 10,
      select: { id: true, headline: true, body: true, source: true, createdAt: true, status: true },
    });

    const pendingPublish = await this.prisma.suggestedBuildUpdate.findMany({
      where: { founderId: founder.id, status: SuggestedUpdateStatus.PENDING },
      orderBy: { createdAt: 'desc' },
      take: 5,
    });

    const cursorConnected = Boolean(
      await this.prisma.integrationCredential.findFirst({
        where: { userId, provider: 'cursor', verifiedAt: { not: null } },
      }),
    );

    const grouped = {
      ideas: items.filter((i) => i.kind === BuildQueueItemKind.IDEA),
      tasks: items.filter((i) => i.kind === BuildQueueItemKind.TASK),
      issues: items.filter((i) => i.kind === BuildQueueItemKind.GITHUB_ISSUE),
      specs: items.filter((i) => i.kind === BuildQueueItemKind.SPEC),
      roadmap: items.filter((i) => i.kind === BuildQueueItemKind.ROADMAP),
    };

    const openIssues = grouped.issues.filter((i) => i.status !== BuildQueueStatus.DONE);

    return {
      repoFullName: repo ?? null,
      cursorConnected,
      githubConnected: Boolean(gh || repo),
      grouped: {
        ideas: grouped.ideas.map(serializeItem),
        tasks: grouped.tasks.map(serializeItem),
        issues: grouped.issues.map(serializeItem),
        specs: grouped.specs.map(serializeItem),
        roadmap: grouped.roadmap.map(serializeItem),
      },
      commits,
      deployments: deploySuggestions.map((d) => ({
        id: d.id,
        headline: d.headline,
        source: d.source,
        status: d.status,
        createdAt: d.createdAt.toISOString(),
      })),
      pullRequests: [] as { title: string; url: string; state: string }[],
      pendingSuggestions: pendingPublish.map((s) => ({
        id: s.id,
        headline: s.headline,
        body: s.body,
        source: s.source,
        createdAt: s.createdAt.toISOString(),
      })),
      cursorCopy: buildCursorCopyBlock({
        title: openIssues[0]?.title ?? grouped.ideas[0]?.title ?? 'Build queue',
        spec: grouped.specs[0]?.spec ?? grouped.ideas[0]?.spec,
        cursorPrompt: grouped.ideas[0]?.cursorPrompt ?? openIssues[0]?.cursorPrompt,
        githubIssues: openIssues.slice(0, 5).map((i) => ({
          title: i.githubIssueTitle ?? i.title,
        })),
      }),
      stats: {
        ideas: grouped.ideas.length,
        tasks: grouped.tasks.filter((t) => t.status !== BuildQueueStatus.DONE).length,
        issues: openIssues.length,
        commits: commits.length,
      },
    };
  }

  async quickBuild(
    userId: string,
    input: { prompt: string; source?: 'QUICK_BUILD' | 'VOICE' },
  ) {
    const founder = await this.requireFounder(userId);
    const prompt = input.prompt?.trim();
    if (!prompt) throw new BadRequestException('Describe what to build');

    const project = founder.projects[0];
    const parsed = processQuickBuild(prompt, project?.name);

    const source =
      input.source === 'VOICE' ? BuildQueueSource.VOICE : BuildQueueSource.QUICK_BUILD;

    const idea = await this.prisma.buildQueueItem.create({
      data: {
        founderId: founder.id,
        projectId: project?.id,
        kind: BuildQueueItemKind.IDEA,
        status: BuildQueueStatus.SPECCED,
        source,
        title: parsed.ideaTitle,
        description: parsed.traderView,
        spec: parsed.spec,
        cursorPrompt: parsed.cursorPrompt,
        sortOrder: 0,
      },
    });

    const specItem = await this.prisma.buildQueueItem.create({
      data: {
        founderId: founder.id,
        projectId: project?.id,
        parentId: idea.id,
        kind: BuildQueueItemKind.SPEC,
        status: BuildQueueStatus.QUEUED,
        source,
        title: `Spec: ${parsed.ideaTitle.slice(0, 60)}`,
        spec: parsed.spec,
        cursorPrompt: parsed.cursorPrompt,
        sortOrder: 1,
      },
    });

    let roadmapItemId: string | undefined;
    if (project?.id) {
      const maxOrder = await this.prisma.projectRoadmapItem.aggregate({
        where: { projectId: project.id },
        _max: { sortOrder: true },
      });
      const roadmap = await this.prisma.projectRoadmapItem.create({
        data: {
          projectId: project.id,
          title: parsed.roadmapTitle,
          status: RoadmapStatus.PLANNED,
          sortOrder: (maxOrder._max.sortOrder ?? 0) + 1,
        },
      });
      roadmapItemId = roadmap.id;
      await this.prisma.buildQueueItem.create({
        data: {
          founderId: founder.id,
          projectId: project.id,
          parentId: idea.id,
          kind: BuildQueueItemKind.ROADMAP,
          status: BuildQueueStatus.QUEUED,
          source,
          title: parsed.roadmapTitle,
          roadmapItemId: roadmap.id,
          sortOrder: 2,
        },
      });
    }

    const taskItems = await Promise.all(
      parsed.tasks.map((task, idx) =>
        this.prisma.buildQueueItem.create({
          data: {
            founderId: founder.id,
            projectId: project?.id,
            parentId: idea.id,
            kind: BuildQueueItemKind.TASK,
            status: BuildQueueStatus.QUEUED,
            source,
            title: task.slice(0, 120),
            description: task,
            sortOrder: 10 + idx,
          },
        }),
      ),
    );

    const issueItems = await Promise.all(
      parsed.githubIssues.map((issue, idx) =>
        this.prisma.buildQueueItem.create({
          data: {
            founderId: founder.id,
            projectId: project?.id,
            parentId: idea.id,
            kind: BuildQueueItemKind.GITHUB_ISSUE,
            status: BuildQueueStatus.QUEUED,
            source,
            title: issue.slice(0, 120),
            githubIssueTitle: issue,
            cursorPrompt: parsed.cursorPrompt,
            sortOrder: 20 + idx,
          },
        }),
      ),
    );

    await this.notifications.notifyUser(userId, {
      type: NotificationType.BUILD_QUEUE,
      title: 'Quick Build captured',
      body: `"${parsed.ideaTitle.slice(0, 80)}" → ${taskItems.length} tasks, ${issueItems.length} GitHub issues queued.`,
      link: '/founder-den?tab=build',
    });

    return {
      ideaId: idea.id,
      specId: specItem.id,
      roadmapItemId,
      taskIds: taskItems.map((t) => t.id),
      issueIds: issueItems.map((i) => i.id),
      cursorPrompt: parsed.cursorPrompt,
      cursorCopy: buildCursorCopyBlock({
        title: parsed.ideaTitle,
        spec: parsed.spec,
        cursorPrompt: parsed.cursorPrompt,
        githubIssues: parsed.githubIssues.map((title) => ({ title })),
      }),
      parsed,
    };
  }

  async runCommand(userId: string, input: { intent: CommandBarIntent; prompt?: string }) {
    const founder = await this.requireFounder(userId);
    if (founder.founderCredits < COMMAND_BAR_CREDITS) {
      throw new BadRequestException(`Need ${COMMAND_BAR_CREDITS} Founder Credits`);
    }

    const project = founder.projects[0];
    const recentPosts = project
      ? await this.prisma.founderBuildPost.findMany({
          where: { projectId: project.id },
          orderBy: { publishedAt: 'desc' },
          take: 5,
          select: { headline: true },
        })
      : [];

    const result = processCommandBar(input.intent, input.prompt ?? '', {
      projectName: project?.name,
      recentHeadlines: recentPosts.map((p) => p.headline),
    });

    const updated = await this.prisma.founder.update({
      where: { id: founder.id },
      data: { founderCredits: { decrement: COMMAND_BAR_CREDITS } },
    });

    await this.prisma.founderCreditLedger.create({
      data: {
        userId,
        founderId: founder.id,
        projectId: project?.id,
        delta: -COMMAND_BAR_CREDITS,
        balanceAfter: updated.founderCredits,
        reason: `COMMAND_BAR:${input.intent}`,
      },
    });

    const parent = await this.prisma.buildQueueItem.create({
      data: {
        founderId: founder.id,
        projectId: project?.id,
        kind: BuildQueueItemKind.SPEC,
        status: BuildQueueStatus.SPECCED,
        source: BuildQueueSource.COMMAND_BAR,
        title: result.title,
        description: result.summary,
        spec: result.body,
        cursorPrompt: result.cursorPrompt,
        metadata: { intent: input.intent } as Prisma.InputJsonValue,
      },
    });

    const children = await Promise.all(
      result.queueItems.map((q, idx) =>
        this.prisma.buildQueueItem.create({
          data: {
            founderId: founder.id,
            projectId: project?.id,
            parentId: parent.id,
            kind: q.kind as BuildQueueItemKind,
            status: BuildQueueStatus.QUEUED,
            source: BuildQueueSource.COMMAND_BAR,
            title: q.title,
            description: q.description,
            cursorPrompt: result.cursorPrompt,
            sortOrder: idx,
          },
        }),
      ),
    );

    await this.notifications.notifyUser(userId, {
      type: NotificationType.BUILD_QUEUE,
      title: result.title,
      body: result.summary,
      link: '/founder-den?tab=build',
    });

    return {
      creditsSpent: COMMAND_BAR_CREDITS,
      parentId: parent.id,
      itemIds: children.map((c) => c.id),
      result,
      cursorCopy: buildCursorCopyBlock({
        title: result.title,
        spec: result.body,
        cursorPrompt: result.cursorPrompt,
      }),
    };
  }

  async updateItem(
    userId: string,
    itemId: string,
    input: { status?: BuildQueueStatus; title?: string },
  ) {
    const founder = await this.requireFounder(userId);
    const updated = await this.prisma.buildQueueItem.updateMany({
      where: { id: itemId, founderId: founder.id },
      data: {
        ...(input.status ? { status: input.status } : {}),
        ...(input.title ? { title: input.title.trim() } : {}),
      },
    });
    if (updated.count === 0) throw new NotFoundException('Queue item not found');
    const item = await this.prisma.buildQueueItem.findUnique({ where: { id: itemId } });
    return { item: item ? serializeItem(item) : null };
  }

  async dismissItem(userId: string, itemId: string) {
    return this.updateItem(userId, itemId, { status: BuildQueueStatus.DISMISSED });
  }

  async getCursorCopy(userId: string, itemId: string) {
    const founder = await this.requireFounder(userId);
    const item = await this.prisma.buildQueueItem.findFirst({
      where: { id: itemId, founderId: founder.id },
    });
    if (!item) throw new NotFoundException('Queue item not found');

    const issues = await this.prisma.buildQueueItem.findMany({
      where: {
        founderId: founder.id,
        kind: BuildQueueItemKind.GITHUB_ISSUE,
        status: { not: BuildQueueStatus.DISMISSED },
        ...(item.parentId ? { parentId: item.parentId } : {}),
      },
      take: 8,
    });

    return {
      cursorCopy: buildCursorCopyBlock({
        title: item.title,
        spec: item.spec,
        cursorPrompt: item.cursorPrompt,
        githubIssues: issues.map((i) => ({ title: i.githubIssueTitle ?? i.title })),
      }),
    };
  }

  async createFromAgentRun(
    userId: string,
    agentRunId: string,
    agent: { template: string; slug: string; project?: { name: string } | null; founder: { name: string } },
    prompt: string,
    output: ReturnType<typeof runWorkforceAgent>,
  ) {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { take: 1 } },
    });
    if (!founder) return;

    const project = founder.projects[0];
    const source = BuildQueueSource.AGENT;

    const idea = await this.prisma.buildQueueItem.create({
      data: {
        founderId: founder.id,
        projectId: project?.id,
        kind: BuildQueueItemKind.IDEA,
        status: BuildQueueStatus.SPECCED,
        source,
        agentRunId,
        title: output.title.slice(0, 120),
        description: output.summary,
        spec: [output.summary, '', ...output.buildPlan.map((b) => `- ${b}`)].join('\n'),
        cursorPrompt: [
          `Agent (${agent.slug}) output for ${agent.project?.name ?? agent.founder.name}:`,
          prompt,
          '',
          ...output.tasks.map((t) => `- ${t}`),
        ].join('\n'),
        metadata: { agentSlug: agent.slug, template: agent.template },
      },
    });

    await Promise.all([
      ...output.tasks.map((task, idx) =>
        this.prisma.buildQueueItem.create({
          data: {
            founderId: founder.id,
            projectId: project?.id,
            parentId: idea.id,
            kind: BuildQueueItemKind.TASK,
            status: BuildQueueStatus.QUEUED,
            source,
            agentRunId,
            title: task.slice(0, 120),
            sortOrder: idx,
          },
        }),
      ),
      ...output.githubIssues.map((issue, idx) =>
        this.prisma.buildQueueItem.create({
          data: {
            founderId: founder.id,
            projectId: project?.id,
            parentId: idea.id,
            kind: BuildQueueItemKind.GITHUB_ISSUE,
            status: BuildQueueStatus.QUEUED,
            source,
            agentRunId,
            title: issue.slice(0, 120),
            githubIssueTitle: issue,
            sortOrder: 100 + idx,
          },
        }),
      ),
    ]);

    await this.notifications.notifyUser(userId, {
      type: NotificationType.AGENT_RESULT,
      title: `Agent: ${output.title.slice(0, 60)}`,
      body: `${output.tasks.length} tasks added to your build queue.`,
      link: '/founder-den?tab=build',
    });
  }
}
