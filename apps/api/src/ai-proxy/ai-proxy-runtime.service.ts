import { Injectable, Logger, ServiceUnavailableException } from '@nestjs/common';
import { createHash, randomUUID } from 'node:crypto';
import {
  AI_PROXY_DDOLLAR_COST,
  FOUNDER_OS_AUTO_MODEL,
  type AiProxyTier,
  type AiProxyRouteDecision,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { SpendingEngine } from '../ddollar/spending-engine.service';
import { ModelRouterService } from '../founder-ai-runtime/model-router.service';
import { FounderBrainProvidersService } from '../founder-ai-runtime/founder-brain-providers.service';
import { getGlmApiBaseUrl, getGlmDefaultModel } from '../founder-os/glm-config';
import { DDOLLAR_ACTION_KEYS } from '../ddollar/ddollar.constants';
import { FlightRecorderService } from '../flight-recorder/flight-recorder.service';
import { RoutingEngineService } from '../routing-engine/routing-engine.service';
import type { AiRuntimeIntent } from '../capability-registry/capability-registry.types';
import { MODEL_ALIASES, MAX_PROMPT_TOKENS_SOFT_CAP, USE_ROUTING_ENGINE_V2 } from './ai-proxy.constants';
import type { ChatCompletionMessageDto, ChatCompletionRequestDto } from './dto/ai-proxy.dto';

/**
 * Server-side shape of the model list returned at /v1/models. Each alias is
 * expanded to a row so OpenAI-compat clients see them as real models.
 */
export const PROXY_MODEL_CATALOG = MODEL_ALIASES.map((id) => ({
  id,
  object: 'model' as const,
  created: 1_725_000_000_000,
  owned_by: 'founder-os',
}));

const DEEPSEEK_CHAT_URL = 'https://api.deepseek.com/chat/completions';

export type ProxyAuth = {
  userId: string;
  nodeId: string;
};

export type ProxyInvokeResult = {
  ok: boolean;
  status: number;
  body: ReadableStream<Uint8Array> | string;
  headers: Record<string, string>;
  provider: string;
  model: string;
  intent: string;
  tier: AiProxyTier;
  cacheLevel: 'hit' | 'partial' | 'miss';
  promptTokens?: number;
  completionTokens?: number;
};

/**
 * Extended route decision carried alongside the AiProxyRouteDecision so the
 * post-request hooks (DDollar spend + usage log + Flight Recorder) have
 * everything they need without re-deriving state.
 *
 * `flightRecorderHasDecisionRow` is true when the Routing Engine v2 already
 * wrote a decision row at route time — in that case the post-request hook
 * UPDATES that row (latency/cost/tokens) instead of inserting a duplicate.
 */
type ResolvedRoute = AiProxyRouteDecision & {
  requestId: string;
  promptHash: string;
  /** Profile that was effective for this request (always 'balanced' today). */
  profile: string;
  /** Scored candidates snapshot — empty for the legacy path. */
  candidates: Array<{ provider: string; model: string; score: number }>;
  cacheKey?: string | null;
  cacheLevel: 'hit' | 'partial' | 'miss';
  /**
   * True when the Routing Engine v2 already recorded a Flight Recorder row
   * for this requestId at decision time. The post-request hook then patches
   * that row with usage data instead of inserting a second row.
   */
  flightRecorderHasDecisionRow: boolean;
};

/**
 * Core AI Proxy runtime. Decides the route (provider + model + tier), invokes
 * the upstream provider (streaming or not), and runs the post-request hooks
 * (DDollar spend + usage log + Flight Recorder row).
 *
 * Routing path:
 *   - USE_ROUTING_ENGINE_V2=false (default, production-safe): the legacy
 *     ModelRouterService picks the provider/model exactly as before. The
 *     only observable change is that a Flight Recorder row is written.
 *   - USE_ROUTING_ENGINE_V2=true: the Routing Engine v2 takes over — cache
 *     lookup → capability gate → intent + cost-latency scoring.
 */
@Injectable()
export class AiProxyRuntimeService {
  private readonly logger = new Logger(AiProxyRuntimeService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly spendingEngine: SpendingEngine,
    private readonly modelRouter: ModelRouterService,
    private readonly brainProviders: FounderBrainProvidersService,
    private readonly routingEngine: RoutingEngineService,
    private readonly flightRecorder: FlightRecorderService,
  ) {}

  /** Expand any model alias (or `founder-os-auto`) into a concrete provider+model+tier. */
  async decideRoute(
    auth: ProxyAuth,
    body: ChatCompletionRequestDto,
  ): Promise<ResolvedRoute> {
    const requestedAlias = body.model ?? FOUNDER_OS_AUTO_MODEL;

    // Map alias → intent hint. The router does the real classification, but
    // the alias lets the founder force a tier from the IDE.
    const forcedIntent: string | null =
      requestedAlias === 'founder-os-code'
        ? 'code'
        : requestedAlias === 'founder-os-reasoning'
          ? 'reasoning'
          : requestedAlias === 'founder-os-fast'
            ? 'simple_qa'
            : null;

    const lastUserMessage = [...(body.messages ?? [])]
      .reverse()
      .find((m) => m.role === 'user');
    const systemMessage = body.messages?.find((m) => m.role === 'system');
    const userPrompt = lastUserMessage?.content ?? '';
    const systemPrompt = systemMessage?.content ?? '';
    const requestId = randomUUID();
    const promptHash = this.computePromptHash(systemPrompt, userPrompt);
    const inferredIntent = this.inferIntent(userPrompt);

    if (USE_ROUTING_ENGINE_V2) {
      try {
        const decision = await this.routingEngine.route({
          userId: auth.userId,
          intent: inferredIntent,
          prompt: `${systemPrompt}\n${userPrompt}`,
          requestId,
        });
        const tier = this.tierForIntent(decision.chosenProvider, inferredIntent, forcedIntent);
        return {
          requestId,
          providerKey: decision.chosenProvider,
          model: decision.chosenModel,
          tier,
          intent: inferredIntent,
          profile: 'balanced',
          candidates: decision.candidates,
          cacheKey: decision.cacheKey ?? null,
          cacheLevel: decision.cacheLevel,
          promptHash,
          // Routing Engine v2 writes a Flight Recorder row at decision time
          // (fire-and-forget). The post-request hook patches it in place.
          flightRecorderHasDecisionRow: true,
        };
      } catch (err) {
        // If v2 cannot serve (e.g. RoutingInfeasibleError, Capability table
        // empty), fall back to the legacy router so the request still
        // completes. Logged at warn so we can spot it in observability.
        this.logger.warn(
          `Routing Engine v2 failed (${err instanceof Error ? err.message : String(err)}); falling back to legacy router.`,
        );
      }
    }

    // Legacy path (default, or v2 fallback). Model selection here is
    // IDENTICAL to pre-Phase-1 wiring so production behavior is unchanged.
    const runtimeRequest = {
      userId: auth.userId,
      system: systemPrompt,
      userPrompt,
      section: 'copilot' as const,
      founderBrainTask: forcedIntent === 'code' ? ('code' as const) : undefined,
    };
    const route = this.modelRouter.route(runtimeRequest);

    const tier: AiProxyTier =
      forcedIntent === 'code'
        ? 'code'
        : forcedIntent === 'reasoning'
          ? 'reasoning'
          : route.tier;

    return {
      requestId,
      providerKey: route.providerKey,
      model: route.model,
      tier,
      intent: route.intent,
      profile: 'balanced',
      candidates: [],
      cacheKey: null,
      cacheLevel: 'miss',
      promptHash,
      // Legacy path: no prior decision row, so the post-request hook
      // inserts a fresh Flight Recorder entry.
      flightRecorderHasDecisionRow: false,
    };
  }

  /** Invoke the upstream provider, returning either a stream or a JSON body. */
  async invoke(
    auth: ProxyAuth,
    body: ChatCompletionRequestDto,
    route: ResolvedRoute,
  ): Promise<ProxyInvokeResult> {
    const apiKey = await this.brainProviders.resolveApiKey(
      route.providerKey as 'glm' | 'deepseek',
    );
    if (!apiKey) {
      throw new ServiceUnavailableException(
        `No API key configured for provider "${route.providerKey}"`,
      );
    }

    const url =
      route.providerKey === 'glm'
        ? `${getGlmApiBaseUrl()}/chat/completions`
        : DEEPSEEK_CHAT_URL;

    const payload = {
      model: route.model,
      messages: body.messages.map((m: ChatCompletionMessageDto) => ({
        role: m.role,
        content: m.content,
      })),
      stream: body.stream ?? false,
      ...(body.max_tokens !== undefined ? { max_tokens: body.max_tokens } : {}),
      ...(body.temperature !== undefined
        ? { temperature: body.temperature }
        : {}),
      ...(body.response_format ? { response_format: body.response_format } : {}),
      ...(body.stop ? { stop: body.stop } : {}),
    };

    const promptTokensEstimate = this.estimateTokens(payload.messages);
    if (promptTokensEstimate > MAX_PROMPT_TOKENS_SOFT_CAP) {
      this.logger.warn(
        `Prompt tokens estimate=${promptTokensEstimate} exceeds soft cap=${MAX_PROMPT_TOKENS_SOFT_CAP} for user=${auth.userId}`,
      );
    }

    const started = Date.now();
    const upstream = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
        Accept: body.stream ? 'text/event-stream' : 'application/json',
      },
      body: JSON.stringify(payload),
    });

    if (!upstream.ok) {
      const errText = await upstream.text().catch(() => '');
      this.logger.error(
        `Upstream ${route.providerKey} ${upstream.status}: ${errText.slice(0, 240)}`,
      );
      return {
        ok: false,
        status: upstream.status,
        body: errText || upstream.statusText,
        headers: {},
        provider: route.providerKey,
        model: route.model,
        intent: route.intent,
        tier: route.tier,
        cacheLevel: 'miss',
      };
    }

    // For non-streaming, we can read the whole body and run post-request
    // hooks before returning. For streaming, we tee the stream so the
    // background hooks fire after the response completes.
    if (body.stream && upstream.body) {
      const [a, b] = upstream.body.tee();
      void this.afterStreamingRequest(auth, route, b, started);
      return {
        ok: true,
        status: 200,
        body: a,
        headers: {
          'Content-Type': 'text/event-stream',
          'Cache-Control': 'no-cache',
          Connection: 'keep-alive',
        },
        provider: route.providerKey,
        model: route.model,
        intent: route.intent,
        tier: route.tier,
        cacheLevel: 'miss',
      };
    }

    const text = await upstream.text();
    const latencyMs = Date.now() - started;
    let promptTokens: number | undefined;
    let completionTokens: number | undefined;
    try {
      const parsed = JSON.parse(text);
      promptTokens = parsed?.usage?.prompt_tokens;
      completionTokens = parsed?.usage?.completion_tokens;
    } catch {
      // provider returned malformed JSON — leave token counts undefined
    }

    void this.afterRequest(auth, route, {
      promptTokens: promptTokens ?? promptTokensEstimate,
      completionTokens: completionTokens ?? 0,
      latencyMs,
    });

    return {
      ok: true,
      status: 200,
      body: text,
      headers: { 'Content-Type': 'application/json' },
      provider: route.providerKey,
      model: route.model,
      intent: route.intent,
      tier: route.tier,
      cacheLevel: 'miss',
      promptTokens,
      completionTokens,
    };
  }

  /** DDollar spend + usage log + Flight Recorder row for a completed non-streaming request. */
  private async afterRequest(
    auth: ProxyAuth,
    route: ResolvedRoute,
    usage: { promptTokens: number; completionTokens: number; latencyMs: number },
  ): Promise<void> {
    const ddollarCost = AI_PROXY_DDOLLAR_COST[route.tier];

    try {
      await this.spendingEngine.spend(auth.userId, ddollarCost, DDOLLAR_ACTION_KEYS.AI_SPEND, {
        aiSpend: true,
      });
    } catch (err) {
      this.logger.warn(
        `DDollar spend failed user=${auth.userId}: ${err instanceof Error ? err.message : String(err)}`,
      );
    }

    try {
      await this.prisma.aiTokenUsageLog.create({
        data: {
          userId: auth.userId,
          provider: route.providerKey,
          source: `ai-proxy:${route.model}`,
          billingSource: 'platform_promo',
          cacheLevel: 'miss',
          localToolUsed: false,
          confidenceScore: 1,
          promptTokens: usage.promptTokens,
          completionTokens: usage.completionTokens,
        },
      });
    } catch (err) {
      this.logger.warn(
        `AiTokenUsageLog write failed user=${auth.userId}: ${err instanceof Error ? err.message : String(err)}`,
      );
    }

    // Flight Recorder — unconditional so the Decision Log starts populating
    // immediately. Best-effort: never fails the request.
    try {
      const costUsd = await this.computeCostUsd(
        route.providerKey,
        route.model,
        usage.promptTokens,
        usage.completionTokens,
      );
      if (route.flightRecorderHasDecisionRow) {
        // v2 path: the Routing Engine already wrote the decision row at
        // route time; patch usage onto it rather than inserting a duplicate.
        await this.flightRecorder.updateUsage(route.requestId, {
          latencyMs: usage.latencyMs,
          costUsd,
          tokenCountPrompt: usage.promptTokens,
          tokenCountCompletion: usage.completionTokens,
        });
      } else {
        // Legacy path: insert a fresh decision row with the full usage
        // payload. This is the row that populates the Decision Log when
        // USE_ROUTING_ENGINE_V2 is off.
        await this.flightRecorder.record({
          requestId: route.requestId,
          userId: auth.userId,
          workspaceId: null,
          intent: route.intent,
          profile: route.profile,
          candidates: route.candidates,
          chosenProvider: route.providerKey,
          chosenModel: route.model,
          cacheLevel: route.cacheLevel,
          cacheKey: route.cacheKey ?? null,
          promptHash: route.promptHash,
          tokenCountPrompt: usage.promptTokens,
          tokenCountCompletion: usage.completionTokens,
          latencyMs: usage.latencyMs,
          costUsd,
        });
      }
    } catch (err) {
      this.logger.warn(
        `Flight Recorder write failed user=${auth.userId}: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  /** Same hooks but parses the SSE stream to recover token counts. */
  private async afterStreamingRequest(
    auth: ProxyAuth,
    route: ResolvedRoute,
    stream: ReadableStream<Uint8Array>,
    startedAt: number,
  ): Promise<void> {
    const reader = stream.getReader();
    const decoder = new TextDecoder();
    let promptTokens = 0;
    let completionTokens = 0;
    let buffer = '';

    try {
      for (;;) {
        const { done, value } = await reader.read();
        if (done) break;
        buffer += decoder.decode(value, { stream: true });
        const lines = buffer.split('\n');
        buffer = lines.pop() ?? '';
        for (const line of lines) {
          if (!line.startsWith('data: ')) continue;
          const payload = line.slice(6).trim();
          if (payload === '[DONE]') continue;
          try {
            const evt = JSON.parse(payload);
            const u = evt?.usage;
            if (u) {
              promptTokens = u.prompt_tokens ?? promptTokens;
              completionTokens = u.completion_tokens ?? completionTokens;
            }
          } catch {
            // ignore malformed chunk
          }
        }
      }
    } finally {
      try {
        await reader.cancel();
      } catch {
        // already closed
      }
    }

    await this.afterRequest(auth, route, {
      promptTokens: promptTokens || this.estimateStreamPromptTokens(route),
      completionTokens,
      latencyMs: Date.now() - startedAt,
    });
  }

  /** Rough 4-chars-per-token estimate for budget warnings. */
  private estimateTokens(messages: { role: string; content: string }[]): number {
    const chars = messages.reduce((sum, m) => sum + (m.content?.length ?? 0), 0);
    return Math.ceil(chars / 4);
  }

  private estimateStreamPromptTokens(route: ResolvedRoute): number {
    void route;
    return 0;
  }

  /**
   * SHA-256 of the normalized system+user prompt prefix. Matches the
   * Routing Engine v2 cache key shape (RoutingEngineCache.computeKey) so
   * future Learning-Engine joins line up.
   */
  private computePromptHash(systemPrompt: string, userPrompt: string): string {
    const normalized = `${systemPrompt}\n${userPrompt}`
      .replace(/\s+/g, ' ')
      .trim()
      .toLowerCase()
      .slice(0, 4096);
    return createHash('sha256').update(normalized, 'utf8').digest('hex');
  }

  /**
   * Lightweight intent classifier used on the legacy path so the Flight
   * Recorder row has a meaningful `intent`. Mirrors what the Routing Engine
   * v2's classifier would produce: short prompts → simple_qa, code fences /
   * backticks → code, everything else → reasoning.
   */
  private inferIntent(userPrompt: string): AiRuntimeIntent {
    if (/```|`/.test(userPrompt)) return 'code';
    if (userPrompt.length < 200) return 'simple_qa';
    return 'reasoning';
  }

  /**
   * Map the v2-decided provider + intent onto the proxy's tier enum so the
   * DDollar spend + AiTokenUsageLog still use the existing tier taxonomy.
   */
  private tierForIntent(
    provider: string,
    intent: AiRuntimeIntent,
    forcedIntent: string | null,
  ): AiProxyTier {
    if (forcedIntent === 'code' || intent === 'code') return 'code';
    if (forcedIntent === 'reasoning' || intent === 'reasoning') return 'reasoning';
    if (intent === 'simple_qa') return 'fast';
    // Default for agent/vision/unknown falls back to whatever the provider
    // is good at — keep it cheap for glm, reasoning-tier for deepseek.
    return provider === 'deepseek' ? 'reasoning' : 'fast';
  }

  /**
   * Compute the USD cost of a request from token counts and the matching
   * Capability row. Returns null if the Capability is missing (e.g. v2 not
   * seeded yet) so the Flight Recorder stores an honest null rather than 0.
   */
  private async computeCostUsd(
    provider: string,
    model: string,
    promptTokens: number,
    completionTokens: number,
  ): Promise<number | null> {
    try {
      const cap = await this.prisma.capability.findUnique({
        where: { provider_model: { provider, model } },
        select: { inputCostPer1M: true, outputCostPer1M: true },
      });
      if (!cap) return null;
      return (
        (promptTokens / 1_000_000) * cap.inputCostPer1M +
        (completionTokens / 1_000_000) * cap.outputCostPer1M
      );
    } catch {
      return null;
    }
  }

  /** Resolve the founder-friendly display model for /v1/models. */
  listModels() {
    return { object: 'list', data: PROXY_MODEL_CATALOG };
  }

  /** Convenience for tests / admin — exposes the current effective model. */
  effectiveModel(): string {
    return getGlmDefaultModel();
  }
}
