import { BadRequestException, Injectable, Logger } from '@nestjs/common';
import { FounderBrainProvidersService } from '../founder-ai-runtime/founder-brain-providers.service';
import { FounderPromoService } from '../founder-os/founder-promo.service';
import { getGlmApiBaseUrl, getGlmDefaultModel } from '../founder-os/glm-config';

export type SecondBrainProviderLabel = 'gemini-flash' | 'openai-mini' | 'glm-last-resort';

export type SecondBrainCritiqueResult = {
  text: string | null;
  provider: SecondBrainProviderLabel | null;
};

export type SecondBrainKeyStatus = {
  gemini: boolean;
  openai: boolean;
  glm: boolean;
  /** True when at least one cheap path (Gemini or OpenAI) is available. */
  cheapPathReady: boolean;
  primaryModel: string;
  fallbackModel: string;
};

/**
 * Second Brain — expert consult for Founder IDE.
 *
 * Cheap cascade (primary → fallback → optional last resort):
 *   1. Gemini Flash  (admin-stored gemini key or GEMINI_API_KEY)
 *   2. OpenAI gpt-4o-mini / Luna-class if OPENAI_API_KEY is set
 *   3. GLM only when allowGlmSpend=true AND cheaper paths failed
 *
 * Never DeepSeek here — DeepSeek is Builder / Platform Brain only.
 * GLM must NEVER be the default Second Brain path (cost rule).
 */
@Injectable()
export class SecondBrainService {
  private readonly logger = new Logger(SecondBrainService.name);

  constructor(
    private readonly brainProviders: FounderBrainProvidersService,
    private readonly founderPromo: FounderPromoService,
  ) {}

  async getKeyStatus(): Promise<SecondBrainKeyStatus> {
    const gemini =
      Boolean(process.env.GEMINI_API_KEY?.trim()) ||
      Boolean(await this.founderPromo.getDecryptedPlatformGeminiKey());
    const openai = Boolean(process.env.OPENAI_API_KEY?.trim());
    const glm =
      Boolean(process.env.GLM_API_KEY?.trim()) ||
      Boolean(await this.brainProviders.resolveApiKey('glm')) ||
      Boolean(await this.founderPromo.getDecryptedPlatformGlmKey());
    return {
      gemini,
      openai,
      glm,
      cheapPathReady: gemini || openai,
      primaryModel: process.env.SECOND_BRAIN_PRIMARY_MODEL?.trim() || 'gemini-3.5-flash',
      fallbackModel: process.env.SECOND_BRAIN_FALLBACK_MODEL?.trim() || 'gpt-4o-mini',
    };
  }

  /**
   * Critique an agent's output via the cheap expert cascade.
   *
   * @param input.allowGlmSpend  Required to reach the GLM last-resort step.
   */
  async critique(input: {
    agentOutput: string;
    context?: string;
    allowGlmSpend?: boolean;
  }): Promise<SecondBrainCritiqueResult> {
    const agentOutput = input.agentOutput?.trim() ?? '';
    if (!agentOutput) {
      throw new BadRequestException('agentOutput is required');
    }

    const system = [
      "You are the Second Brain — a critical reviewer of an AI agent's output.",
      'Be concise, specific, and skeptical. Flag anything wrong, risky, or missing.',
      'Do not flatter. If the output is correct, say so in one line.',
    ].join(' ');
    const user = [
      'Context:',
      input.context?.trim() ?? '(none)',
      '',
      'Agent output to critique:',
      agentOutput,
    ].join('\n');

    const geminiKey =
      process.env.GEMINI_API_KEY?.trim() ||
      (await this.founderPromo.getDecryptedPlatformGeminiKey());
    const gemini = await this.tryOpenAiCompat({
      label: 'gemini-flash',
      apiKey: geminiKey,
      baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
      // Gemini 2.0 Flash was shut down on 2026-06-01. Keep this default on a
      // supported multimodal Flash model; operators can still pin an override.
      model: process.env.SECOND_BRAIN_PRIMARY_MODEL?.trim() || 'gemini-3.5-flash',
      system,
      user,
    });
    if (gemini) return { text: gemini, provider: 'gemini-flash' };

    const openaiKey = process.env.OPENAI_API_KEY?.trim() || null;
    if (openaiKey) {
      const openai = await this.tryOpenAiCompat({
        label: 'openai-mini',
        apiKey: openaiKey,
        baseUrl: 'https://api.openai.com/v1',
        model: process.env.SECOND_BRAIN_FALLBACK_MODEL?.trim() || 'gpt-4o-mini',
        system,
        user,
      });
      if (openai) return { text: openai, provider: 'openai-mini' };
    }

    if (!input.allowGlmSpend) {
      this.logger.warn(
        'second_brain.critique: cheap cascade exhausted; GLM skipped (allowGlmSpend=false).',
      );
      return { text: null, provider: null };
    }

    const glmKey =
      (await this.brainProviders.resolveApiKey('glm')) ||
      (await this.founderPromo.getDecryptedPlatformGlmKey());
    if (!glmKey) {
      this.logger.warn('second_brain.critique skipped — no cheap path and no GLM key');
      return { text: null, provider: null };
    }

    const glm = await this.tryOpenAiCompat({
      label: 'glm-last-resort',
      apiKey: glmKey,
      baseUrl: getGlmApiBaseUrl(),
      model: getGlmDefaultModel(),
      system,
      user,
    });
    return { text: glm, provider: glm ? 'glm-last-resort' : null };
  }

  /** Admin / health smoke: tiny critique proving cascade (never DeepSeek). */
  async testCascade(opts?: { allowGlmSpend?: boolean }): Promise<{
    ok: boolean;
    provider: SecondBrainProviderLabel | null;
    message: string;
    keys: SecondBrainKeyStatus;
    latencyMs: number;
  }> {
    const started = Date.now();
    const keys = await this.getKeyStatus();
    if (!keys.cheapPathReady && !(opts?.allowGlmSpend && keys.glm)) {
      return {
        ok: false,
        provider: null,
        message: 'No Second Brain path ready (need Gemini and/or OPENAI_API_KEY; GLM optional last resort)',
        keys,
        latencyMs: Date.now() - started,
      };
    }

    const result = await this.critique({
      agentOutput: 'Ping: 2+2=4. Reply with one short confirmation line.',
      context: 'admin second-brain cascade smoke test',
      allowGlmSpend: Boolean(opts?.allowGlmSpend),
    });

    if (result.provider === null || !result.text) {
      return {
        ok: false,
        provider: null,
        message: 'Cascade returned empty — check Gemini / OpenAI / GLM keys',
        keys,
        latencyMs: Date.now() - started,
      };
    }

    // Hard assert: DeepSeek must never appear as the Second Brain provider.
    if (String(result.provider).toLowerCase().includes('deepseek')) {
      return {
        ok: false,
        provider: result.provider,
        message: 'FAIL: DeepSeek used for Second Brain (forbidden)',
        keys,
        latencyMs: Date.now() - started,
      };
    }

    return {
      ok: true,
      provider: result.provider,
      message: `Second Brain OK via ${result.provider}`,
      keys,
      latencyMs: Date.now() - started,
    };
  }

  private async tryOpenAiCompat(opts: {
    label: SecondBrainProviderLabel;
    apiKey: string | null;
    baseUrl: string;
    model: string;
    system: string;
    user: string;
  }): Promise<string | null> {
    if (!opts.apiKey) return null;
    if (opts.baseUrl.toLowerCase().includes('deepseek')) {
      this.logger.error('second_brain refused DeepSeek path — Builder/Platform Brain only');
      return null;
    }
    try {
      const res = await fetch(`${opts.baseUrl.replace(/\/$/, '')}/chat/completions`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${opts.apiKey}`,
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({
          model: opts.model,
          messages: [
            { role: 'system', content: opts.system },
            { role: 'user', content: opts.user },
          ],
          temperature: 0.2,
          stream: false,
        }),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        this.logger.warn(
          `second_brain.critique ${opts.label} non-OK ${res.status}: ${body.slice(0, 200)}`,
        );
        return null;
      }
      const data = (await res.json()) as {
        choices?: Array<{ message?: { content?: string } }>;
      };
      return data?.choices?.[0]?.message?.content?.trim() ?? null;
    } catch (err) {
      this.logger.warn(
        `second_brain.critique ${opts.label} error: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }
}
