import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { DexscreenerModule } from '../dexscreener/dexscreener.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { PointsModule } from '../points/points.module';
import { PredictionMarketsModule } from '../prediction-markets/prediction-markets.module';
import { ListingApplicationsController } from './listing-applications.controller';
import { ListingApplicationsService } from './listing-applications.service';
import { ListingPublishService } from './listing-publish.service';
import { ListingVotesService } from './listing-votes.service';

@Module({
  imports: [DexscreenerModule, AuthModule, PointsModule, NotificationsModule, PredictionMarketsModule],
  controllers: [ListingApplicationsController],
  providers: [ListingApplicationsService, ListingPublishService, ListingVotesService],
})
export class ListingApplicationsModule {}
