import { Global, Module } from '@nestjs/common';
import { ContextBuilderService } from './context-builder.service';
import { FounderAiRuntimeService } from './founder-ai-runtime.service';
import { ModelRouterService } from './model-router.service';
import { PromptCacheService } from './prompt-cache.service';

@Global()
@Module({
  providers: [PromptCacheService, ModelRouterService, ContextBuilderService, FounderAiRuntimeService],
  exports: [FounderAiRuntimeService, PromptCacheService, ModelRouterService, ContextBuilderService],
})
export class FounderAiRuntimeModule {}
