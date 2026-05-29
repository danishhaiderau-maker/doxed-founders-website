import { Module, forwardRef } from '@nestjs/common';
import { BuilderModule } from '../builder/builder.module';
import { GitHubModule } from '../github/github.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { EventsModule } from '../events/events.module';
import { BuildQueueController } from './build-queue.controller';
import { BuildQueueService } from './build-queue.service';

@Module({
  imports: [NotificationsModule, GitHubModule, BuilderModule, forwardRef(() => EventsModule)],
  controllers: [BuildQueueController],
  providers: [BuildQueueService],
  exports: [BuildQueueService],
})
export class BuildQueueModule {}
