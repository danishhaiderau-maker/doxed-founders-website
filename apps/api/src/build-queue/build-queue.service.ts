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
  FounderEventType,
  NotificationType,
  Prisma,
  RoadmapStatus,
  SuggestedUpdateStatus,
} from '@prisma/client';
import {
  COMMAND_BAR_CREDITS,
  CommandBarIntent,
  WORKFORCE_TEMPLATES,
  WorkforceAgentOutput,
  WorkforceRuntimeResult,
  buildCursorCopyBlock,
  buildWorkforceAgentSystemPrompt,
  buildWorkforceGithubContext,
  emptyWorkforceRuntime,
  mergeWorkforceAgentWithLlm,
  processCommandBar,
  runWorkforceAgent,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { NotificationsService } from '../notifications/notifications.service';
import { GitHubApiService } from '../github/github-api.service';
import { BuilderService } from '../builder/builder.service';
import { EventsService } from '../events/events.service';

function labelFromTemplate(template: string) {
  return WORKFORCE_TEMPLATES.find((t) => t.key === template)?.label ?? template.replace(/_/g, ' ');
}

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
    private readonly github: GitHubApiService,
    private readonly builder: BuilderService,
    private readonly events: EventsService,
  ) {}

  private orchestratorFingerprint(template: string, prompt: string) {
    return `${template}:${prompt.trim().toLowerCase().replace(/\s+/g, ' ').slice(0, 240)}`;
  }

  private async findOpenOrchestratorIdea(founderId: string, template: string, prompt: string) {
    const fingerprint = this.orchestratorFingerprint(template, prompt);
    const candidates = await this.prisma.buildQueueItem.findMany({
      where: {
        founderId,
        kind: BuildQueueItemKind.IDEA,
        status: { notIn: [BuildQueueStatus.DISMISSED, BuildQueueStatus.DONE] },
        source: BuildQueueSource.AGENT,
        createdAt: { gte: new Date(Date.now() - 24 * 60 * 60 * 1000) },
      },
      orderBy: { createdAt: 'desc' },
      take: 25,
    });
    const match = candidates.find((item) => {
      const meta = item.metadata as Record<string, unknown> | null;
      return meta?.template === template && meta?.promptFingerprint === fingerprint;
    });
    return match?.id ?? null;
  }

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
    const repo = await this.github.resolveRepo(userId, founder.githubRepoFullName, project?.githubRepoFullName);
    const githubTokenConnected = await this.github.hasToken(userId);

    let commits: { sha: string; message: string; date: string }[] = [];
    let pullRequests: { title: string; url: string; state: string; number: number }[] = [];
    if (repo) {
      commits = await this.github.listCommits(userId, repo);
      if (githubTokenConnected) {
        pullRequests = await this.github.listPullRequests(userId, repo);
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

    const builderSettings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });

    return {
      repoFullName: repo ?? null,
      cursorConnected,
      githubConnected: Boolean(gh || repo),
      githubTokenConnected,
      defaultAiProvider: builderSettings?.defaultProvider ?? 'RULE_BASED',
      autoCreateGitHubIssues: builderSettings?.autoCreateGitHubIssues ?? false,
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
      pullRequests,
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
    const parsed = await this.builder.enhanceQuickBuild(userId, prompt, project?.name);

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

    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    let githubIssuesCreated = 0;
    if (settings?.autoCreateGitHubIssues) {
      githubIssuesCreated = await this.publishIssueRows(userId, founder.id, issueItems, parsed.spec);
    }

    await this.notifications.notifyUser(userId, {
      type: NotificationType.BUILD_QUEUE,
      title: 'Quick Build captured',
      body: `"${parsed.ideaTitle.slice(0, 80)}" → ${taskItems.length} tasks, ${issueItems.length} issues queued${
        githubIssuesCreated ? `, ${githubIssuesCreated} on GitHub` : ''
      }.`,
      link: '/founder-den?tab=build',
    });

    await this.events.emit({
      founderId: founder.id,
      projectId: project?.id,
      userId,
      type: FounderEventType.BUILD_QUEUE_CAPTURED,
      source: source === BuildQueueSource.VOICE ? 'voice' : 'founder-os',
      title: parsed.ideaTitle.slice(0, 120),
      payload: { ideaId: idea.id, taskCount: taskItems.length, issueCount: issueItems.length },
    });

    let openHandsDispatch:
      | (Awaited<ReturnType<BuilderService['dispatchOpenHandsBuildTask']>> & { error?: undefined })
      | { error: string }
      | null = null;
    let cursorCloudDispatch:
      | (Awaited<ReturnType<BuilderService['dispatchCursorBuildTask']>> & { error?: undefined })
      | { error: string }
      | null = null;

    if (settings?.defaultProvider === 'OPENHANDS') {
      try {
        const repo = founder.githubRepoFullName ?? project?.githubRepoFullName ?? undefined;
        openHandsDispatch = await this.builder.dispatchOpenHandsBuildTask(userId, {
          spec: parsed.spec,
          cursorPrompt: parsed.cursorPrompt,
          repository: repo,
        });
        await this.prisma.buildQueueItem.update({
          where: { id: specItem.id },
          data: { status: BuildQueueStatus.IN_PROGRESS },
        });
      } catch (err) {
        openHandsDispatch = {
          error: err instanceof Error ? err.message : 'OpenHands dispatch failed',
        };
      }
    } else if (settings?.defaultProvider === 'CURSOR') {
      try {
        const repo = founder.githubRepoFullName ?? project?.githubRepoFullName ?? undefined;
        cursorCloudDispatch = await this.builder.dispatchCursorBuildTask(userId, {
          spec: parsed.spec,
          cursorPrompt: parsed.cursorPrompt,
          repository: repo,
        });
        await this.prisma.buildQueueItem.update({
          where: { id: specItem.id },
          data: { status: BuildQueueStatus.IN_PROGRESS },
        });
      } catch (err) {
        cursorCloudDispatch = {
          error: err instanceof Error ? err.message : 'Cursor dispatch failed',
        };
      }
    }

    return {
      ideaId: idea.id,
      specId: specItem.id,
      roadmapItemId,
      taskIds: taskItems.map((t) => t.id),
      issueIds: issueItems.map((i) => i.id),
      githubIssuesCreated,
      cursorPrompt: parsed.cursorPrompt,
      cursorCopy: buildCursorCopyBlock({
        title: parsed.ideaTitle,
        spec: parsed.spec,
        cursorPrompt: parsed.cursorPrompt,
        githubIssues: parsed.githubIssues.map((title) => ({ title })),
      }),
      openHandsDispatch,
      cursorCloudDispatch,
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

    await this.events.emit({
      founderId: founder.id,
      projectId: project?.id,
      userId,
      type: FounderEventType.QUICK_COMMAND,
      source: 'command-bar',
      title: result.title,
      payload: { intent: input.intent, parentId: parent.id },
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

  async runOrchestratedWorkforce(userId: string, template: string, prompt: string) {
    const founder = await this.requireFounder(userId);
    const project = founder.projects[0];
    const projectName = project?.name ?? founder.name;
    const label = WORKFORCE_TEMPLATES.find((t) => t.key === template)?.label ?? template;

    const repo = await this.github.resolveRepo(
      userId,
      founder.githubRepoFullName,
      project?.githubRepoFullName,
    );
    const recentCommits = repo ? await this.github.listCommits(userId, repo, 5).catch(() => []) : [];
    const openTasks = await this.prisma.buildQueueItem.findMany({
      where: {
        founderId: founder.id,
        kind: BuildQueueItemKind.TASK,
        status: { notIn: [BuildQueueStatus.DISMISSED, BuildQueueStatus.DONE] },
      },
      take: 5,
      orderBy: { updatedAt: 'desc' },
    });

    let output = runWorkforceAgent(template, prompt, projectName);
    let answerProvider: 'RULE_BASED' | 'LLM' = 'RULE_BASED';

    const systemPrompt = buildWorkforceAgentSystemPrompt(template, label, projectName);
    const contextBlock = buildWorkforceGithubContext({
      repoFullName: repo,
      recentCommits,
      openTasks: openTasks.map((t) => t.title),
    });
    const llmText = await this.builder.tryAiCompletion(
      userId,
      systemPrompt,
      `${prompt}${contextBlock}`,
    );
    if (llmText) {
      output = mergeWorkforceAgentWithLlm(output, llmText);
      answerProvider = 'LLM';
    }

    const ideaId = await this.createFromOrchestratorOutput(userId, template, prompt, output);
    const runtime = ideaId
      ? await this.executeWorkforceRuntime(userId, template, ideaId, output)
      : emptyWorkforceRuntime(template);

    if (ideaId) {
      await this.emitWorkforceComplete(userId, founder.id, project?.id, template, output, runtime, {
        orchestrator: true,
        ideaId,
      });
    }

    return {
      output,
      answerProvider,
      template,
      label,
      runtime,
      ideaId,
    };
  }

  async executeWorkforceRuntime(
    userId: string,
    template: string,
    ideaId: string,
    output: WorkforceAgentOutput,
  ): Promise<WorkforceRuntimeResult> {
    const runtime = emptyWorkforceRuntime(template);
    const founder = await this.requireFounder(userId);
    const project = founder.projects[0];
    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const idea = await this.prisma.buildQueueItem.findUnique({ where: { id: ideaId } });
    if (!idea) return runtime;

    const repo = await this.github.resolveRepo(
      userId,
      founder.githubRepoFullName,
      project?.githubRepoFullName,
    );

    if (
      runtime.permissions.includes('github_issues') &&
      output.githubIssues.length > 0 &&
      repo &&
      (await this.github.hasToken(userId))
    ) {
      const issueItems = await this.prisma.buildQueueItem.findMany({
        where: {
          parentId: ideaId,
          kind: BuildQueueItemKind.GITHUB_ISSUE,
          githubIssueUrl: null,
          status: { notIn: [BuildQueueStatus.DISMISSED, BuildQueueStatus.DONE] },
        },
      });
      if (issueItems.length > 0) {
        runtime.githubIssuesCreated = await this.publishIssueRows(
          userId,
          founder.id,
          issueItems,
          output.summary,
        );
        if (runtime.githubIssuesCreated > 0) {
          runtime.toolsUsed.push('github_issues');
          runtime.githubRepo = repo;
          await this.events.emit({
            founderId: founder.id,
            projectId: project?.id,
            userId,
            type: FounderEventType.GITHUB_ISSUE_CREATED,
            source: 'copilot',
            title: `Created ${runtime.githubIssuesCreated} GitHub issue(s)`,
            payload: { created: runtime.githubIssuesCreated, repo, template, ideaId },
          });
        }
      }
    }

    if (
      runtime.permissions.includes('cursor_agent') &&
      settings?.defaultProvider === 'CURSOR' &&
      idea.cursorPrompt
    ) {
      try {
        const dispatch = await this.builder.dispatchCursorBuildTask(userId, {
          spec: output.summary,
          cursorPrompt: idea.cursorPrompt,
          repository: repo ?? founder.githubRepoFullName ?? project?.githubRepoFullName ?? undefined,
        });
        runtime.cursorDispatched = true;
        runtime.cursorAgentUrl = dispatch.agentUrl ?? null;
        runtime.toolsUsed.push('cursor_agent');
        await this.prisma.buildQueueItem.update({
          where: { id: ideaId },
          data: { status: BuildQueueStatus.IN_PROGRESS },
        });
        await this.events.emit({
          founderId: founder.id,
          projectId: project?.id,
          userId,
          type: FounderEventType.CURSOR_BUILD_SESSION,
          source: 'cursor',
          title: `Cursor agent started for ${labelFromTemplate(template)}`,
          payload: { agentUrl: dispatch.agentUrl, ideaId, template },
        });
      } catch {
        /* Cursor optional */
      }
    }

    if (runtime.permissions.includes('community_draft') && output.summary.trim()) {
      runtime.communityDraftSaved = true;
      runtime.toolsUsed.push('community_draft');
      await this.notifications.notifyUser(userId, {
        type: NotificationType.BUILD_QUEUE,
        title: `${labelFromTemplate(template)} draft ready`,
        body: output.summary.slice(0, 180),
        link: '/founder-den?tab=community',
      });
    }

    if (runtime.permissions.includes('raise_room')) {
      runtime.raiseRoomLinked = true;
      runtime.toolsUsed.push('raise_room');
    }

    return runtime;
  }

  private async emitWorkforceComplete(
    userId: string,
    founderId: string,
    projectId: string | undefined,
    template: string,
    output: WorkforceAgentOutput,
    runtime: WorkforceRuntimeResult,
    payload: Record<string, unknown>,
    eventSource: 'copilot' | 'agent' = 'copilot',
  ) {
    await this.events.emit({
      founderId,
      projectId,
      userId,
      type: FounderEventType.AGENT_RUN_COMPLETE,
      source: eventSource,
      title: `${labelFromTemplate(template)}: ${output.title.slice(0, 80)}`,
      payload: {
        ...payload,
        template,
        taskCount: output.tasks.length,
        githubIssuesCreated: runtime.githubIssuesCreated,
        cursorDispatched: runtime.cursorDispatched,
        toolsUsed: runtime.toolsUsed,
      },
    });

    const actionParts = [`${output.tasks.length} tasks queued`];
    if (runtime.githubIssuesCreated > 0) {
      actionParts.push(`${runtime.githubIssuesCreated} GitHub issue(s) created`);
    }
    if (runtime.cursorDispatched) actionParts.push('Cursor agent started');

    await this.notifications.notifyUser(userId, {
      type: NotificationType.AGENT_RESULT,
      title: `${labelFromTemplate(template)}: ${output.title.slice(0, 50)}`,
      body: actionParts.join(' · '),
      link: '/founder-den?tab=build',
    });
  }

  async createFromOrchestratorOutput(
    userId: string,
    template: string,
    prompt: string,
    output: WorkforceAgentOutput,
  ): Promise<string | null> {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { take: 1 } },
    });
    if (!founder) return null;

    const fingerprint = this.orchestratorFingerprint(template, prompt);
    const existingId = await this.findOpenOrchestratorIdea(founder.id, template, prompt);
    if (existingId) return existingId;

    const project = founder.projects[0];
    const source = BuildQueueSource.AGENT;
    const agentSlug = `copilot-${template.toLowerCase()}`;

    const idea = await this.prisma.buildQueueItem.create({
      data: {
        founderId: founder.id,
        projectId: project?.id,
        kind: BuildQueueItemKind.IDEA,
        status: BuildQueueStatus.SPECCED,
        source,
        title: output.title.slice(0, 120),
        description: output.summary,
        spec: [output.summary, '', ...output.buildPlan.map((b) => `- ${b}`)].join('\n'),
        cursorPrompt: [
          `Copilot → ${template} worker for ${project?.name ?? founder.name}:`,
          prompt,
          '',
          ...output.tasks.map((t) => `- ${t}`),
        ].join('\n'),
        metadata: { orchestrator: true, template, agentSlug, copilot: true, promptFingerprint: fingerprint },
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
            title: task.slice(0, 120),
            sortOrder: idx,
            metadata: { orchestrator: true, template },
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
            title: issue.slice(0, 120),
            githubIssueTitle: issue,
            sortOrder: 100 + idx,
            metadata: { orchestrator: true, template },
          },
        }),
      ),
    ]);

    return idea.id;
  }

  async createFromAgentRun(
    userId: string,
    agentRunId: string,
    agent: { template: string; slug: string; project?: { name: string } | null; founder: { name: string } },
    prompt: string,
    output: ReturnType<typeof runWorkforceAgent>,
  ): Promise<string | null> {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { take: 1 } },
    });
    if (!founder) return null;

    const fingerprint = this.orchestratorFingerprint(agent.template, prompt);
    const existingId = await this.findOpenOrchestratorIdea(founder.id, agent.template, prompt);
    if (existingId) return existingId;

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
        metadata: {
          agentSlug: agent.slug,
          template: agent.template,
          promptFingerprint: fingerprint,
        },
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

    return idea.id;
  }

  async finalizeAgentRun(
    userId: string,
    template: string,
    ideaId: string | null,
    output: WorkforceAgentOutput,
    meta: { agentSlug: string; agentRunId: string; marketplace: true },
  ) {
    if (!ideaId) return emptyWorkforceRuntime(template);
    const runtime = await this.executeWorkforceRuntime(userId, template, ideaId, output);
    const founder = await this.requireFounder(userId);
    const project = founder.projects[0];
    await this.emitWorkforceComplete(userId, founder.id, project?.id, template, output, runtime, {
      ...meta,
      ideaId,
    }, 'agent');
    return runtime;
  }

  async publishGitHubIssues(userId: string) {
    const founder = await this.requireFounder(userId);
    const project = founder.projects[0];
    const repo = await this.github.resolveRepo(userId, founder.githubRepoFullName, project?.githubRepoFullName);
    if (!repo) throw new BadRequestException('Connect a GitHub repository first');

    const issues = await this.prisma.buildQueueItem.findMany({
      where: {
        founderId: founder.id,
        kind: BuildQueueItemKind.GITHUB_ISSUE,
        githubIssueUrl: null,
        status: { notIn: [BuildQueueStatus.DISMISSED, BuildQueueStatus.DONE] },
      },
      take: 20,
    });

    const created = await this.publishIssueRows(userId, founder.id, issues);
    if (created > 0) {
      await this.events.emit({
        founderId: founder.id,
        projectId: project?.id,
        userId,
        type: FounderEventType.GITHUB_ISSUE_CREATED,
        source: 'github',
        title: `Published ${created} GitHub issue(s)`,
        payload: { created, repo },
      });
    }
    return { created, repoFullName: repo };
  }

  private async publishIssueRows(
    userId: string,
    founderId: string,
    items: { id: string; githubIssueTitle: string | null; title: string; spec: string | null; githubIssueUrl?: string | null }[],
    specBody?: string,
  ): Promise<number> {
    const founder = await this.prisma.founder.findUnique({
      where: { id: founderId },
      include: { projects: { take: 1 } },
    });
    if (!founder) return 0;

    const repo = await this.github.resolveRepo(
      userId,
      founder.githubRepoFullName,
      founder.projects[0]?.githubRepoFullName,
    );
    if (!repo || !(await this.github.hasToken(userId))) return 0;

    let count = 0;
    for (const item of items) {
      if (item.githubIssueUrl) continue;
      try {
        const title = item.githubIssueTitle ?? item.title;
        const body = item.spec ?? specBody ?? title;
        const created = await this.github.createIssue(userId, repo, title, body);
        await this.prisma.buildQueueItem.update({
          where: { id: item.id },
          data: {
            githubIssueUrl: created.url,
            status: BuildQueueStatus.QUEUED,
            metadata: { githubNumber: created.number } as Prisma.InputJsonValue,
          },
        });
        count += 1;
      } catch {
        /* skip failed issue */
      }
    }
    return count;
  }
}
