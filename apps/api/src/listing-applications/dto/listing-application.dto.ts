import { ChainSlug } from '@prisma/client';
import { Transform } from 'class-transformer';
import {
  IsEnum,
  IsIn,
  IsOptional,
  IsString,
  IsUrl,
  MaxLength,
  MinLength,
  ValidateIf,
} from 'class-validator';

function emptyToUndefined({ value }: { value: unknown }) {
  if (value === '' || value === null || value === undefined) return undefined;
  return value;
}

export class PreviewDexScreenerDto {
  @IsString()
  @MinLength(10)
  url!: string;
}

export class PreviewContractDto {
  @IsEnum(ChainSlug)
  chainSlug!: ChainSlug;

  @IsString()
  @MinLength(4)
  contractAddress!: string;
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

  @Transform(emptyToUndefined)
  @IsOptional()
  @ValidateIf((_o, v) => v != null)
  @IsUrl({ require_tld: false })
  websiteUrl?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @ValidateIf((_o, v) => v != null)
  @IsUrl({ require_tld: false })
  docsUrl?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @ValidateIf((_o, v) => v != null)
  @IsUrl({ require_tld: false })
  whitepaperUrl?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  contractAddress?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsEnum(ChainSlug)
  chainSlug?: ChainSlug;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  dexscreenerUrl?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @ValidateIf((_o, v) => v != null)
  @IsUrl({ require_tld: false })
  logoUrl?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @ValidateIf((_o, v) => v != null)
  @IsUrl({ require_tld: false })
  telegramUrl?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  @MaxLength(120)
  founderName?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @ValidateIf((_o, v) => v != null)
  @IsUrl({ require_tld: false })
  founderLinkedIn?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  founderTwitter?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @ValidateIf((_o, v) => v != null)
  @IsUrl({ require_tld: false })
  founderGithub?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @ValidateIf((_o, v) => v != null)
  @IsUrl({ require_tld: false })
  founderVideoUrl?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @ValidateIf((_o, v) => v != null)
  @IsUrl({ require_tld: false })
  founderInterviewUrl?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  @MaxLength(5000)
  companyDetails?: string;

  @Transform(emptyToUndefined)
  @IsOptional()
  @ValidateIf((_o, v) => v != null)
  @IsUrl({ require_tld: false })
  auditUrl?: string;

  @Transform(emptyToUndefined)
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
