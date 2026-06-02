import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { DexscreenerModule } from '../dexscreener/dexscreener.module';
import { FeedModule } from '../feed/feed.module';
import { FoundersController, ProjectsController } from './projects.controller';
import { ProjectsClaimController } from './projects-claim.controller';
import { MetricsSyncService } from './metrics-sync.service';
import { ProjectsService } from './projects.service';

@Module({
  imports: [AuthModule, DexscreenerModule, FeedModule],
  controllers: [ProjectsController, FoundersController, ProjectsClaimController],
  providers: [ProjectsService, MetricsSyncService],
  exports: [ProjectsService, MetricsSyncService],
})
export class ProjectsModule {}
