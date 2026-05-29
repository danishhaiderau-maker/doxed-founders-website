import { Body, Controller, Get, Param, Post, Query, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { Public } from '../auth/public.decorator';
import { CurrentUser } from '../auth/current-user.decorator';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { AuthUser } from '../auth/auth.types';
import { FounderDenService } from './founder-den.service';
import {
  AllocateRaiseDto,
  CommunityCommentDto,
  CommunityThreadDto,
  CreateBuildPostDto,
  CreateFounderVideoDto,
  CreateSimulatedRaiseDto,
  FounderApplicationDto,
  ListTokenDto,
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
  @Get('discover')
  discover(@Query('filter') filter?: string, @Query('stageBucket') stageBucket?: string) {
    return this.founderDen.getDiscover(filter, stageBucket);
  }

  @Public()
  @Get('ecosystem/pulse')
  ecosystemPulse() {
    return this.founderDen.getEcosystemPulse();
  }

  @Public()
  @Get('economy/stats')
  economyStats() {
    return this.founderDen.getEconomyStats();
  }

  @Public()
  @Get('founders/:slug')
  founderRoom(@Param('slug') slug: string) {
    return this.founderDen.getFounderRoom(slug);
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get('projects/:slug/room')
  projectRoom(@Param('slug') slug: string, @CurrentUser() user?: AuthUser) {
    return this.founderDen.getProjectRoom(slug, user?.id);
  }

  @Get('dashboard')
  dashboard(@CurrentUser() user: AuthUser) {
    return this.founderDen.getDashboard(user.id);
  }

  @Post('founder-application')
  founderApplication(@CurrentUser() user: AuthUser, @Body() dto: FounderApplicationDto) {
    return this.founderDen.submitFounderApplication(user.id, dto);
  }

  @Post('simulated-raises')
  createRaise(@CurrentUser() user: AuthUser, @Body() dto: CreateSimulatedRaiseDto) {
    return this.founderDen.createSimulatedRaise(user.id, dto.projectId, dto);
  }

  @Post('projects/:projectId/launchpad-request')
  launchpadRequest(@CurrentUser() user: AuthUser, @Param('projectId') projectId: string) {
    return this.founderDen.requestLaunchpadAccess(user.id, projectId);
  }

  @Post('projects/:projectId/list-token')
  listToken(
    @CurrentUser() user: AuthUser,
    @Param('projectId') projectId: string,
    @Body() dto: ListTokenDto,
  ) {
    return this.founderDen.listToken(user.id, projectId, dto);
  }

  @Post('projects/:projectId/follow')
  follow(@CurrentUser() user: AuthUser, @Param('projectId') projectId: string) {
    return this.founderDen.followProject(user.id, projectId);
  }

  @Post('projects/:projectId/unfollow')
  unfollow(@CurrentUser() user: AuthUser, @Param('projectId') projectId: string) {
    return this.founderDen.unfollowProject(user.id, projectId);
  }

  @Post('projects/:projectId/threads')
  createThread(
    @CurrentUser() user: AuthUser,
    @Param('projectId') projectId: string,
    @Body() dto: CommunityThreadDto,
  ) {
    return this.founderDen.createCommunityThread(user.id, projectId, dto);
  }

  @Public()
  @Get('threads/:threadId')
  getThread(@Param('threadId') threadId: string) {
    return this.founderDen.getCommunityThread(threadId);
  }

  @Post('threads/:threadId/comments')
  addComment(
    @CurrentUser() user: AuthUser,
    @Param('threadId') threadId: string,
    @Body() dto: CommunityCommentDto,
  ) {
    return this.founderDen.addCommunityComment(user.id, threadId, dto);
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
