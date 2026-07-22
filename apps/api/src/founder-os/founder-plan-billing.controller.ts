import { BadRequestException, Body, Controller, Delete, Get, Param, Patch, Post, Req } from '@nestjs/common';
import type { RawBodyRequest } from '@nestjs/common';
import type { Request } from 'express';
import { CurrentUser } from '../auth/current-user.decorator';
import type { AuthUser } from '../auth/auth.types';
import { Public } from '../auth/public.decorator';
import { FounderPlanBillingService } from './founder-plan-billing.service';
import { FounderPlanEntitlementsService } from './founder-plan-entitlements.service';
import { FounderPlanTeamService } from './founder-plan-team.service';

@Controller('founder-plans')
export class FounderPlanBillingController {
  constructor(
    private readonly billing: FounderPlanBillingService,
    private readonly entitlements: FounderPlanEntitlementsService,
    private readonly teams: FounderPlanTeamService,
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

  @Get('team')
  team(@CurrentUser() user: AuthUser) {
    return this.teams.overview(user.id);
  }

  @Post('team/members')
  addTeamMember(
    @CurrentUser() user: AuthUser,
    @Body() body: { email: string; role?: 'ADMIN' | 'MEMBER' },
  ) {
    return this.teams.addMember(user.id, body.email, body.role ?? 'MEMBER');
  }

  @Patch('team/members/:memberId')
  changeTeamRole(
    @CurrentUser() user: AuthUser,
    @Param('memberId') memberId: string,
    @Body() body: { role: 'ADMIN' | 'MEMBER' },
  ) {
    return this.teams.changeRole(user.id, memberId, body.role);
  }

  @Delete('team/members/:memberId')
  removeTeamMember(
    @CurrentUser() user: AuthUser,
    @Param('memberId') memberId: string,
  ) {
    return this.teams.removeMember(user.id, memberId);
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
