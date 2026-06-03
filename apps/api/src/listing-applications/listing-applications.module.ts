import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { DexscreenerModule } from '../dexscreener/dexscreener.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { PointsModule } from '../points/points.module';
import { PredictionMarketsModule } from '../prediction-markets/prediction-markets.module';
import { MessagesModule } from '../messages/messages.module';
import { EventsModule } from '../events/events.module';
import { ListingApplicationsController } from './listing-applications.controller';
import { ListingApplicationsService } from './listing-applications.service';
import { ListingPublishService } from './listing-publish.service';
import { ListingVotesService } from './listing-votes.service';
import { ListedProjectGithubSyncService } from './listed-project-github-sync.service';

@Module({
  imports: [
    DexscreenerModule,
    AuthModule,
    PointsModule,
    NotificationsModule,
    PredictionMarketsModule,
    MessagesModule,
    EventsModule,
  ],
  controllers: [ListingApplicationsController],
  providers: [
    ListingApplicationsService,
    ListingPublishService,
    ListingVotesService,
    ListedProjectGithubSyncService,
  ],
  exports: [ListingVotesService, ListingApplicationsService],
})
export class ListingApplicationsModule {}
