import { Body, Controller, Get, Param, Post, Query } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { Public } from '../auth/public.decorator';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { AddInitialCommentDto, CreateFeedCommentDto } from './dto/feed.dto';
import { FeedService } from './feed.service';
import { FeedShareService } from './feed-share.service';
import { UnifiedFeedService } from './unified-feed.service';
import { FeedTerminalService } from './feed-terminal.service';
import { FeedHubService } from './feed-hub.service';
import type { FeedTerminalTab, UnifiedFeedCategory } from '@dcf/utils';

@SkipThrottle()
@Controller('feed')
export class FeedController {
  constructor(
    private readonly feed: FeedService,
    private readonly unifiedFeed: UnifiedFeedService,
    private readonly feedShare: FeedShareService,
    private readonly feedTerminal: FeedTerminalService,
    private readonly feedHub: FeedHubService,
  ) {}

  @Public()
  @Get('hub')
  hub(
    @Query('category') category?: UnifiedFeedCategory,
    @Query('tab') tab?: FeedTerminalTab,
    @Query('project') projectSlug?: string,
    @Query('limit') limit?: string,
  ) {
    const parsedLimit = limit ? Math.min(100, Math.max(10, parseInt(limit, 10) || 50)) : 50;
    return this.feedHub.getHub(category ?? 'all', tab ?? 'all', projectSlug || undefined, parsedLimit);
  }

  @Public()
  @Get('terminal')
  terminal(
    @Query('tab') tab?: FeedTerminalTab,
    @Query('project') projectSlug?: string,
  ) {
    return this.feedTerminal.getTerminal(tab ?? 'all', projectSlug || undefined);
  }

  @Public()
  @Get('flashes')
  flashes(@Query('since') since?: string) {
    return this.unifiedFeed.getEngagementFlashes(since);
  }

  @Public()
  @Get('unified')
  unified(@Query('category') category?: 'all' | 'founder' | 'trading' | 'community' | 'market') {
    return this.unifiedFeed.getUnifiedFeed(category ?? 'all');
  }

  @Public()
  @Get('pulse')
  pulse() {
    return this.unifiedFeed.getPulse();
  }

  @Public()
  @Get()
  list(@Query('filter') filter?: 'recent' | 'discussed' | 'highlighted') {
    return this.feed.getFeed(filter ?? 'recent');
  }

  @Post('enrich-hot-buy-share')
  enrichHotBuyShare(
    @CurrentUser() user: AuthUser,
    @Body()
    body: {
      projectSlug: string;
      buyerNames?: string[];
      pctOfActive?: number;
      detailLine?: string;
    },
  ) {
    return this.feedShare.enrichHotBuyShare(user.id, body);
  }

  @Public()
  @Get(':id/comments')
  comments(@Param('id') id: string) {
    return this.feed.getComments(id);
  }

  @Public()
  @Post(':id/comments')
  addComment(@Param('id') id: string, @Body() dto: CreateFeedCommentDto) {
    return this.feed.addComment(id, dto);
  }

  @Public()
  @Post(':id/initial-comment')
  addInitialComment(@Param('id') id: string, @Body() dto: AddInitialCommentDto) {
    return this.feed.addInitialComment(id, dto);
  }
}
