import { Controller, Get, Post, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AdminGuard } from '../auth/guards';
import { DemoModeGuard } from './demo-mode.guard';
import { DemoSeedService } from './demo-seed.service';

@SkipThrottle()
@Controller('admin/demo')
@UseGuards(AdminGuard)
export class DemoController {
  constructor(private readonly demo: DemoSeedService) {}

  @Post('seed')
  @UseGuards(DemoModeGuard)
  seed() {
    return this.demo.seedEcosystem();
  }

  @Post('reset')
  @UseGuards(DemoModeGuard)
  reset() {
    return this.demo.resetDemoData();
  }

  @Get('status')
  status() {
    return this.demo.getStatus();
  }

  @Post('smoke')
  @UseGuards(DemoModeGuard)
  runSmoke() {
    return this.demo.runSmokeChecks();
  }

  @Get('smoke')
  @UseGuards(DemoModeGuard)
  runSmokeGet() {
    return this.demo.runSmokeChecks();
  }
}
