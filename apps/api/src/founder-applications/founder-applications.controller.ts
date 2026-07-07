import {
  Body,
  Controller,
  Get,
  NotFoundException,
  Param,
  Patch,
  Post,
  UseGuards,
  BadRequestException,
} from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { AdminGuard } from '../auth/guards';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { PrismaService } from '../prisma/prisma.service';
import {
  CreateFounderApplicationDto,
  ReviewFounderApplicationDto,
} from './dto/founder-application.dto';

/**
 * Doxxing applications — the application a Visitor submits to upgrade to
 * Doxxed Builder. Lands in the admin review inbox at /admin/founder-applications
 * alongside scout-submitted listing applications. See docs/BILLING.md §4
 * "Verification flow" and docs/PRODUCT.md "Doxxing is the product".
 *
 * Auth: optional JWT for the visitor-facing submit/mine endpoints (anonymous
 * submit is rejected at the controller — we need a userId). Admin endpoints
 * require AdminGuard (JWT is enforced by the global APP_GUARD).
 */
@SkipThrottle()
@Controller('founder-applications')
@UseGuards(OptionalJwtAuthGuard)
export class FounderApplicationsController {
  constructor(private readonly prisma: PrismaService) {}

  @Get('mine')
  async mine(@CurrentUser() user: AuthUser | null) {
    if (!user?.id) return [];
    return this.prisma.founderApplication.findMany({
      where: { userId: user.id },
      orderBy: { createdAt: 'desc' },
      take: 10,
    });
  }

  @Post()
  async create(
    @Body() dto: CreateFounderApplicationDto,
    @CurrentUser() user: AuthUser | null,
  ) {
    if (!user?.id) {
      throw new BadRequestException('Sign in to submit a doxxing application.');
    }
    if (!dto.githubUrl || !dto.videoUrl) {
      throw new BadRequestException(
        'GitHub URL and founder video URL are required for Doxxing review.',
      );
    }
    return this.prisma.founderApplication.create({
      data: {
        userId: user.id,
        projectName: dto.projectName,
        twitterHandle: dto.twitterHandle ?? null,
        githubUrl: dto.githubUrl,
        videoUrl: dto.videoUrl,
        websiteUrl: dto.websiteUrl ?? null,
        ideaDescription: dto.ideaDescription,
        status: 'SUBMITTED',
      },
    });
  }

  /**
   * GET /founder-applications/pending — admin-only. Returns all SUBMITTED
   * doxxing applications, newest first, with the related user (email, name)
   * so the admin can see who submitted. Mirrors the listing-applications
   * admin inbox pattern.
   */
  @UseGuards(AdminGuard)
  @Get('pending')
  async pending() {
    return this.prisma.founderApplication.findMany({
      where: { status: 'SUBMITTED' },
      orderBy: { createdAt: 'desc' },
      include: {
        user: {
          select: {
            id: true,
            email: true,
            name: true,
            twitterHandle: true,
            platformHandle: true,
          },
        },
      },
    });
  }

  /**
   * PATCH /founder-applications/:id — admin-only. Records the review
   * decision on the application row. On APPROVED, also flips the founder's
   * `builderTier` to VERIFIED_BUILDER so the doxxing-tier caps and launch
   * rights unlock (docs/BILLING.md §4 tier matrix).
   */
  @UseGuards(AdminGuard)
  @Patch(':id')
  async review(
    @Param('id') id: string,
    @Body() dto: ReviewFounderApplicationDto,
    @CurrentUser() admin: AuthUser,
  ) {
    const existing = await this.prisma.founderApplication.findUnique({
      where: { id },
    });
    if (!existing) {
      throw new NotFoundException(`Founder application ${id} not found`);
    }

    const updated = await this.prisma.founderApplication.update({
      where: { id },
      data: {
        // The schema's FounderApplicationStatus enum is {DRAFT,SUBMITTED,ACTIVE,REJECTED}.
        // The admin-facing API uses APPROVED, which maps to ACTIVE internally
        // (an approved doxxing application is an active one).
        status: dto.status === 'APPROVED' ? 'ACTIVE' : 'REJECTED',
        reviewNotes: dto.reviewNotes ?? null,
        reviewerId: admin.id,
        reviewedAt: new Date(),
      },
    });

    if (dto.status === 'APPROVED') {
      await this.prisma.user.update({
        where: { id: existing.userId },
        data: { builderTier: 'VERIFIED_BUILDER' },
      });
    }

    return updated;
  }
}
