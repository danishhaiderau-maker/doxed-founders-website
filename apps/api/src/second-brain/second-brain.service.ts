import { Injectable, Logger } from '@nestjs/common';
import { FounderBrainProvidersService } from '../founder-ai-runtime/founder-brain-providers.service';
import { FounderPromoService } from '../founder-os/founder-promo.service';
import { getGlmApiBaseUrl, getGlmDefaultModel } from '../founder-os/glm-config';

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

  /**
   * Critique an agent's output via the cheap expert cascade.
   *
   * @param input.allowGlmSpend  Required to reach the GLM last-resort step.
   */
  async critique(input: {
    agentOutput: string;
    context?: string;
    allowGlmSpend?: boolean;
  }): Promise<string | null> {
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
      input.agentOutput,
    ].join('\n');

    const geminiKey =
      process.env.GEMINI_API_KEY?.trim() ||
      (await this.founderPromo.getDecryptedPlatformGeminiKey());
    const gemini = await this.tryOpenAiCompat({
      label: 'gemini-flash',
      apiKey: geminiKey,
      baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
      model: process.env.SECOND_BRAIN_PRIMARY_MODEL?.trim() || 'gemini-2.0-flash',
      system,
      user,
    });
    if (gemini) return gemini;

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
      if (openai) return openai;
    }

    if (!input.allowGlmSpend) {
      this.logger.warn(
        'second_brain.critique: cheap cascade exhausted; GLM skipped (allowGlmSpend=false).',
      );
      return null;
    }

    const glmKey =
      (await this.brainProviders.resolveApiKey('glm')) ||
      (await this.founderPromo.getDecryptedPlatformGlmKey());
    if (!glmKey) {
      this.logger.warn('second_brain.critique skipped — no cheap path and no GLM key');
      return null;
    }

    return this.tryOpenAiCompat({
      label: 'glm-last-resort',
      apiKey: glmKey,
      baseUrl: getGlmApiBaseUrl(),
      model: getGlmDefaultModel(),
      system,
      user,
    });
  }

  private async tryOpenAiCompat(opts: {
    label: string;
    apiKey: string | null;
    baseUrl: string;
    model: string;
    system: string;
    user: string;
  }): Promise<string | null> {
    if (!opts.apiKey) return null;
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
