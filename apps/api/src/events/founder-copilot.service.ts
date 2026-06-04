import {
  BadRequestException,
  ForbiddenException,
  Inject,
  Injectable,
  forwardRef,
} from '@nestjs/common';
import {
  AiProvider,
  BuildQueueStatus,
  FounderEventType,
  RoadmapStatus,
  SimulatedRaiseStatus,
  SuggestedUpdateStatus,
} from '@prisma/client';
import {
  buildCommunityUpdateFromSummary,
  buildFounderUpdateFallback,
  formatCommitsLast24hForTraders,
  resolveProjectDisplayForSocial,
  PLATFORM_X_SHARE_FOOTER,
  filterCommitsSince,
  buildDailyStandup,
  buildMissingLinkNarrativeHints,
  buildResumeCursorPrompt,
  buildSocialDraftFounderAccountBlock,
  buildSocialDraftSystemPrompt,
  buildWeeklySummary,
  formatAutopilotInfrastructureBlock,
  formatCommitsByDay,
  formatLastCommitDetail,
  composeFounderUpdateFeedBody,
  formatFounderUpdateContextBlock,
  founderUpdateFromLegacyFallback,
  parseFounderUpdateLlmResponse,
  pickFounderUpdateDisplayBody,
  computeProjectProgress,
  detectAutopilotIntent,
  detectHandsFreeAction,
  detectCursorDispatchIntent,
  detectWorkforceIntent,
  formatOrchestratorCopilotAnswer,
  formatRelativeTime,
  formatWorkspaceActivityForPrompt,
  FOUNDER_OS_MEMORY_DIR,
  stripDeviceMemoryToMetadata,
  isMetadataOnlyPayload,
  extractVaultRelaySummary,
  buildContinueFromMissionPrompt,
  classifyFounderBrainTask,
  detectContinueMissionIntent,
  formatMissionStateBlock,
  resolveMissionBuildTask,
  getFounderBrainRouteLabel,
  shouldDispatchBuilderForCodeAsk,
  deriveMissionIntelligence,
  formatFounderBrainContextForPrompt,
  formatRuleBasedBrainAnswer,
  type DeviceMemoryPayload,
  type DeviceMemoryMetadataPayload,
  type FounderBrainContextInput,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { BuildQueueService } from '../build-queue/build-queue.service';
import { BuilderService } from '../builder/builder.service';
import { GitHubApiService } from '../github/github-api.service';
import { FounderOsMemoryService } from '../github/founder-os-memory.service';
import { FounderOsService } from '../founder-os/founder-os.service';
import { EventsService } from './events.service';
import { FounderAutopilotService } from './founder-autopilot.service';
import { FounderMetricsService } from './founder-metrics.service';
import { FounderMemoryGraphService } from '../founder-memory/founder-memory-graph.service';
import type { FounderMemoryGraphPatch } from '@dcf/utils';

@Injectable()
export class FounderCopilotService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly events: EventsService,
    private readonly metrics: FounderMetricsService,
    private readonly builder: BuilderService,
    private readonly github: GitHubApiService,
    private readonly memory: FounderOsMemoryService,
    private readonly memoryGraph: FounderMemoryGraphService,
    @Inject(forwardRef(() => BuildQueueService))
    private readonly buildQueue: BuildQueueService,
    @Inject(forwardRef(() => FounderOsService))
    private readonly founderOs: FounderOsService,
    @Inject(forwardRef(() => FounderAutopilotService))
    private readonly autopilot: FounderAutopilotService,
  ) {}

  async getMemoryGraph(userId: string) {
    return this.memoryGraph.resolveForUser(userId);
  }

  async patchMemoryGraph(userId: string, body: Record<string, unknown>) {
    const patch: FounderMemoryGraphPatch = {};
    const keys = [
      'project',
      'active_goal',
      'current_sprint',
      'current_task',
      'blocked_by',
      'next_action',
      'current_branch',
      'current_pr',
      'hypothesis',
      'experiment_status',
    ] as const;
    for (const k of keys) {
      if (body[k] !== undefined) {
        (patch as Record<string, unknown>)[k] = body[k];
      }
    }
    return this.memoryGraph.patchForUser(userId, patch);
  }

  async applyMemoryGraphAfterBuild(
    userId: string,
    body: {
      task: string;
      status: string;
      result?: string | null;
      branch?: string | null;
      prUrl?: string | null;
    },
  ) {
    if (!body.task?.trim() || !body.status?.trim()) {
      throw new BadRequestException('task and status required');
    }
    return this.memoryGraph.applyAfterBuild(userId, {
      task: body.task.trim(),
      status: body.status.trim(),
      result: body.result ?? null,
      branch: body.branch ?? null,
      prUrl: body.prUrl ?? null,
    });
  }

  async getProjectMemory(userId: string) {
    void this.founderOs.autoSyncGitHubCommits(userId).catch(() => undefined);

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

    const githubMemory = repo ? await this.memory.readRepoMemory(userId, repo) : null;
    const currentBranch = repo ? await this.github.getDefaultBranch(userId, repo) : null;
    const goalFromGithub = githubMemory?.currentGoalFromRepo?.trim();

    const deviceSyncRow = await this.prisma.projectMemoryDeviceSync.findUnique({
      where: { userId },
    });

    const connectedNodes =
      settings?.memoryStorageMode === 'FOUNDER_NODE'
        ? (
            await this.prisma.founderNode.findMany({
              where: { userId },
              orderBy: { lastSeenAt: 'desc' },
            })
          ).map((n) => ({
            nodeId: n.nodeId,
            label: n.label,
            status:
              n.lastSeenAt && Date.now() - n.lastSeenAt.getTime() < 180_000
                ? 'online'
                : 'offline',
            lastSeenAt: n.lastSeenAt?.toISOString() ?? null,
            ramGb: n.ramGb,
            storageGb: n.storageGb,
            storageFreeGb: n.storageFreeGb,
            vaultHealthy: n.vaultHealthy,
            platform: n.platform,
          }))
        : undefined;

    const vaultRelay = extractVaultRelaySummary({
      memoryStorageMode: settings?.memoryStorageMode ?? 'PLATFORM',
      deviceSync: deviceSyncRow
        ? {
            updatedAt: deviceSyncRow.updatedAt.toISOString(),
            deviceLabel: deviceSyncRow.deviceLabel,
            payload: deviceSyncRow.payload as DeviceMemoryPayload | DeviceMemoryMetadataPayload,
          }
        : null,
      connectedNodes,
    });

    const goalFromVault = vaultRelay?.currentGoal?.trim();
    const effectiveGoal =
      settings?.memoryStorageMode === 'FOUNDER_NODE' && goalFromVault
        ? goalFromVault
        : settings?.memoryStorageMode === 'LOCAL_SYNC' && goalFromVault
          ? goalFromVault
          : goalFromGithub || currentGoal;

    const cursorCopy = buildResumeCursorPrompt({
      projectName: project?.name ?? founder.name,
      currentGoal: effectiveGoal,
      suggestedNext,
      openTasks: tasks.map((t) => t.title),
      lastCommit: lastCommit ?? undefined,
    });

    const memoryPrefix = repo
      ? `Read ${FOUNDER_OS_MEMORY_DIR}/project-context.md, roadmap.md, and tasks.json in this repo first.\n\n`
      : '';
    const cursorCopyWithRepo = `${memoryPrefix}${cursorCopy}`;

    const deviceSyncPayload = deviceSyncRow?.payload as
      | DeviceMemoryPayload
      | DeviceMemoryMetadataPayload
      | undefined;

    return {
      welcomeMessage: `Welcome back${founder.user?.name ? `, ${founder.user.name.split(' ')[0]}` : ''}.`,
      project: project
        ? { id: project.id, name: project.name, slug: project.slug, lifecycleStage: project.lifecycleStage }
        : null,
      currentGoal: effectiveGoal,
      progressPercent,
      launchReadiness: readiness.score,
      buildStreakDays: founder.buildStreakDays,
      lastActivityAt: lastEvent?.createdAt.toISOString() ?? null,
      lastActivityLabel: formatRelativeTime(lastEvent?.createdAt),
      lastCommit,
      repoFullName: repo,
      currentBranch,
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
      memoryStorageMode: settings?.memoryStorageMode ?? 'PLATFORM',
      cursorCopy: cursorCopyWithRepo,
      deviceSync: deviceSyncRow
        ? {
            updatedAt: deviceSyncRow.updatedAt.toISOString(),
            deviceLabel: deviceSyncRow.deviceLabel,
            payload: deviceSyncPayload!,
          }
        : null,
      vaultRelay,
      githubMemory: githubMemory
        ? {
            repoFullName: githubMemory.repoFullName,
            hasProjectContext: Boolean(githubMemory.projectContext),
            hasRoadmap: Boolean(githubMemory.roadmap),
            openTasksFromRepo: githubMemory.openTasksFromRepo,
          }
        : null,
      connectedNodes,
      memoryGraph: await this.memoryGraph.resolveForUser(userId),
    };
  }

  /** Dynamic mission signals for command center + Founder Brain (P0). */
  async computeMissionIntelligenceForUser(userId: string) {
    const memory = await this.getProjectMemory(userId);
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
    if (!founder) throw new ForbiddenException('Founder profile required');
    const project = founder.projects[0];
    const repo = memory.repoFullName;
    const weekAgo = new Date(Date.now() - 7 * 86400000);

    const [commits, pullRequests, deployEvents, githubMemory] = await Promise.all([
      repo ? this.github.listCommits(userId, repo, 40) : Promise.resolve([]),
      repo ? this.github.listPullRequests(userId, repo) : Promise.resolve([]),
      this.prisma.founderEvent.findMany({
        where: {
          founderId: founder.id,
          type: { in: [FounderEventType.DEPLOY_SUCCESS, FounderEventType.DEPLOY_STARTED] },
          createdAt: { gte: weekAgo },
        },
        orderBy: { createdAt: 'desc' },
        take: 8,
      }),
      repo ? this.memory.readRepoMemory(userId, repo) : Promise.resolve(null),
    ]);

    const workspaceActivity = repo
      ? await this.builder.getWorkspaceActivity(userId, repo).catch(() => null)
      : null;

    const vaultNote = this.buildVaultContextLines(memory).join('\n') || null;
    const inProgressRoadmap = project?.roadmapItems?.find(
      (r) => r.status === RoadmapStatus.IN_PROGRESS,
    );

    const brainInput: FounderBrainContextInput = {
      projectName: project?.name ?? founder.name,
      projectDescription: githubMemory?.projectContext?.slice(0, 400) ?? null,
      repoFullName: repo,
      currentGoal: memory.currentGoal,
      progressPercent: memory.progressPercent,
      launchReadiness: memory.launchReadiness,
      suggestedNextStep: memory.suggestedNextStep,
      openTasks: memory.openTasks.map((t) => t.title),
      roadmapInProgress: inProgressRoadmap?.title ?? null,
      memoryGraph: memory.memoryGraph,
      commits: commits.map((c) => ({ sha: c.sha, message: c.message, date: c.date })),
      pullRequests,
      recentDeploys: deployEvents.map((e) => ({
        title: e.title,
        at: e.createdAt.toISOString(),
      })),
      projectContextExcerpt: githubMemory?.projectContext ?? null,
      roadmapExcerpt: githubMemory?.roadmap ?? null,
      repoTasks: githubMemory?.openTasksFromRepo?.map((t) => t.title),
      workspaceActivityBlock: workspaceActivity
        ? formatWorkspaceActivityForPrompt(workspaceActivity)
        : null,
      vaultNote,
    };

    return deriveMissionIntelligence(brainInput);
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

  async askContinueFromMissionState(userId: string) {
    const memory = await this.getProjectMemory(userId);
    const graph = memory.memoryGraph ?? (await this.memoryGraph.resolveForUser(userId));
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const block = formatMissionStateBlock(graph, {
      lastCommit: memory.lastCommit,
      openTaskCount: memory.openTasks.length,
    });

    const systemPrompt = `${this.memoryGraph.getPrefix(graph)}You are Founder Copilot. The user wants to continue where they left off. Use Mission State as ground truth. Give a short, actionable plan (3–6 bullets max). Do not ask what they are building.`;
    const brainTask = classifyFounderBrainTask(buildContinueFromMissionPrompt(graph));
    const aiResult = await this.builder.tryCopilotChatCompletion(
      userId,
      systemPrompt,
      buildContinueFromMissionPrompt(graph),
      { founderBrainTask: brainTask },
    );

    const routeLabel = getFounderBrainRouteLabel(brainTask);
    const answer = aiResult.ok
      ? `${block}\n\n---\n\n${aiResult.text}`
      : `${block}\n\n**Do this next:** ${graph.next_action ?? graph.current_task ?? memory.suggestedNextStep}`;

    await this.events.emit({
      founderId: founder.id,
      projectId: memory.project?.id,
      userId,
      type: FounderEventType.COPILOT_COMMAND,
      source: 'copilot',
      title: 'Continue mission state',
      payload: { intent: 'continue', goal: graph.active_goal },
    });

    return {
      answer,
      answerProvider: aiResult.ok ? 'FOUNDER_BRAIN' : 'RULE_BASED',
      routedAgent: aiResult.ok ? { template: brainTask, label: routeLabel } : undefined,
      founderBrain: { task: brainTask, label: routeLabel },
      stats: {
        commits: 0,
        deploys: 0,
        followers: memory.community.followers,
        featureRequests: memory.community.featureRequests,
        launchReadiness: memory.launchReadiness,
        buildStreak: memory.buildStreakDays,
      },
    };
  }

  /** Sprint 7d — dispatch builder from Mission State (current task / next action). */
  async runMissionBuild(userId: string, input?: { worker?: 'CURSOR' | 'OPENHANDS' }) {
    const memory = await this.getProjectMemory(userId);
    const graph = memory.memoryGraph ?? (await this.memoryGraph.resolveForUser(userId));
    const { spec, taskLabel } = resolveMissionBuildTask(graph);

    if (!taskLabel.trim()) {
      throw new BadRequestException('Set a goal, task, or next action in Mission State first');
    }

    if (!graph.current_task?.trim() && graph.next_action?.trim()) {
      await this.memoryGraph.patchForUser(userId, { current_task: graph.next_action.trim() });
    }

    if (memory.repoFullName) {
      try {
        await this.founderOs.syncGitHubCommits(userId);
      } catch {
        /* optional sync */
      }
    }

    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const githubPrefix = memory.repoFullName
      ? `Read ${FOUNDER_OS_MEMORY_DIR}/ in repo ${memory.repoFullName} first.\n\n`
      : '';

    const dispatch = await this.builder.executeBuildTask(userId, {
      spec,
      cursorPrompt: `${githubPrefix}${buildContinueFromMissionPrompt(graph)}`,
      repository: memory.repoFullName ?? undefined,
      worker: input?.worker,
    });

    await this.events.emit({
      founderId: founder.id,
      projectId: memory.project?.id,
      userId,
      type: FounderEventType.COPILOT_COMMAND,
      source: 'mission_build',
      title: 'Mission build',
      payload: {
        action: 'mission_build',
        task: taskLabel,
        worker: dispatch.worker,
        status: dispatch.status,
      },
    });

    const refreshedGraph = await this.memoryGraph.resolveForUser(userId);

    return {
      graph: refreshedGraph,
      taskLabel,
      spec,
      memory,
      worker: dispatch.worker,
      status: dispatch.status,
      agentUrl: dispatch.agentUrl ?? null,
      message: this.formatMissionBuildMessage(memory, dispatch, taskLabel),
      cursorCloudDispatch:
        dispatch.status === 'dispatched' && dispatch.worker === 'CURSOR'
          ? (dispatch.cursorCloud ?? null)
          : dispatch.status === 'error' && dispatch.worker === 'CURSOR'
            ? { error: dispatch.error ?? 'Dispatch failed' }
            : null,
      openHandsDispatch:
        dispatch.status === 'dispatched' && dispatch.worker === 'OPENHANDS'
          ? (dispatch.openHands ?? null)
          : dispatch.status === 'error' && dispatch.worker === 'OPENHANDS'
            ? { error: dispatch.error ?? 'Dispatch failed' }
            : null,
      agentId: dispatch.status === 'dispatched' && dispatch.worker === 'CURSOR' ? dispatch.agentId : null,
      runId: dispatch.status === 'dispatched' && dispatch.worker === 'CURSOR' ? dispatch.runId : null,
      conversationId:
        dispatch.status === 'dispatched' && dispatch.worker === 'OPENHANDS'
          ? dispatch.conversationId ?? null
          : null,
      mode: dispatch.status === 'dispatched' && dispatch.worker === 'CURSOR' ? dispatch.mode : null,
      queuedMessage: dispatch.status === 'queued' ? dispatch.message : null,
    };
  }

  private formatMissionBuildMessage(
    memory: Awaited<ReturnType<FounderCopilotService['getProjectMemory']>>,
    dispatch: Awaited<ReturnType<BuilderService['executeBuildTask']>>,
    taskLabel: string,
  ): string {
    if (dispatch.status === 'dispatched') {
      if (dispatch.worker === 'CURSOR') {
        return `Builder agent ${dispatch.mode === 'follow_up' ? 'resumed' : 'started'} on ${memory.repoFullName ?? 'your repo'} — ${taskLabel}`;
      }
      return `OpenHands dispatched — ${taskLabel}`;
    }
    if (dispatch.status === 'error') {
      return `Build could not start: ${dispatch.error ?? 'unknown error'}`;
    }
    return (
      dispatch.message ??
      'Connect Cursor or OpenHands in Settings → Builder to run this task remotely.'
    );
  }

  async resumeWork(userId: string) {
    const build = await this.runMissionBuild(userId);
    const graph = build.graph;

    let message = formatMissionStateBlock(graph, {
      lastCommit: build.memory.lastCommit,
      openTaskCount: build.memory.openTasks.length,
    });

    if (build.status === 'dispatched') {
      message = build.message;
    } else if (build.status === 'error') {
      message = `${message}\n\n${build.message}`;
    } else if (build.status === 'queued') {
      message = `${message}\n\n${build.message}`;
    }

    return {
      message,
      memory: build.memory,
      worker: build.worker,
      cursorCopy: build.memory.cursorCopy,
      cursorCloudDispatch: build.cursorCloudDispatch,
      openHandsDispatch: build.openHandsDispatch,
      dispatchHint: build.agentUrl
        ? `Remote agent running — ${build.agentUrl}`
        : 'Connect Cursor or OpenHands in Settings, then use Run build on Mission State.',
      missionBuild: {
        taskLabel: build.taskLabel,
        status: build.status,
        agentId: build.agentId,
        runId: build.runId,
        conversationId: build.conversationId,
      },
    };
  }

  private async dispatchCursorFromCopilot(userId: string, prompt: string) {
    const memory = await this.getProjectMemory(userId);
    const cursorCred = await this.prisma.integrationCredential.findFirst({
      where: { userId, provider: 'cursor', verifiedAt: { not: null } },
    });
    if (!cursorCred) {
      return {
        answer:
          'Connect Cursor in AI Stack first (Cursor Cloud API key), then say "command cursor: your task".',
        answerProvider: 'RULE_BASED',
        stats: {
          commits: 0,
          deploys: 0,
          followers: 0,
          featureRequests: 0,
          launchReadiness: memory.launchReadiness,
          buildStreak: memory.buildStreakDays,
        },
      };
    }

    const taskPrompt =
      prompt.replace(/^(command|run|dispatch|start|use)\s+cursor\s*[:\-]?\s*/i, '').trim() ||
      prompt.replace(/^cursor[:\s]+/i, '').trim() ||
      memory.currentGoal ||
      prompt;

    try {
      try {
        await this.founderOs.syncGitHubCommits(userId);
      } catch {
        /* still dispatch with live GitHub activity fetch */
      }

      await this.memoryGraph.patchForUser(userId, {
        current_task: taskPrompt.slice(0, 500),
        current_branch: memory.currentBranch ?? undefined,
      });

      const dispatch = await this.builder.dispatchCursorBuildTask(userId, {
        spec: taskPrompt,
        cursorPrompt: taskPrompt,
        repository: memory.repoFullName ?? undefined,
      });
      const founder = await this.prisma.founder.findUnique({ where: { userId } });
      await this.events.emit({
        founderId: founder!.id,
        projectId: memory.project?.id,
        userId,
        type: FounderEventType.CURSOR_BUILD_SESSION,
        source: 'copilot',
        title: `Cursor: ${taskPrompt.slice(0, 60)}`,
        payload: { agentUrl: dispatch.agentUrl, mode: dispatch.mode },
      });
      const repo = memory.repoFullName ?? undefined;
      return {
        answer: [
          `**Cursor** · ${dispatch.mode === 'follow_up' ? 'continuing on repo' : 'coding on your repo'}`,
          repo ? `Repository: \`${repo}\`` : '',
          '',
          `**Your task**`,
          taskPrompt.slice(0, 1200),
          '',
          `**Status:** ${dispatch.status}`,
          '',
          '_Live agent output streams in Founder Copilot chat — polling now._',
        ]
          .filter(Boolean)
          .join('\n'),
        answerProvider: 'CURSOR',
        runtime: {
          toolsUsed: ['cursor_agent'],
          githubIssuesCreated: 0,
          githubRepo: memory.repoFullName,
          cursorDispatched: true,
          cursorAgentUrl: dispatch.agentUrl,
          cursorAgentId: dispatch.agentId,
          cursorRunId: dispatch.runId,
          cursorMode: dispatch.mode,
        },
        stats: {
          commits: 0,
          deploys: 0,
          followers: memory.community.followers,
          featureRequests: memory.community.featureRequests,
          launchReadiness: memory.launchReadiness,
          buildStreak: memory.buildStreakDays,
        },
      };
    } catch (err) {
      return {
        answer: err instanceof Error ? err.message : 'Cursor dispatch failed',
        answerProvider: 'RULE_BASED',
        stats: {
          commits: 0,
          deploys: 0,
          followers: memory.community.followers,
          featureRequests: memory.community.featureRequests,
          launchReadiness: memory.launchReadiness,
          buildStreak: memory.buildStreakDays,
        },
      };
    }
  }

  async ask(userId: string, prompt: string, options?: { agentTemplate?: string | null }) {
    const text = prompt.trim();
    if (!text) throw new BadRequestException('Prompt required');

    if (detectAutopilotIntent(text)) {
      return this.autopilot.runAutopilot(userId, text);
    }

    if (detectCursorDispatchIntent(text)) {
      return this.dispatchCursorFromCopilot(userId, text);
    }

    const intent = detectWorkforceIntent(text, options?.agentTemplate);
    if (intent) {
      return this.askViaOrchestrator(userId, text, intent);
    }

    if (detectContinueMissionIntent(text)) {
      return this.askContinueFromMissionState(userId);
    }

    const brainTask = classifyFounderBrainTask(text);
    if (shouldDispatchBuilderForCodeAsk(text, brainTask)) {
      const cursorCred = await this.prisma.integrationCredential.findFirst({
        where: { userId, provider: 'cursor', verifiedAt: { not: null } },
      });
      const openHandsCred = await this.prisma.integrationCredential.findFirst({
        where: { userId, provider: 'openhands', verifiedAt: { not: null } },
      });
      if (cursorCred || openHandsCred) {
        return this.dispatchCursorFromCopilot(userId, text);
      }
    }

    const memory = await this.getProjectMemory(userId);

    if (memory.repoFullName) {
      try {
        await this.founderOs.syncGitHubCommits(userId);
      } catch {
        /* optional sync */
      }
    }

    const refreshedMemory = memory.repoFullName
      ? await this.getProjectMemory(userId)
      : memory;

    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: {
        projects: {
          where: { approved: true },
          take: 1,
          include: { roadmapItems: { orderBy: { sortOrder: 'asc' } } },
        },
        buildPosts: { orderBy: { publishedAt: 'desc' }, take: 5 },
      },
    });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const project = founder.projects[0];
    const weekAgo = new Date(Date.now() - 7 * 86400000);

    const [commitCount, deployCount, followerCount, featureRequests, recentCommits, openIdeas] =
      await Promise.all([
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
      refreshedMemory.repoFullName
        ? this.github.listCommits(userId, refreshedMemory.repoFullName, 8)
        : Promise.resolve([]),
      this.prisma.buildQueueItem.findMany({
        where: {
          founderId: founder.id,
          kind: 'IDEA',
          status: { notIn: [BuildQueueStatus.DISMISSED, BuildQueueStatus.DONE] },
        },
        orderBy: [{ sortOrder: 'asc' }, { createdAt: 'desc' }],
        take: 5,
      }),
    ]);

    const githubMemory = refreshedMemory.repoFullName
      ? await this.memory.readRepoMemory(userId, refreshedMemory.repoFullName)
      : null;

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

    const [deepCommits, pullRequests, deployEvents, workspaceActivity, memoryGraph] =
      await Promise.all([
        refreshedMemory.repoFullName
          ? this.github.listCommits(userId, refreshedMemory.repoFullName, 40)
          : Promise.resolve(recentCommits),
        refreshedMemory.repoFullName
          ? this.github.listPullRequests(userId, refreshedMemory.repoFullName)
          : Promise.resolve([]),
        this.prisma.founderEvent.findMany({
          where: {
            founderId: founder.id,
            type: { in: [FounderEventType.DEPLOY_SUCCESS, FounderEventType.DEPLOY_STARTED] },
            createdAt: { gte: weekAgo },
          },
          orderBy: { createdAt: 'desc' },
          take: 8,
        }),
        refreshedMemory.repoFullName
          ? this.builder.getWorkspaceActivity(userId, refreshedMemory.repoFullName)
          : Promise.resolve(null),
        this.memoryGraph.resolveForUser(userId),
      ]);

    const inProgressRoadmap = project?.roadmapItems.find((r) => r.status === RoadmapStatus.IN_PROGRESS);
    const vaultNote = this.buildVaultContextLines(refreshedMemory).join('\n') || null;

    const brainInput: FounderBrainContextInput = {
      projectName: project?.name ?? founder.name,
      projectDescription: githubMemory?.projectContext?.slice(0, 400) ?? null,
      repoFullName: refreshedMemory.repoFullName,
      currentGoal: refreshedMemory.currentGoal,
      progressPercent: refreshedMemory.progressPercent,
      launchReadiness: refreshedMemory.launchReadiness,
      suggestedNextStep: refreshedMemory.suggestedNextStep,
      openTasks: refreshedMemory.openTasks.map((t) => t.title),
      roadmapInProgress: inProgressRoadmap?.title ?? null,
      memoryGraph,
      commits: deepCommits.map((c) => ({ sha: c.sha, message: c.message, date: c.date })),
      pullRequests,
      recentDeploys: deployEvents.map((e) => ({
        title: e.title,
        at: e.createdAt.toISOString(),
      })),
      projectContextExcerpt: githubMemory?.projectContext ?? null,
      roadmapExcerpt: githubMemory?.roadmap ?? null,
      repoTasks: githubMemory?.openTasksFromRepo?.map((t) => t.title),
      weeklySummary: summary.body,
      workspaceActivityBlock: workspaceActivity
        ? formatWorkspaceActivityForPrompt(workspaceActivity)
        : null,
      vaultNote,
    };

    const intelligence = deriveMissionIntelligence(brainInput);
    const contextBlock = formatFounderBrainContextForPrompt(brainInput, intelligence);
    const memoryPrefix = this.memoryGraph.getPrefix(memoryGraph);

    const systemPrompt = `${memoryPrefix}You are Founder Brain — the command center for crypto founders. Use the assembled context below (commits, PRs, deployments, initiatives, mission graph). Summarize outcomes and initiatives, not raw task records. Structure answers: current initiative · what shipped · why it matters · blockers · next step. Never reply with only "define milestone" or task.json titles when GitHub context exists. Reply in plain markdown. API keys stay server-side — never ask users to paste secrets.`;

    const aiResult = await this.builder.tryCopilotChatCompletion(
      userId,
      systemPrompt,
      `${prompt}\n\n---\n${contextBlock}`,
      { founderBrainTask: brainTask },
    );

    const ruleBased = formatRuleBasedBrainAnswer(intelligence, brainInput, prompt);

    await this.events.emit({
      founderId: founder.id,
      projectId: project?.id,
      userId,
      type: FounderEventType.COPILOT_COMMAND,
      source: 'copilot',
      title: prompt.slice(0, 80),
      payload: { intent: 'ask' },
    });

    let answer = ruleBased;
    let answerProvider: string = 'RULE_BASED';
    let llmErrors: string[] | undefined;

    if (aiResult.ok) {
      answer = aiResult.text;
      answerProvider = 'FOUNDER_BRAIN';
    } else if (aiResult.llmErrors.length > 0) {
      llmErrors = aiResult.llmErrors;
      answer = ruleBased;
      answerProvider = 'RULE_BASED';
    }

    const routeLabel = getFounderBrainRouteLabel(brainTask);

    return {
      answer,
      answerProvider,
      llmErrors,
      summary,
      routedAgent: aiResult.ok
        ? { template: brainTask, label: routeLabel }
        : undefined,
      founderBrain: { task: brainTask, label: routeLabel },
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

  private async askViaOrchestrator(
    userId: string,
    prompt: string,
    intent: NonNullable<ReturnType<typeof detectWorkforceIntent>>,
  ) {
    const memory = await this.getProjectMemory(userId);
    const orchestrated = await this.buildQueue.runOrchestratedWorkforce(
      userId,
      intent.template,
      prompt,
    );

    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { where: { approved: true }, take: 1 } },
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
      : { score: memory.launchReadiness, previous: memory.launchReadiness };

    await this.events.emit({
      founderId: founder.id,
      projectId: project?.id,
      userId,
      type: FounderEventType.COPILOT_COMMAND,
      source: 'copilot',
      title: prompt.slice(0, 80),
      payload: { intent: 'orchestrate', template: intent.template, worker: intent.label },
    });

    const answer = formatOrchestratorCopilotAnswer(
      intent,
      orchestrated.output,
      orchestrated.answerProvider,
      orchestrated.runtime,
    );

    return {
      answer,
      answerProvider: orchestrated.answerProvider,
      routedAgent: { template: intent.template, label: intent.label },
      orchestrator: {
        title: orchestrated.output.title,
        tasks: orchestrated.output.tasks,
        taskCount: orchestrated.output.tasks.length,
      },
      runtime: orchestrated.runtime,
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

  private buildCopilotContextBlock(input: {
    memory: Awaited<ReturnType<FounderCopilotService['getProjectMemory']>>;
    githubMemory: Awaited<ReturnType<FounderOsMemoryService['readRepoMemory']>>;
    recentCommits: { sha: string; message: string; date: string }[];
    openIdeas: { title: string; description: string | null }[];
    project: { name: string; roadmapItems: { title: string; status: RoadmapStatus }[] } | undefined;
    summaryBody: string;
    workspaceActivity?: Awaited<ReturnType<BuilderService['getWorkspaceActivity']>> | null;
  }) {
    const lines: string[] = [
      `Project: ${input.memory.project?.name ?? 'Not linked'}`,
      `Repo: ${input.memory.repoFullName ?? 'none'}`,
      `Current goal: ${input.memory.currentGoal}`,
      `Progress: ${input.memory.progressPercent}% · Launch readiness ${input.memory.launchReadiness}%`,
      `Suggested next: ${input.memory.suggestedNextStep}`,
      `Open tasks: ${input.memory.openTasks.map((t) => t.title).join('; ') || 'none'}`,
      `Last commit: ${input.memory.lastCommit ?? 'none'}`,
    ];

    if (input.recentCommits.length > 0) {
      lines.push(
        'Recent commits:',
        ...input.recentCommits.slice(0, 6).map((c) => `- ${c.sha.slice(0, 7)} ${c.message}`),
      );
    }

    if (input.workspaceActivity?.repoFullName) {
      lines.push('', formatWorkspaceActivityForPrompt(input.workspaceActivity));
    }

    if (input.githubMemory?.projectContext) {
      lines.push('GitHub project-context.md (excerpt):', input.githubMemory.projectContext.slice(0, 1200));
    }
    if (input.githubMemory?.roadmap) {
      lines.push('GitHub roadmap.md (excerpt):', input.githubMemory.roadmap.slice(0, 800));
    }
    if (input.githubMemory?.openTasksFromRepo?.length) {
      lines.push(
        'Repo tasks.json open items:',
        input.githubMemory.openTasksFromRepo.map((t) => `- ${t.title}`).join('\n'),
      );
    }

    if (input.openIdeas.length > 0) {
      lines.push('Ideas queue:', ...input.openIdeas.map((i) => `- ${i.title}`));
    }

    const vaultLines = this.buildVaultContextLines(input.memory);
    if (vaultLines.length > 0) {
      lines.push(...vaultLines);
    }

    const inProgressRoadmap = input.project?.roadmapItems.find((r) => r.status === RoadmapStatus.IN_PROGRESS);
    if (inProgressRoadmap) {
      lines.push(`Roadmap in progress: ${inProgressRoadmap.title}`);
    }

    lines.push('', 'Weekly summary:', input.summaryBody);
    return lines.join('\n');
  }

  private buildVaultContextLines(
    memory: Awaited<ReturnType<FounderCopilotService['getProjectMemory']>>,
  ): string[] {
    const relay = memory.vaultRelay;
    if (!relay) return [];

    const lines: string[] = ['Founder Vault (privacy mode):'];

    if (relay.mode === 'FOUNDER_NODE') {
      lines.push(
        `- Storage: Founder Vault on founder machine (${relay.nodeLabel ?? 'Founder Node'})`,
        `- Node status: ${relay.nodeOnline ? 'online' : 'offline — open Founder Node to refresh vault context'}`,
        `- Vault health: ${relay.vaultHealthy ? 'healthy' : 'check local vault files'}`,
      );
    } else {
      lines.push('- Storage: local-first with encrypted cloud relay');
    }

    if (relay.lastSyncedAt) {
      lines.push(`- Last vault sync: ${relay.lastSyncedAt}`);
    }
    if (relay.deviceLabel) {
      lines.push(`- Sync device: ${relay.deviceLabel}`);
    }
    if (relay.tasksRemaining > 0) {
      lines.push(`- Open tasks in vault: ${relay.tasksRemaining} (full task bodies stay on founder device)`);
    }
    if (relay.hasEncryptedBlob) {
      lines.push(
        '- Encrypted vault blob relayed — server cannot read private notes, roadmap markdown, or task bodies',
      );
    } else if (relay.mode === 'FOUNDER_NODE') {
      lines.push('- No encrypted snapshot yet — pair Founder Node and wait for sync (~60s)');
    }

    lines.push(
      'When answering: prefer vault current goal and task count. Do not invent private doc contents.',
    );

    return lines;
  }

  private isPriorityCopilotPrompt(prompt: string) {
    return /pressing|priority|urgent|should i work|focus on|most important|what'?s going on/i.test(
      prompt,
    );
  }

  private buildRuleBasedCopilotAnswer(input: {
    memory: Awaited<ReturnType<FounderCopilotService['getProjectMemory']>>;
    githubMemory: Awaited<ReturnType<FounderOsMemoryService['readRepoMemory']>>;
    recentCommits: { sha: string; message: string }[];
    prompt: string;
  }) {
    if (this.isPriorityCopilotPrompt(input.prompt)) {
      const topTask =
        input.githubMemory?.openTasksFromRepo?.[0]?.title ??
        input.memory.openTasks[0]?.title ??
        input.memory.suggestedNextStep ??
        'Define your next MVP milestone in Tasks or GitHub tasks.json';
      return [
        `Most pressing issue: ${topTask}`,
        '',
        `Progress: ${input.memory.progressPercent}% · Launch readiness ${input.memory.launchReadiness}/100`,
        '',
        `Current goal: ${input.memory.currentGoal}`,
        '',
        input.memory.lastCommit
          ? `Latest commit: ${input.memory.lastCommit}`
          : 'GitHub: connect repo in Builder settings and sync commits to pull recent work.',
        '',
        `Recommended focus today: ${input.memory.suggestedNextStep}`,
        input.memory.repoFullName ? `\nRepo: ${input.memory.repoFullName}` : '',
      ]
        .filter(Boolean)
        .join('\n');
    }

    const wantsProjectContext =
      /what|working|tell me|listen|github|commit|task|goal|leave|resume|project|build|ship|mvp|status|progress|connected|explain|week|launch|investor|changed|remain|risk|timeline|brain|copilot|cursor|vercel|left off|was working|working at|working on/i.test(
        input.prompt,
      );

    if (wantsProjectContext || input.recentCommits.length > 0 || input.memory.openTasks.length > 0) {
      const commitLines =
        input.recentCommits.length > 0
          ? input.recentCommits
              .slice(0, 6)
              .map((c) => `• ${c.message}`)
              .join('\n')
          : '• No recent commits synced — connect GitHub in Builder settings and tap Sync commits.';

      const repoTasks =
        input.githubMemory?.openTasksFromRepo?.length
          ? input.githubMemory.openTasksFromRepo.map((t) => `• ${t.title}`).join('\n')
          : input.memory.vaultRelay?.tasksRemaining
            ? `• ${input.memory.vaultRelay.tasksRemaining} task(s) in Founder Vault (details on your machine)`
            : input.memory.openTasks.length > 0
              ? input.memory.openTasks.map((t) => `• ${t.title}`).join('\n')
              : '• No open tasks in queue — add one in Founder Copilot chat.';

      const vaultNote =
        input.memory.memoryStorageMode === 'FOUNDER_NODE'
          ? input.memory.vaultRelay?.nodeOnline
            ? `Founder Vault: synced from ${input.memory.vaultRelay.nodeLabel ?? 'Founder Node'}${input.memory.vaultRelay.hasEncryptedBlob ? ' · encrypted relay active' : ''}.`
            : 'Founder Vault: Founder Node offline — open your desktop node to refresh private memory.'
          : null;

      return [
        `You're building ${input.memory.project?.name ?? 'your project'} (${input.memory.progressPercent}% progress · launch readiness ${input.memory.launchReadiness}%).`,
        '',
        `Current goal: ${input.memory.currentGoal}`,
        vaultNote ? `\n${vaultNote}` : '',
        '',
        'Recent GitHub commits:',
        commitLines,
        '',
        'Open tasks:',
        repoTasks,
        '',
        `Pick up here: ${input.memory.suggestedNextStep}`,
        input.memory.repoFullName
          ? `\nRepo: ${input.memory.repoFullName}`
          : '\nConnect GitHub owner/repo in the stack panel to sync commits.',
        input.memory.lastCommit ? `\nLast commit: ${input.memory.lastCommit}` : '',
      ]
        .filter(Boolean)
        .join('\n');
    }

    if (input.githubMemory?.currentGoalFromRepo) {
      return `Goal from your repo: ${input.githubMemory.currentGoalFromRepo}\n\nNext: ${input.memory.suggestedNextStep}\n\n${input.memory.lastCommit ? `Last commit: ${input.memory.lastCommit}` : 'Sync GitHub to pull latest commits.'}`;
    }

    return [
      input.memory.currentGoal,
      '',
      input.memory.suggestedNextStep,
      input.memory.lastCommit ? `Last commit: ${input.memory.lastCommit}` : '',
    ]
      .filter(Boolean)
      .join('\n');
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
        const summaryPayload =
          'summary' in result && result.summary && typeof result.summary === 'object'
            ? (result.summary as { title: string; body: string; traderView: string })
            : {
                title: 'Community update',
                body: result.answer,
                traderView: 'Founder Copilot generated this update from project activity.',
              };
        const body = buildCommunityUpdateFromSummary(summaryPayload);
        return {
          action,
          answer: body,
          summary: 'summary' in result ? result.summary : undefined,
          stats: 'stats' in result ? result.stats : undefined,
        };
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
      case 'autopilot': {
        const result = await this.autopilot.runAutopilot(userId, text);
        return {
          action,
          answer: result.answer,
          answerProvider: result.answerProvider,
          autopilot: {
            steps: result.steps,
            published: result.published,
            builderDispatch: result.builderDispatch,
          },
        };
      }
      case 'resume_work': {
        const result = await this.resumeWork(userId);
        const cursorStarted =
          result.cursorCloudDispatch &&
          'agentUrl' in result.cursorCloudDispatch &&
          Boolean(result.cursorCloudDispatch.agentUrl);
        if (cursorStarted) {
          return {
            action,
            answer: result.message,
            memory: result.memory,
            cursorCopy: result.cursorCopy,
          };
        }
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
      case 'cursor_dispatch': {
        const result = await this.dispatchCursorFromCopilot(userId, text);
        return {
          action,
          answer: result.answer,
          stats: result.stats,
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

  private static readonly CODE_DRAFT_AGENTS = new Set(['CURSOR', 'OPENHANDS']);

  private mapSocialDraftProvider(providerKey?: string): AiProvider | undefined {
    const key = providerKey?.trim().toUpperCase();
    if (key === 'DEEPSEEK') return AiProvider.DEEPSEEK;
    if (key === 'OPENAI') return AiProvider.OPENAI;
    if (key === 'ANTHROPIC' || key === 'CLAUDE') return AiProvider.ANTHROPIC;
    if (key === 'GEMINI') return AiProvider.GEMINI;
    if (key === 'OPENROUTER') return AiProvider.OPENROUTER;
    if (key === 'PHALA') return AiProvider.PHALA;
    return undefined;
  }

  async draftSocialUpdate(
    userId: string,
    options?: {
      provider?: string;
      audience?: 'trader' | 'developer';
      achievement?: { title: string; detail: string; kind?: string };
    },
  ) {
    const providerKey = options?.provider?.trim().toUpperCase();
    const codeAgent =
      providerKey && FounderCopilotService.CODE_DRAFT_AGENTS.has(providerKey) ? providerKey : null;

    try {
      await this.founderOs.syncGitHubCommits(userId);
    } catch {
      /* continue with live GitHub fetch */
    }

    const memory = await this.getProjectMemory(userId);

    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: {
        user: { select: { name: true } },
        projects: {
          where: { approved: true },
          take: 1,
          include: { roadmapItems: { orderBy: { sortOrder: 'asc' } } },
        },
      },
    });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const project = founder.projects[0];
    const { displayName: projectDisplayName, ticker: projectTicker } = resolveProjectDisplayForSocial(
      project ? { name: project.name, ticker: project.ticker } : null,
    );

    const since24h = new Date(Date.now() - 24 * 60 * 60 * 1000);
    const since7d = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
    const audience = options?.audience === 'developer' ? 'developer' : 'trader';

    const workspaceActivity = memory.repoFullName
      ? await this.builder.getWorkspaceActivity(userId, memory.repoFullName)
      : null;

    const commits30Raw = memory.repoFullName
      ? await this.github.listCommitsOnRef(userId, memory.repoFullName, 30)
      : [];

    const commits24h =
      workspaceActivity?.commitsLast24h?.length
        ? workspaceActivity.commitsLast24h
        : memory.repoFullName
          ? filterCommitsSince(commits30Raw, 24 * 60 * 60 * 1000)
          : [];

    const pullRequests = memory.repoFullName
      ? (await this.github.listPullRequests(userId, memory.repoFullName)).slice(0, 10)
      : [];

    const projectId = project?.id;

    const [
      openIdeas,
      openTasks,
      infraSnapshot,
      paperPortfolio,
      doneTasks24h,
      deployEvents24h,
      platformRow,
      recentBuildPosts,
      communityComments,
      recentPaperTrades,
    ] = await Promise.all([
      this.prisma.buildQueueItem.findMany({
        where: {
          founderId: founder.id,
          kind: 'IDEA',
          status: { notIn: [BuildQueueStatus.DISMISSED, BuildQueueStatus.DONE] },
        },
        orderBy: [{ sortOrder: 'asc' }, { createdAt: 'desc' }],
        take: 5,
      }),
      this.prisma.buildQueueItem.findMany({
        where: {
          founderId: founder.id,
          kind: 'TASK',
          status: { notIn: [BuildQueueStatus.DISMISSED, BuildQueueStatus.DONE] },
        },
        orderBy: [{ sortOrder: 'asc' }, { createdAt: 'desc' }],
        take: 10,
        select: { title: true },
      }),
      this.autopilot.buildDraftInfrastructureSnapshot(userId).catch(() => null),
      this.prisma.paperPortfolio.findUnique({ where: { userId }, select: { cashBalance: true } }),
      this.prisma.buildQueueItem.findMany({
        where: {
          founderId: founder.id,
          status: BuildQueueStatus.DONE,
          updatedAt: { gte: since24h },
        },
        orderBy: { updatedAt: 'desc' },
        take: 8,
        select: { title: true },
      }),
      this.prisma.founderEvent.findMany({
        where: {
          founderId: founder.id,
          type: FounderEventType.DEPLOY_SUCCESS,
          createdAt: { gte: since24h },
        },
        orderBy: { createdAt: 'desc' },
        take: 5,
        select: { title: true, payload: true },
      }),
      this.prisma.platformSettings.findUnique({ where: { id: 'default' } }),
      this.prisma.founderUpdate.findMany({
        where: { founderId: founder.id },
        orderBy: { publishedAt: 'desc' },
        take: 3,
        select: { headline: true, summary: true },
      }),
      projectId
        ? this.prisma.communityComment.findMany({
            where: {
              thread: { projectId },
              createdAt: { gte: since7d },
            },
            orderBy: { createdAt: 'desc' },
            take: 8,
            select: {
              body: true,
              thread: { select: { channel: true, title: true } },
            },
          })
        : Promise.resolve([]),
      this.prisma.paperTrade.findMany({
        where: { userId, createdAt: { gte: since7d } },
        orderBy: { createdAt: 'desc' },
        take: 5,
        select: { side: true, totalUsd: true, project: { select: { ticker: true } } },
      }),
    ]);

    const platformClosing =
      platformRow?.globalShareFooter?.trim() || PLATFORM_X_SHARE_FOOTER;

    const githubMemory = memory.repoFullName
      ? await this.memory.readRepoMemory(userId, memory.repoFullName)
      : null;

    const contextBlock = this.buildCopilotContextBlock({
      memory,
      githubMemory,
      recentCommits: commits24h.length > 0 ? commits24h : [],
      openIdeas,
      project: project ?? undefined,
      summaryBody: formatCommitsLast24hForTraders(commits24h, projectDisplayName),
      workspaceActivity,
    });

    const lastCommit = commits24h[0] ?? null;

    const accountBlock = buildSocialDraftFounderAccountBlock({
      founderName: founder.name,
      projectName: projectDisplayName,
      journeyStage: founder.journeyStage,
      buildStreakDays: founder.buildStreakDays,
      reputationScore: founder.reputationScore,
      founderCredits: founder.founderCredits,
      paperCashUsd: paperPortfolio ? Number(paperPortfolio.cashBalance) : undefined,
      launchReadiness: memory.launchReadiness,
      progressPercent: memory.progressPercent,
      currentGoal: memory.currentGoal,
      userDisplayName: founder.user?.name ?? undefined,
    });

    const infraBlock = infraSnapshot
      ? formatAutopilotInfrastructureBlock({
          steps: infraSnapshot.steps,
          controlPlane: infraSnapshot.syncStatus.controlPlane,
          siteUrl: process.env.NEXT_PUBLIC_SITE_URL ?? 'https://doxxedcrypto.digital',
        })
      : 'Hybrid control plane: snapshot unavailable — mention GitHub + Stack connections only if commits support it.';

    const missingLinkHints = buildMissingLinkNarrativeHints({
      commits: commits24h,
      missingPlatforms: infraSnapshot?.syncStatus.controlPlane.missingForFullStack ?? [],
    });

    const connections = await this.builder.getBuildWorkerConnections(userId);
    if (codeAgent === 'CURSOR' && !connections.cursor) {
      throw new BadRequestException('Connect Cursor in AI Stack first');
    }
    if (codeAgent === 'OPENHANDS' && !connections.openHands) {
      throw new BadRequestException('Connect OpenHands in AI Stack first');
    }

    const forceProvider = this.mapSocialDraftProvider(providerKey);
    const forcedLlmLabel =
      forceProvider === AiProvider.DEEPSEEK
        ? 'DEEPSEEK'
        : forceProvider === AiProvider.OPENAI
          ? 'OPENAI'
          : forceProvider === AiProvider.ANTHROPIC
            ? 'ANTHROPIC'
            : forceProvider === AiProvider.GEMINI
              ? 'GEMINI'
              : forceProvider === AiProvider.OPENROUTER
                ? 'OPENROUTER'
                : forceProvider === AiProvider.PHALA
                  ? 'PHALA'
                  : undefined;

    const systemPrompt = buildSocialDraftSystemPrompt(codeAgent, {
      forcedLlm: forcedLlmLabel as
        | 'DEEPSEEK'
        | 'OPENAI'
        | 'ANTHROPIC'
        | 'GEMINI'
        | 'OPENROUTER'
        | 'PHALA'
        | undefined,
    });

    const completedTasks = [
      ...doneTasks24h.map((t) => t.title),
      ...deployEvents24h.map((e) => e.title),
    ];

    const projectDescription = [project?.description?.trim(), project?.summary?.trim()]
      .filter(Boolean)
      .join('\n\n');

    const roadmapLines =
      project?.roadmapItems.map(
        (r) => `${r.title} [${r.status.replace(/_/g, ' ')}]`,
      ) ?? [];

    const commitsLast30Block = formatCommitsByDay(commits30Raw, { maxDays: 14, maxPerDay: 4 });
    const commitsLast24hBlock = formatCommitsLast24hForTraders(commits24h, projectDisplayName);

    const contextLayer = formatFounderUpdateContextBlock({
      projectDisplayName,
      projectTicker,
      projectDescription: projectDescription || undefined,
      currentGoal: memory.currentGoal,
      suggestedNext: memory.suggestedNextStep,
      launchReadiness: memory.launchReadiness,
      progressPercent: memory.progressPercent,
      roadmapLines,
      commitsLast30: commitsLast30Block,
      commitsLast24h: commitsLast24hBlock,
      openTasks: openTasks.map((t) => t.title),
      recentDeployments: deployEvents24h.map((e) => e.title),
      founderNotes: recentBuildPosts.map(
        (p) => `${p.headline}: ${(p.summary ?? '').slice(0, 160)}`,
      ),
      communityActivity: communityComments.map(
        (c) =>
          `[${c.thread.channel}] ${c.thread.title}: ${c.body.slice(0, 100)}`,
      ),
      ddollarActivity: [
        paperPortfolio
          ? `Paper wallet balance: ${Number(paperPortfolio.cashBalance).toFixed(0)} DDollar`
          : null,
        ...recentPaperTrades.map(
          (t) =>
            `${t.side} $${t.project.ticker} · $${Number(t.totalUsd).toFixed(0)} paper`,
        ),
      ].filter((line): line is string => Boolean(line)),
      recentPullRequests: pullRequests.map(
        (pr) => `#${pr.number} [${pr.state}] ${pr.title}`,
      ),
      missionControlMemory: contextBlock,
      accountBlock,
      infraBlock,
      achievementSeed: options?.achievement,
    });

    const userPrompt = [
      `PROJECT DISPLAY NAME: ${projectDisplayName}`,
      projectTicker ? `TICKER: ${projectTicker}` : '',
      memory.repoFullName ? `GITHUB REPO: ${memory.repoFullName}` : 'GITHUB: not linked',
      codeAgent ? `Draft voice: ${codeAgent}` : '',
      forceProvider ? `LLM: ${forceProvider}` : '',
      `Audience emphasis: ${audience}`,
      '',
      contextLayer,
      '',
      '=== Last commit detail ===',
      formatLastCommitDetail(lastCommit),
      completedTasks.length > 0
        ? ['', '=== Tasks/deploys completed (24h) ===', ...completedTasks.map((t) => `- ${t}`)]
        : [],
      '',
      '=== Missing-link narrative ===',
      missingLinkHints,
      '',
      '=== PLATFORM CLOSING ===',
      platformClosing,
      '',
      'Explain what was actually shipped. Do not output commit-count headlines.',
    ]
      .flat()
      .filter(Boolean)
      .join('\n');

    const aiResult = forceProvider
      ? await this.builder.tryCopilotChatCompletion(userId, systemPrompt, userPrompt, {
          forceProvider,
        })
      : await this.builder.tryCopilotChatCompletion(userId, systemPrompt, userPrompt);

    const displayProvider =
      codeAgent ?? (forceProvider ? String(forceProvider) : aiResult.ok ? aiResult.provider : 'RULE_BASED');

    const mapParsedToResponse = (
      parsed: ReturnType<typeof parseFounderUpdateLlmResponse>,
      meta: { llmProvider: string; fallback?: boolean; llmErrors?: string[] },
    ) => {
      const feedBody = composeFounderUpdateFeedBody(parsed, platformClosing);
      const displayBody = pickFounderUpdateDisplayBody(parsed, audience);
      return {
        headline: parsed.headline,
        body: feedBody,
        displayBody,
        xHook: parsed.tweetVersion,
        whatShipped: parsed.whatShipped,
        whyItMatters: parsed.whyItMatters,
        whatUsersNotice: parsed.whatUsersNotice,
        whatsNext: parsed.whatsNext,
        developerSummary: parsed.developerSummary,
        traderSummary: parsed.traderSummary,
        tweetVersion: parsed.tweetVersion,
        feedVersion: parsed.feedVersion,
        impactLevel: parsed.impactLevel,
        launchReadinessDelta: parsed.launchReadinessDelta,
        audience,
        provider: displayProvider,
        llmProvider: meta.llmProvider,
        projectDisplayName,
        platformClosing,
        fallback: meta.fallback,
        llmErrors: meta.llmErrors,
      };
    };

    if (aiResult.ok) {
      return mapParsedToResponse(parseFounderUpdateLlmResponse(aiResult.text), {
        llmProvider: aiResult.provider,
      });
    }

    const legacy = buildFounderUpdateFallback({
      projectDisplayName,
      commits24h,
      currentGoal: memory.currentGoal,
      suggestedNext: memory.suggestedNextStep,
      launchReadiness: memory.launchReadiness,
      buildStreakDays: founder.buildStreakDays,
      completedTasks,
      platformClosing,
    });

    return mapParsedToResponse(founderUpdateFromLegacyFallback(legacy), {
      llmProvider: 'RULE_BASED',
      fallback: true,
      llmErrors: aiResult.llmErrors,
    });
  }

  async getDeviceMemorySync(userId: string) {
    const row = await this.prisma.projectMemoryDeviceSync.findUnique({ where: { userId } });
    if (!row) return { payload: null, updatedAt: null, deviceLabel: null };
    return {
      updatedAt: row.updatedAt.toISOString(),
      deviceLabel: row.deviceLabel,
      payload: row.payload as DeviceMemoryPayload,
    };
  }

  async saveDeviceMemorySync(
    userId: string,
    payload: DeviceMemoryPayload | DeviceMemoryMetadataPayload,
  ) {
    if (payload.version !== 1 || !payload.currentGoal?.trim()) {
      throw new BadRequestException('Invalid memory payload');
    }

    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    if (settings?.memoryStorageMode === 'LOCAL_DEVICE') {
      throw new BadRequestException(
        'Cloud relay is off in "This device only" mode. Enable Local + cloud sync to resume on other devices.',
      );
    }

    const relayPayload: DeviceMemoryPayload | DeviceMemoryMetadataPayload =
      settings?.memoryStorageMode === 'FOUNDER_NODE' || isMetadataOnlyPayload(payload)
        ? isMetadataOnlyPayload(payload)
          ? payload
          : stripDeviceMemoryToMetadata(payload as DeviceMemoryPayload)
        : (payload as DeviceMemoryPayload);

    const row = await this.prisma.projectMemoryDeviceSync.upsert({
      where: { userId },
      create: {
        userId,
        payload: relayPayload,
        deviceLabel: relayPayload.deviceLabel ?? null,
      },
      update: {
        payload: relayPayload,
        deviceLabel: relayPayload.deviceLabel ?? null,
      },
    });

    await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: { userId, currentGoalFocus: payload.currentGoal.trim() },
      update: { currentGoalFocus: payload.currentGoal.trim() },
    });

    return {
      success: true,
      updatedAt: row.updatedAt.toISOString(),
    };
  }
}
