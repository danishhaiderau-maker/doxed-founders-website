import { Module } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { FeedController } from './feed.controller';
import { FeedService } from './feed.service';
import { HotBuyService } from './hot-buy.service';
import { UnifiedFeedService } from './unified-feed.service';

@Module({
  imports: [NotificationsModule],
  controllers: [FeedController],
  providers: [FeedService, HotBuyService, UnifiedFeedService],
  exports: [FeedService, HotBuyService, UnifiedFeedService],
})
export class FeedModule {}
