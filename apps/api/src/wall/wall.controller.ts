import { Body, Controller, Get, HttpException, HttpStatus, Param, Post, Query, UseGuards } from '@nestjs/common';
import { Public } from '../auth/public.decorator';
import { CurrentUser } from '../auth/current-user.decorator';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { AuthUser } from '../auth/auth.types';
import { WallService } from './wall.service';
import { PinWallMessageDto, PostWallMessageDto } from './dto/wall.dto';
import { RateLimiterService } from '../events/rate-limiter.service';

@Controller('wall')
export class WallController {
  constructor(
    private readonly wall: WallService,
    private readonly rateLimiter: RateLimiterService,
  ) {}

  private async enforceLimit(userId: string, endpoint: string): Promise<void> {
    const rateCheck = await this.rateLimiter.checkLimit(userId, endpoint);
    if (!rateCheck.allowed) {
      throw new HttpException(
        {
          statusCode: HttpStatus.TOO_MANY_REQUESTS,
          message: `Rate limit: ${rateCheck.reason}. Try again in ${Math.ceil(rateCheck.resetInMs / 1000)}s`,
          resetInMs: rateCheck.resetInMs,
        },
        HttpStatus.TOO_MANY_REQUESTS,
      );
    }
  }

  /** Public wall for a single project — Telegram-style message stream (cursor-paginated). */
  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get('projects/:slug/messages')
  messages(
    @Param('slug') slug: string,
    @Query('before') before?: string,
    @Query('limit') limit?: string,
  ) {
    const cursor = before ? new Date(before) : undefined;
    void limit; // page size fixed at MESSAGE_PAGE_LIMIT in the service for now
    return this.wall.listMessages(slug, cursor);
  }

  /** Membership + summarizer-eligibility probe for the current viewer. */
  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get('projects/:slug/membership')
  membership(@Param('slug') slug: string, @CurrentUser() user?: AuthUser) {
    return this.wall.getMembership(slug, user?.id);
  }

  /** Public: latest cached Chat Summarizer output + subscription state. */
  @Public()
  @Get('projects/:slug/summary')
  summary(@Param('slug') slug: string) {
    return this.wall.getSummary(slug);
  }

  /** Activate (or renew) the Chat Summarizer — spends 1,000 DDollar for a 30-day window. */
  @Post('projects/:slug/summarize')
  async summarize(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    await this.enforceLimit(user.id, 'wall:summarize');
    return this.wall.activateSummarizer(user.id, slug);
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

  /** Per-project unread counts + total for the current user (header badge + drawer rail). */
  @Get('me/unread')
  unread(@CurrentUser() user: AuthUser) {
    return this.wall.getUnread(user.id);
  }

  /** Mark a project as read up to now for the current user (clears its unread badge). */
  @Post('me/read/:slug')
  markRead(@CurrentUser() user: AuthUser, @Param('slug') slug: string) {
    return this.wall.markRead(user.id, slug);
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
  async pin(
    @CurrentUser() user: AuthUser,
    @Param('messageId') messageId: string,
    @Body() dto: PinWallMessageDto,
  ) {
    await this.enforceLimit(user.id, 'wall:pin');
    return this.wall.pinMessage(user.id, messageId, dto.kind ?? 'pin', dto.amount);
  }
}
