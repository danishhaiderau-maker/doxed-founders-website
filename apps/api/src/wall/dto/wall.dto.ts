import { IsEnum, IsInt, IsOptional, IsString, Max, MaxLength, Min, MinLength } from 'class-validator';

export class PostWallMessageDto {
  @IsString()
  @MinLength(1)
  @MaxLength(4000)
  body!: string;

  @IsOptional()
  @IsString()
  replyToId?: string;
}

export class PinWallMessageDto {
  @IsOptional()
  @IsEnum(['pin', 'highlight', 'promote'])
  kind?: 'pin' | 'highlight' | 'promote';

  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(100000)
  amount?: number;
}

export class WallReactDto {
  @IsString()
  @MinLength(1)
  @MaxLength(8)
  emoji!: string;
}

export class ReportWallMessageDto {
  @IsString()
  @MinLength(3)
  @MaxLength(500)
  reason!: string;
}

export class MuteWallUserDto {
  @IsString()
  @MinLength(1)
  userId!: string;

  @IsOptional()
  @IsInt()
  @Min(1)
  @Max(168)
  hours?: number;

  @IsOptional()
  @IsString()
  @MaxLength(280)
  reason?: string;
}

export class UpdateWallSettingsDto {
  @IsOptional()
  @IsEnum(['OPEN', 'ANNOUNCEMENTS'])
  postingMode?: 'OPEN' | 'ANNOUNCEMENTS';

  @IsOptional()
  @IsInt()
  @Min(0)
  @Max(3600)
  slowModeSeconds?: number;
}
