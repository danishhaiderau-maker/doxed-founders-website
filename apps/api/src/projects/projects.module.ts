import { Module, forwardRef } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { DexscreenerModule } from '../dexscreener/dexscreener.module';
import { FeedModule } from '../feed/feed.module';
import { LaunchQualificationModule } from '../launch-qualification/launch-qualification.module';
import { FoundersController, ProjectsController } from './projects.controller';
import { ProjectsClaimController } from './projects-claim.controller';
import { ComplianceTimelineController } from './compliance-timeline.controller';
import { ComplianceTimelineService } from './compliance-timeline.service';
import { MetricsSyncService } from './metrics-sync.service';
import { PlatformAdoptionService } from './platform-adoption.service';
import { ProjectsService } from './projects.service';

@Module({
  imports: [forwardRef(() => AuthModule), DexscreenerModule, forwardRef(() => FeedModule), LaunchQualificationModule],
  controllers: [ProjectsController, FoundersController, ProjectsClaimController, ComplianceTimelineController],
  providers: [ProjectsService, MetricsSyncService, PlatformAdoptionService, ComplianceTimelineService],
  exports: [ProjectsService, MetricsSyncService, PlatformAdoptionService, ComplianceTimelineService],
})
export class ProjectsModule {}
