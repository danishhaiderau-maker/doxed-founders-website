import {
  Body,
  Controller,
  Get,
  HttpCode,
  NotFoundException,
  Param,
  Post,
  Query,
} from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { Public } from '../auth/public.decorator';
import { TokenLaunchService } from './token-launch.service';
import { DexStubService } from './dex-stub.service';

class CommitDto {
  amount!: number;
}

class SwapDto {
  inputAmount!: number;
}

/**
 * Token Launch endpoints — the Phase 8 flagship revenue flow.
 *
 * Public read paths (so the Raise Room can show live launches to visitors):
 *   GET  /api/token-launch/:projectId/eligibility
 *   GET  /api/token-launch/:projectId/pledges
 *   GET  /api/token-launch/launch/:launchId
 *   GET  /api/token-launch/launch/:launchId/price
 *
 * Auth-required write paths:
 *   POST /api/token-launch/:projectId/initiate       (founder releases token)
 *   POST /api/token-launch/launch/:launchId/commit   (community member pledges)
 *   POST /api/token-launch/launch/:launchId/swap     (DEX swap, 0.1% fee)
 */
@SkipThrottle()
@Controller('token-launch')
export class TokenLaunchController {
  constructor(
    private readonly tokenLaunch: TokenLaunchService,
    private readonly dex: DexStubService,
  ) {}

  /** Launch eligibility + pledge progress for a project (the launch card surface). */
  @Public()
  @Get(':projectId/eligibility')
  eligibility(@Param('projectId') projectId: string) {
    return this.tokenLaunch.checkLaunchEligibility(projectId);
  }

  /** Top pledgers for a project with projected token allocation. */
  @Public()
  @Get(':projectId/pledges')
  pledges(
    @Param('projectId') projectId: string,
    @Query('limit') limit?: string,
  ) {
    const n = limit ? Number.parseInt(limit, 10) : 25;
    return this.tokenLaunch.getPledgeLeaderboard(
      projectId,
      Number.isFinite(n) ? n : 25,
    );
  }

  /** Full launch status (15-day window progress, mint address, allocations). */
  @Public()
  @Get('launch/:launchId')
  status(@Param('launchId') launchId: string) {
    return this.tokenLaunch.getLaunchStatus(launchId);
  }

  /** DEX stub price for a launch. */
  @Public()
  @Get('launch/:launchId/price')
  price(@Param('launchId') launchId: string) {
    return this.dex.getPrice(launchId);
  }

  /** DEX volume + fees for a launch. */
  @Public()
  @Get('launch/:launchId/volume')
  volume(@Param('launchId') launchId: string) {
    return this.dex.getVolume(launchId);
  }

  /** Founder releases the token (PLEDGING → WINDOW_OPEN + Solana devnet mint). */
  @Post(':projectId/initiate')
  @HttpCode(200)
  initiate(@Param('projectId') projectId: string) {
    return this.tokenLaunch.initiateLaunch(projectId);
  }

  /** Community member commits DDollar to a launch. */
  @Post('launch/:launchId/commit')
  @HttpCode(200)
  commit(
    @Param('launchId') launchId: string,
    @CurrentUser() user: AuthUser,
    @Body() body: CommitDto,
  ) {
    if (!body?.amount || typeof body.amount !== 'number') {
      throw new NotFoundException('amount required');
    }
    return this.tokenLaunch.commitToLaunch(launchId, user.id, body.amount);
  }

  /** DEX swap (LIVE launches only). 0.1% fee accrues to PlatformTreasury. */
  @Post('launch/:launchId/swap')
  @HttpCode(200)
  swap(
    @Param('launchId') launchId: string,
    @CurrentUser() user: AuthUser,
    @Body() body: SwapDto,
  ) {
    return this.dex.swap(launchId, user.id, body.inputAmount);
  }
}
