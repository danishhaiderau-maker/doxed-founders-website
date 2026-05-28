import { Module } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { XSocialModule } from '../x-social/x-social.module';
import { FounderUpdatesController } from './founder-updates.controller';
import { FounderUpdatesService } from './founder-updates.service';

@Module({
  imports: [NotificationsModule, XSocialModule],
  controllers: [FounderUpdatesController],
  providers: [FounderUpdatesService],
  exports: [FounderUpdatesService],
})
export class FounderUpdatesModule {}
