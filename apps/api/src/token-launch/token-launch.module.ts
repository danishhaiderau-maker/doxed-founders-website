import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { TokenLaunchController } from './token-launch.controller';
import { TokenLaunchService } from './token-launch.service';
import { TokenLaunchCron } from './token-launch.cron';
import { SolanaMintService } from './solana-mint.service';
import { DexStubService } from './dex-stub.service';

/**
 * Phase 8 — Raise Room → Token Launch module.
 *
 * The flagship revenue flow. Surfaces:
 *   - Eligibility + pledge leaderboard (public read)
 *   - Founder release (auth) — Solana devnet mint + 15-day window
 *   - Community commitment (auth) — DDollar escrow toward the 100K threshold
 *   - DEX stub swaps (auth) — 0.1% fee accrues to PlatformTreasury
 *
 * Depends only on PrismaModule + the in-module Solana/Dex services. No
 * application-code imports (kernel boundary respected).
 */
@Module({
  imports: [PrismaModule],
  controllers: [TokenLaunchController],
  providers: [
    TokenLaunchService,
    TokenLaunchCron,
    SolanaMintService,
    DexStubService,
  ],
  exports: [TokenLaunchService, DexStubService, SolanaMintService],
})
export class TokenLaunchModule {}
