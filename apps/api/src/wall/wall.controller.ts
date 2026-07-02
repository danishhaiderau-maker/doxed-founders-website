import { Body, Controller, Get, Param, Post, Query, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { Public } from '../auth/public.decorator';
import { CurrentUser } from '../auth/current-user.decorator';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { AuthUser } from '../auth/auth.types';
import { WallService } from './wall.service';
import { PinWallMessageDto, PostWallMessageDto } from './dto/wall.dto';

@SkipThrottle()
@Controller('wall')
export class WallController {
  constructor(private readonly wall: WallService) {}

  /** Public wall for a single project — Telegram-style message stream. */
  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get('projects/:slug/messages')
  messages(@Param('slug') slug: string) {
    return this.wall.listMessages(slug);
  }

  /** Projects the current user has joined (followed) — for the Founder Chat drawer. */
  @Get('me/groups')
  myGroups(@CurrentUser() user: AuthUser) {
    return this.wall.listMyGroups(user.id);
  }

  /** Aggregated wall across every joined project — the unified "All" feed. */
  @Get('me/aggregated')
  aggregated(@CurrentUser() user: AuthUser, @Query('limit') limit?: string) {
    const n = limit ? Number.parseInt(limit, 10) : 60;
    return this.wall.listAggregated(user.id, Number.isFinite(n) ? n : 60);
  }

  /** Join a project wall (follows the project). Idempotent. */
  @Post('projects/:slug/join')
  join(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.wall.joinProject(user.id, slug);
  }

  /** Post a chat message to a project wall. */
  @Post('projects/:slug/messages')
  post(
    @CurrentUser() user: AuthUser,
    @Param('slug') slug: string,
    @Body() dto: PostWallMessageDto,
  ) {
    return this.wall.postMessage(user.id, slug, dto.body);
  }

  /** Upgrade a subtopic (pin / highlight / promote) — spends DDollar. */
  @Post('messages/:messageId/pin')
  pin(
    @CurrentUser() user: AuthUser,
    @Param('messageId') messageId: string,
    @Body() dto: PinWallMessageDto,
  ) {
    return this.wall.pinMessage(user.id, messageId, dto.kind ?? 'pin', dto.amount);
  }
}
