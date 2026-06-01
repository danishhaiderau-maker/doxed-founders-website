import { Body, Controller, Get, Param, Post, Query, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { InvestigationStatus } from '@prisma/client';
import { Public } from '../auth/public.decorator';
import { CurrentUser } from '../auth/current-user.decorator';
import { AuthUser } from '../auth/auth.types';
import { Roles } from '../auth/roles.decorator';
import { RolesGuard } from '../auth/roles.guard';
import { TrustCenterService, FileTrustReportDto } from './trust-center.service';
import { TrustWeightService } from './trust-weight.service';

@SkipThrottle()
@Controller('trust-center')
export class TrustCenterController {
  constructor(
    private readonly trustCenter: TrustCenterService,
    private readonly trustWeight: TrustWeightService,
  ) {}

  @Public()
  @Get('overview')
  overview() {
    return this.trustCenter.getOverview();
  }

  @Public()
  @Get('pending-listings')
  pendingListings() {
    return this.trustCenter.getPendingListings();
  }

  @Public()
  @Get('investigations')
  investigations(@Query('status') status?: InvestigationStatus) {
    return this.trustCenter.getInvestigations(status);
  }

  @Public()
  @Get('investigations/:id')
  investigation(@Param('id') id: string) {
    return this.trustCenter.getInvestigation(id);
  }

  @Public()
  @Get('recently-listed')
  recentlyListed() {
    return this.trustCenter.getRecentlyListed();
  }

  @Public()
  @Get('recently-delisted')
  recentlyDelisted() {
    return this.trustCenter.getRecentlyDelisted();
  }

  @Public()
  @Get('community-reviews')
  communityReviews() {
    return this.trustCenter.getCommunityReviews();
  }

  @Public()
  @Get('projects/:slug/metrics')
  projectMetrics(@Param('slug') slug: string) {
    return this.trustCenter.getProjectTrustMetrics(slug);
  }

  @Post('projects/:slug/report')
  fileReport(
    @Param('slug') slug: string,
    @CurrentUser() user: AuthUser,
    @Body() dto: FileTrustReportDto,
  ) {
    return this.trustCenter.fileReport(slug, user.id, dto);
  }

  @Get('my-weight')
  myWeight(@CurrentUser() user: AuthUser) {
    return this.trustWeight.forUser(user.id).then((weight) => ({ weight }));
  }

  @Post('investigations/:id/resolve')
  @UseGuards(RolesGuard)
  @Roles('ADMIN')
  resolveInvestigation(
    @Param('id') id: string,
    @Body() body: { decision: 'KEEP' | 'DELIST'; notes?: string },
  ) {
    return this.trustCenter.resolveInvestigation(id, body.decision, body.notes);
  }
}
