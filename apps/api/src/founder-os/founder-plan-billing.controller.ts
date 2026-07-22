import { BadRequestException, Body, Controller, Get, Post, Req } from '@nestjs/common';
import type { RawBodyRequest } from '@nestjs/common';
import type { Request } from 'express';
import { CurrentUser } from '../auth/current-user.decorator';
import type { AuthUser } from '../auth/auth.types';
import { Public } from '../auth/public.decorator';
import { FounderPlanBillingService } from './founder-plan-billing.service';
import { FounderPlanEntitlementsService } from './founder-plan-entitlements.service';

@Controller('founder-plans')
export class FounderPlanBillingController {
  constructor(
    private readonly billing: FounderPlanBillingService,
    private readonly entitlements: FounderPlanEntitlementsService,
  ) {}

  @Public()
  @Get()
  catalog() {
    return this.billing.catalog();
  }

  @Get('me')
  me(@CurrentUser() user: AuthUser) {
    return this.entitlements.resolve(user.id);
  }

  @Post('builder/checkout')
  createBuilderCheckout(@CurrentUser() user: AuthUser) {
    return this.billing.createBuilderCheckout(user.id);
  }

  @Post('portal')
  createPortal(@CurrentUser() user: AuthUser) {
    return this.billing.createPortal(user.id);
  }

  @Post('team/checkout')
  createTeamCheckout(@Body() _body: Record<string, never>) {
    throw new BadRequestException(
      'Team checkout is unavailable until its price and shared allowance are approved.',
    );
  }

  @Public()
  @Post('stripe/webhook')
  stripeWebhook(@Req() req: RawBodyRequest<Request>) {
    const signature = req.headers['stripe-signature'];
    return this.billing.handleWebhook(
      req.rawBody ?? Buffer.from(''),
      typeof signature === 'string' ? signature : undefined,
    );
  }
}
