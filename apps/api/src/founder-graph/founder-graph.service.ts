import { Injectable } from '@nestjs/common';
import { FounderEventType, Prisma } from '@prisma/client';
import {
  FOUNDER_DECISION_LOG_KEY,
  buildFounderGraph,
  formatFounderGraphForPrompt,
  getFounderGraphMiniChain,
  parseFounderDecisionLog,
  parseFounderGraph,
  parseFounderMemoryGraph,
  type FounderGraph,
  type FounderGraphBuildInput,
  normalizePlatformConnections,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { FounderAgentRunService } from '../founder-agent-run/founder-agent-run.service';
import { GitHubApiService } from '../github/github-api.service';

@Injectable()
export class FounderGraphService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly github: GitHubApiService,
    private readonly agentRuns: FounderAgentRunService,
  ) {}

  async getStored(userId: string): Promise<FounderGraph | null> {
    const settings = await this.prisma.founderBuilderSettings.findUnique({
      where: { userId },
      select: { founderGraph: true },
    });
    return parseFounderGraph(settings?.founderGraph);
  }

  async save(userId: string, graph: FounderGraph) {
    await this.prisma.founderBuilderSettings.upsert({
      where: { userId },
      create: { userId, founderGraph: graph as unknown as Prisma.InputJsonValue },
      update: { founderGraph: graph as unknown as Prisma.InputJsonValue },
    });
    return graph;
  }

  async rebuildForUser(userId: string, opts?: { currentInitiative?: string | null }): Promise<FounderGraph> {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: {
        projects: { where: { approved: true }, take: 1 },
        buildPosts: { orderBy: { publishedAt: 'desc' }, take: 5 },
      },
    });
    if (!founder) {
      const empty = buildFounderGraph({
        projectName: 'Founder project',
        memoryGraph: null,
        commits: [],
        pullRequests: [],
        recentDeploys: [],
        founderUpdates: [],
        decisions: [],
      });
      return this.save(userId, empty);
    }

    const settings = await this.prisma.founderBuilderSettings.findUnique({ where: { userId } });
    const memoryGraph = parseFounderMemoryGraph(settings?.memoryGraph);
    const platformConnections = normalizePlatformConnections(settings?.platformConnections);
    const decisionRaw =
      settings?.memoryGraph &&
      typeof settings.memoryGraph === 'object' &&
      !Array.isArray(settings.memoryGraph)
        ? (settings.memoryGraph as Record<string, unknown>)[FOUNDER_DECISION_LOG_KEY]
        : null;
    const decisions = parseFounderDecisionLog(decisionRaw);

    const gh = await this.prisma.gitHubConnection.findUnique({ where: { userId } });
    const repo =
      gh?.repoFullName && !gh.repoFullName.endsWith('/pending-setup')
        ? gh.repoFullName
        : founder.projects[0]?.githubRepoFullName ?? null;

    const weekAgo = new Date(Date.now() - 14 * 86400000);
    const [commits, pullRequests, deployEvents, agentRun] = await Promise.all([
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
      this.agentRuns.getActive(userId),
    ]);

    const input: FounderGraphBuildInput = {
      projectName: founder.projects[0]?.name ?? founder.name,
      memoryGraph,
      currentInitiative: opts?.currentInitiative ?? memoryGraph?.current_sprint ?? null,
      commits: commits.map((c) => ({ sha: c.sha, message: c.message, date: c.date })),
      pullRequests,
      recentDeploys: deployEvents.map((e) => ({
        title: e.title,
        at: e.createdAt.toISOString(),
        source: typeof e.source === 'string' ? e.source : 'deploy',
      })),
      founderUpdates: founder.buildPosts.map((p) => ({
        id: p.id,
        headline: p.headline,
        at: p.publishedAt.toISOString(),
      })),
      decisions,
      agentRun: agentRun
        ? {
            task: agentRun.task,
            status: agentRun.status,
            prUrl: agentRun.prUrl,
            startedAt: agentRun.startedAt,
          }
        : null,
      platformConnections,
    };

    const graph = buildFounderGraph(input);
    return this.save(userId, graph);
  }

  formatForBrain(graph: FounderGraph | null): string | null {
    return formatFounderGraphForPrompt(graph);
  }

  miniChain(graph: FounderGraph | null) {
    return getFounderGraphMiniChain(graph);
  }
}
