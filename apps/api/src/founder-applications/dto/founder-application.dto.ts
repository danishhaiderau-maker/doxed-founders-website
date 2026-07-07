import {
  IsIn,
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
 * Admin review payload for PATCH /founder-applications/:id. APPROVED flips
 * the applicant's `builderTier` to VERIFIED_BUILDER (the doxxed tier —
 * see docs/BILLING.md §4). REJECTED leaves the tier alone and records the
 * decision on the application row.
 */
export class ReviewFounderApplicationDto {
  @IsIn(['APPROVED', 'REJECTED'])
  status!: 'APPROVED' | 'REJECTED';

  @Transform(emptyToUndefined)
  @IsOptional()
  @IsString()
  @MaxLength(2000)
  reviewNotes?: string;
}
