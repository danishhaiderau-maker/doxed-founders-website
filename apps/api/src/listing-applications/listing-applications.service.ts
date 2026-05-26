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
      select: {
        id: true,
        projectName: true,
        ticker: true,
        chainSlug: true,
        websiteUrl: true,
        founderName: true,
        founderVideoUrl: true,
        founderInterviewUrl: true,
        founderLinkedIn: true,
        founderGithub: true,
        companyDetails: true,
        dexscreenerUrl: true,
        logoUrl: true,
        verificationScore: true,
        verificationCriteria: true,
        status: true,
        createdAt: true,
      },
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

    const verification = scoreFounderVerification({
      founderName: application.founderName,
      founderLinkedIn: application.founderLinkedIn,
      founderGithub: application.founderGithub,
      companyDetails: application.companyDetails,
      founderVideoUrl: application.founderVideoUrl,
      founderInterviewUrl: application.founderInterviewUrl,
    });

    if (dto.status === 'APPROVED') {
      const published = await this.publish.publishApprovedApplication(application);

      const updated = await this.prisma.listingApplication.update({
        where: { id },
        data: {
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
