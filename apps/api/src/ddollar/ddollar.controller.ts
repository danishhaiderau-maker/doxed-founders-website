import { Controller, Get, UseGuards } from '@nestjs/common';
import { SkipThrottle } from '@nestjs/throttler';
import { AdminGuard } from '../auth/guards';
import { DdollarRuntimeService } from './ddollar-runtime.service';
import { isDdollarRuntimeEnabled } from './ddollar.constants';

@SkipThrottle()
@Controller('admin/ddollar')
@UseGuards(AdminGuard)
export class DdollarController {
  constructor(private readonly runtime: DdollarRuntimeService) {}

  @Get('status')
  status() {
    return {
      enabled: isDdollarRuntimeEnabled(),
      featureFlag: 'DDOLLAR_RUNTIME_ENABLED',
    };
  }

  @Get('treasury')
  treasury() {
    return this.runtime.getTreasuryAudit();
  }

  @Get('emissions')
  async emissions() {
    await this.runtime.ensureDailyEmissionStub();
    const rows = await this.runtime.getDailyEmissions();
    return { rows, note: 'Daily emission worker not yet scheduled — stub rows only' };
  }
}
