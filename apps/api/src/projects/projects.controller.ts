import { Controller, Get, Param, Post, Query } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { Public } from '../auth/public.decorator';
import { MetricsSyncService } from './metrics-sync.service';
import { ProjectsService } from './projects.service';

@Public()
@SkipThrottle()
@Controller('projects')
export class ProjectsController {
  constructor(
    private readonly projects: ProjectsService,
    private readonly metricsSync: MetricsSyncService,
  ) {}

  @Get()
  list(
    @Query('featured') featured?: string,
    @Query('category') category?: string,
  ) {
    return this.projects.findAll({
      featured: featured === 'true',
      category: category || undefined,
    });
  }

  @Get('featured/list')
  featured() {
    return this.projects.findAll({ featured: true });
  }

  @Get('platform/stats')
  platformStats() {
    return this.projects.getPlatformStats();
  }

  @Post('sync-metrics')
  syncMetrics() {
    return this.metricsSync.syncStaleProjects();
  }

  @Get(':slug')
  bySlug(@Param('slug') slug: string) {
    return this.projects.findBySlug(slug);
  }
}

@Public()
@SkipThrottle()
@Controller('founders')
export class FoundersController {
  constructor(private readonly projects: ProjectsService) {}

  @Get()
  list() {
    return this.projects.findFounders();
  }

  @Get(':slug')
  bySlug(@Param('slug') slug: string) {
    return this.projects.findFounderBySlug(slug);
  }
}
