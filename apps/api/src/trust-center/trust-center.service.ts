import {
  BadRequestException,
  ConflictException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import {
  INVESTIGATION_SCAM_THRESHOLD_PERCENT,
  INVESTIGATION_WINDOW_HOURS,
  POINTS,
  POSITIVE_VALIDATION,
  computeTrustWeight,
  tallyWeightedVotes,
  validationCategoryToVote,
  type CommunityValidationCategory,
} from '@dcf/utils';
import { InvestigationStatus, ListingStatus } from '@prisma/client';
import { PointsService } from '../points/points.service';
import { PrismaService } from '../prisma/prisma.service';
import { TrustWeightService } from './trust-weight.service';
import { ListingVotesService } from '../listing-applications/listing-votes.service';

export type FileTrustReportDto = {
  category: CommunityValidationCategory;
  evidenceUrl?: string;
  comment?: string;
};

@Injectable()
export class TrustCenterService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly points: PointsService,
    private readonly trustWeight: TrustWeightService,
    private readonly listingVotes: ListingVotesService,
  ) {}

  async getOverview() {
    const now = new Date();
    const [
      pendingListings,
      activeInvestigations,
      recentlyListed,
      recentlyDelisted,
      topScouts,
      platformStats,
    ] = await Promise.all([
      this.prisma.listingApplication.count({
        where: {
          status: ListingStatus.COMMUNITY_VOTING,
          OR: [{ votingClosesAt: null }, { votingClosesAt: { gt: now } }],
        },
      }),
      this.prisma.projectInvestigation.count({
        where: { status: { in: [InvestigationStatus.ACTIVE, InvestigationStatus.ADMIN_REVIEW] } },
      }),
      this.prisma.project.count({
        where: { approved: true, createdAt: { gte: new Date(Date.now() - 14 * 86400000) } },
      }),
      this.prisma.listingApplication.count({
        where: {
          status: ListingStatus.REJECTED,
          updatedAt: { gte: new Date(Date.now() - 14 * 86400000) },
        },
      }),
      this.prisma.user.findMany({
        where: { banned: false, reputationPoints: { gt: 0 } },
        orderBy: { reputationPoints: 'desc' },
        take: 10,
        select: {
          id: true,
          name: true,
          reputationPoints: true,
          contributorLevel: true,
          createdAt: true,
          emailVerified: true,
          twitterHandle: true,
        },
      }),
      this.listingVotes.getVotingStats(),
    ]);

    const scouts = await Promise.all(
      topScouts.map(async (u) => ({
        ...u,
        trustWeight: computeTrustWeight({
          verifiedAccount: Boolean(u.emailVerified || u.twitterHandle),
          contributorLevel: u.contributorLevel,
          reputationPoints: u.reputationPoints,
          accountAgeDays: Math.floor((Date.now() - u.createdAt.getTime()) / 86400000),
        }),
      })),
    );

    return {
      pendingListings,
      activeInvestigations,
      recentlyListed,
      recentlyDelisted,
      topScouts: scouts,
      platformStats,
      thresholds: {
        listingApprovalPercent: platformStats.minYesPercent,
        investigationScamPercent: INVESTIGATION_SCAM_THRESHOLD_PERCENT,
        windowHours: INVESTIGATION_WINDOW_HOURS,
      },
    };
  }

  async getPendingListings() {
    return this.listingVotes.findOpenForVoting();
  }

  async getInvestigations(status?: InvestigationStatus) {
    const where = status ? { status } : { status: { not: InvestigationStatus.RESOLVED_DELIST } };
    return this.prisma.projectInvestigation.findMany({
      where,
      orderBy: { createdAt: 'desc' },
      include: {
        project: {
          select: {
            id: true,
            slug: true,
            name: true,
            ticker: true,
            logoUrl: true,
            approved: true,
          },
        },
        reports: {
          include: {
            user: { select: { id: true, name: true, contributorLevel: true } },
          },
          orderBy: { createdAt: 'desc' },
          take: 20,
        },
      },
    });
  }

  async getInvestigation(id: string) {
    const investigation = await this.prisma.projectInvestigation.findUnique({
      where: { id },
      include: {
        project: true,
        reports: {
          include: {
            user: { select: { id: true, name: true, contributorLevel: true } },
          },
          orderBy: { createdAt: 'desc' },
        },
      },
    });
    if (!investigation) throw new NotFoundException('Investigation not found');

    const tally = this.tallyReports(investigation.reports);
    return { ...investigation, tally };
  }

  async getRecentlyListed(limit = 12) {
    return this.prisma.project.findMany({
      where: { approved: true },
      orderBy: { createdAt: 'desc' },
      take: limit,
      select: {
        id: true,
        slug: true,
        name: true,
        ticker: true,
        logoUrl: true,
        createdAt: true,
        founder: { select: { name: true } },
      },
    });
  }

  async getRecentlyDelisted(limit = 12) {
    return this.prisma.listingApplication.findMany({
      where: { status: ListingStatus.REJECTED },
      orderBy: { updatedAt: 'desc' },
      take: limit,
      select: {
        id: true,
        projectName: true,
        ticker: true,
        logoUrl: true,
        reviewNotes: true,
        updatedAt: true,
      },
    });
  }

  async getProjectTrustMetrics(slug: string) {
    const project = await this.prisma.project.findUnique({
      where: { slug },
      include: {
        investigations: {
          where: { status: { in: [InvestigationStatus.ACTIVE, InvestigationStatus.ADMIN_REVIEW] } },
          take: 1,
        },
        trustReports: {
          orderBy: { createdAt: 'desc' },
          take: 50,
          include: { user: { select: { id: true, name: true, contributorLevel: true } } },
        },
      },
    });
    if (!project) throw new NotFoundException('Project not found');

    const tally = this.tallyReports(project.trustReports);
    return {
      project: {
        id: project.id,
        slug: project.slug,
        name: project.name,
        ticker: project.ticker,
        logoUrl: project.logoUrl,
        approved: project.approved,
      },
      trustPercent: tally.yesPercent,
      suspiciousPercent: tally.scamPercent,
      activeInvestigation: project.investigations[0] ?? null,
      recentReports: project.trustReports,
      tally,
    };
  }

  async fileReport(projectSlug: string, userId: string, dto: FileTrustReportDto) {
    const project = await this.prisma.project.findUnique({ where: { slug: projectSlug } });
    if (!project) throw new NotFoundException('Project not found');
    if (!project.approved) {
      throw new BadRequestException('Trust reports are only for listed projects');
    }

    const weight = await this.trustWeight.forUser(userId);
    const vote = validationCategoryToVote(dto.category);

    let investigation = await this.prisma.projectInvestigation.findFirst({
      where: {
        projectId: project.id,
        status: { in: [InvestigationStatus.ACTIVE, InvestigationStatus.ADMIN_REVIEW] },
      },
    });

    if (!investigation && NEGATIVE_REPORT(dto.category)) {
      investigation = await this.openInvestigation(project.id, dto.comment ?? 'Community scam report');
    }

    try {
      await this.prisma.projectTrustReport.create({
        data: {
          projectId: project.id,
          userId,
          investigationId: investigation?.id,
          category: dto.category,
          voteWeight: weight,
          evidenceUrl: dto.evidenceUrl?.trim(),
          comment: dto.comment?.trim(),
        },
      });
    } catch {
      throw new ConflictException('You already submitted a report for this investigation window');
    }

    if (vote === 'YES' && dto.comment && dto.comment.trim().length >= 40) {
      await this.points.award(userId, POINTS.VALIDATION_HELPFUL, 'VALIDATION_HELPFUL');
    }

    if (investigation) {
      await this.refreshInvestigationScores(investigation.id);
    }

    return this.getProjectTrustMetrics(projectSlug);
  }

  async openInvestigation(projectId: string, reason: string) {
    const closesAt = new Date(Date.now() + INVESTIGATION_WINDOW_HOURS * 60 * 60 * 1000);
    const activeUsers = await this.prisma.user.count({ where: { banned: false } });
    const minVoters = Math.max(5, Math.min(20, Math.ceil(Math.sqrt(activeUsers))));

    return this.prisma.projectInvestigation.create({
      data: {
        projectId,
        reason,
        closesAt,
        minVoters,
        scamThreshold: INVESTIGATION_SCAM_THRESHOLD_PERCENT,
        status: InvestigationStatus.ACTIVE,
      },
    });
  }

  private async refreshInvestigationScores(investigationId: string) {
    const investigation = await this.prisma.projectInvestigation.findUnique({
      where: { id: investigationId },
      include: { reports: true },
    });
    if (!investigation) return;

    const tally = this.tallyReports(investigation.reports);
    const status =
      tally.totalVoters >= investigation.minVoters &&
      tally.scamPercent >= investigation.scamThreshold
        ? InvestigationStatus.ADMIN_REVIEW
        : investigation.status;

    await this.prisma.projectInvestigation.update({
      where: { id: investigationId },
      data: {
        trustScore: tally.yesPercent,
        scamScore: tally.scamPercent,
        status,
      },
    });
  }

  private tallyReports(
    reports: { category: CommunityValidationCategory; voteWeight: number }[],
  ) {
    const votes = reports.map((r) => ({
      vote: validationCategoryToVote(r.category) as 'YES' | 'NO',
      weight: r.voteWeight,
    }));
    return tallyWeightedVotes(votes, 5, 100 - INVESTIGATION_SCAM_THRESHOLD_PERCENT);
  }
}

function NEGATIVE_REPORT(category: CommunityValidationCategory) {
  return !POSITIVE_VALIDATION.includes(category);
}
