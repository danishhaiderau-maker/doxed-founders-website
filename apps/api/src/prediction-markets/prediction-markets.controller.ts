import { Body, Controller, Get, Param, Post, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { PredictionMarketsService } from './prediction-markets.service';

@SkipThrottle()
@Controller('prediction-markets')
export class PredictionMarketsController {
  constructor(private readonly markets: PredictionMarketsService) {}

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get('hot')
  listHot(@CurrentUser() user?: AuthUser) {
    return this.markets.listHot(user?.id);
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get()
  listGlobal(@CurrentUser() user?: AuthUser) {
    return this.markets.listGlobal(user?.id);
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get('project/:slug')
  forProject(@Param('slug') slug: string, @CurrentUser() user?: AuthUser) {
    return this.markets.listForProject(slug, user?.id);
  }

  @Post()
  create(
    @CurrentUser() user: AuthUser,
    @Body() body: { projectSlug: string; question: string },
  ) {
    return this.markets.createMarket(user.id, body);
  }

  @Post(':marketId/stake')
  stake(
    @CurrentUser() user: AuthUser,
    @Param('marketId') marketId: string,
    @Body() body: { side: 'YES' | 'NO'; amountUsd: number },
  ) {
    return this.markets.stake(user.id, marketId, body.side, body.amountUsd);
  }
}
