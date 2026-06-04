import { ForbiddenException, Injectable } from '@nestjs/common';
import {
  BuildQueueItemKind,
  BuildQueueStatus,
  FounderEventType,
  ScoutMarketStatus,
  SuggestedUpdateStatus,
} from '@prisma/client';
import {
  founderQueueToAttention,
  sortAttentionItems,
  sortFounderQueue,
  type AttentionItem,
  type FounderQueueItem,
  planAgentBusHandoffs,
  type AgentBusHandoff,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { GitHubApiService } from '../github/github-api.service';
import { BuilderService } from '../builder/builder.service';
import { BuildQueueService } from '../build-queue/build-queue.service';
import { FounderCopilotService } from './founder-copilot.service';

@Injectable()
export class FounderCommandCenterService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly github: GitHubApiService,
    private readonly builder: BuilderService,
    private readonly buildQueue: BuildQueueService,
    private readonly copilot: FounderCopilotService,
  ) {}

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
          action: 'open_url',
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
    for (const u of pendingUpdates) {
      items.push({
        id: `pub-${u.id}`,
        kind: 'PUBLISH_UPDATE',
        priority: 2,
        title: `Publish: ${u.headline.slice(0, 72)}`,
        detail: 'Ship to feed, X, and community',
        action: 'publish',
        href: '/founder-den?tab=social',
        prompt: 'Publish all pending build updates',
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
        title: 'Check deployment status',
        detail: failedDeploy.title,
        action: 'settings',
        href: '/settings/builder',
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

  /** Preview handoffs from a completed workforce/build event (v1 — execution in follow-up). */
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

  /** Apply v1 bus handoffs — enqueue build ideas / queue items. */
  async applyAgentBusHandoffs(userId: string, handoffs: AgentBusHandoff[]) {
    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) throw new ForbiddenException('Founder profile required');

    const applied: string[] = [];
    for (const h of handoffs) {
      if (h.to === 'builder' && h.payload.spec) {
        await this.buildQueue.quickBuild(userId, {
          prompt: h.payload.spec ?? `${h.title}. ${h.detail}`.slice(0, 1200),
        });
        applied.push(h.id);
      }
    }
    return { applied: applied.length, handoffIds: applied };
  }
}
