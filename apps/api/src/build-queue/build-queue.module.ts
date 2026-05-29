import { Module } from '@nestjs/common';
import { BuilderModule } from '../builder/builder.module';
import { GitHubModule } from '../github/github.module';
import { NotificationsModule } from '../notifications/notifications.module';
import { BuildQueueController } from './build-queue.controller';
import { BuildQueueService } from './build-queue.service';

@Module({
  imports: [NotificationsModule, GitHubModule, BuilderModule],
  controllers: [BuildQueueController],
  providers: [BuildQueueService],
  exports: [BuildQueueService],
})
export class BuildQueueModule {}
