import { Body, Controller, ForbiddenException, Get, Param, Post, Query, Req, UseGuards } from '@nestjs/common';
import type { RawBodyRequest } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import type { Request } from 'express';
import { JwtAuthGuard } from '../auth/guards';
import { OptionalJwtAuthGuard } from '../auth/optional-jwt.guard';
import { AuthUser } from '../auth/auth.types';
import { Public } from '../auth/public.decorator';
import { PrismaService } from '../prisma/prisma.service';
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
import {
  assertPaperPortfolioAccess,
  readPaperSessionToken,
  redactPaperPortfolioEmail,
} from './paper-session.util';

type AuthedRequest = Request & { user?: AuthUser };

@SkipThrottle()
@Controller('paper-trading')
export class PaperTradingController {
  constructor(
    private readonly paperTrading: PaperTradingService,
    private readonly limitOrders: PaperLimitOrderService,
    private readonly stripe: PaperTradingStripeService,
    private readonly prisma: PrismaService,
  ) {}

  private async assertAccess(
    userId: string,
    req: AuthedRequest,
    sessionToken?: string,
  ): Promise<{ isOwner: boolean }> {
    const authUserId = req.user?.id;
    const token = sessionToken ?? readPaperSessionToken(req.headers as Record<string, unknown>);
    await assertPaperPortfolioAccess(this.prisma, userId, { sessionToken: token, authUserId });
    return { isOwner: Boolean(authUserId && authUserId === userId) };
  }

  @Public()
  @Post('session')
  createSession(@Body() dto: CreatePaperSessionDto) {
    return this.paperTrading.createSession(dto.displayName);
  }

  @Public()
  @Get('portfolio/:userId/public')
  getPublicPortfolio(
    @Param('userId') userId: string,
    @Query('includeOlder') includeOlder?: string,
  ) {
    return this.paperTrading.getPublicPortfolio(userId, {
      includeOlder: includeOlder === 'true' || includeOlder === '1',
    });
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get('portfolio/:userId')
  async getPortfolio(@Param('userId') userId: string, @Req() req: AuthedRequest) {
    const { isOwner } = await this.assertAccess(userId, req);
    const portfolio = await this.paperTrading.getPortfolio(userId);
    return redactPaperPortfolioEmail(portfolio, isOwner);
  }

  @Public()
  @Post('preview')
  preview(@Body() dto: PreviewPaperTradeDto) {
    return this.paperTrading.previewToken(dto.url);
  }

  @UseGuards(JwtAuthGuard)
  @Post('migrate-guest')
  migrateGuest(@Body() dto: MigrateGuestPortfolioDto, @Req() req: AuthedRequest) {
    if (req.user!.id !== dto.targetUserId) {
      throw new ForbiddenException('Cannot migrate to another user account');
    }
    return this.paperTrading.migrateGuestPortfolio(dto.guestUserId, dto.targetUserId);
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Post('trade')
  async trade(@Body() dto: PaperTradeDto, @Req() req: AuthedRequest) {
    await this.assertAccess(dto.userId, req);
    return this.paperTrading.executeTrade(dto);
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Post('close')
  async close(@Body() dto: ClosePaperPositionDto, @Req() req: AuthedRequest) {
    await this.assertAccess(dto.userId, req);
    return this.paperTrading.closePosition(dto.userId, dto.projectId, {
      comment: dto.comment,
      sellPercent: dto.sellPercent,
    });
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Post('swap')
  async swap(@Body() dto: SwapPaperTokensDto, @Req() req: AuthedRequest) {
    await this.assertAccess(dto.userId, req);
    return this.paperTrading.swapTokens(
      dto.userId,
      dto.fromProjectId,
      dto.toDexscreenerUrl,
      { comment: dto.comment },
    );
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Get('limit-orders/:userId')
  async listLimitOrders(@Param('userId') userId: string, @Req() req: AuthedRequest) {
    await this.assertAccess(userId, req);
    return this.limitOrders.list(userId);
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Post('limit-orders')
  async createLimitOrder(@Body() dto: CreatePaperLimitOrderDto, @Req() req: AuthedRequest) {
    await this.assertAccess(dto.userId, req);
    return this.limitOrders.create(dto);
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Post('limit-orders/:orderId/cancel')
  async cancelLimitOrder(
    @Param('orderId') orderId: string,
    @Body() dto: CancelPaperLimitOrderDto,
    @Req() req: AuthedRequest,
  ) {
    await this.assertAccess(dto.userId, req);
    return this.limitOrders.cancel(dto.userId, orderId);
  }

  @Public()
  @Get('leaderboard/missed-alpha')
  missedAlphaLeaderboard() {
    return this.paperTrading.getMissedAlphaLeaderboard();
  }

  @Public()
  @Get('leaderboard')
  leaderboard() {
    return this.paperTrading.getLeaderboard();
  }

  @Public()
  @Get('busted')
  busted() {
    return this.paperTrading.getBustedTraders();
  }

  @Public()
  @Get('reset-info')
  resetInfo() {
    return this.paperTrading.getResetInfo();
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Post('reset')
  async reset(@Body() dto: ResetPortfolioDto, @Req() req: AuthedRequest) {
    await this.assertAccess(dto.userId, req);
    return this.paperTrading.resetPortfolio(dto.userId);
  }

  @Public()
  @UseGuards(OptionalJwtAuthGuard)
  @Post('checkout/reset')
  async createResetCheckout(@Body() dto: ResetPortfolioDto, @Req() req: AuthedRequest) {
    await this.assertAccess(dto.userId, req);
    return this.stripe.createResetCheckout(dto.userId);
  }

  @Public()
  @Post('stripe/webhook')
  stripeWebhook(@Req() req: RawBodyRequest<Request>) {
    const signature = req.headers['stripe-signature'];
    return this.stripe.handleWebhook(
      req.rawBody ?? Buffer.from(''),
      typeof signature === 'string' ? signature : undefined,
    );
  }
}
