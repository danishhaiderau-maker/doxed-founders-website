import { Body, Controller, Get, Param, Post, Query } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { Public } from '../auth/public.decorator';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { FounderDenService } from './founder-den.service';
import {
  AllocateRaiseDto,
  CreateBuildPostDto,
  CreateFounderVideoDto,
  VotePollDto,
} from './dto/founder-den.dto';

@SkipThrottle()
@Controller('founder-den')
export class FounderDenController {
  constructor(private readonly founderDen: FounderDenService) {}

  @Public()
  @Get('videos/latest')
  latestVideos(@Query('limit') limit?: string) {
    return this.founderDen.getLatestVideos(limit ? Number(limit) : 12);
  }

  @Public()
  @Get('build-feed')
  buildFeed(@Query('limit') limit?: string) {
    return this.founderDen.getBuildFeed(limit ? Number(limit) : 40);
  }

  @Public()
  @Get('demand-heatmap')
  demandHeatmap() {
    return this.founderDen.getDemandHeatmap();
  }

  @Public()
  @Get('founders/:slug')
  founderRoom(@Param('slug') slug: string) {
    return this.founderDen.getFounderRoom(slug);
  }

  @Public()
  @Get('projects/:slug/room')
  projectRoom(@Param('slug') slug: string) {
    return this.founderDen.getProjectRoom(slug);
  }

  @Post('build-posts')
  createBuildPost(@CurrentUser() user: AuthUser, @Body() dto: CreateBuildPostDto) {
    return this.founderDen.createBuildPost(user.id, dto);
  }

  @Post('videos')
  addVideo(@CurrentUser() user: AuthUser, @Body() dto: CreateFounderVideoDto) {
    return this.founderDen.addVideo(user.id, dto);
  }

  @Post('raises/:raiseId/allocate')
  allocate(@CurrentUser() user: AuthUser, @Param('raiseId') raiseId: string, @Body() dto: AllocateRaiseDto) {
    return this.founderDen.allocateToRaise(user.id, raiseId, dto.amountUsd);
  }

  @Post('polls/:pollId/vote')
  votePoll(@CurrentUser() user: AuthUser, @Param('pollId') pollId: string, @Body() dto: VotePollDto) {
    return this.founderDen.votePoll(user.id, pollId, dto.optionKey);
  }
}
