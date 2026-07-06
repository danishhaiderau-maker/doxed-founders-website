import { Injectable, Logger } from '@nestjs/common';
import type {
  AiRuntimeRequest,
  AiRuntimeResponse,
  ModelRoute,
} from './founder-ai-runtime.types';
import { ContextBuilderService } from './context-builder.service';
import { ModelRouterService } from './model-router.service';
import { PromptCacheService } from './prompt-cache.service';

/**
 * Central gateway for Founder OS AI calls (Phase 0).
 * Phase 0: prompt hash cache + intent routing metadata; provider calls remain
 * in BuilderService / AiInvokerService until Phase 1 mandatory gateway.
 */
@Injectable()
export class FounderAiRuntimeService {
  private readonly logger = new Logger(FounderAiRuntimeService.name);

  constructor(
    private readonly promptCache: PromptCacheService,
    private readonly modelRouter: ModelRouterService,
    private readonly contextBuilder: ContextBuilderService,
  ) {}

  isEnabled(): boolean {
    return process.env.AI_RUNTIME_ENABLED === 'true';
  }

  /** Classify intent and pick model tier (logged for telemetry; Phase 1 enforces). */
  route(request: AiRuntimeRequest): ModelRoute {
    return this.modelRouter.route(request);
  }

  /** Normalize request (context pruning) before cache or provider calls. */
  prepareRequest(request: AiRuntimeRequest): AiRuntimeRequest {
    if (!this.isEnabled()) return request;
    return this.contextBuilder.prepareRequest(request);
  }

  maxOutputTokensFor(request: AiRuntimeRequest): number {
    const route = this.modelRouter.route(request);
    return this.contextBuilder.maxOutputTokens(route.intent);
  }

  /** Cache lookup — returns a hit or null (caller runs existing provider cascade). */
  async tryCacheHit(request: AiRuntimeRequest): Promise<AiRuntimeResponse | null> {
    if (!this.isEnabled() || request.skipCache) return null;
    const prepared = this.prepareRequest(request);
    const key = this.promptCache.buildKey(prepared);
    const hit = await this.promptCache.get(key);
    if (hit?.ok && hit.text) {
      this.logger.debug(
        `cache HIT section=${prepared.section} key=${key.slice(0, 12)}… intent=${hit.intent ?? 'n/a'}`,
      );
      return {
        ...hit,
        cacheLevel: 'L2_prompt_hash',
        confidenceScore: hit.confidenceScore ?? 1,
      };
    }
    return null;
  }

  /** Persist a successful provider response for future cache hits. */
  async recordResponse(request: AiRuntimeRequest, response: AiRuntimeResponse): Promise<void> {
    if (!this.isEnabled() || request.skipCache || !response.ok || !response.text?.trim()) {
      return;
    }
    const prepared = this.prepareRequest(request);
    const route = this.modelRouter.route(prepared);
    const key = this.promptCache.buildKey(prepared);
    await this.promptCache.set(key, {
      ...response,
      intent: response.intent ?? route.intent,
      model: response.model ?? route.model,
    });
  }

  /**
   * Single entry point — Phase 0 delegates to cache + optional invoke callback.
   * Phase 1: all provider calls flow through here (no direct fetch in app code).
   */
  async complete(
    request: AiRuntimeRequest,
    invoke?: (route: ModelRoute, ctx: { maxOutputTokens: number; request: AiRuntimeRequest }) => Promise<AiRuntimeResponse>,
  ): Promise<AiRuntimeResponse> {
    const cached = await this.tryCacheHit(request);
    if (cached) return cached;

    const prepared = this.prepareRequest(request);
    const route = this.modelRouter.route(prepared);
    const maxOutputTokens = this.contextBuilder.maxOutputTokens(route.intent);
    if (!invoke) {
      return { ok: false, intent: route.intent, model: route.model, cacheHit: false, cacheLevel: 'miss' };
    }

    const result = await invoke(route, { maxOutputTokens, request: prepared });
    if (result.ok) {
      await this.recordResponse(prepared, { ...result, intent: route.intent, cacheLevel: 'miss' });
    }
    return { ...result, intent: route.intent, cacheHit: false, cacheLevel: 'miss' };
  }

  cacheStats() {
    return this.promptCache.stats();
  }
}
