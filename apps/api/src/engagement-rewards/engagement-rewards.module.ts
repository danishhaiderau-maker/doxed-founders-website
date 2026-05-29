import { Module } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { EngagementRewardsController } from './engagement-rewards.controller';
import { EngagementRewardsService } from './engagement-rewards.service';

@Module({
  imports: [NotificationsModule],
  controllers: [EngagementRewardsController],
  providers: [EngagementRewardsService],
  exports: [EngagementRewardsService],
})
export class EngagementRewardsModule {}
