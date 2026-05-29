import { Module } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { BuildQueueController } from './build-queue.controller';
import { BuildQueueService } from './build-queue.service';

@Module({
  imports: [NotificationsModule],
  controllers: [BuildQueueController],
  providers: [BuildQueueService],
  exports: [BuildQueueService],
})
export class BuildQueueModule {}
