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
  computeVotingThreshold,
  tallyListingVotes,
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

  async getVotingStats() {
    const activeUsers = await this.prisma.user.count({
      where: { banned: false, role: 'USER' },
    });
    return computeVotingThreshold(activeUsers);
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
        app.votes.map((v) => ({ vote: v.vote })),
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
            user: { select: { id: true, name: true, contributorLevel: true } },
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
        app.votes.map((v) => ({ vote: v.vote })),
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

    if (dto.vote === 'YES' && (!dto.whyList?.trim() || !dto.whyDoxxed?.trim())) {
      throw new BadRequestException('YES votes require whyList and whyDoxxed');
    }

    try {
      await this.prisma.listingVote.create({
        data: {
          applicationId,
          userId,
          vote: dto.vote as ListingVoteValue,
          whyList: dto.whyList?.trim(),
          whyDoxxed: dto.whyDoxxed?.trim(),
          comment: dto.comment?.trim(),
        },
      });
    } catch {
      throw new ConflictException('You already voted on this listing');
    }

    await this.points.award(userId, POINTS.LISTING_VOTE, 'LISTING_VOTE');

    const updated = await this.findOneForVoting(applicationId);
    if (updated.tally.passed) {
      await this.promoteToAdminReview(applicationId, application.projectName);
    }

    return updated;
  }

  async promoteToAdminReview(applicationId: string, projectName: string) {
    const application = await this.prisma.listingApplication.findUnique({
      where: { id: applicationId },
    });
    if (!application || application.status !== ListingStatus.COMMUNITY_VOTING) return;

    await this.prisma.listingApplication.update({
      where: { id: applicationId },
      data: { status: ListingStatus.PENDING },
    });

    await this.notifications.notifyAllUsers({
      type: 'LISTING_VOTING',
      title: `${projectName} passed community vote`,
      body: 'Scout listing cleared the community bar and is queued for admin review. See votes and thesis comments on the scout board.',
      link: `/scout-votes/${applicationId}`,
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
      const tally = tallyListingVotes(
        app.votes.map((v) => ({ vote: v.vote })),
        app.requiredVoters,
        app.minYesPercent,
      );

      await this.prisma.listingApplication.update({
        where: { id: app.id },
        data: {
          status: ListingStatus.REJECTED,
          reviewNotes: tally.passed
            ? 'Voting passed but admin did not approve before the 48h window closed.'
            : `48h voting ended (${tally.yes}/${tally.total} yes, ${tally.yesPercent}%) — not listed.`,
        },
      });
      expiredCount += 1;
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
