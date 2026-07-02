import {
  Injectable,
  Logger,
  ServiceUnavailableException,
} from '@nestjs/common';
import { parseAnthropicUsage, parseOpenAiStyleUsage } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { PlatformAdoptionService } from '../projects/platform-adoption.service';
import { AiRoutingService } from './ai-routing.service';

export type ChatMessage = { role: 'system' | 'user' | 'assistant'; content: string };

export type InvokeOptions = {
  /** Section slug — resolved via AiSectionRouting to a provider. */
  section: string;
  messages: ChatMessage[];
  temperature?: number;
  maxTokens?: number;
  userId?: string;
  projectId?: string | null;
  /** billingSource written to AiTokenUsageLog. Defaults to 'platform_routed'. */
  billingSource?: string;
  /** Override the routed provider (skip the routing lookup). */
  providerKey?: string;
  /** Override the model (otherwise the provider's defaultModel is used). */
  model?: string;
};

export type InvokeResult = {
  content: string;
  usage: { promptTokens: number; completionTokens: number };
  provider: string;
  model: string;
};

const DEFAULT_TIMEOUT_MS = 90_000;

/**
 * Generic, runtime-routable AI invoker. Every AI section on the platform that
 * has been refactored to use this service gets:
 *   1. Admin-routable provider selection (AiSectionRouting → AiRoutingProvider).
 *   2. Centralised token logging via PlatformAdoptionService.recordAiUsage
 *      (billingSource = 'platform_routed' by default), so the adoption chart
 *      counts the call automatically.
 *   3. A single OpenAI-compatible call path with adapter hooks for Anthropic
 *      (native messages API) and Gemini (OpenAI-compat layer).
 *
 * Adding a new AI is now: admin adds an AiRoutingProvider row (key + baseUrl +
 * model + key), toggles it enabled, and assigns it to a section. No code change.
 */
@Injectable()
export class AiInvokerService {
  private readonly logger = new Logger(AiInvokerService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly routing: AiRoutingService,
    private readonly adoption: PlatformAdoptionService,
  ) {}

  async invoke(options: InvokeOptions): Promise<InvokeResult> {
    const section = options.section;
    const providerKey =
      options.providerKey ?? (await this.resolveProviderKey(section));
    if (!providerKey) {
      throw new ServiceUnavailableException(
        `No AI provider routed for section "${section}". Configure it in /admin/control → AI Routing.`,
      );
    }

    const provider = await this.prisma.aiRoutingProvider.findUnique({
      where: { key: providerKey },
    });
    if (!provider) {
      throw new ServiceUnavailableException(
        `AI provider "${providerKey}" is not registered. Add it in /admin/control → AI Routing.`,
      );
    }
    if (!provider.enabled) {
      throw new ServiceUnavailableException(
        `AI provider "${provider.label}" is disabled. Enable it in /admin/control → AI Routing.`,
      );
    }
    const apiKey = await this.routing.getDecryptedKey(providerKey);
    if (!apiKey) {
      throw new ServiceUnavailableException(
        `No AI key set for ${provider.label} — add it in /admin/control → AI Routing.`,
      );
    }

    const model = options.model ?? provider.defaultModel;
    const temperature = options.temperature ?? 0.4;

    let result: InvokeResult;
    try {
      if (provider.adapter === 'anthropic') {
        result = await this.invokeAnthropic(provider.baseUrl, apiKey, model, options, temperature);
      } else {
        // openai_compat and gemini_native (when baseUrl points at the compat layer) share this path.
        result = await this.invokeOpenAiCompat(provider.baseUrl, apiKey, model, options, temperature);
      }
      result.provider = providerKey;
      result.model = model;
    } catch (err) {
      this.logger.warn(
        `invoke(${section}, provider=${providerKey}, model=${model}) failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      throw new ServiceUnavailableException(
        `AI call to ${provider.label} failed. Try again in a moment, or route "${section}" to a different provider in /admin/control → AI Routing.`,
      );
    }

    // Centralised token logging — best-effort, never throws into the request path.
    const billingSource = options.billingSource ?? 'platform_routed';
    void this.adoption
      .recordAiUsage({
        userId: options.userId ?? '',
        provider: providerKey.toUpperCase(),
        source: section,
        billingSource,
        promptTokens: result.usage.promptTokens,
        completionTokens: result.usage.completionTokens,
        projectId: options.projectId ?? null,
      })
      .catch(() => undefined);

    return result;
  }

  /**
   * Returns the routed provider key for a section, or null if no routing row
   * exists. Does NOT check enabled/key — the caller (invoke) does that and
   * raises a clear admin message. Useful for the X Share modal to show which
   * provider will be used without performing a call.
   */
  async resolveProviderKey(section: string): Promise<string | null> {
    await this.routing.seedDefaults();
    const row = await this.prisma.aiSectionRouting.findUnique({
      where: { section },
      select: { providerKey: true },
    });
    return row?.providerKey ?? null;
  }

  // ─── OpenAI-compatible (DeepSeek, GLM, OpenAI, Xiaomi, Ollama, Gemini-compat) ──

  private async invokeOpenAiCompat(
    baseUrl: string,
    apiKey: string,
    model: string,
    options: InvokeOptions,
    temperature: number,
  ): Promise<InvokeResult> {
    const url = `${baseUrl.replace(/\/$/, '')}/chat/completions`;
    const res = await fetch(url, {
      method: 'POST',
      headers: {
        Authorization: `Bearer ${apiKey}`,
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model,
        messages: options.messages,
        temperature,
        ...(options.maxTokens ? { max_tokens: options.maxTokens } : {}),
      }),
      signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
    });

    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`OpenAI-compat HTTP ${res.status}${body ? `: ${body.slice(0, 200)}` : ''}`);
    }
    const data = (await res.json()) as {
      choices?: { message?: { content?: string } }[];
      usage?: { prompt_tokens?: number; completion_tokens?: number };
    };
    const content = data.choices?.[0]?.message?.content ?? '';
    if (!content) throw new Error('OpenAI-compat returned empty content');
    const usage = parseOpenAiStyleUsage(data);
    return {
      content,
      usage: {
        promptTokens: usage?.promptTokens ?? 0,
        completionTokens: usage?.completionTokens ?? 0,
      },
      provider: model,
      model,
    };
  }

  // ─── Anthropic native messages API ────────────────────────────────────────

  private async invokeAnthropic(
    baseUrl: string,
    apiKey: string,
    model: string,
    options: InvokeOptions,
    temperature: number,
  ): Promise<InvokeResult> {
    const url = `${baseUrl.replace(/\/$/, '')}/messages`;
    // Anthropic splits system from the messages array.
    const system = options.messages.find((m) => m.role === 'system')?.content ?? '';
    const turns = options.messages
      .filter((m) => m.role !== 'system')
      .map((m) => ({ role: m.role, content: m.content }));

    const res = await fetch(url, {
      method: 'POST',
      headers: {
        'x-api-key': apiKey,
        'anthropic-version': '2023-06-01',
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        model,
        max_tokens: options.maxTokens ?? 1024,
        temperature,
        ...(system ? { system } : {}),
        messages: turns,
      }),
      signal: AbortSignal.timeout(DEFAULT_TIMEOUT_MS),
    });

    if (!res.ok) {
      const body = await res.text().catch(() => '');
      throw new Error(`Anthropic HTTP ${res.status}${body ? `: ${body.slice(0, 200)}` : ''}`);
    }
    const data = (await res.json()) as {
      content?: { type: string; text?: string }[];
      usage?: { input_tokens?: number; output_tokens?: number };
    };
    const content = (data.content ?? []).map((c) => c.text ?? '').join('').trim();
    if (!content) throw new Error('Anthropic returned empty content');
    const usage = parseAnthropicUsage(data);
    return {
      content,
      usage: {
        promptTokens: usage?.promptTokens ?? 0,
        completionTokens: usage?.completionTokens ?? 0,
      },
      provider: model,
      model,
    };
  }
}
