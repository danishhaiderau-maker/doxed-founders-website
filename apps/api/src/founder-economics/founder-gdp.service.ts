/**
 * Founder Economics GDP metrics — the high-level dashboard numbers.
 *
 * "Founder GDP" is the sum of value the platform's founders have created,
 * measured in DDollar (raw + reputation-weighted) plus verified revenue.
 * Components:
 *   - aiValueCreated       = sum of DDollar from build posts + knowledge contributions
 *   - knowledgeShared      = count + impact of KnowledgeNodes
 *   - productsShipped      = count of PRODUCT_LAUNCH_VIA_RAISE_ROOM grants
 *   - companiesLaunched    = count of verified COMPANY_MILESTONE_VERIFIED grants
 *   - revenueVerified      = sum of verifiedMetric across ProofOfSuccess records
 */

import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

@Injectable()
export class FounderGdpService {
  constructor(private readonly prisma: PrismaService) {}

  async computeGdp() {
    const [
      buildPostGrants,
      knowledgeContributions,
      productLaunches,
      companyMilestones,
      proofAgg,
      knowledgeNodeCount,
      totalDdollarAgg,
      activeFoundersCount,
    ] = await Promise.all([
      this.prisma.dDollarGrant.aggregate({
        where: { activityType: 'BUILD_POST_LINKED_TO_COMMIT', reverted: false },
        _sum: { amount: true },
        _count: true,
      }),
      this.prisma.dDollarGrant.aggregate({
        where: { activityType: 'KNOWLEDGE_CONTRIBUTION', reverted: false },
        _sum: { amount: true },
        _count: true,
      }),
      this.prisma.dDollarGrant.aggregate({
        where: { activityType: 'PRODUCT_LAUNCH_VIA_RAISE_ROOM', reverted: false },
        _count: true,
      }),
      this.prisma.dDollarGrant.aggregate({
        where: { activityType: 'COMPANY_MILESTONE_VERIFIED', reverted: false },
        _count: true,
      }),
      this.prisma.proofOfSuccess.aggregate({
        _sum: { verifiedMetric: true },
        _count: true,
      }),
      this.prisma.knowledgeNode.count(),
      this.prisma.user.aggregate({ _sum: { reputationPoints: true } }),
      this.prisma.user.count({ where: { reputationPoints: { gt: 0 } } }),
    ]);

    const aiValueCreated =
      (buildPostGrants._sum.amount ?? 0) + (knowledgeContributions._sum.amount ?? 0);
    const knowledgeShared = {
      nodes: knowledgeNodeCount,
      contributions: knowledgeContributions._count,
      impactDdollar: knowledgeContributions._sum.amount ?? 0,
    };
    const productsShipped = productLaunches._count;
    const companiesLaunched = companyMilestones._count;
    const revenueVerified = proofAgg._sum.verifiedMetric ?? 0;
    const totalDdollarSupply = totalDdollarAgg._sum.reputationPoints ?? 0;

    return {
      aiValueCreated,
      knowledgeShared,
      productsShipped,
      companiesLaunched,
      revenueVerified,
      verifiedMilestoneCount: proofAgg._count,
      totalDdollarSupply,
      activeFounders: activeFoundersCount,
      computedAt: new Date().toISOString(),
    };
  }
}
