import { IsOptional, IsString, MaxLength, MinLength } from 'class-validator';

export class ParaphraseShareDto {
  @IsString()
  @MinLength(1)
  @MaxLength(4000)
  text!: string;

  @IsOptional()
  @IsString()
  @MaxLength(120)
  projectName?: string;

  @IsOptional()
  @IsString()
  @MaxLength(32)
  ticker?: string;

  @IsOptional()
  @IsString()
  @MaxLength(120)
  slug?: string;
}
