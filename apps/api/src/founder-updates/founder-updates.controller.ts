import { Controller, Get, Post, UseGuards } from '@nestjs/common';
import { Public } from '../auth/public.decorator';
import { AdminGuard } from '../auth/guards';
import { FounderUpdatesService } from './founder-updates.service';

@Controller('founder-updates')
export class FounderUpdatesController {
  constructor(private readonly founderUpdates: FounderUpdatesService) {}

  @Public()
  @Get('pinned')
  findPinned() {
    return this.founderUpdates.findPinned();
  }

  @Public()
  @Get('spotlight')
  spotlight() {
    return this.founderUpdates.findSpotlightProjects();
  }

  @UseGuards(AdminGuard)
  @Post('sync-x')
  syncX() {
    return this.founderUpdates.syncTwitterUpdates();
  }
}
