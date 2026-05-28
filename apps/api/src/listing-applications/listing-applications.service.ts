import {
  BadRequestException,
  Injectable,
  NotFoundException,
} from '@nestjs/common';
import { scoreFounderVerification } from '@dcf/utils';
import { ListingStatus, Prisma } from '@prisma/client';
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

@Injectable()
export class ListingApplicationsService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly publish: ListingPublishService,
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

  async create(dto: CreateListingApplicationDto) {
    const verification = this.computeVerification(dto);

    if (!verification.meetsSubmissionThreshold) {
      throw new BadRequestException(
        'Add at least one public founder video or interview/podcast URL. Anyone can submit if they found public proof on X, YouTube, etc.',
      );
    }

    return this.prisma.listingApplication.create({
      data: {
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
        marketPreview: dto.marketPreview as Prisma.InputJsonValue | undefined,
        verificationScore: verification.score,
        verificationCriteria: verification.criteria,
        status: ListingStatus.PENDING,
      },
    });
  }

  async findPending() {
    return this.prisma.listingApplication.findMany({
      where: { status: ListingStatus.PENDING },
      orderBy: [{ verificationScore: 'desc' }, { createdAt: 'desc' }],
    });
  }

  async findById(id: string) {
    const application = await this.prisma.listingApplication.findUnique({
      where: { id },
    });
    if (!application) {
      throw new NotFoundException('Listing application not found');
    }
    return application;
  }

  async review(id: string, dto: ReviewListingApplicationDto) {
    const application = await this.findById(id);

    if (application.status !== ListingStatus.PENDING) {
      throw new BadRequestException('Application has already been reviewed');
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
