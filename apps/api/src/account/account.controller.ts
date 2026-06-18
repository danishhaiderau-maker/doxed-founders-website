import { Body, Controller, Delete, Get, Param, Post, Put, Query } from '@nestjs/common';
import type { NotificationPreferenceGroups } from '@dcf/utils';
import { AuthUser } from '../auth/auth.types';
import { CurrentUser } from '../auth/current-user.decorator';
import { AccountService } from './account.service';
import { PlatformHandleService } from './platform-handle.service';
import { ReferralService } from './referral.service';
import { FounderPromoService } from '../founder-os/founder-promo.service';

@Controller('account')
export class AccountController {
  constructor(
    private readonly account: AccountService,
    private readonly platformHandles: PlatformHandleService,
    private readonly referrals: ReferralService,
    private readonly founderPromoService: FounderPromoService,
  ) {}

  @Get('overview')
  overview(@CurrentUser() user: AuthUser) {
    return this.account.getOverview(user.id);
  }

  @Get('point-ledger')
  pointLedger(@CurrentUser() user: AuthUser, @Query('limit') limit?: string) {
    const parsed = limit ? Math.min(100, Math.max(1, parseInt(limit, 10) || 50)) : 50;
    return this.account.getPointLedger(user.id, parsed);
  }

  @Get('notification-preferences')
  notificationPreferences(@CurrentUser() user: AuthUser) {
    return this.account.getNotificationPreferences(user.id);
  }

  @Put('notification-preferences')
  updateNotificationPreferences(
    @CurrentUser() user: AuthUser,
    @Body() body: Partial<NotificationPreferenceGroups>,
  ) {
    return this.account.updateNotificationPreferences(user.id, body);
  }

  @Get('following')
  following(@CurrentUser() user: AuthUser) {
    return this.account.listFollowing(user.id);
  }

  @Post('follow/:userId')
  follow(@CurrentUser() user: AuthUser, @Param('userId') targetId: string) {
    return this.account.followUser(user.id, targetId);
  }

  @Delete('follow/:userId')
  unfollow(@CurrentUser() user: AuthUser, @Param('userId') targetId: string) {
    return this.account.unfollowUser(user.id, targetId);
  }

  @Get('activity')
  activity(@CurrentUser() user: AuthUser, @Query('limit') limit?: string) {
    const parsed = limit ? Math.min(100, Math.max(1, parseInt(limit, 10) || 40)) : 40;
    return this.account.getActivityHistory(user.id, parsed);
  }

  @Get('founder-promo')
  founderPromo(@CurrentUser() user: AuthUser) {
    return this.founderPromoService.getUserPromoStatus(user.id);
  }

  @Put('platform-handle')
  updatePlatformHandle(
    @CurrentUser() user: AuthUser,
    @Body() body: { platformHandle: string },
  ) {
    return this.platformHandles.updateHandle(user.id, body.platformHandle);
  }

  @Get('referral')
  referral(@CurrentUser() user: AuthUser) {
    return this.referrals.getSummary(user.id);
  }

  @Post('referral/claim')
  claimReferral(
    @CurrentUser() user: AuthUser,
    @Body() body: { referralCode: string },
  ) {
    return this.referrals.claimReferralCode(user.id, body.referralCode);
  }
}
