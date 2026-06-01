import {
  IsIn,
  IsOptional,
  IsString,
  MaxLength,
  MinLength,
  ValidateIf,
} from 'class-validator';

const VALIDATION_CATEGORIES = [
  'LOOKS_LEGIT',
  'BUILDING_CONSISTENTLY',
  'COMMUNITY_EXISTS',
  'NEEDS_MORE_PROOF',
  'SUSPICIOUS',
  'LIKELY_SCAM',
] as const;

export class CastListingVoteDto {
  @ValidateIf((o) => !o.validationCategory)
  @IsIn(['YES', 'NO'])
  vote?: 'YES' | 'NO';

  @IsOptional()
  @IsIn(VALIDATION_CATEGORIES)
  validationCategory?: (typeof VALIDATION_CATEGORIES)[number];

  @ValidateIf((o) => o.vote === 'YES' && !o.validationCategory)
  @IsString()
  @MinLength(20)
  @MaxLength(2000)
  whyList?: string;

  @ValidateIf((o) => o.vote === 'YES' && !o.validationCategory)
  @IsString()
  @MinLength(20)
  @MaxLength(2000)
  whyDoxxed?: string;

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  comment?: string;
}
