import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { AirdropModule } from '../airdrop/airdrop.module';
import { DdollarModule } from '../ddollar/ddollar.module';
import { FounderDenModule } from '../founder-den/founder-den.module';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { LaunchQualificationModule } from '../launch-qualification/launch-qualification.module';
import { ProjectsModule } from '../projects/projects.module';
import { TradingAgentsModule } from '../trading-agents/trading-agents.module';
import { DemoController } from './demo.controller';
import { DemoModeGuard } from './demo-mode.guard';
import { DemoSeedService } from './demo-seed.service';
import { BusinessJourneyService } from './business-journey.service';
import { ExtendedSmokeService } from './extended-smoke.service';
import { DemoStressService } from './demo-stress.service';
import { ReadinessScorecardService } from './readiness-scorecard.service';
import { DemoHarnessService } from './demo-harness.service';

@Module({
  imports: [
    AuthModule,
    ProjectsModule,
    FounderDenModule,
    FounderOsModule,
    DdollarModule,
    AirdropModule,
    LaunchQualificationModule,
    TradingAgentsModule,
  ],
  controllers: [DemoController],
  providers: [
    DemoSeedService,
    DemoModeGuard,
    BusinessJourneyService,
    ExtendedSmokeService,
    DemoStressService,
    ReadinessScorecardService,
    DemoHarnessService,
  ],
  exports: [DemoSeedService, BusinessJourneyService, DemoHarnessService],
})
export class DemoModule {}
