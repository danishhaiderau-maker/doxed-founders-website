import { Module } from '@nestjs/common';
import { CapabilityRegistryModule } from '../capability-registry/capability-registry.module';
import { FlightRecorderModule } from '../flight-recorder/flight-recorder.module';
import { PrismaModule } from '../prisma/prisma.module';
import { PrismaService } from '../prisma/prisma.service';
import { ExecutionProfileService } from './execution-profile.service';
import {
  createRoutingCache,
  InMemoryRoutingCache,
  ROUTING_CACHE,
  RoutingEngineCache,
} from './routing-engine.cache';
import { RoutingEngineService } from './routing-engine.service';

/**
 * Routing Engine v2 — providers.
 *
 * The cache backend is selected at bootstrap via `ROUTING_CACHE_BACKEND`
 * (memory | neon | redis). The DI token `ROUTING_CACHE` resolves to whatever
 * `createRoutingCache` returns; `RoutingEngineCache` (the original class
 * name) is still provided as an alias so any consumer that imports the old
 * token keeps resolving.
 */
@Module({
  imports: [CapabilityRegistryModule, FlightRecorderModule, PrismaModule],
  providers: [
    {
      provide: InMemoryRoutingCache,
      useFactory: () => new InMemoryRoutingCache(),
    },
    {
      provide: ROUTING_CACHE,
      useFactory: (prisma: PrismaService) => createRoutingCache(process.env, prisma),
      inject: [PrismaService],
    },
    // Back-compat alias — old code that injects `RoutingEngineCache` by
    // class name still resolves to the active backend.
    {
      provide: RoutingEngineCache,
      useExisting: ROUTING_CACHE,
    },
    RoutingEngineService,
    ExecutionProfileService,
  ],
  exports: [
    RoutingEngineService,
    ExecutionProfileService,
    ROUTING_CACHE,
    RoutingEngineCache,
  ],
})
export class RoutingEngineModule {}
