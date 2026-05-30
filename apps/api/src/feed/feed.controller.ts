import { Body, Controller, Get, Param, Post, Query } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { Public } from '../auth/public.decorator';
import { AddInitialCommentDto, CreateFeedCommentDto } from './dto/feed.dto';
import { FeedService } from './feed.service';
import { UnifiedFeedService } from './unified-feed.service';

@Public()
@SkipThrottle()
@Controller('feed')
export class FeedController {
  constructor(
    private readonly feed: FeedService,
    private readonly unifiedFeed: UnifiedFeedService,
  ) {}

  @Get('unified')
  unified(@Query('category') category?: 'all' | 'founder' | 'trading' | 'community' | 'market') {
    return this.unifiedFeed.getUnifiedFeed(category ?? 'all');
  }

  @Get('pulse')
  pulse() {
    return this.unifiedFeed.getPulse();
  }

  @Get()
  list(@Query('filter') filter?: 'recent' | 'discussed' | 'highlighted') {
    return this.feed.getFeed(filter ?? 'recent');
  }

  @Get(':id/comments')
  comments(@Param('id') id: string) {
    return this.feed.getComments(id);
  }

  @Post(':id/comments')
  addComment(@Param('id') id: string, @Body() dto: CreateFeedCommentDto) {
    return this.feed.addComment(id, dto);
  }

  @Post(':id/initial-comment')
  addInitialComment(@Param('id') id: string, @Body() dto: AddInitialCommentDto) {
    return this.feed.addInitialComment(id, dto);
  }
}
