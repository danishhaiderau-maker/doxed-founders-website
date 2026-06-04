import {
  ForbiddenException,
  Inject,
  Injectable,
  NotFoundException,
  forwardRef,
} from '@nestjs/common';
import {
  BuildQueueItemKind,
  BuildQueueStatus,
  FounderEventType,
  NotificationType,
  ScoutMarketStatus,
  SuggestedUpdateStatus,
} from '@prisma/client';
import {
  founderQueueToAttention,
  isBuilderRunFailureStatus,
  isBuilderRunSuccessStatus,
  sortAttentionItems,
  sortFounderQueue,
  type AttentionItem,
  type FounderQueueItem,
  planAgentBusHandoffs,
  agentBusHandoffFingerprint,
  type AgentBusHandoff,
  type WorkforceAgentOutput,
  isAgentRunActive,
} from '@dcf/utils';
import { FounderAgentRunService } from '../founder-agent-run/founder-agent-run.service';
import { PrismaService } from '../prisma/prisma.service';
import { GitHubApiService } from '../github/github-api.service';
import { BuilderService } from '../builder/builder.service';
import { BuildQueueService } from '../build-queue/build-queue.service';
import { FounderCopilotService } from './founder-copilot.service';
import { FounderOsService } from '../founder-os/founder-os.service';
import { FounderAutopilotService } from './founder-autopilot.service';
import { NotificationsService } from '../notifications/notifications.service';

export type AgentBusRunResult = {
  handoffs: AgentBusHandoff[];
  applied: number;
  handoffIds: string[];
  skipped?: number;
  contentDraftId?: string;
};

@Injectable()
export class FounderCommandCenterService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly github: GitHubApiService,
    private readonly builder: BuilderService,
    private readonly notifications: NotificationsService,
    @Inject(forwardRef(() => BuildQueueService))
    private readonly buildQueue: BuildQueueService,
    @Inject(forwardRef(() => FounderCopilotService))
    private readonly copilot: FounderCopilotService,
    @Inject(forwardRef(() => FounderOsService))
    private readonly founderOs: FounderOsService,
    @Inject(forwardRef(() => FounderAutopilotService))
    private readonly autopilot: FounderAutopilotService,
    private readonly agentRuns: FounderAgentRunService,
  ) {}

  async getActiveAgentRun(userId: string) {
    const run = await this.agentRuns.getActive(userId);
    return { run, active: isAgentRunActive(run) };
  }

  async getFounderQueue(userId: string) {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: {
        projects: { where: { approved: true }, take: 1 },
      },
    });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const memory = await this.copilot.getProjectMemory(userId);
    const items: FounderQueueItem[] = [];
    const repo = memory.repoFullName;

    if (repo) {
      const prs = await this.github.listPullRequests(userId, repo);
      for (const pr of prs.filter((p) => p.state === 'open').slice(0, 5)) {
        items.push({
          id: `pr-${pr.number}`,
          kind: 'REVIEW_PR',
          priority: 1,
          title: `Review PR #${pr.number}: ${pr.title.slice(0, 80)}`,
          detail: pr.url,
          action: 'merge_pr',
          targetId: String(pr.number),
          href: pr.url,
          prompt: `Review PR #${pr.number} and suggest next steps`,
        });
      }
    }

    const pendingUpdates = await this.prisma.suggestedBuildUpdate.findMany({
      where: { founderId: founder.id, status: SuggestedUpdateStatus.PENDING },
      orderBy: { createdAt: 'asc' },
      take: 5,
    });
    if (pendingUpdates.length > 1) {
      items.push({
        id: 'publish-all-pending',
        kind: 'PUBLISH_UPDATE',
        priority: 2,
        title: `Publish ${pendingUpdates.length} pending updates`,
        detail: 'Ship to feed, X, and community',
        action: 'publish',
        targetId: 'all',
        href: '/founder-den?tab=social',
      });
    }
    for (const u of pendingUpdates) {
      items.push({
        id: `pub-${u.id}`,
        kind: 'PUBLISH_UPDATE',
        priority: 2,
        title: `Publish: ${u.headline.slice(0, 72)}`,
        detail: 'Ship to feed, X, and community',
        action: 'publish',
        targetId: u.id,
        href: '/founder-den?tab=social',
      });
    }

    const graph = memory.memoryGraph;
    if (graph?.next_action?.trim() && memory.repoFullName) {
      items.push({
        id: 'run-build-mission',
        kind: 'RUN_BUILD',
        priority: graph.blocked_by ? 1 : 3,
        title: `Run build: ${(graph.current_task ?? graph.next_action).slice(0, 64)}`,
        detail: graph.blocked_by ? `Blocked: ${graph.blocked_by.slice(0, 100)}` : undefined,
        action: 'dispatch_build',
        prompt: graph.next_action,
      });
    }

    const openTasks = await this.prisma.buildQueueItem.findMany({
      where: {
        founderId: founder.id,
        kind: BuildQueueItemKind.TASK,
        status: { notIn: [BuildQueueStatus.DISMISSED, BuildQueueStatus.DONE] },
      },
      orderBy: [{ sortOrder: 'asc' }, { createdAt: 'desc' }],
      take: 3,
    });
    for (const t of openTasks) {
      items.push({
        id: `task-${t.id}`,
        kind: 'RUN_BUILD',
        priority: 4,
        title: t.title.slice(0, 80),
        detail: 'From build queue',
        action: 'dispatch_build',
        targetId: t.id,
        prompt: t.spec ?? t.title,
      });
    }

    if (!repo) {
      items.push({
        id: 'connect-github',
        kind: 'CONNECT_STACK',
        priority: 1,
        title: 'Connect GitHub repository',
        detail: 'Required for commits, PRs, and Builder Agent',
        action: 'settings',
        href: '/settings/builder',
      });
    } else {
      const worker = await this.builder.getWorkerStatus(userId);
      if (!worker.llmConnected) {
        items.push({
          id: 'connect-llm',
          kind: 'CONNECT_STACK',
          priority: 2,
          title: 'Connect chat AI (DeepSeek, OpenAI, or Claude)',
          detail: 'Founder Brain needs an LLM for tailored answers',
          action: 'settings',
          href: '/settings/builder',
        });
      }
    }

    const project = founder.projects[0];
    if (project) {
      const expiringScout = await this.prisma.scoutMarket.findFirst({
        where: {
          projectId: project.id,
          status: ScoutMarketStatus.OPEN,
          resolvesAt: { lte: new Date(Date.now() + 48 * 3600000) },
        },
        orderBy: { resolvesAt: 'asc' },
      });
      if (expiringScout) {
        items.push({
          id: `scout-${expiringScout.id}`,
          kind: 'SCOUT_ACTION',
          priority: 2,
          title: `Scout market closing soon: ${expiringScout.question.slice(0, 60)}`,
          action: 'open_url',
          href: `/predict`,
          prompt: `Review scout market: ${expiringScout.question}`,
        });
      }
    }

    const weekAgo = new Date(Date.now() - 7 * 86400000);
    const failedDeploy = await this.prisma.founderEvent.findFirst({
      where: {
        founderId: founder.id,
        type: FounderEventType.DEPLOY_STARTED,
        createdAt: { gte: weekAgo },
      },
      orderBy: { createdAt: 'desc' },
    });
    if (failedDeploy && !memory.deployments.some((d) => d.healthy)) {
      items.push({
        id: 'deploy-check',
        kind: 'DEPLOY_CHECK',
        priority: 1,
        title: 'Run platform autopilot sync',
        detail: failedDeploy.title,
        action: 'sync',
        prompt: 'Run platform autopilot sync and verify Vercel/Railway',
      });
    }

    const sorted = sortFounderQueue(items);
    return {
      items: sorted,
      count: sorted.length,
      missionIntelligence: await this.copilot.computeMissionIntelligenceForUser(userId).catch(
        () => null,
      ),
    };
  }

  async getAttentionCenter(userId: string) {
    const queue = await this.getFounderQueue(userId);
    const attention: AttentionItem[] = queue.items.map(founderQueueToAttention);
    const sorted = sortAttentionItems(attention);
    return {
      items: sorted,
      count: sorted.length,
      urgentCount: sorted.filter((a) => a.severity === 'urgent').length,
    };
  }

  /** Agent Bus v1 — plan + apply handoffs (P1 stage 2). */
  async runAgentBus(
    userId: string,
    input: {
      kind: 'RESEARCH_COMPLETED' | 'BUILD_COMPLETED' | 'BUILD_FAILED';
      title: string;
      detail: string;
      sourceTask?: string;
      buildStatus?: string;
      prUrl?: string | null;
      result?: string | null;
    },
  ): Promise<AgentBusRunResult | null> {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { where: { approved: true }, take: 1 } },
    });
    if (!founder) return null;

    const handoffs = planAgentBusHandoffs({
      kind: input.kind,
      founderId: founder.id,
      projectId: founder.projects[0]?.id,
      title: input.title,
      detail: input.detail,
      sourceTask: input.sourceTask,
      researchSummary: input.kind === 'RESEARCH_COMPLETED' ? input.detail : undefined,
      buildOutput:
        input.kind !== 'RESEARCH_COMPLETED'
          ? { status: input.buildStatus ?? 'UNKNOWN', prUrl: input.prUrl, result: input.result }
          : undefined,
    });

    if (handoffs.length === 0) return { handoffs: [], applied: 0, handoffIds: [] };

    return this.applyAgentBusHandoffs(userId, handoffs, founder.id, founder.projects[0]?.id);
  }

  async onWorkforceComplete(
    userId: string,
    template: string,
    prompt: string,
    output: WorkforceAgentOutput,
  ) {
    if (template !== 'RESEARCHER') return null;
    return this.runAgentBus(userId, {
      kind: 'RESEARCH_COMPLETED',
      title: output.title,
      detail: output.summary,
      sourceTask: prompt,
    });
  }

  async onBuildFinished(
    userId: string,
    body: {
      task: string;
      status: string;
      result?: string | null;
      branch?: string | null;
      prUrl?: string | null;
    },
  ) {
    if (!isBuilderRunFailureStatus(body.status) && !isBuilderRunSuccessStatus(body.status)) {
      return null;
    }
    return this.runAgentBus(userId, {
      kind: isBuilderRunFailureStatus(body.status) ? 'BUILD_FAILED' : 'BUILD_COMPLETED',
      title: body.task,
      detail: body.result?.trim() || body.status,
      sourceTask: body.task,
      buildStatus: body.status,
      prUrl: body.prUrl,
      result: body.result,
    });
  }

  async previewAgentBusHandoffs(
    userId: string,
    input: {
      kind: 'RESEARCH_COMPLETED' | 'BUILD_COMPLETED' | 'BUILD_FAILED';
      title: string;
      detail: string;
      sourceTask?: string;
      buildStatus?: string;
      prUrl?: string | null;
      result?: string | null;
    },
  ) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const handoffs = planAgentBusHandoffs({
      kind: input.kind,
      founderId: founder.id,
      title: input.title,
      detail: input.detail,
      sourceTask: input.sourceTask,
      researchSummary: input.kind === 'RESEARCH_COMPLETED' ? input.detail : undefined,
      buildOutput:
        input.kind !== 'RESEARCH_COMPLETED'
          ? { status: input.buildStatus ?? 'UNKNOWN', prUrl: input.prUrl, result: input.result }
          : undefined,
    });

    return { handoffs, count: handoffs.length };
  }

  async applyAgentBusHandoffs(
    userId: string,
    handoffs: AgentBusHandoff[],
    founderId?: string,
    projectId?: string | null,
  ): Promise<AgentBusRunResult> {
    const founder =
      founderId != null
        ? { id: founderId }
        : await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const project =
      projectId !== undefined
        ? projectId
        : (
            await this.prisma.founder.findUnique({
              where: { userId },
              include: { projects: { where: { approved: true }, take: 1 } },
            })
          )?.projects[0]?.id;

    const applied: string[] = [];
    const skipped: string[] = [];
    let contentDraftId: string | undefined;

    for (const h of handoffs) {
      if (await this.shouldSkipBusHandoff(founder.id, h)) {
        skipped.push(h.id);
        continue;
      }

      if (h.to === 'builder' && h.payload.spec) {
        await this.buildQueue.quickBuild(userId, {
          prompt: h.payload.spec ?? `${h.title}. ${h.detail}`.slice(0, 1200),
        });
        applied.push(h.id);
        await this.notifications.notifyUser(userId, {
          type: NotificationType.BUILD_QUEUE,
          title: 'Build queued from research',
          body: h.title.slice(0, 120),
          link: '/founder-den?tab=build',
        });
      }

      if (h.to === 'content') {
        const headline = h.title.slice(0, 120);
        const trader = (h.payload.prompt ?? h.detail).slice(0, 500);
        const body = `${h.detail}\n\n${h.payload.prompt ?? ''}`.trim().slice(0, 4000);
        const draft = await this.prisma.suggestedBuildUpdate.create({
          data: {
            founderId: founder.id,
            projectId: project ?? undefined,
            headline,
            body: body || headline,
            devSummary: h.detail.slice(0, 2000),
            traderSummary: trader || headline,
            source: 'agent_bus',
          },
        });
        contentDraftId = draft.id;
        applied.push(h.id);
        await this.notifications.notifyUser(userId, {
          type: NotificationType.BUILD_QUEUE,
          title: 'Draft update ready',
          body: headline,
          link: '/founder-den?tab=social',
        });
      }

      if (h.to === 'founder_queue') {
        applied.push(h.id);
        await this.notifications.notifyUser(userId, {
          type: NotificationType.AGENT_RESULT,
          title: h.title.slice(0, 80),
          body: h.detail.slice(0, 180),
          link: '/founder-den?tab=activity',
        });
      }
    }

    return {
      handoffs,
      applied: applied.length,
      handoffIds: applied,
      skipped: skipped.length,
      contentDraftId,
    };
  }

  private async shouldSkipBusHandoff(founderId: string, h: AgentBusHandoff): Promise<boolean> {
    const fp = agentBusHandoffFingerprint(h);
    const dayAgo = new Date(Date.now() - 24 * 60 * 60 * 1000);

    if (h.to === 'builder' && h.payload.spec) {
      const recent = await this.prisma.buildQueueItem.findFirst({
        where: {
          founderId,
          kind: BuildQueueItemKind.IDEA,
          createdAt: { gte: dayAgo },
          title: { contains: h.title.slice(0, 40), mode: 'insensitive' },
        },
      });
      if (recent) return true;
    }

    if (h.to === 'content') {
      const recent = await this.prisma.suggestedBuildUpdate.findFirst({
        where: {
          founderId,
          source: 'agent_bus',
          createdAt: { gte: dayAgo },
          headline: h.title.slice(0, 120),
        },
      });
      if (recent) return true;
    }

    void fp;
    return false;
  }

  /** Control action from Founder Queue row (P1 stage 2). */
  async executeQueueAction(userId: string, itemId: string) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    if (itemId === 'publish-all-pending' || itemId.startsWith('pub-')) {
      const targetId = itemId === 'publish-all-pending' ? 'all' : itemId.slice(4);
      const pending = await this.prisma.suggestedBuildUpdate.findMany({
        where: {
          founderId: founder.id,
          status: SuggestedUpdateStatus.PENDING,
          ...(targetId !== 'all' ? { id: targetId } : {}),
        },
        orderBy: { createdAt: 'asc' },
      });
      if (pending.length === 0) throw new NotFoundException('No pending updates');

      const published: string[] = [];
      const errors: string[] = [];
      for (const s of pending) {
        try {
          await this.founderOs.publishSuggestedUpdate(userId, s.id, {
            buildFeed: true,
            x: true,
            community: true,
          });
          published.push(s.id);
        } catch (err) {
          errors.push(err instanceof Error ? err.message : 'Publish failed');
        }
      }

      return {
        action: 'publish' as const,
        published: published.length,
        errors: errors.length > 0 ? errors : undefined,
        message:
          published.length > 0
            ? `Published ${published.length} update(s) to feed, X, and community.`
            : 'Publish failed — check Social Hub connections.',
      };
    }

    if (itemId === 'run-build-mission') {
      const result = await this.copilot.runMissionBuild(userId);
      return {
        action: 'dispatch_build' as const,
        message: result.message ?? 'Mission build dispatched.',
        worker: result.worker,
        status: result.status,
        agentUrl: result.agentUrl,
        agentId: result.agentId,
        runId: result.runId,
        conversationId: result.conversationId,
      };
    }

    if (itemId.startsWith('task-')) {
      const taskId = itemId.slice(5);
      const task = await this.prisma.buildQueueItem.findFirst({
        where: { id: taskId, founderId: founder.id },
      });
      if (!task) throw new NotFoundException('Task not found');

      const memory = await this.copilot.getProjectMemory(userId);
      const dispatch = await this.builder.executeBuildTask(userId, {
        spec: task.spec ?? task.title,
        cursorPrompt: task.cursorPrompt ?? task.spec ?? task.title,
        repository: memory.repoFullName ?? undefined,
      });
      if (dispatch.status === 'dispatched' && dispatch.worker === 'CURSOR' && dispatch.agentId && dispatch.runId) {
        await this.agentRuns.start(userId, {
          worker: 'CURSOR',
          status: 'CREATING',
          task: task.spec ?? task.title,
          repository: memory.repoFullName,
          agentId: dispatch.agentId,
          runId: dispatch.runId,
        });
      } else if (
        dispatch.status === 'dispatched' &&
        dispatch.worker === 'OPENHANDS' &&
        dispatch.conversationId
      ) {
        await this.agentRuns.start(userId, {
          worker: 'OPENHANDS',
          status: 'WORKING',
          task: task.spec ?? task.title,
          repository: memory.repoFullName,
          conversationId: dispatch.conversationId,
        });
      }

      return {
        action: 'dispatch_build' as const,
        message:
          dispatch.status === 'dispatched'
            ? `Builder started: ${task.title.slice(0, 60)}`
            : dispatch.status === 'error'
              ? dispatch.error ?? 'Builder dispatch failed'
              : 'Connect Cursor or OpenHands in Settings → Builder',
        dispatch,
      };
    }

    if (itemId.startsWith('pr-')) {
      const prNumber = Number(itemId.slice(3));
      if (!Number.isFinite(prNumber)) throw new NotFoundException('Invalid PR item');
      const memory = await this.copilot.getProjectMemory(userId);
      const repo = memory.repoFullName;
      if (!repo) throw new ForbiddenException('Connect a GitHub repository first');
      const result = await this.github.mergePullRequest(userId, repo, prNumber);
      return {
        action: 'merge_pr' as const,
        message: result.message,
        merged: result.merged,
        prNumber,
      };
    }

    if (itemId === 'deploy-check') {
      const result = await this.autopilot.runAutopilot(
        userId,
        'Take full control — sync everything on Vercel, Railway, Neon, and GitHub',
      );
      return {
        action: 'sync' as const,
        message: result.answer,
        steps: result.steps,
      };
    }

    throw new NotFoundException(`Unknown queue item: ${itemId}`);
  }
}
