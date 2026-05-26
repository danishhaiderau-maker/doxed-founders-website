import { ChainSlug } from '@prisma/client';
import {
  IsEnum,
  IsIn,
  IsOptional,
  IsString,
  IsUrl,
  MaxLength,
  MinLength,
} from 'class-validator';

export class PreviewDexScreenerDto {
  @IsString()
  @MinLength(10)
  url!: string;
}

export class CreateListingApplicationDto {
  @IsString()
  @MinLength(2)
  @MaxLength(120)
  projectName!: string;

  @IsString()
  @MinLength(1)
  @MaxLength(20)
  ticker!: string;

  @IsOptional()
  @IsUrl()
  websiteUrl?: string;

  @IsOptional()
  @IsUrl()
  docsUrl?: string;

  @IsOptional()
  @IsUrl()
  whitepaperUrl?: string;

  @IsOptional()
  @IsString()
  contractAddress?: string;

  @IsOptional()
  @IsEnum(ChainSlug)
  chainSlug?: ChainSlug;

  @IsOptional()
  @IsString()
  dexscreenerUrl?: string;

  @IsOptional()
  @IsUrl()
  logoUrl?: string;

  @IsOptional()
  @IsUrl()
  telegramUrl?: string;

  @IsOptional()
  @IsString()
  @MaxLength(120)
  founderName?: string;

  @IsOptional()
  @IsUrl()
  founderLinkedIn?: string;

  @IsOptional()
  @IsString()
  founderTwitter?: string;

  @IsOptional()
  @IsUrl()
  founderGithub?: string;

  @IsOptional()
  @IsUrl()
  founderVideoUrl?: string;

  @IsOptional()
  @IsUrl()
  founderInterviewUrl?: string;

  @IsOptional()
  @IsString()
  @MaxLength(5000)
  companyDetails?: string;

  @IsOptional()
  @IsUrl()
  auditUrl?: string;

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  summary?: string;

  @IsOptional()
  marketPreview?: Record<string, unknown>;
}

export class ReviewListingApplicationDto {
  @IsIn(['APPROVED', 'REJECTED'])
  status!: 'APPROVED' | 'REJECTED';

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  reviewNotes?: string;
}
