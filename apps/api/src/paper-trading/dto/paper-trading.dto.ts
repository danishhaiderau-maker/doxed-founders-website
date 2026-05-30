import {
  IsEnum,
  IsIn,
  IsNumber,
  IsOptional,
  IsString,
  Max,
  MaxLength,
  Min,
  MinLength,
} from 'class-validator';
import { Type } from 'class-transformer';
import { PaperTradeSide } from '@prisma/client';

export class CreatePaperSessionDto {
  @IsOptional()
  @IsString()
  @MinLength(2)
  displayName?: string;
}

export class PaperTradeDto {
  @IsString()
  userId!: string;

  @IsString()
  @MinLength(10)
  dexscreenerUrl!: string;

  @IsEnum(PaperTradeSide)
  side!: PaperTradeSide;

  @Type(() => Number)
  @IsNumber()
  @Min(1)
  @Max(100000)
  amountUsd!: number;

  @IsOptional()
  @IsString()
  @MaxLength(2000)
  comment?: string;

  @IsOptional()
  @IsString()
  @MaxLength(280)
  catalyst?: string;

  @IsOptional()
  @Type(() => Number)
  @IsNumber()
  @Min(0)
  targetUsd?: number;

  @IsOptional()
  @IsString()
  @MaxLength(80)
  timeHorizon?: string;
}

export class PreviewPaperTradeDto {
  @IsString()
  @MinLength(10)
  url!: string;
}

export class MigrateGuestPortfolioDto {
  @IsString()
  guestUserId!: string;

  @IsString()
  targetUserId!: string;
}

export class ResetPortfolioDto {
  @IsString()
  userId!: string;
}

export class CreateCryptoTopUpDto {
  @IsOptional()
  @IsIn(['USDC', 'SOL'])
  asset?: 'USDC' | 'SOL';
}

export class ConfirmCryptoTopUpDto {
  @IsString()
  paymentId!: string;

  @IsString()
  @MinLength(32)
  txSignature!: string;
}
