import { Module } from '@nestjs/common';
import { PrismaModule } from '../prisma/prisma.module';
import { FounderEconomicsModule } from '../founder-economics/founder-economics.module';
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
 *   - On LIVE finalization → PRODUCT_LAUNCH_VIA_RAISE_ROOM DDollar grant
 *
 * Imports FounderEconomicsModule for the LIVE→DDollar grant path only.
 */
@Module({
  imports: [PrismaModule, FounderEconomicsModule],
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