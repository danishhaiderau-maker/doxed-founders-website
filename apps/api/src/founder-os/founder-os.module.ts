import { Module, forwardRef } from '@nestjs/common';
import { GitHubModule } from '../github/github.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { EventsModule } from '../events/events.module';
import { XSocialModule } from '../x-social/x-social.module';
import { FounderOsController } from './founder-os.controller';
import { FounderOsIntegrationService } from './founder-os-integration.service';
import { FounderOsService } from './founder-os.service';
import { GithubAutoSyncService } from './github-auto-sync.service';

import { PlatformConnectionsService } from './platform-connections.service';
import { FounderCloudService } from './founder-cloud.service';
import { FounderPromoService } from './founder-promo.service';
import { FounderPlanEntitlementsService } from './founder-plan-entitlements.service';
import { FounderPlanBillingController } from './founder-plan-billing.controller';
import { FounderPlanBillingService } from './founder-plan-billing.service';

@Module({
  imports: [NotificationsModule, forwardRef(() => XSocialModule), GitHubModule, forwardRef(() => EventsModule)],
  controllers: [FounderOsController, FounderPlanBillingController],
  providers: [
    FounderOsService,
    FounderOsIntegrationService,
    GithubAutoSyncService,
    PlatformConnectionsService,
    FounderCloudService,
    FounderPlanEntitlementsService,
    FounderPlanBillingService,
    FounderPromoService,
  ],
  exports: [FounderOsService, GithubAutoSyncService, FounderCloudService, FounderPlanEntitlementsService, FounderPromoService],
})
export class FounderOsModule {}
