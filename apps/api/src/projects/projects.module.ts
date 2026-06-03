import { Module, forwardRef } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { DexscreenerModule } from '../dexscreener/dexscreener.module';
import { FeedModule } from '../feed/feed.module';
import { FoundersController, ProjectsController } from './projects.controller';
import { ProjectsClaimController } from './projects-claim.controller';
import { MetricsSyncService } from './metrics-sync.service';
import { PlatformAdoptionService } from './platform-adoption.service';
import { ProjectsService } from './projects.service';

@Module({
  imports: [AuthModule, DexscreenerModule, forwardRef(() => FeedModule)],
  controllers: [ProjectsController, FoundersController, ProjectsClaimController],
  providers: [ProjectsService, MetricsSyncService, PlatformAdoptionService],
  exports: [ProjectsService, MetricsSyncService, PlatformAdoptionService],
})
export class ProjectsModule {}
