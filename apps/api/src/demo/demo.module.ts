import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { AirdropModule } from '../airdrop/airdrop.module';
import { DdollarModule } from '../ddollar/ddollar.module';
import { FounderDenModule } from '../founder-den/founder-den.module';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { LaunchQualificationModule } from '../launch-qualification/launch-qualification.module';
import { ProjectsModule } from '../projects/projects.module';
import { TradingAgentsModule } from '../trading-agents/trading-agents.module';
import { AiProxyModule } from '../ai-proxy/ai-proxy.module';
import { RoutingEngineModule } from '../routing-engine/routing-engine.module';
import { MemoryEngineModule } from '../memory-engine/memory-engine.module';
import { LearningEngineModule } from '../learning-engine/learning-engine.module';
import { IdeaValidatorModule } from '../idea-validator/idea-validator.module';
import { LamModule } from '../lam/lam.module';
import { DeploymentModesModule } from '../deployment-modes/deployment-modes.module';
import { RaiseRoomModule } from '../raise-room/raise-room.module';
import { TokenLaunchModule } from '../token-launch/token-launch.module';
import { FounderEconomicsModule } from '../founder-economics/founder-economics.module';
import { PrismaModule } from '../prisma/prisma.module';
import { DemoController } from './demo.controller';
import { DemoModeGuard } from './demo-mode.guard';
import { DemoSeedService } from './demo-seed.service';
import { BusinessJourneyService } from './business-journey.service';
import { ExtendedSmokeService } from './extended-smoke.service';
import { DemoStressService } from './demo-stress.service';
import { KernelPillarsService } from './kernel-pillars.service';
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
    AiProxyModule,
    RoutingEngineModule,
    MemoryEngineModule,
    LearningEngineModule,
    IdeaValidatorModule,
    LamModule,
    DeploymentModesModule,
    RaiseRoomModule,
    TokenLaunchModule,
    FounderEconomicsModule,
    PrismaModule,
  ],
  controllers: [DemoController],
  providers: [
    DemoSeedService,
    DemoModeGuard,
    BusinessJourneyService,
    ExtendedSmokeService,
    DemoStressService,
    KernelPillarsService,
    ReadinessScorecardService,
    DemoHarnessService,
  ],
  exports: [DemoSeedService, BusinessJourneyService, DemoHarnessService],
})
export class DemoModule {}
