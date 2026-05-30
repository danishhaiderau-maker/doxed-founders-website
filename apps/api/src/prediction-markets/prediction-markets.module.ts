import { Module } from '@nestjs/common';
import { EventsModule } from '../events/events.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { PointsModule } from '../points/points.module';
import { PredictionMarketsController } from './prediction-markets.controller';
import { PredictionMarketsService } from './prediction-markets.service';

@Module({
  imports: [PointsModule, EventsModule, NotificationsModule],
  controllers: [PredictionMarketsController],
  providers: [PredictionMarketsService],
  exports: [PredictionMarketsService],
})
export class PredictionMarketsModule {}
