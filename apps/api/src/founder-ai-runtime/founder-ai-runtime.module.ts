import { Global, Module } from '@nestjs/common';
import { FounderOsModule } from '../founder-os/founder-os.module';
import { ContextBuilderService } from './context-builder.service';
import { FounderAiRuntimeService } from './founder-ai-runtime.service';
import { FounderBrainProvidersService } from './founder-brain-providers.service';
import { ModelRouterService } from './model-router.service';
import { PromptCacheService } from './prompt-cache.service';

@Global()
@Module({
  imports: [FounderOsModule],
  providers: [
    PromptCacheService,
    ModelRouterService,
    ContextBuilderService,
    FounderBrainProvidersService,
    FounderAiRuntimeService,
  ],
  exports: [
    FounderAiRuntimeService,
    PromptCacheService,
    ModelRouterService,
    ContextBuilderService,
    FounderBrainProvidersService,
  ],
})
export class FounderAiRuntimeModule {}
