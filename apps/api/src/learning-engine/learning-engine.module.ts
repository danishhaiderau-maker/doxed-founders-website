import { Module } from '@nestjs/common';
import { AuthModule } from '../auth/auth.module';
import { CapabilityRegistryModule } from '../capability-registry/capability-registry.module';
import { FlightRecorderModule } from '../flight-recorder/flight-recorder.module';
import { PrismaModule } from '../prisma/prisma.module';
import { LearningEngineController } from './learning-engine.controller';
import { LearningEngineScheduler } from './learning-engine.scheduler';
import { LearningEngineService } from './learning-engine.service';
import { RetryDetectorService } from './retry-detector.service';

/**
 * Learning Engine — Phase 4 kernel module (docs/KERNEL.md §3.6).
 *
 * Boundary:
 *   - Imports only kernel modules (FlightRecorder, CapabilityRegistry,
 *     Prisma) + the AuthModule (for the AdminGuard on the status route).
 *   - Exports LearningEngineService and RetryDetectorService so the AI Proxy
 *     can wire the retry detector into its afterRequest hook.
 *
 * ScheduleModule is NOT imported here — it is registered once in the root
 * AppModule so all kernel schedulers share one scheduler instance.
 */
@Module({
  imports: [
    PrismaModule,
    FlightRecorderModule,
    CapabilityRegistryModule,
    AuthModule,
  ],
  controllers: [LearningEngineController],
  providers: [
    LearningEngineService,
    LearningEngineScheduler,
    RetryDetectorService,
  ],
  exports: [LearningEngineService, RetryDetectorService],
})
export class LearningEngineModule {}
