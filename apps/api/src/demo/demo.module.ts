import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { AirdropModule } from '../airdrop/airdrop.module';
import { DdollarModule } from '../ddollar/ddollar.module';
import { FounderDenModule } from '../founder-den/founder-den.module';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { ProjectsModule } from '../projects/projects.module';
import { DemoController } from './demo.controller';
import { DemoModeGuard } from './demo-mode.guard';
import { DemoSeedService } from './demo-seed.service';
import { BusinessJourneyService } from './business-journey.service';

@Module({
  imports: [
    AuthModule,
    ProjectsModule,
    FounderDenModule,
    FounderOsModule,
    DdollarModule,
    AirdropModule,
  ],
  controllers: [DemoController],
  providers: [DemoSeedService, DemoModeGuard, BusinessJourneyService],
  exports: [DemoSeedService, BusinessJourneyService],
})
export class DemoModule {}
