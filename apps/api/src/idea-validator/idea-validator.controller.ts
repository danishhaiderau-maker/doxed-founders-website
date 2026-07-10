import {
  Body,
  Controller,
  Get,
  HttpCode,
  NotFoundException,
  Param,
  Patch,
  Post,
  Query,
  ServiceUnavailableException,
} from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { IdeaValidatorService } from './idea-validator.service';
import { CheckIdeaDto, PatchIdeaCheckDto } from './dto/idea-validator.dto';
import { ideaValidatorEnabled } from './idea-validator.module';

/**
 * Founder Idea Validator — the Phase 6 application that turns an idea
 * description into a competitive-landscape report using the Browser Use
 * LAM research hand. See docs/FOUNDER-IDEA-VALIDATOR.md.
 *
 * All endpoints are JWT-authed (the global JwtAuthGuard enforces it).
 * The module is gated behind IDEA_VALIDATOR_ENABLED (see .module.ts) —
 * when off, the controller still mounts so the API boots, but the
 * check endpoint returns 503 so prod can disable without redeploy.
 */
@Controller('idea-validator')
export class IdeaValidatorController {
  constructor(
    private readonly ideaValidator: IdeaValidatorService,
    private readonly config: ConfigService,
  ) {}

  /**
   * POST /idea-validator/check — kick off (or reuse) a check.
   * Returns the IdeaCheck row; status PENDING/RUNNING means the client
   * should poll GET /idea-validator/check/:id until status is COMPLETED
   * or FAILED.
   */
  @Post('check')
  @HttpCode(202)
  async check(@Body() dto: CheckIdeaDto, @CurrentUser() user: AuthUser) {
    if (!ideaValidatorEnabled(this.config)) {
      throw new ServiceUnavailableException('Idea Validator is disabled (IDEA_VALIDATOR_ENABLED=false).');
    }
    const row = await this.ideaValidator.checkIdea(
      { userId: user.id, nodeId: 'api' },
      {
        ideaText: dto.ideaText,
        projectId: dto.projectId,
        applicationId: dto.applicationId,
        force: dto.force,
      },
    );
    return row;
  }

  /**
   * GET /idea-validator/checks — list the user's recent checks.
   */
  @Get('checks')
  async list(@CurrentUser() user: AuthUser, @Query('limit') limit?: string) {
    const n = limit ? Number.parseInt(limit, 10) : 20;
    return this.ideaValidator.listChecks(user.id, Number.isFinite(n) ? n : 20);
  }

  /**
   * GET /idea-validator/check/:id — full check including resultJson.
   */
  @Get('check/:id')
  async getOne(@Param('id') id: string, @CurrentUser() user: AuthUser) {
    const row = await this.ideaValidator.getCheck(id, user.id);
    if (!row) throw new NotFoundException(`Idea check ${id} not found`);
    return row;
  }

  /**
   * GET /idea-validator/latest-for-user — most recent COMPLETED check,
   * for the daily proactive pop-up. The frontend polls this once on
   * load; if it returns a row that's unviewed, the pop-up appears.
   */
  @Get('latest-for-user')
  async latestForUser(@CurrentUser() user: AuthUser) {
    const row = await this.ideaValidator.latestCompletedForUser(user.id);
    return row ?? null;
  }

  /**
   * PATCH /idea-validator/check/:id — dismiss or mark-viewed.
   * Used by the pop-up ("dismiss") and the result panel ("viewed").
   */
  @Patch('check/:id')
  async patch(
    @Param('id') id: string,
    @Body() dto: PatchIdeaCheckDto,
    @CurrentUser() user: AuthUser,
  ) {
    const row = await this.ideaValidator.patchCheck(id, user.id, dto);
    if (!row) throw new NotFoundException(`Idea check ${id} not found`);
    return row;
  }

  /**
   * POST /idea-validator/cron/daily-pop-up — manual trigger for the daily
   * proactive pop-up sweep (Part C). The @Cron job in the service runs
   * this automatically; this endpoint exists so it can be triggered
   * manually during the thin-slice phase and from admin tooling.
   */
  @Post('cron/daily-pop-up')
  async runDailyPopUp() {
    const userIds = await this.ideaValidator.usersWithUnviewedCompletedChecks();
    return { triggeredFor: userIds.length, userIds };
  }
}
