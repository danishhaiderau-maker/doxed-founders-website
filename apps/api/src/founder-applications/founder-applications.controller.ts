import {
  Body,
  Controller,
  Get,
  Post,
  UseGuards,
  BadRequestException,
} from '@nestjs/common';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { PrismaService } from '../prisma/prisma.service';
import {
  IsOptional,
  IsString,
  IsUrl,
  MaxLength,
  MinLength,
  ValidateIf,
} from 'class-validator';
import { Transform } from 'class-transformer';

function emptyToUndefined({ value }: { value: unknown }) {
  if (value === '' || value === null || value === undefined) return undefined;
  return value;
}

export class CreateFounderApplicationDto {
  @IsString()
  @MinLength(2)
  @MaxLength(120)
  projectName!: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  @MinLength(2)
  @MaxLength(60)
  twitterHandle?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @ValidateIf((_o, v) => v != null)
  @IsUrl({ require_tld: false })
  githubUrl?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @ValidateIf((_o, v) => v != null)
  @IsUrl({ require_tld: false })
  videoUrl?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  @MinLength(2)
  @MaxLength(120)
  websiteUrl?: string;

  @IsString()
  @MinLength(20)
  @MaxLength(5000)
  ideaDescription!: string;
}

/**
 * Doxxing applications — the application a Visitor submits to upgrade to
 * Doxxed Builder. Lands in the admin review inbox at /admin/applications
 * alongside scout-submitted listing applications. See RAISE_ROOM_LAUNCH_FLOW.md
 * §"Verification flow".
 *
 * Auth: optional JWT. Anonymous submissions are rejected at the controller
 * (we need a userId to attach the application to).
 */
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
}
