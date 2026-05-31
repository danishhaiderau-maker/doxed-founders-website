import { Module, forwardRef } from '@nestjs/common';
import { NotificationsModule } from '../notifications/notifications.module';
import { BuilderModule } from '../builder/builder.module';
import { GitHubModule } from '../github/github.module';
import { BuildQueueModule } from '../build-queue/build-queue.module';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { EventOrchestratorService } from './event-orchestrator.service';
import { EventsController } from './events.controller';
import { EventsService } from './events.service';
import { FounderCopilotService } from './founder-copilot.service';
import { FounderMetricsService } from './founder-metrics.service';

@Module({
  imports: [
    NotificationsModule,
    forwardRef(() => BuilderModule),
    GitHubModule,
    forwardRef(() => BuildQueueModule),
    forwardRef(() => FounderOsModule),
  ],
  controllers: [EventsController],
  providers: [
    EventsService,
    EventOrchestratorService,
    FounderCopilotService,
    FounderMetricsService,
  ],
  exports: [EventsService, FounderMetricsService, FounderCopilotService],
})
export class EventsModule {}
