import { Body, Controller, Get, Param, Post, Req } from '@nestjs/common';
import type { RawBodyRequest } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import type { Request } from 'express';
import { Public } from '../auth/public.decorator';
import {
  CreatePaperSessionDto,
  PaperTradeDto,
  PreviewPaperTradeDto,
  MigrateGuestPortfolioDto,
  ResetPortfolioDto,
} from './dto/paper-trading.dto';
import { PaperTradingStripeService } from './paper-trading-stripe.service';
import { PaperTradingService } from './paper-trading.service';

@Public()
@SkipThrottle()
@Controller('paper-trading')
export class PaperTradingController {
  constructor(
    private readonly paperTrading: PaperTradingService,
    private readonly stripe: PaperTradingStripeService,
  ) {}

  @Post('session')
  createSession(@Body() dto: CreatePaperSessionDto) {
    return this.paperTrading.createSession(dto.displayName);
  }

  @Get('portfolio/:userId/public')
  getPublicPortfolio(@Param('userId') userId: string) {
    return this.paperTrading.getPublicPortfolio(userId);
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
