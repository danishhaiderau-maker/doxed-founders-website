import { Module } from '@nestjs/common';
import { AiProxyController } from './ai-proxy.controller';
import { AiProxyRuntimeService } from './ai-proxy-runtime.service';
import { AiProxyUsageService } from './ai-proxy-usage.service';
import { PrismaModule } from '../prisma/prisma.module';
import { DdollarModule } from '../ddollar/ddollar.module';
import { FounderAiRuntimeModule } from '../founder-ai-runtime/founder-ai-runtime.module';
import { FounderNodeModule } from '../founder-node/founder-node.module';
import { FounderNodeGuard } from '../founder-node/founder-node.guard';
import { AiRoutingModule } from '../ai-routing/ai-routing.module';

@Module({
  imports: [
    PrismaModule,
    AiRoutingModule,
    FounderAiRuntimeModule,
    FounderNodeModule,
    DdollarModule,
  ],
  controllers: [AiProxyController],
  providers: [AiProxyRuntimeService, AiProxyUsageService, FounderNodeGuard],
  exports: [AiProxyRuntimeService, AiProxyUsageService],
})
export class AiProxyModule {}
