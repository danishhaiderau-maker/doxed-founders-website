import {
  IsEnum,
  IsNumber,
  IsOptional,
  IsString,
  MaxLength,
  Min,
  MinLength,
} from 'class-validator';
import { Type } from 'class-transformer';
import { FounderVideoType, ProjectLifecycleStage } from '@prisma/client';

export class CreateBuildPostDto {
  @IsString()
  @MinLength(3)
  @MaxLength(200)
  headline!: string;

  @IsString()
  @MinLength(10)
  @MaxLength(5000)
  body!: string;

  @IsOptional()
  @IsString()
  projectId?: string;

  @IsOptional()
  @Type(() => Number)
  @IsNumber()
  dayNumber?: number;

  @IsOptional()
  @IsString()
  githubUrl?: string;
}

export class CreateFounderVideoDto {
  @IsString()
  @MinLength(3)
  @MaxLength(200)
  title!: string;

  @IsString()
  @MinLength(10)
  url!: string;

  @IsEnum(FounderVideoType)
  type!: FounderVideoType;

  @IsOptional()
  @IsString()
  projectId?: string;

  @IsOptional()
  @Type(() => Number)
  @IsNumber()
  @Min(1)
  durationMin?: number;
}

export class AllocateRaiseDto {
  @Type(() => Number)
  @IsNumber()
  @Min(1)
  amountUsd!: number;
}

export class VotePollDto {
  @IsString()
  @MinLength(1)
  optionKey!: string;
}

export class FounderApplicationDto {
  @IsString()
  @MinLength(2)
  @MaxLength(120)
  projectName!: string;

  @IsOptional()
  @IsString()
  websiteUrl?: string;

  @IsOptional()
  @IsString()
  twitterHandle?: string;

  @IsOptional()
  @IsString()
  githubUrl?: string;

  @IsOptional()
  @IsString()
  videoUrl?: string;

  @IsString()
  @MinLength(20)
  @MaxLength(5000)
  ideaDescription!: string;

  @IsEnum(ProjectLifecycleStage)
  lifecycleStage!: ProjectLifecycleStage;
}

export class CreateSimulatedRaiseDto {
  @IsString()
  projectId!: string;

  @Type(() => Number)
  @IsNumber()
  @Min(1000)
  goalUsd!: number;

  @Type(() => Number)
  @IsNumber()
  @Min(7)
  durationDays!: number;

  @IsOptional()
  @IsString()
  tokenAllocation?: string;

  @IsOptional()
  @IsString()
  plannedLaunchDate?: string;
}

export class ListTokenDto {
  @IsString()
  contractAddress!: string;

  @IsString()
  chainSlug!: string;

  @IsOptional()
  @IsString()
  websiteUrl?: string;

  @IsOptional()
  @IsString()
  dexscreenerUrl?: string;
}

export class CommunityThreadDto {
  @IsString()
  channel!: string;

  @IsString()
  @MinLength(3)
  @MaxLength(200)
  title!: string;

  @IsString()
  @MinLength(3)
  @MaxLength(5000)
  body!: string;
}

export class CommunityCommentDto {
  @IsString()
  @MinLength(1)
  @MaxLength(3000)
  body!: string;

  @IsOptional()
  @IsString()
  parentId?: string;
}
