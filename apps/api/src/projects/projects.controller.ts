import { Controller, Get, Param, Post, Query, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { Public } from '../auth/public.decorator';
import { MetricsSyncGuard } from './metrics-sync.guard';
import { MetricsSyncService } from './metrics-sync.service';
import { PlatformAdoptionService } from './platform-adoption.service';
import { ProjectsService } from './projects.service';

@Public()
@SkipThrottle()
@Controller('projects')
export class ProjectsController {
  constructor(
    private readonly projects: ProjectsService,
    private readonly metricsSync: MetricsSyncService,
    private readonly adoption: PlatformAdoptionService,
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

  @Get('platform/activity')
  platformActivity(@Query('limit') limit?: string) {
    const n = limit ? Number.parseInt(limit, 10) : 10;
    return this.projects.getPlatformActivity(Number.isFinite(n) ? n : 10);
  }

  @Get('platform/adoption-metrics')
  adoptionMetrics(@Query('days') days?: string) {
    const n = days ? Number.parseInt(days, 10) : 14;
    return this.adoption.getAdoptionMetrics(Number.isFinite(n) ? n : 14);
  }

  @Post('sync-metrics')
  @UseGuards(MetricsSyncGuard)
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
