import { Module } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { FounderOsController } from './founder-os.controller';
import { FounderOsService } from './founder-os.service';

@Module({
  imports: [NotificationsModule],
  controllers: [FounderOsController],
  providers: [FounderOsService],
  exports: [FounderOsService],
})
export class FounderOsModule {}
