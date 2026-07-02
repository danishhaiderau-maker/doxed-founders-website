import { IsEnum, IsInt, IsOptional, IsString, Max, MaxLength, Min, MinLength } from 'class-validator';

export class PostWallMessageDto {
  @IsString()
  @MinLength(1)
  @MaxLength(4000)
  body!: string;
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
