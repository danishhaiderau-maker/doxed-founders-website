import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { computeVotingThreshold, POINTS, applyProofLinkUrl, scoreFounderVerification, validateListingForApproval } from '@dcf/utils';
import { ListingStatus, NotificationType, Prisma } from '@prisma/client';
import { PointsService } from '../points/points.service';
import { NotificationsService } from '../notifications/notifications.service';
import { PrismaService } from '../prisma/prisma.service';
import {
  CreateListingApplicationDto,
  ReviewListingApplicationDto,
} from './dto/listing-application.dto';
import { ListingPublishService } from './listing-publish.service';
import {
  extractAdminReviewUpdates,
  mergeListingApplication,
  toPrismaAdminUpdates,
} from './listing-application-review.util';
import { ListingVotesService } from './listing-votes.service';
import { PredictionMarketsService } from '../prediction-markets/prediction-markets.service';

@Injectable()
export class ListingApplicationsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly publish: ListingPublishService,
    private readonly points: PointsService,
    private readonly votes: ListingVotesService,
    private readonly predictionMarkets: PredictionMarketsService,
    private readonly notifications: NotificationsService,
  ) {}

  private computeVerification(dto: CreateListingApplicationDto) {
    return scoreFounderVerification({
      founderName: dto.founderName,
      founderLinkedIn: dto.founderLinkedIn,
      founderGithub: dto.founderGithub,
      companyDetails: dto.companyDetails,
      founderVideoUrl: dto.founderVideoUrl,
      founderInterviewUrl: dto.founderInterviewUrl,
    });
  }

  async create(dto: CreateListingApplicationDto, submitterUserId?: string | null) {
    const mapped = applyProofLinkUrl({
      ...dto,
      dexscreenerUrl: dto.dexscreenerUrl,
      founderDoxxedStatus: dto.founderDoxxedStatus,
      proofLinkUrl: dto.proofLinkUrl,
      founderVideoUrl: dto.founderVideoUrl,
      founderInterviewUrl: dto.founderInterviewUrl,
      founderTwitter: dto.founderTwitter,
      founderLinkedIn: dto.founderLinkedIn,
      founderGithub: dto.founderGithub,
      websiteUrl: dto.websiteUrl,
      chainSlug: dto.chainSlug,
      contractAddress: dto.contractAddress,
      founderName: dto.founderName,
      projectName: dto.projectName,
    });

    const doxxedStatus =
      mapped.founderDoxxedStatus ??
      (dto.whyDoxxed?.toLowerCase().includes('building in public')
        ? 'BUILDING_IN_PUBLIC'
        : 'DOXXED');

    const approval = validateListingForApproval({
      ...mapped,
      founderDoxxedStatus: doxxedStatus,
    });

    if (!approval.ok) {
      throw new BadRequestException(approval.errors.join(' '));
    }

    if (!dto.whyList?.trim() && !dto.scoutHighlightNote?.trim()) {
      throw new BadRequestException('Add a short note on why this founder deserves community review.');
    }

    const verification = this.computeVerification({
      ...dto,
      founderVideoUrl: mapped.founderVideoUrl,
      founderInterviewUrl: mapped.founderInterviewUrl,
      founderLinkedIn: mapped.founderLinkedIn,
      founderTwitter: mapped.founderTwitter,
      founderGithub: mapped.founderGithub,
    });

    const activeUsers = await this.prisma.user.count({
      where: { banned: false },
    });
    const threshold = computeVotingThreshold(activeUsers);
    const now = new Date();

    const application = await this.prisma.listingApplication.create({
      data: {
        userId: submitterUserId ?? undefined,
        projectName: dto.projectName,
        ticker: dto.ticker.toUpperCase(),
        websiteUrl: dto.websiteUrl,
        docsUrl: dto.docsUrl,
        whitepaperUrl: dto.whitepaperUrl,
        contractAddress: dto.contractAddress,
        chainSlug: dto.chainSlug,
        dexscreenerUrl: dto.dexscreenerUrl,
        logoUrl: dto.logoUrl,
        telegramUrl: dto.telegramUrl,
        founderName: dto.founderName,
        founderLinkedIn: mapped.founderLinkedIn,
        founderTwitter: mapped.founderTwitter,
        founderGithub: mapped.founderGithub,
        founderVideoUrl: mapped.founderVideoUrl,
        founderInterviewUrl: mapped.founderInterviewUrl,
        companyDetails: dto.companyDetails,
        auditUrl: dto.auditUrl,
        summary: dto.summary,
        whyList: dto.whyList?.trim() ?? dto.scoutHighlightNote?.trim() ?? 'Community listing submission',
        whyDoxxed: dto.whyDoxxed?.trim() ?? null,
        founderDoxxedStatus: doxxedStatus,
        scoutHighlightNote:
          dto.scoutHighlightNote?.trim() ||
          (doxxedStatus === 'BUILDING_IN_PUBLIC'
            ? dto.whyDoxxed?.replace(/^\[Building in public[^\]]*\]\s*/i, '').trim() || null
            : null),
        marketPreview: dto.marketPreview as Prisma.InputJsonValue | undefined,
        verificationScore: verification.score,
        verificationCriteria: verification.criteria,
        status: ListingStatus.COMMUNITY_VOTING,
        votingOpensAt: now,
        votingClosesAt: ListingVotesService.votingWindowEnd(now),
        requiredVoters: threshold.requiredVoters,
        minYesPercent: threshold.minYesPercent,
      },
    });

    if (submitterUserId) {
      await this.points.award(submitterUserId, POINTS.LISTING_SUBMIT, 'LISTING_SUBMIT');
    }

    return application;
  }

  async updateScoutFields(
    id: string,
    userId: string,
    input: {
      scoutHighlightNote?: string;
      whyList?: string;
      whyDoxxed?: string;
      founderDoxxedStatus?: 'DOXXED' | 'BUILDING_IN_PUBLIC';
    },
  ) {
    const application = await this.prisma.listingApplication.findUnique({ where: { id } });
    if (!application) throw new NotFoundException('Listing application not found');
    if (application.userId !== userId) {
      throw new BadRequestException('Only the submitter can edit this listing');
    }
    if (application.status !== ListingStatus.COMMUNITY_VOTING) {
      throw new BadRequestException('Scout fields can only be edited while community voting is open');
    }

    return this.prisma.listingApplication.update({
      where: { id },
      data: {
        scoutHighlightNote: input.scoutHighlightNote?.trim() ?? undefined,
        whyList: input.whyList?.trim() ?? undefined,
        whyDoxxed: input.whyDoxxed?.trim() ?? undefined,
        founderDoxxedStatus: input.founderDoxxedStatus ?? undefined,
      },
    });
  }

  async findPending() {
    return this.prisma.listingApplication.findMany({
      where: {
        status: { in: [ListingStatus.PENDING, ListingStatus.COMMUNITY_VOTING] },
      },
      orderBy: [{ verificationScore: 'desc' }, { createdAt: 'desc' }],
      include: {
        user: { select: { id: true, name: true, email: true, reputationPoints: true } },
        votes: {
          include: {
            user: { select: { id: true, name: true, contributorLevel: true } },
          },
          orderBy: { createdAt: 'desc' },
        },
      },
    });
  }

  async findById(id: string) {
    const application = await this.prisma.listingApplication.findUnique({
      where: { id },
      include: {
        votes: {
          include: {
            user: { select: { id: true, name: true, contributorLevel: true } },
          },
          orderBy: { createdAt: 'desc' },
        },
      },
    });
    if (!application) {
      throw new NotFoundException('Listing application not found');
    }
    return application;
  }

  async review(id: string, dto: ReviewListingApplicationDto) {
    const application = await this.findById(id);

    if (
      application.status !== ListingStatus.PENDING &&
      application.status !== ListingStatus.COMMUNITY_VOTING
    ) {
      throw new BadRequestException(
        'Only listings in community voting or awaiting admin review can be approved or rejected',
      );
    }

    const adminUpdates = extractAdminReviewUpdates(dto);
    const prismaUpdates = toPrismaAdminUpdates(adminUpdates);
    const merged = mergeListingApplication(application, adminUpdates);

    const verification = scoreFounderVerification({
      founderName: merged.founderName,
      founderLinkedIn: merged.founderLinkedIn,
      founderGithub: merged.founderGithub,
      companyDetails: merged.companyDetails,
      founderVideoUrl: merged.founderVideoUrl,
      founderInterviewUrl: merged.founderInterviewUrl,
    });

    if (dto.status === 'APPROVED') {
      const published = await this.publish.publishApprovedApplication(merged);

      const updated = await this.prisma.listingApplication.update({
        where: { id },
        data: {
          ...prismaUpdates,
          status: ListingStatus.APPROVED,
          reviewNotes: dto.reviewNotes,
          reviewedAt: new Date(),
          verificationScore: verification.score,
          verificationCriteria: verification.criteria,
        },
      });

      if (application.userId) {
        await this.points.award(application.userId, POINTS.LISTING_SCOUT_APPROVED, 'LISTING_SCOUT_APPROVED');
      }

      for (const vote of application.votes) {
        if (vote.vote === 'YES') {
          await this.points.award(vote.userId, POINTS.VALIDATION_CORRECT, 'VALIDATION_CORRECT');
        }
      }

      await this.predictionMarkets.seedMarketsForProject(published.projectId, {
        isNewListing: true,
      });

      await this.notifications.notifyAllUsers({
        type: NotificationType.LISTING_APPROVED,
        title: `New listing: ${published.projectName}`,
        body: `${published.projectName} (${merged.ticker}) is live — predict, paper trade, and scout the founder.`,
        link: `/project/${published.projectSlug}`,
      });

      return {
        application: updated,
        published,
      };
    }

    const updated = await this.prisma.listingApplication.update({
      where: { id },
      data: {
        ...prismaUpdates,
        status: ListingStatus.REJECTED,
        reviewNotes: dto.reviewNotes,
        reviewedAt: new Date(),
        verificationScore: verification.score,
        verificationCriteria: verification.criteria,
      },
    });

    return { application: updated, published: null };
  }
}
