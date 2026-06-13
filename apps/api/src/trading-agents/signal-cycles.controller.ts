import {
  Body,
  Controller,
  Delete,
  Get,
  Param,
  Post,
  Query,
  Req,
  UnauthorizedException,
  UseGuards,
} from '@nestjs/common';
import type { SignalCycleEventType } from '@dcf/utils';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { Public } from '../auth/public.decorator';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { JwtAuthGuard } from '../auth/guards';
import { SIGNAL_API_KEY_HEADER, SignalApiKeyGuard } from './signal-api-key.guard';
import { SignalCyclesService } from './signal-cycles.service';

type SignalReq = {
  signalApiKey?: { userId: string; agentId: string; keyId: string };
};

@Controller('trading-agents/:slug/signals')
export class SignalCyclesController {
  constructor(private readonly cycles: SignalCyclesService) {}

  @Public()
  @Get('mandate')
  mandate() {
    return this.cycles.getSubscriberMandate();
  }

  @Public()
  @UseGuards(SignalApiKeyGuard)
  @Get('latest')
  latest(@Param('slug') slug: string, @Req() req: SignalReq) {
    return this.cycles.getLatest(slug, req.signalApiKey ?? null);
  }

  @Public()
  @UseGuards(SignalApiKeyGuard, OptionalJwtAuthGuard)
  @Get('cycles')
  listCycles(
    @Param('slug') slug: string,
    @CurrentUser() user: AuthUser | undefined,
    @Req() req: SignalReq,
    @Query('limit') limit?: string,
  ) {
    const userId = user?.id ?? req.signalApiKey?.userId;
    if (!userId) throw new UnauthorizedException('JWT or X-Signal-Api-Key required');
    const n = limit ? Number(limit) : 20;
    return this.cycles.listCycles(slug, userId, Number.isFinite(n) ? n : 20);
  }

  @Public()
  @UseGuards(SignalApiKeyGuard, OptionalJwtAuthGuard)
  @Get('cycles/:cycleId')
  getCycle(
    @Param('slug') slug: string,
    @Param('cycleId') cycleId: string,
    @CurrentUser() user: AuthUser | undefined,
    @Req() req: SignalReq,
  ) {
    const userId = user?.id ?? req.signalApiKey?.userId;
    if (!userId) throw new UnauthorizedException('JWT or X-Signal-Api-Key required');
    return this.cycles.getCycle(slug, cycleId, userId);
  }

  @Public()
  @UseGuards(SignalApiKeyGuard)
  @Post('cycles/:cycleId/events')
  postEvent(
    @Param('slug') slug: string,
    @Param('cycleId') cycleId: string,
    @Req() req: SignalReq,
    @Body()
    body: {
      event: SignalCycleEventType;
      venue?: string;
      local_mark_at_signal?: number;
      limit_price?: number;
      fill_price?: number;
      exit_price?: number;
      qty?: number;
      pnl_usd?: number;
      pnl_margin_pct?: number;
      stop_loss_placed?: boolean;
      stop_loss_margin_pct?: number;
      exit_reason?: string;
    },
  ) {
    const ctx = this.cycles.requireApiKey(req.signalApiKey ?? null);
    return this.cycles.postEvent(slug, cycleId, ctx, body);
  }

  @Public()
  @UseGuards(SignalApiKeyGuard)
  @Post('cycles/:cycleId/settle')
  settleFee(
    @Param('slug') slug: string,
    @Param('cycleId') cycleId: string,
    @Req() req: SignalReq,
    @Body() body: { tx_signature: string },
  ) {
    const ctx = this.cycles.requireApiKey(req.signalApiKey ?? null);
    return this.cycles.confirmSolanaFeePayment(slug, cycleId, ctx, body.tx_signature);
  }

  @UseGuards(JwtAuthGuard)
  @Post('api-keys')
  createKey(
    @Param('slug') slug: string,
    @CurrentUser() user: AuthUser,
    @Body() body: { label?: string },
  ) {
    return this.cycles.createApiKey(user.id, slug, body.label);
  }

  @UseGuards(JwtAuthGuard)
  @Get('api-keys')
  listKeys(@Param('slug') slug: string, @CurrentUser() user: AuthUser) {
    return this.cycles.listApiKeys(user.id, slug);
  }

  @UseGuards(JwtAuthGuard)
  @Delete('api-keys/:keyId')
  revokeKey(
    @Param('slug') slug: string,
    @Param('keyId') keyId: string,
    @CurrentUser() user: AuthUser,
  ) {
    return this.cycles.revokeApiKey(user.id, slug, keyId);
  }
}

export { SIGNAL_API_KEY_HEADER };
