import { Module, forwardRef } from '@nestjs/common';
import { GitHubModule } from '../github/github.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { EventsModule } from '../events/events.module';
import { XSocialModule } from '../x-social/x-social.module';
import { FounderOsController } from './founder-os.controller';
import { FounderOsIntegrationService } from './founder-os-integration.service';
import { FounderOsService } from './founder-os.service';
import { GithubAutoSyncService } from './github-auto-sync.service';

@Module({
  imports: [NotificationsModule, XSocialModule, GitHubModule, forwardRef(() => EventsModule)],
  controllers: [FounderOsController],
  providers: [FounderOsService, FounderOsIntegrationService, GithubAutoSyncService],
  exports: [FounderOsService, GithubAutoSyncService],
})
export class FounderOsModule {}
