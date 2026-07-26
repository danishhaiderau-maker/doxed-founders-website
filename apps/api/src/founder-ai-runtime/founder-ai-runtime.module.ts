import { Global, Module, forwardRef } from '@nestjs/common';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { MemoryEngineModule } from '../memory-engine/memory-engine.module';
import { ContextBuilderService } from './context-builder.service';
import { FounderAiRuntimeService } from './founder-ai-runtime.service';
import { FounderBrainProvidersService } from './founder-brain-providers.service';
import { ModelRouterService } from './model-router.service';
import { PromptCacheService } from './prompt-cache.service';
import { ProviderEgressAuditService } from './provider-egress-audit.service';

@Global()
@Module({
  // forwardRef: MemoryEngineModule imports FounderNodeModule, which sits across
  // the kernel boundary and may transitively reach back into the AI runtime.
  imports: [FounderOsModule, forwardRef(() => MemoryEngineModule)],
  providers: [
    PromptCacheService,
    ModelRouterService,
    ContextBuilderService,
    FounderBrainProvidersService,
    ProviderEgressAuditService,
    FounderAiRuntimeService,
  ],
  exports: [
    FounderAiRuntimeService,
    PromptCacheService,
    ModelRouterService,
    ContextBuilderService,
    FounderBrainProvidersService,
    ProviderEgressAuditService,
  ],
})
export class FounderAiRuntimeModule {}
