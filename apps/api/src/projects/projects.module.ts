import { Module } from '@nestjs/common';
import { DexscreenerModule } from '../dexscreener/dexscreener.module';
import { FoundersController, ProjectsController } from './projects.controller';
import { MetricsSyncService } from './metrics-sync.service';
import { ProjectsService } from './projects.service';

@Module({
  imports: [DexscreenerModule],
  controllers: [ProjectsController, FoundersController],
  providers: [ProjectsService, MetricsSyncService],
  exports: [ProjectsService, MetricsSyncService],
})
export class ProjectsModule {}
