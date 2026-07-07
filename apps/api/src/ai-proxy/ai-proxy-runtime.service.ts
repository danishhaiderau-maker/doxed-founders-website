import { Injectable, Logger, ServiceUnavailableException } from '@nestjs/common';
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
 * Core AI Proxy runtime. Decides the route (provider + model + tier), invokes
 * the upstream provider (streaming or not), and runs the post-request hooks
 * (DDollar spend + usage log + Flight Recorder entry when v2 is on).
 *
 * The route decision is delegated to either:
 *   - ModelRouterService (v1, default) — pattern-based intent + provider cfg
 *   - RoutingEngineService (v2, behind USE_ROUTING_ENGINE_V2) — capability
 *     gate + cost-latency scoring, logged to Flight Recorder
 */
@Injectable()
export class AiProxyRuntimeService {
  private readonly logger = new Logger(AiProxyRuntimeService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly spendingEngine: SpendingEngine,
    private readonly modelRouter: ModelRouterService,
    private readonly brainProviders: FounderBrainProvidersService,
  ) {}

  /** Expand any model alias (or `founder-os-auto`) into a concrete provider+model+tier. */
  decideRoute(
    auth: ProxyAuth,
    body: ChatCompletionRequestDto,
  ): AiProxyRouteDecision {
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
    const runtimeRequest = {
      userId: auth.userId,
      system:
        body.messages?.find((m) => m.role === 'system')?.content ?? '',
      userPrompt: lastUserMessage?.content ?? '',
      section: 'copilot' as const,
      founderBrainTask: forcedIntent === 'code' ? ('code' as const) : undefined,
    };

    const route = this.modelRouter.route(runtimeRequest);

    // If the founder forced an alias, override the tier but keep the model
    // selection from the router (so the alias becomes a "preference", not a
    // hard assignment). This keeps the v1 path simple — v2 will use the
    // Execution Profile + Capability Registry for the same job.
    const tier: AiProxyTier =
      forcedIntent === 'code'
        ? 'code'
        : forcedIntent === 'reasoning'
          ? 'reasoning'
          : route.tier;

    void USE_ROUTING_ENGINE_V2; // v2 wiring lands in Phase 1 step 7

    return {
      providerKey: route.providerKey,
      model: route.model,
      tier,
      intent: route.intent,
    };
  }

  /** Invoke the upstream provider, returning either a stream or a JSON body. */
  async invoke(
    auth: ProxyAuth,
    body: ChatCompletionRequestDto,
    route: AiProxyRouteDecision,
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

  /** DDollar spend + usage log for a completed non-streaming request. */
  private async afterRequest(
    auth: ProxyAuth,
    route: AiProxyRouteDecision,
    usage: { promptTokens: number; completionTokens: number; latencyMs: number },
  ): Promise<void> {
    const ddollarCost = AI_PROXY_DDOLLAR_COST[route.tier];
    void usage.latencyMs; // TODO Phase 4 — store latency once RoutingDecision ships

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
  }

  /** Same hooks but parses the SSE stream to recover token counts. */
  private async afterStreamingRequest(
    auth: ProxyAuth,
    route: AiProxyRouteDecision,
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

  private estimateStreamPromptTokens(route: AiProxyRouteDecision): number {
    void route;
    return 0;
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
