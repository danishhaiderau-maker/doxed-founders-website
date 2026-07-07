import { Module } from '@nestjs/common';
import { CapabilityRegistryModule } from '../capability-registry/capability-registry.module';
import { FlightRecorderModule } from '../flight-recorder/flight-recorder.module';
import { PrismaModule } from '../prisma/prisma.module';
import { ExecutionProfileService } from './execution-profile.service';
import { RoutingEngineCache } from './routing-engine.cache';
import { RoutingEngineService } from './routing-engine.service';

@Module({
  imports: [CapabilityRegistryModule, FlightRecorderModule, PrismaModule],
  providers: [RoutingEngineService, ExecutionProfileService, RoutingEngineCache],
  exports: [RoutingEngineService, ExecutionProfileService, RoutingEngineCache],
})
export class RoutingEngineModule {}
