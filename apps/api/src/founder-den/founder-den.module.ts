import { Module, OnModuleInit } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { EventsModule } from '../events/events.module';
import { BuilderModule } from '../builder/builder.module';
import { PredictionMarketsModule } from '../prediction-markets/prediction-markets.module';
import { FounderDenController } from './founder-den.controller';
import { FounderDenService } from './founder-den.service';

@Module({
  imports: [NotificationsModule, FounderOsModule, EventsModule, BuilderModule, PredictionMarketsModule],
  controllers: [FounderDenController],
  providers: [FounderDenService],
  exports: [FounderDenService],
})
export class FounderDenModule implements OnModuleInit {
  constructor(private readonly founderDen: FounderDenService) {}

  onModuleInit() {
    this.founderDen.syncAllFounders().catch(() => {});
  }
}
