import { Global, Module } from '@nestjs/common';
import { AntiAbuseService } from './anti-abuse.service';
import { DdollarController } from './ddollar.controller';
import { DdollarRuntimeService } from './ddollar-runtime.service';
import { RewardEngine } from './reward-engine.service';
import { SpendingEngine } from './spending-engine.service';

@Global()
@Module({
  controllers: [DdollarController],
  providers: [AntiAbuseService, RewardEngine, SpendingEngine, DdollarRuntimeService],
  exports: [DdollarRuntimeService, RewardEngine, SpendingEngine],
})
export class DdollarModule {}
