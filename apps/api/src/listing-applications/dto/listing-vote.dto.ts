import {
  IsIn,
  IsOptional,
  IsString,
  MaxLength,
  MinLength,
  ValidateIf,
} from 'class-validator';

export class CastListingVoteDto {
  @IsIn(['YES', 'NO'])
  vote!: 'YES' | 'NO';

  @ValidateIf((o) => o.vote === 'YES')
  @IsString()
  @MinLength(20)
  @MaxLength(2000)
  whyList?: string;

  @ValidateIf((o) => o.vote === 'YES')
  @IsString()
  @MinLength(20)
  @MaxLength(2000)
  whyDoxxed?: string;

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  comment?: string;
}
