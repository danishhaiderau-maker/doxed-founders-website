import { Injectable, Logger } from '@nestjs/common';
import type {
  AiRuntimeRequest,
  AiRuntimeResponse,
  ModelRoute,
} from './founder-ai-runtime.types';
import { ContextBuilderService } from './context-builder.service';
import { ModelRouterService } from './model-router.service';
import { PromptCacheService } from './prompt-cache.service';
import { ProviderEgressAuditService } from './provider-egress-audit.service';
import { isFounderAiRuntimeEnabled } from './founder-ai-runtime.config';
import {
  runtimeCallSiteForSection,
  type ProviderEgressBudgetDomain,
} from './provider-egress-audit.types';

export type AiRuntimeExecutionPolicy = {
  /**
   * Accounting boundary for the provider call. BYOK must stay outside the
   * Founder-managed allowance even though it uses the same runtime controls.
   */
  budgetDomain?: ProviderEgressBudgetDomain;
};

export type AiRuntimeInvokeContext = {
  maxOutputTokens: number;
  request: AiRuntimeRequest;
};

/** Central gateway for Founder OS managed and BYOK AI calls. */
@Injectable()
export class FounderAiRuntimeService {
  private readonly logger = new Logger(FounderAiRuntimeService.name);

  constructor(
    private readonly promptCache: PromptCacheService,
    private readonly modelRouter: ModelRouterService,
    private readonly contextBuilder: ContextBuilderService,
    private readonly providerEgressAudit: ProviderEgressAuditService,
  ) {}

  isEnabled(): boolean {
    return isFounderAiRuntimeEnabled();
  }

  /** Classify intent and pick the enforced model route. */
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
   * Single entry point for governed routing, limits, cache, and provider
   * attribution. The optional callback owns the adapter-specific network call.
   */
  async complete(
    request: AiRuntimeRequest,
    invoke?: (route: ModelRoute, ctx: AiRuntimeInvokeContext) => Promise<AiRuntimeResponse>,
    policy?: AiRuntimeExecutionPolicy,
  ): Promise<AiRuntimeResponse> {
    const cached = await this.tryCacheHit(request);
    if (cached) return cached;

    const prepared = this.prepareRequest(request);
    const route = this.modelRouter.route(prepared);
    const maxOutputTokens = this.contextBuilder.maxOutputTokens(route.intent);
    if (!invoke) {
      return { ok: false, intent: route.intent, model: route.model, cacheHit: false, cacheLevel: 'miss' };
    }

    const result = await this.providerEgressAudit.runWithContext(
      {
        boundary: 'founder_ai_runtime',
        callSiteId: runtimeCallSiteForSection(prepared.section),
        budgetDomain: policy?.budgetDomain ?? 'founder_managed',
      },
      () => invoke(route, { maxOutputTokens, request: prepared }),
    );
    if (result.ok) {
      await this.recordResponse(prepared, { ...result, intent: route.intent, cacheLevel: 'miss' });
    }
    return { ...result, intent: route.intent, cacheHit: false, cacheLevel: 'miss' };
  }

  /**
   * Govern a lazy provider stream with the same routing, context-pruning,
   * output-cap, and budget-domain policy as complete().
   */
  stream<TYield, TReturn, TNext = unknown>(
    request: AiRuntimeRequest,
    invoke: (
      route: ModelRoute,
      ctx: AiRuntimeInvokeContext,
    ) => AsyncGenerator<TYield, TReturn, TNext>,
    policy?: AiRuntimeExecutionPolicy,
  ): AsyncGenerator<TYield, TReturn, TNext> {
    const prepared = this.prepareRequest({ ...request, skipCache: true });
    const route = this.modelRouter.route(prepared);
    const maxOutputTokens =
      this.contextBuilder.maxOutputTokens(route.intent);
    const iterator = invoke(route, { maxOutputTokens, request: prepared });

    return this.providerEgressAudit.wrapAsyncGeneratorWithContext(
      {
        boundary: 'founder_ai_runtime',
        callSiteId: runtimeCallSiteForSection(prepared.section),
        budgetDomain: policy?.budgetDomain ?? 'founder_managed',
      },
      iterator,
    );
  }

  cacheStats() {
    return this.promptCache.stats();
  }
}
