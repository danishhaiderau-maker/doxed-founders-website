import { Injectable, Logger } from '@nestjs/common';
import { FounderBrainProvidersService } from '../founder-ai-runtime/founder-brain-providers.service';
import { getGlmApiBaseUrl, getGlmDefaultModel } from '../founder-os/glm-config';

/**
 * Second Brain Service — the SOLE sanctioned GLM (Zhipu / z.ai) call site in
 * the entire codebase.
 *
 * HARD COST RULE (do not violate):
 *   "GLM is very expensive, so we cannot add it in a general ID chat box.
 *    GLM is a separate thing and it's very expensive, so it has to be used
 *    very carefully, only for the second brain, just to get an opinion and
 *    other things. GLM will be used wisely. otherwise we can not be
 *    profitable."
 *
 * What this means in code:
 *   - GLM must NEVER be reachable from the general Founder IDE chat composer,
 *     the AI Auto Router (ModelRouterService), the vision preprocessor
 *     fallback, the intent classifier, or any other general-traffic path.
 *   - GLM tokens may be spent ONLY by this service, for the Second Brain
 *     critical-review surface (an opinion/critique of agent output).
 *   - If you find yourself adding a `resolveApiKey('glm')` call anywhere else,
 *     STOP. Route the request through DeepSeek (text) or Gemini (vision)
 *     instead. GLM here is the deliberate, premium, rare path.
 *
 * This is a thin, deliberately-minimal boundary. The full Second Brain
 * feature (UX, persistence, scheduling) is NOT implemented here — this module
 * exists only to (a) document the rule, (b) provide the one sanctioned GLM
 * invocation helper, and (c) make it trivial for code review to grep for the
 * only legitimate GLM caller.
 *
 * Usage (Second Brain only):
 *   const review = await secondBrain.critique({ agentOutput, context });
 *
 * The method is currently a guarded stub: it refuses to call GLM until the
 * caller explicitly opts in via `allowGlmSpend: true`, so an accidental wiring
 * cannot silently burn the GLM budget. Wire the real Second Brain trigger to
 * pass that flag once the feature ships.
 */
@Injectable()
export class SecondBrainService {
  private readonly logger = new Logger(SecondBrainService.name);

  constructor(
    private readonly brainProviders: FounderBrainProvidersService,
  ) {}

  /**
   * Ask GLM to critique an agent's output. This is the ONLY sanctioned GLM
   * call in the codebase.
   *
   * @param input.agentOutput  The text the upstream agent produced.
   * @param input.context      Optional surrounding context (user goal, files).
   * @param input.allowGlmSpend  HARD GATE. Must be `true` to actually invoke
   *                             GLM. Defaults to `false` so an accidental
   *                             caller cannot spend tokens.
   * @returns GLM's critique, or null if GLM is unavailable / not allowed.
   */
  async critique(input: {
    agentOutput: string;
    context?: string;
    allowGlmSpend?: boolean;
  }): Promise<string | null> {
    if (!input.allowGlmSpend) {
      this.logger.warn(
        'second_brain.critique called without allowGlmSpend=true; refusing to spend GLM tokens.',
      );
      return null;
    }

    const apiKey = await this.brainProviders.resolveApiKey('glm');
    if (!apiKey) {
      this.logger.warn('second_brain.critique skipped — no GLM API key configured');
      return null;
    }

    const model = getGlmDefaultModel();
    const system = [
      'You are the Second Brain — a critical reviewer of an AI agent\'s output.',
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

    try {
      const res = await fetch(`${getGlmApiBaseUrl()}/chat/completions`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${apiKey}`,
          'Content-Type': 'application/json',
          Accept: 'application/json',
        },
        body: JSON.stringify({
          model,
          messages: [
            { role: 'system', content: system },
            { role: 'user', content: user },
          ],
          temperature: 0.2,
          stream: false,
        }),
      });
      if (!res.ok) {
        const body = await res.text().catch(() => '');
        this.logger.warn(
          `second_brain.critique GLM non-OK ${res.status}: ${body.slice(0, 200)}`,
        );
        return null;
      }
      const data = (await res.json()) as {
        choices?: Array<{ message?: { content?: string } }>;
      };
      return data?.choices?.[0]?.message?.content?.trim() ?? null;
    } catch (err) {
      this.logger.warn(
        `second_brain.critique GLM error: ${err instanceof Error ? err.message : String(err)}`,
      );
      return null;
    }
  }
}