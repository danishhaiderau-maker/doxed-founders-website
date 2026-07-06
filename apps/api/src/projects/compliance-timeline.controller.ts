import { Controller, Get, Param } from '@nestjs/common';
import { Public } from '../auth/public.decorator';
import { ComplianceTimelineService } from './compliance-timeline.service';

@Controller('projects')
export class ComplianceTimelineController {
  constructor(private readonly timeline: ComplianceTimelineService) {}

  @Public()
  @Get(':slug/compliance-timeline')
  getTimeline(@Param('slug') slug: string) {
    return this.timeline.getTimeline(slug);
  }
}
