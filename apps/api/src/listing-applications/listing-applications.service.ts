import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { computeVotingThreshold, POINTS, scoreFounderVerification } from '@dcf/utils';
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
    const verification = this.computeVerification(dto);

    if (!verification.meetsSubmissionThreshold) {
      throw new BadRequestException(
        'Add at least one public founder video or interview/podcast URL. Anyone can submit if they found public proof on X, YouTube, etc.',
      );
    }

    if (!dto.whyList?.trim()) {
      throw new BadRequestException('Explain why this project should be listed (whyList).');
    }

    const doxxedStatus =
      dto.founderDoxxedStatus ??
      (dto.whyDoxxed?.toLowerCase().includes('building in public')
        ? 'BUILDING_IN_PUBLIC'
        : 'DOXXED');
    if (doxxedStatus === 'DOXXED' && !dto.whyDoxxed?.trim()) {
      throw new BadRequestException(
        'Explain why the founder is doxxed (whyDoxxed), or select "Building in public (not fully doxxed)" and add a scout highlight.',
      );
    }
    if (doxxedStatus === 'BUILDING_IN_PUBLIC' && !dto.scoutHighlightNote?.trim()) {
      const fromWhy = dto.whyDoxxed
        ?.replace(/^\[Building in public[^\]]*\]\s*/i, '')
        .trim();
      if (!fromWhy || fromWhy.length < 20) {
        throw new BadRequestException(
          'Add a scout highlight — e.g. building in public, podcast appearances, GitHub activity.',
        );
      }
    }

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
        founderLinkedIn: dto.founderLinkedIn,
        founderTwitter: dto.founderTwitter,
        founderGithub: dto.founderGithub,
        founderVideoUrl: dto.founderVideoUrl,
        founderInterviewUrl: dto.founderInterviewUrl,
        companyDetails: dto.companyDetails,
        auditUrl: dto.auditUrl,
        summary: dto.summary,
        whyList: dto.whyList.trim(),
        whyDoxxed: dto.whyDoxxed?.trim() ?? null,
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
      if (application.votingClosesAt && application.votingClosesAt.getTime() <= Date.now()) {
        throw new BadRequestException(
          'Voting window has closed without admin approval — this listing cannot be published. Traders can still paper-trade the token.',
        );
      }

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
