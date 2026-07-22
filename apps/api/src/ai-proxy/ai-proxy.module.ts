import { Module } from '@nestjs/common';
import { AiProxyController } from './ai-proxy.controller';
import { AiProxyRuntimeService } from './ai-proxy-runtime.service';
import { AiProxyUsageService } from './ai-proxy-usage.service';
import { IntentClassifierService } from './intent-classifier.service';
import { PrismaModule } from '../prisma/prisma.module';
import { DdollarModule } from '../ddollar/ddollar.module';
import { FounderAiRuntimeModule } from '../founder-ai-runtime/founder-ai-runtime.module';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { FounderNodeGuard } from '../founder-node/founder-node.guard';
import { AiRoutingModule } from '../ai-routing/ai-routing.module';
import { CapabilityRegistryModule } from '../capability-registry/capability-registry.module';
import { FlightRecorderModule } from '../flight-recorder/flight-recorder.module';
import { LearningEngineModule } from '../learning-engine/learning-engine.module';
import { RoutingEngineModule } from '../routing-engine/routing-engine.module';
import { FounderOsModule } from '../founder-os/founder-os.module';

@Module({
  imports: [
    PrismaModule,
    AiRoutingModule,
    FounderAiRuntimeModule,
    FounderOsModule,
    FounderNodeModule,
    DdollarModule,
    CapabilityRegistryModule,
    FlightRecorderModule,
    RoutingEngineModule,
    LearningEngineModule,
  ],
  controllers: [AiProxyController],
  providers: [AiProxyRuntimeService, AiProxyUsageService, IntentClassifierService, FounderNodeGuard],
  exports: [AiProxyRuntimeService, AiProxyUsageService, IntentClassifierService],
})
export class AiProxyModule {}
