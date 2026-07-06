import { Global, Module } from '@nestjs/common';
import { FounderAiRuntimeService } from './founder-ai-runtime.service';
import { ModelRouterService } from './model-router.service';
import { PromptCacheService } from './prompt-cache.service';

@Global()
@Module({
  providers: [PromptCacheService, ModelRouterService, FounderAiRuntimeService],
  exports: [FounderAiRuntimeService, PromptCacheService, ModelRouterService],
})
export class FounderAiRuntimeModule {}
