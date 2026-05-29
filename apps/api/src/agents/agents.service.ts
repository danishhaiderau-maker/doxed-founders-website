import {
  BadRequestException,
  ForbiddenException,
  Injectable,
  NotFoundException,
  OnModuleInit,
} from '@nestjs/common';
import { AgentCategory, AgentWorkforceTemplate, Prisma } from '@prisma/client';
import {
  AGENT_RUN_CREDITS,
  WORKFORCE_TEMPLATES,
  agentRating,
  runWorkforceAgent,
  slugify,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { BuildQueueService } from '../build-queue/build-queue.service';

function serializeAgent(agent: Prisma.FounderAgentGetPayload<{
  include: {
    founder: { select: { id: true; slug: true; name: true } };
    project: { select: { id: true; slug: true; name: true } };
  };
}>) {
  return {
    id: agent.id,
    slug: agent.slug,
    name: agent.name,
    description: agent.description,
    category: agent.category,
    template: agent.template,
    isPublic: agent.isPublic,
    followerCount: agent.followerCount,
    usageCount: agent.usageCount,
    rating: agentRating(agent.ratingSum, agent.ratingCount),
    ratingCount: agent.ratingCount,
    revenueCredits: agent.revenueCredits,
    founder: agent.founder,
    project: agent.project,
    createdAt: agent.createdAt,
  };
}

@Injectable()
export class AgentsService implements OnModuleInit {
  constructor(
    private readonly prisma: PrismaService,
    private readonly buildQueue: BuildQueueService,
  ) {}

  async onModuleInit() {
    await this.ensureSeedAgents().catch(() => undefined);
  }

  private agentInclude() {
    return {
      founder: { select: { id: true, slug: true, name: true } },
      project: { select: { id: true, slug: true, name: true } },
    } as const;
  }

  async ensureSeedAgents() {
    const count = await this.prisma.founderAgent.count();
    if (count > 0) return;

    const founder = await this.prisma.founder.findFirst({
      include: { projects: { take: 1 } },
    });
    if (!founder?.userId) return;

    const project = founder.projects[0];
    const seeds: {
      name: string;
      description: string;
      category: AgentCategory;
      template: AgentWorkforceTemplate;
      slug: string;
    }[] = [
      {
        name: 'Audit Agent',
        slug: 'audit-agent',
        category: AgentCategory.AUDIT,
        template: AgentWorkforceTemplate.CUSTOM,
        description: 'Security and contract review checklist for crypto startups.',
      },
      {
        name: 'Research Agent',
        slug: 'research-agent',
        category: AgentCategory.RESEARCH,
        template: AgentWorkforceTemplate.RESEARCHER,
        description: 'Competitor scans and market briefs tied to your project.',
      },
      {
        name: 'Community Agent',
        slug: 'community-agent',
        category: AgentCategory.COMMUNITY,
        template: AgentWorkforceTemplate.COMMUNITY_MANAGER,
        description: 'FAQ drafts and project room announcements.',
      },
    ];

    for (const seed of seeds) {
      await this.prisma.founderAgent.create({
        data: {
          slug: seed.slug,
          name: seed.name,
          description: seed.description,
          category: seed.category,
          template: seed.template,
          founderId: founder.id,
          projectId: project?.id,
          creatorUserId: founder.userId,
          isPublic: true,
          usageCount: Math.floor(Math.random() * 40) + 5,
          followerCount: Math.floor(Math.random() * 80) + 10,
          ratingSum: 20,
          ratingCount: 5,
        },
      });
    }
  }

  async listPublic(category?: AgentCategory) {
    await this.ensureSeedAgents();
    const agents = await this.prisma.founderAgent.findMany({
      where: { isPublic: true, ...(category ? { category } : {}) },
      include: this.agentInclude(),
      orderBy: [{ usageCount: 'desc' }, { followerCount: 'desc' }],
      take: 50,
    });
    return {
      agents: agents.map(serializeAgent),
      templates: WORKFORCE_TEMPLATES,
      categories: Object.keys(AgentCategory),
    };
  }

  async getBySlug(slug: string, userId?: string) {
    const agent = await this.prisma.founderAgent.findUnique({
      where: { slug },
      include: {
        ...this.agentInclude(),
        installs: userId ? { where: { userId }, take: 1 } : false,
        followers: userId ? { where: { userId }, take: 1 } : false,
      },
    });
    if (!agent || (!agent.isPublic && agent.creatorUserId !== userId)) {
      throw new NotFoundException('Agent not found');
    }
    return {
      ...serializeAgent(agent),
      installed: Boolean(userId && agent.installs && agent.installs.length > 0),
      following: Boolean(userId && agent.followers && agent.followers.length > 0),
    };
  }

  async createAgent(userId: string, dto: {
    name: string;
    description?: string;
    category: AgentCategory;
    template?: AgentWorkforceTemplate;
    projectId?: string;
  }) {
    const founder = await this.prisma.founder.findUnique({
      where: { userId },
      include: { projects: { take: 1 } },
    });
    if (!founder) {
      throw new ForbiddenException('Activate your founder profile first');
    }

    const projectId = dto.projectId ?? founder.projects[0]?.id;
    let slug = slugify(dto.name);
    const existing = await this.prisma.founderAgent.findUnique({ where: { slug } });
    if (existing) slug = `${slug}-${Date.now().toString(36).slice(-4)}`;

    const agent = await this.prisma.founderAgent.create({
      data: {
        slug,
        name: dto.name.trim(),
        description: dto.description?.trim(),
        category: dto.category,
        template: dto.template ?? AgentWorkforceTemplate.CUSTOM,
        founderId: founder.id,
        projectId,
        creatorUserId: userId,
        isPublic: true,
      },
      include: this.agentInclude(),
    });

    return serializeAgent(agent);
  }

  async installAgent(userId: string, agentId: string) {
    const agent = await this.prisma.founderAgent.findUnique({ where: { id: agentId } });
    if (!agent?.isPublic) throw new NotFoundException('Agent not found');

    await this.prisma.agentInstall.upsert({
      where: { agentId_userId: { agentId, userId } },
      create: { agentId, userId },
      update: {},
    });

    await this.prisma.founderAgent.update({
      where: { id: agentId },
      data: { followerCount: { increment: 1 } },
    });

    return { ok: true };
  }

  async followAgent(userId: string, agentId: string) {
    const agent = await this.prisma.founderAgent.findUnique({ where: { id: agentId } });
    if (!agent?.isPublic) throw new NotFoundException('Agent not found');

    await this.prisma.agentFollow.upsert({
      where: { agentId_userId: { agentId, userId } },
      create: { agentId, userId },
      update: {},
    });

    await this.prisma.founderAgent.update({
      where: { id: agentId },
      data: { followerCount: { increment: 1 } },
    });

    return { ok: true };
  }

  async runAgent(userId: string, agentId: string, prompt: string) {
    const agent = await this.prisma.founderAgent.findUnique({
      where: { id: agentId },
      include: { project: true, founder: true },
    });
    if (!agent?.isPublic) throw new NotFoundException('Agent not found');

    const founder = await this.prisma.founder.findUnique({ where: { userId } });
    if (!founder) {
      throw new ForbiddenException('Founder profile required to run agents');
    }
    if (founder.founderCredits < AGENT_RUN_CREDITS) {
      throw new BadRequestException(`Need ${AGENT_RUN_CREDITS} Founder Credits`);
    }

    const output = runWorkforceAgent(
      agent.template,
      prompt,
      agent.project?.name ?? agent.founder.name,
    );

    const updated = await this.prisma.founder.update({
      where: { id: founder.id },
      data: { founderCredits: { decrement: AGENT_RUN_CREDITS } },
    });

    await this.prisma.founderCreditLedger.create({
      data: {
        userId,
        founderId: founder.id,
        projectId: agent.projectId,
        delta: -AGENT_RUN_CREDITS,
        balanceAfter: updated.founderCredits,
        reason: `AGENT_RUN:${agent.slug}`,
      },
    });

    const run = await this.prisma.agentRun.create({
      data: {
        agentId,
        userId,
        inputPrompt: prompt,
        output: output as unknown as Prisma.InputJsonValue,
        status: 'COMPLETED',
        creditsSpent: AGENT_RUN_CREDITS,
        completedAt: new Date(),
      },
    });

    await this.prisma.founderAgent.update({
      where: { id: agentId },
      data: {
        usageCount: { increment: 1 },
        revenueCredits: { increment: Math.floor(AGENT_RUN_CREDITS / 2) },
      },
    });

    await this.buildQueue.createFromAgentRun(userId, run.id, agent, prompt, output);

    return { runId: run.id, creditsSpent: AGENT_RUN_CREDITS, output };
  }

  async rateAgent(userId: string, agentId: string, rating: number) {
    if (rating < 1 || rating > 5) throw new BadRequestException('Rating 1–5');
    const ran = await this.prisma.agentRun.findFirst({ where: { agentId, userId } });
    if (!ran) throw new ForbiddenException('Run the agent before rating');

    await this.prisma.founderAgent.update({
      where: { id: agentId },
      data: { ratingSum: { increment: rating }, ratingCount: { increment: 1 } },
    });
    return { ok: true };
  }

  async myAgents(userId: string) {
    const created = await this.prisma.founderAgent.findMany({
      where: { creatorUserId: userId },
      include: this.agentInclude(),
      orderBy: { createdAt: 'desc' },
    });
    const installed = await this.prisma.agentInstall.findMany({
      where: { userId },
      include: { agent: { include: this.agentInclude() } },
      orderBy: { installedAt: 'desc' },
    });
    return {
      created: created.map(serializeAgent),
      installed: installed.map((i) => serializeAgent(i.agent)),
    };
  }
}
