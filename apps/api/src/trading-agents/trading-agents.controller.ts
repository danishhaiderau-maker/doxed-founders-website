import { Controller, Delete, Get, Param, Post, Query, UseGuards } from '@nestjs/common';
import { TradingAgentKind } from '@prisma/client';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { TradingAgentsService } from './trading-agents.service';

@Controller('trading-agents')
export class TradingAgentsController {
  constructor(private readonly tradingAgents: TradingAgentsService) {}

  @Public()
  @Get()
  list(@Query('kind') kind?: TradingAgentKind) {
    return this.tradingAgents.list(kind);
  }

  @Public()
  @Get('leaderboard')
  leaderboard() {
    return this.tradingAgents.leaderboard();
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get(':slug')
  getBySlug(@Param('slug') slug: string, @CurrentUser() user?: AuthUser) {
    return this.tradingAgents.getBySlug(slug, user?.id);
  }

  @Public()
  @Get(':slug/dashboard')
  dashboard(@Param('slug') slug: string) {
    return this.tradingAgents.getDashboard(slug);
  }

  @Public()
  @Get(':slug/activity')
  activity(@Param('slug') slug: string, @Query('limit') limit?: string) {
    const n = limit ? Number(limit) : 30;
    return this.tradingAgents.listActivity(slug, Number.isFinite(n) ? n : 30);
  }

  @Post(':id/follow')
  follow(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.tradingAgents.follow(user.id, id);
  }

  @Delete(':id/follow')
  unfollow(@CurrentUser() user: AuthUser, @Param('id') id: string) {
    return this.tradingAgents.unfollow(user.id, id);
  }
}
