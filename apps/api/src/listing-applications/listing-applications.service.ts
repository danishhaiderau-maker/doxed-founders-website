import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import {
  computeVotingThreshold,
  POINTS,
  applyProofLinkUrl,
  scoreFounderVerification,
  userHasTwitterConnected,
  validateListingForApproval,
} from '@dcf/utils';
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
import { MessagesService } from '../messages/messages.service';
import { ListedProjectGithubSyncService } from './listed-project-github-sync.service';

@Injectable()
export class ListingApplicationsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly publish: ListingPublishService,
    private readonly points: PointsService,
    private readonly votes: ListingVotesService,
    private readonly predictionMarkets: PredictionMarketsService,
    private readonly notifications: NotificationsService,
    private readonly messages: MessagesService,
    private readonly listedGithubSync: ListedProjectGithubSyncService,
  ) {}

  private computeVerification(dto: CreateListingApplicationDto) {
    return scoreFounderVerification({
      founderName: dto.founderName,
      founderLinkedIn: dto.founderLinkedIn,
      founderGithub: dto.founderGithub,
      projectGithubUrl: dto.projectGithubUrl,
      companyDetails: dto.companyDetails,
      founderVideoUrl: dto.founderVideoUrl,
      founderInterviewUrl: dto.founderInterviewUrl,
    });
  }

  async create(dto: CreateListingApplicationDto, submitterUserId?: string | null) {
    await this.assertSubmitterCanList(submitterUserId);

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
      projectGithubUrl: dto.projectGithubUrl,
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
      projectGithubUrl: mapped.projectGithubUrl,
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
        projectGithubUrl: dto.projectGithubUrl?.trim() || mapped.projectGithubUrl,
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
    const applications = await this.prisma.listingApplication.findMany({
      where: {
        status: { in: [ListingStatus.PENDING, ListingStatus.COMMUNITY_VOTING] },
      },
      orderBy: [{ verificationScore: 'desc' }, { createdAt: 'desc' }],
      include: {
        user: {
          select: {
            id: true,
            name: true,
            email: true,
            platformHandle: true,
            twitterHandle: true,
            reputationPoints: true,
            oauthAccounts: { select: { provider: true } },
          },
        },
        votes: {
          include: {
            user: { select: { id: true, name: true, contributorLevel: true } },
          },
          orderBy: { createdAt: 'desc' },
        },
      },
    });

    return Promise.all(
      applications.map(async (app) => ({
        ...app,
        relistPreview: await this.publish.getRelistPreview(app),
      })),
    );
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
      projectGithubUrl: merged.projectGithubUrl,
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
        isNewListing: !published.relisted,
      });

      void this.listedGithubSync.syncAll().catch(() => undefined);

      const relistNote =
        published.relisted && (published.changedFieldCount ?? 0) > 0
          ? ` ${published.changedFieldCount} field(s) updated on the live listing.`
          : published.relisted
            ? ' Listing refreshed with latest scout data.'
            : '';

      await this.notifications.notifyAllUsers({
        type: NotificationType.LISTING_APPROVED,
        title: published.relisted
          ? `Listing updated: ${published.projectName}`
          : `New listing: ${published.projectName}`,
        body: `${published.projectName} (${merged.ticker}) is live — predict, paper trade, and scout the founder.${relistNote}`,
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

  async requestMoreProof(adminUserId: string, applicationId: string, message: string) {
    const application = await this.findById(applicationId);
    if (
      application.status !== ListingStatus.PENDING &&
      application.status !== ListingStatus.COMMUNITY_VOTING
    ) {
      throw new BadRequestException('Cannot request proof on a closed application');
    }
    if (!application.userId) {
      throw new BadRequestException(
        'This listing has no linked account — submitter must sign in with X (Twitter) to receive messages',
      );
    }

    const trimmed =
      message.trim() ||
      'We need stronger public proof before we can list this project — please add a founder video, interview, verification page, or official team link.';

    await this.messages.sendMessage(adminUserId, application.userId, trimmed, {
      applicationId: application.id,
    });

    await this.notifications.notifyUser(application.userId, {
      type: NotificationType.LISTING_PROOF_REQUEST,
      title: `More proof needed: ${application.projectName}`,
      body: trimmed.slice(0, 500),
      link: `/account?tab=messages&with=${adminUserId}`,
      metadata: { applicationId: application.id, fromAdminId: adminUserId },
    });

    const updated = await this.prisma.listingApplication.update({
      where: { id: applicationId },
      data: {
        reviewNotes: trimmed,
      },
    });

    return { application: updated, messageSent: true };
  }

  private async assertSubmitterCanList(submitterUserId?: string | null) {
    if (!submitterUserId) {
      throw new BadRequestException(
        'Sign in with X (Twitter) to submit a listing — admins can message you when more proof is needed.',
      );
    }
    const user = await this.prisma.user.findUnique({
      where: { id: submitterUserId },
      include: { oauthAccounts: true },
    });
    if (!user) {
      throw new BadRequestException('Sign in to submit a listing');
    }
    if (!userHasTwitterConnected(user)) {
      throw new BadRequestException(
        'Connect X (Twitter) before submitting a listing. Only Twitter sign-in accounts can list projects and receive admin proof requests in Messages.',
      );
    }
  }
}
