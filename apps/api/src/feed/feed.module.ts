import { Module } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { BuilderModule } from '../builder/builder.module';
import { FeedController } from './feed.controller';
import { FeedService } from './feed.service';
import { FeedShareService } from './feed-share.service';
import { HotBuyService } from './hot-buy.service';
import { UnifiedFeedService } from './unified-feed.service';
import { FeedTerminalService } from './feed-terminal.service';
import { FeedHubService } from './feed-hub.service';

@Module({
  imports: [NotificationsModule, BuilderModule],
  controllers: [FeedController],
  providers: [
    FeedService,
    FeedShareService,
    HotBuyService,
    UnifiedFeedService,
    FeedTerminalService,
    FeedHubService,
  ],
  exports: [
    FeedService,
    FeedShareService,
    HotBuyService,
    UnifiedFeedService,
    FeedTerminalService,
    FeedHubService,
  ],
})
export class FeedModule {}
