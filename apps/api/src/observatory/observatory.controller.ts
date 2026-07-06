import { Controller, Get, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AdminGuard } from '../auth/guards';
import { ObservatoryService } from './observatory.service';

@SkipThrottle()
@Controller('admin/observatory')
@UseGuards(AdminGuard)
export class ObservatoryController {
  constructor(private readonly observatory: ObservatoryService) {}

  @Get()
  overview() {
    return this.observatory.getOverview();
  }
}
