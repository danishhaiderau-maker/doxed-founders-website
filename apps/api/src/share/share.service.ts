import {
  Injectable,
  Logger,
  ServiceUnavailableException,
} from '@nestjs/common';
import { estimateLlmTokensFromText, parseOpenAiStyleUsage } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { FounderPromoService } from '../founder-os/founder-promo.service';
import { PlatformAdoptionService } from '../projects/platform-adoption.service';

/**
 * System prompt for the DeepSeek paraphrase pass.
 *
 * Goal: turn the founder's draft (or the default onboarding template) into a
 * clean, Twitter-ready founder-onboarding message. No hype / pump language.
 * Preserve the project name + ticker header, any @handle claim line, any URL,
 * and the trailing hashtags. Keep it within a single tweet (≤ 280 chars) when
 * possible — but never strip the placeholders / link / hashtags.
 */
const PARAPHRASE_SYSTEM_PROMPT = [
  'You are the Founder OS share-paraphrase agent.',
  'Rewrite the user\'s draft tweet into a clean, founder-onboarding message that invites the project founder to manage their community on Doxxed Crypto.',
  'Tone: plain, direct, founder-onboarding. No hype, no pump language, no price predictions, no "accumulate before the next move", no rocket emojis.',
  'Hard constraints:',
  '1. Preserve the project name and $TICKER header if present.',
  '2. Preserve any @handle (founder / project Twitter handle) line.',
  '3. Preserve any URL (the project / share link).',
  '4. Preserve the trailing hashtags line (e.g. "#Crypto #FounderOS @DoxxedCrypto").',
  '5. Keep the whole tweet within 280 characters if possible — trim your prose, never the placeholders / link / hashtags.',
  '6. Return ONLY the final tweet text — no quotes, no markdown fences, no explanation.',
  '7. Mention AI agents coming soon to help run the community (talk to investors directly + an AI summarizer that turns code commits into plain language).',
].join('\n');

const DEEPSEEK_BASE_URL = 'https://api.deepseek.com';
const DEEPSEEK_DEFAULT_MODEL = 'deepseek-chat';
const DEEPSEEK_TIMEOUT_MS = 60_000;

@Injectable()
export class ShareService {
  private readonly logger = new Logger(ShareService.name);

  constructor(
    private readonly founderPromo: FounderPromoService,
    private readonly prisma: PrismaService,
    private readonly adoption: PlatformAdoptionService,
  ) {}

  /**
   * Paraphrase a draft tweet into a Twitter-ready founder-onboarding message
   * using the platform DeepSeek key. Reuses `FounderPromoService.getDecryptedPlatformDeepseekKey`
   * (the same accessor the BuilderService platform-brain fallback uses).
   */
  async paraphraseTweet(
    userId: string,
    input: { text: string; projectName?: string; ticker?: string; slug?: string },
  ): Promise<{ text: string }> {
    const apiKey = await this.founderPromo.getDecryptedPlatformDeepseekKey();
    if (!apiKey) {
      throw new ServiceUnavailableException(
        'AI paraphrase is not configured. Ask an admin to set the platform DeepSeek key in Connected Accounts.',
      );
    }

    const draft = input.text?.trim();
    if (!draft) {
      return { text: '' };
    }

    const contextBits: string[] = [];
    if (input.projectName) contextBits.push(`Project name: ${input.projectName}`);
    if (input.ticker) contextBits.push(`Ticker: $${input.ticker.replace(/^\$/, '')}`);
    if (input.slug) contextBits.push(`Slug: ${input.slug}`);
    const contextLine = contextBits.length ? `\n\nContext (preserve these in the output):\n${contextBits.join('\n')}` : '';

    const userPrompt = `Draft tweet to paraphrase:\n"""\n${draft}\n"""${contextLine}\n\nReturn the rewritten tweet now.`;

    let text: string | null = null;
    let usage: { promptTokens: number; completionTokens: number } | null = null;
    try {
      const res = await fetch(`${DEEPSEEK_BASE_URL}/chat/completions`, {
        method: 'POST',
        headers: {
          Authorization: `Bearer ${apiKey}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          model: DEEPSEEK_DEFAULT_MODEL,
          messages: [
            { role: 'system', content: PARAPHRASE_SYSTEM_PROMPT },
            { role: 'user', content: userPrompt },
          ],
          temperature: 0.4,
        }),
        signal: AbortSignal.timeout(DEEPSEEK_TIMEOUT_MS),
      });

      if (res.ok) {
        const data = (await res.json()) as {
          choices?: { message?: { content?: string } }[];
          usage?: { prompt_tokens?: number; completion_tokens?: number };
        };
        text = data.choices?.[0]?.message?.content ?? null;
        usage = parseOpenAiStyleUsage(data);
      } else {
        const body = await res.text().catch(() => '');
        this.logger.warn(
          `DeepSeek paraphrase HTTP ${res.status}${body ? `: ${body.slice(0, 200)}` : ''}`,
        );
      }
    } catch (err) {
      this.logger.warn(
        `DeepSeek paraphrase call failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      text = null;
    }

    if (!text) {
      throw new ServiceUnavailableException(
        'AI paraphrase call failed. Try again in a moment.',
      );
    }

    // Log token usage so the Platform Adoption chart counts share-paraphrase
    // inference. Falls back to a conservative char/4 estimate when the
    // provider omits usage.
    const promptTokens =
      usage?.promptTokens ?? estimateLlmTokensFromText(`${PARAPHRASE_SYSTEM_PROMPT}\n${userPrompt}`);
    const completionTokens = usage?.completionTokens ?? estimateLlmTokensFromText(text);
    const projectId = await this.resolveProjectId(input.slug);
    void this.adoption
      .recordAiUsage({
        userId,
        projectId,
        provider: 'DEEPSEEK',
        source: 'share_paraphrase',
        billingSource: 'platform_brain',
        promptTokens,
        completionTokens,
      })
      .catch(() => undefined);

    // Strip any stray markdown fences / surrounding quotes the model sometimes adds.
    const cleaned = text
      .replace(/^```(?:[a-z]*)?/i, '')
      .replace(/```$/i, '')
      .replace(/^["“”']+|["“”']+$/g, '')
      .trim();

    return { text: cleaned || draft };
  }

  /** Best-effort project lookup from slug for token-usage attribution. */
  private async resolveProjectId(slug?: string | null): Promise<string | null> {
    if (!slug) return null;
    try {
      const project = await this.prisma.project.findFirst({
        where: { slug },
        select: { id: true },
      });
      return project?.id ?? null;
    } catch {
      return null;
    }
  }
}
