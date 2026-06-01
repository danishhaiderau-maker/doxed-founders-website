import { Body, Controller, Get, Param, Post, Query, Req } from '@nestjs/common';
import type { RawBodyRequest } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import type { Request } from 'express';
import { Public } from '../auth/public.decorator';
import {
  CancelPaperLimitOrderDto,
  ClosePaperPositionDto,
  CreatePaperLimitOrderDto,
  CreatePaperSessionDto,
  PaperTradeDto,
  PreviewPaperTradeDto,
  MigrateGuestPortfolioDto,
  ResetPortfolioDto,
  SwapPaperTokensDto,
} from './dto/paper-trading.dto';
import { PaperLimitOrderService } from './paper-limit-order.service';
import { PaperTradingStripeService } from './paper-trading-stripe.service';
import { PaperTradingService } from './paper-trading.service';

@Public()
@SkipThrottle()
@Controller('paper-trading')
export class PaperTradingController {
  constructor(
    private readonly paperTrading: PaperTradingService,
    private readonly limitOrders: PaperLimitOrderService,
    private readonly stripe: PaperTradingStripeService,
  ) {}

  @Post('session')
  createSession(@Body() dto: CreatePaperSessionDto) {
    return this.paperTrading.createSession(dto.displayName);
  }

  @Get('portfolio/:userId/public')
  getPublicPortfolio(
    @Param('userId') userId: string,
    @Query('includeOlder') includeOlder?: string,
  ) {
    return this.paperTrading.getPublicPortfolio(userId, {
      includeOlder: includeOlder === 'true' || includeOlder === '1',
    });
  }

  @Get('portfolio/:userId')
  getPortfolio(@Param('userId') userId: string) {
    return this.paperTrading.getPortfolio(userId);
  }

  @Post('preview')
  preview(@Body() dto: PreviewPaperTradeDto) {
    return this.paperTrading.previewToken(dto.url);
  }

  @Post('migrate-guest')
  migrateGuest(@Body() dto: MigrateGuestPortfolioDto) {
    return this.paperTrading.migrateGuestPortfolio(dto.guestUserId, dto.targetUserId);
  }

  @Post('trade')
  trade(@Body() dto: PaperTradeDto) {
    return this.paperTrading.executeTrade(dto);
  }

  @Post('close')
  close(@Body() dto: ClosePaperPositionDto) {
    return this.paperTrading.closePosition(dto.userId, dto.projectId, {
      comment: dto.comment,
      sellPercent: dto.sellPercent,
    });
  }

  @Post('swap')
  swap(@Body() dto: SwapPaperTokensDto) {
    return this.paperTrading.swapTokens(
      dto.userId,
      dto.fromProjectId,
      dto.toDexscreenerUrl,
      { comment: dto.comment },
    );
  }

  @Get('limit-orders/:userId')
  listLimitOrders(@Param('userId') userId: string) {
    return this.limitOrders.list(userId);
  }

  @Post('limit-orders')
  createLimitOrder(@Body() dto: CreatePaperLimitOrderDto) {
    return this.limitOrders.create(dto);
  }

  @Post('limit-orders/:orderId/cancel')
  cancelLimitOrder(
    @Param('orderId') orderId: string,
    @Body() dto: CancelPaperLimitOrderDto,
  ) {
    return this.limitOrders.cancel(dto.userId, orderId);
  }

  @Get('leaderboard/missed-alpha')
  missedAlphaLeaderboard() {
    return this.paperTrading.getMissedAlphaLeaderboard();
  }

  @Get('leaderboard')
  leaderboard() {
    return this.paperTrading.getLeaderboard();
  }

  @Get('busted')
  busted() {
    return this.paperTrading.getBustedTraders();
  }

  @Get('reset-info')
  resetInfo() {
    return this.paperTrading.getResetInfo();
  }

  @Post('reset')
  reset(@Body() dto: ResetPortfolioDto) {
    return this.paperTrading.resetPortfolio(dto.userId);
  }

  @Post('checkout/reset')
  createResetCheckout(@Body() dto: ResetPortfolioDto) {
    return this.stripe.createResetCheckout(dto.userId);
  }

  @Post('stripe/webhook')
  stripeWebhook(@Req() req: RawBodyRequest<Request>) {
    const signature = req.headers['stripe-signature'];
    return this.stripe.handleWebhook(
      req.rawBody ?? Buffer.from(''),
      typeof signature === 'string' ? signature : undefined,
    );
  }
}
