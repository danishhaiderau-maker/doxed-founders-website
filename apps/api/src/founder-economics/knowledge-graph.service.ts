/**
 * Knowledge Graph Service — tracks knowledge contributions + reuse + lineage.
 *
 * Connects to the existing Founder Memory Graph (founder-memory-graph.ts in
 * utils) for input: a Founder Memory node that another founder reuses becomes
 * a KnowledgeNode with `parentNodeId` set, and the parent's contributor earns
 * a KNOWLEDGE_REUSED_IMPACT DDollar grant (compounding per hop).
 */

import { Injectable, Logger, NotFoundException } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { DdollarEngineService } from './ddollar-engine.service';
import {
  KNOWLEDGE_REUSE_GRANT_MAX,
  KNOWLEDGE_REUSE_GRANT_MIN,
  computeReuseDdollarGrant,
  type KnowledgeType,
} from '@dcf/utils';

const VALID_TYPES: KnowledgeType[] = [
  'PLAYBOOK',
  'RESEARCH_NOTE',
  'PATTERN',
  'FOUNDER_MEMORY_NODE',
  'POST_MORTEM',
];

function assertKnowledgeType(value: string): asserts value is KnowledgeType {
  if (!VALID_TYPES.includes(value as KnowledgeType)) {
    throw new Error(`Invalid knowledge type: ${value}`);
  }
}

@Injectable()
export class KnowledgeGraphService {
  private readonly logger = new Logger(KnowledgeGraphService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly ddollarEngine: DdollarEngineService,
  ) {}

  /** Contribute a new knowledge node (no parent → original contribution). */
  async contribute(
    founderId: string,
    knowledgeType: KnowledgeType,
    content: string,
    parentNodeId?: string,
  ): Promise<{ id: string; impactDdollar: number }> {
    assertKnowledgeType(knowledgeType);
    if (!content.trim()) {
      throw new Error('Knowledge content cannot be empty.');
    }
    if (parentNodeId) {
      const parent = await this.prisma.knowledgeNode.findUnique({
        where: { id: parentNodeId },
      });
      if (!parent) throw new NotFoundException('Parent knowledge node not found.');
    }

    const node = await this.prisma.knowledgeNode.create({
      data: { founderId, knowledgeType, content, parentNodeId: parentNodeId ?? null },
    });

    // Original contribution earns KNOWLEDGE_CONTRIBUTION DDollar.
    const baseAmount = KNOWLEDGE_REUSE_GRANT_MIN; // 100 floor per spec min
    const { amount: impactDdollar } = await this.ddollarEngine.grant(
      founderId,
      'KNOWLEDGE_CONTRIBUTION',
      node.id,
      baseAmount,
      { label: `Knowledge contributed: ${knowledgeType}` },
    );

    // If this is a reuse, grant KNOWLEDGE_REUSED_IMPACT to the parent's contributor.
    if (parentNodeId) {
      await this.grantReuseImpact(parentNodeId, 1);
    }

    return { id: node.id, impactDdollar };
  }

  /**
   * Grant KNOWLEDGE_REUSED_IMPACT to the parent's contributor, walking up
   * the lineage so ancestors get a decaying grant (compounding).
   */
  async grantReuseImpact(nodeId: string, hop: number): Promise<void> {
    if (hop < 1) return;
    const node = await this.prisma.knowledgeNode.findUnique({
      where: { id: nodeId },
    });
    if (!node) return;

    const baseGrant = KNOWLEDGE_REUSE_GRANT_MAX; // top of spec range for direct reuse
    const grant = computeReuseDdollarGrant(hop, baseGrant);
    if (grant <= 0) return;

    await this.ddollarEngine.grant(
      node.founderId,
      'KNOWLEDGE_REUSED_IMPACT',
      `reuse-${node.id}-hop${hop}-${Date.now()}`,
      grant,
      { label: `Knowledge reused (hop ${hop})` },
    );

    // Bump impact score on the parent.
    await this.prisma.knowledgeNode.update({
      where: { id: nodeId },
      data: { impactScore: { increment: 1 } },
    });

    // Recurse up the lineage.
    if (node.parentNodeId) {
      await this.grantReuseImpact(node.parentNodeId, hop + 1);
    }
  }

  /** Return recent knowledge nodes for the dashboard visualization. */
  async recentKnowledge(limit = 25) {
    return this.prisma.knowledgeNode.findMany({
      orderBy: { createdAt: 'desc' },
      take: limit,
      include: {
        parent: { select: { id: true, founderId: true, knowledgeType: true } },
      },
    });
  }

  /** Return a founder's knowledge nodes + total impact. */
  async founderKnowledge(founderId: string) {
    const [nodes, impactAgg] = await Promise.all([
      this.prisma.knowledgeNode.findMany({
        where: { founderId },
        orderBy: { createdAt: 'desc' },
      }),
      this.prisma.knowledgeNode.aggregate({
        where: { founderId },
        _sum: { impactScore: true },
      }),
    ]);
    return {
      nodes,
      totalImpactScore: impactAgg._sum.impactScore ?? 0,
    };
  }
}
