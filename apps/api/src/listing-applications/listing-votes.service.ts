import {
  BadRequestException,
  ConflictException,
  Injectable,
  Logger,
  NotFoundException,
  OnModuleInit,
} from '@nestjs/common';
import {
  POINTS,
  VOTING_WINDOW_HOURS,
  computeTrustWeight,
  computeVotingThreshold,
  tallyListingVotes,
  validationCategoryToVote,
  type CommunityValidationCategory,
} from '@dcf/utils';
import { ListingStatus, ListingVoteValue } from '@prisma/client';
import { NotificationsService } from '../notifications/notifications.service';
import { PointsService } from '../points/points.service';
import { PrismaService } from '../prisma/prisma.service';
import { CastListingVoteDto } from './dto/listing-vote.dto';

@Injectable()
export class ListingVotesService implements OnModuleInit {
  private readonly logger = new Logger(ListingVotesService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly points: PointsService,
    private readonly notifications: NotificationsService,
  ) {}

  onModuleInit() {
    void this.expireClosedVoting();
    setInterval(() => void this.expireClosedVoting(), 15 * 60 * 1000);
  }

  private async trustWeightForUser(userId: string) {
    const user = await this.prisma.user.findUnique({
      where: { id: userId },
      include: { oauthAccounts: { select: { provider: true } } },
    });
    if (!user) return 1;
    return computeTrustWeight({
      verifiedAccount: Boolean(
        user.emailVerified ||
          user.twitterHandle?.trim() ||
          user.oauthAccounts.some((a) => a.provider === 'google' || a.provider === 'twitter'),
      ),
      contributorLevel: user.contributorLevel,
      reputationPoints: user.reputationPoints,
      accountAgeDays: Math.floor((Date.now() - user.createdAt.getTime()) / 86400000),
    });
  }

  async getVotingStats() {
    const activeUsers = await this.prisma.user.count({
      where: { banned: false, role: 'USER' },
    });
    return computeVotingThreshold(activeUsers);
  }

  private mapVotes(votes: { vote: ListingVoteValue; voteWeight: number }[]) {
    return votes.map((v) => ({ vote: v.vote, weight: v.voteWeight }));
  }

  async findOpenForVoting() {
    const now = new Date();
    const applications = await this.prisma.listingApplication.findMany({
      where: {
        status: ListingStatus.COMMUNITY_VOTING,
        OR: [{ votingClosesAt: null }, { votingClosesAt: { gt: now } }],
      },
      orderBy: [{ verificationScore: 'desc' }, { createdAt: 'desc' }],
      include: {
        user: { select: { id: true, name: true, reputationPoints: true, contributorLevel: true } },
        votes: {
          include: {
            user: { select: { id: true, name: true, contributorLevel: true } },
          },
          orderBy: { createdAt: 'desc' },
        },
      },
    });

    const stats = await this.getVotingStats();

    return applications.map((app) => ({
      ...app,
      tally: tallyListingVotes(
        this.mapVotes(app.votes),
        app.requiredVoters,
        app.minYesPercent,
      ),
      platformVoting: stats,
    }));
  }

  async findOneForVoting(id: string) {
    const app = await this.prisma.listingApplication.findUnique({
      where: { id },
      include: {
        user: { select: { id: true, name: true, reputationPoints: true, contributorLevel: true } },
        votes: {
          include: {
            user: { select: { id: true, name: true, contributorLevel: true, reputationPoints: true } },
          },
          orderBy: { createdAt: 'desc' },
        },
      },
    });
    if (!app) throw new NotFoundException('Listing application not found');

    const stats = await this.getVotingStats();
    return {
      ...app,
      tally: tallyListingVotes(
        this.mapVotes(app.votes),
        app.requiredVoters,
        app.minYesPercent,
      ),
      platformVoting: stats,
    };
  }

  async castVote(applicationId: string, userId: string, dto: CastListingVoteDto) {
    const application = await this.prisma.listingApplication.findUnique({
      where: { id: applicationId },
    });
    if (!application) throw new NotFoundException('Listing application not found');

    if (application.status !== ListingStatus.COMMUNITY_VOTING) {
      throw new BadRequestException('This listing is no longer open for community voting');
    }

    if (application.votingClosesAt && application.votingClosesAt < new Date()) {
      throw new BadRequestException('Voting window has closed');
    }

    if (application.userId === userId) {
      throw new BadRequestException('Scouts cannot vote on their own submission');
    }

    if (!dto.validationCategory && !dto.vote) {
      throw new BadRequestException('Select a validation option or YES/NO vote');
    }

    const vote = dto.validationCategory
      ? validationCategoryToVote(dto.validationCategory)
      : dto.vote;

    if (vote === 'YES' && dto.validationCategory && POSITIVE_REQUIRES_COMMENT(dto.validationCategory)) {
      if (!dto.comment?.trim() || dto.comment.trim().length < 20) {
        throw new BadRequestException('Add a short review comment (20+ characters)');
      }
    }

    if (vote === 'YES' && !dto.validationCategory && (!dto.whyList?.trim() || !dto.whyDoxxed?.trim())) {
      throw new BadRequestException('YES votes require whyList and whyDoxxed, or use a validation category');
    }

    const voteWeight = await this.trustWeightForUser(userId);

    try {
      await this.prisma.listingVote.create({
        data: {
          applicationId,
          userId,
          vote: vote as ListingVoteValue,
          validationCategory: dto.validationCategory ?? undefined,
          voteWeight,
          whyList: dto.whyList?.trim(),
          whyDoxxed: dto.whyDoxxed?.trim(),
          comment: dto.comment?.trim(),
        },
      });
    } catch {
      throw new ConflictException('You already voted on this listing');
    }

    await this.points.award(userId, POINTS.LISTING_VOTE, 'LISTING_VOTE');
    if (dto.comment && dto.comment.trim().length >= 40) {
      await this.points.award(userId, POINTS.VALIDATION_HELPFUL, 'VALIDATION_HELPFUL');
    }

    const updated = await this.findOneForVoting(applicationId);
    if (updated.tally.passed) {
      await this.promoteToAdminReview(applicationId, application.projectName);
    }

    return updated;
  }

  async promoteToAdminReview(applicationId: string, projectName: string, reason?: string) {
    const application = await this.prisma.listingApplication.findUnique({
      where: { id: applicationId },
    });
    if (!application || application.status !== ListingStatus.COMMUNITY_VOTING) return;

    const adminDeadline = new Date(Date.now() + 7 * 24 * 60 * 60 * 1000);
    await this.prisma.listingApplication.update({
      where: { id: applicationId },
      data: {
        status: ListingStatus.PENDING,
        votingClosesAt: adminDeadline,
        reviewNotes: reason ?? application.reviewNotes,
      },
    });

    await this.notifications.notifyAllUsers({
      type: 'LISTING_VOTING',
      title: `${projectName} passed community vote`,
      body: 'Scout listing cleared the community bar and is queued for admin review. See votes and thesis comments in Trust Center.',
      link: `/trust-center?tab=pending`,
    });
  }

  async expireClosedVoting() {
    const now = new Date();
    const expired = await this.prisma.listingApplication.findMany({
      where: {
        status: { in: [ListingStatus.COMMUNITY_VOTING, ListingStatus.PENDING] },
        votingClosesAt: { lt: now },
      },
      include: { votes: true },
    });

    let expiredCount = 0;
    for (const app of expired) {
      if (app.status === ListingStatus.COMMUNITY_VOTING) {
        const tally = tallyListingVotes(
          this.mapVotes(app.votes),
          app.requiredVoters,
          app.minYesPercent,
        );
        const note = tally.passed
          ? 'Community validation passed — queued for admin review.'
          : `48h community window ended (${tally.yes}/${tally.total} yes, ${tally.yesPercent}% weighted) — admin review queue.`;
        await this.promoteToAdminReview(app.id, app.projectName, note);
        expiredCount += 1;
        continue;
      }

      if (app.status === ListingStatus.PENDING) {
        await this.prisma.listingApplication.update({
          where: { id: app.id },
          data: {
            status: ListingStatus.REJECTED,
            reviewNotes:
              app.reviewNotes ??
              'Admin review window expired without approval.',
          },
        });
        expiredCount += 1;
      }
    }

    if (expiredCount > 0) {
      this.logger.log(`Expired ${expiredCount} listing vote window(s) without admin approval`);
    }

    return { expired: expired.length, expiredCount };
  }

  static votingWindowEnd(from = new Date()) {
    return new Date(from.getTime() + VOTING_WINDOW_HOURS * 60 * 60 * 1000);
  }
}

function POSITIVE_REQUIRES_COMMENT(category: CommunityValidationCategory) {
  return ['LOOKS_LEGIT', 'BUILDING_CONSISTENTLY', 'COMMUNITY_EXISTS'].includes(category);
}
