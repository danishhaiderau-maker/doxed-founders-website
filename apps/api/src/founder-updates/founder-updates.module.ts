import { Module } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { FounderUpdatesController } from './founder-updates.controller';
import { FounderUpdatesService } from './founder-updates.service';

@Module({
  imports: [NotificationsModule],
  controllers: [FounderUpdatesController],
  providers: [FounderUpdatesService],
  exports: [FounderUpdatesService],
})
export class FounderUpdatesModule {}
