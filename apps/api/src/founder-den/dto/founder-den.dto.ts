import { IsEnum, IsNumber, IsOptional, IsString, MaxLength, Min, MinLength } from 'class-validator';
import { Type } from 'class-transformer';
import { FounderVideoType } from '@prisma/client';

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
