import {
  HttpException,
  HttpStatus,
  Injectable,
  Logger,
  ServiceUnavailableException,
} from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';
import { AiInvokerService } from '../ai-routing/ai-invoker.service';
import { RateLimiterService } from '../events/rate-limiter.service';

/**
 * System prompt for the share-paraphrase pass.
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

@Injectable()
export class ShareService {
  private readonly logger = new Logger(ShareService.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly aiInvoker: AiInvokerService,
    private readonly rateLimiter: RateLimiterService,
  ) {}

  /**
   * Paraphrase a draft tweet into a Twitter-ready founder-onboarding message
   * via the routed AI provider for section `share_paraphrase` (admin-configurable
   * in /admin/control → AI Routing). Token usage is logged centrally by the
   * AiInvokerService (billingSource = 'platform_routed').
   */
  async paraphraseTweet(
    userId: string,
    input: { text: string; projectName?: string; ticker?: string; slug?: string },
  ): Promise<{ text: string }> {
    const draft = input.text?.trim();
    if (!draft) {
      return { text: '' };
    }

    // Per-user DB-backed rate limit. Default cap is the platform-wide
    // `rateLimitHourly` (10/hr) — a hard floor on this previously wide-open
    // platform-DeepSeek-key path. Admin can tighten via PlatformSettings.
    const rateCheck = await this.rateLimiter.checkLimit(userId, 'share:paraphrase');
    if (!rateCheck.allowed) {
      throw new HttpException(
        {
          statusCode: HttpStatus.TOO_MANY_REQUESTS,
          message: `Rate limit: ${rateCheck.reason}. Try again in ${Math.ceil(rateCheck.resetInMs / 1000)}s`,
          resetInMs: rateCheck.resetInMs,
        },
        HttpStatus.TOO_MANY_REQUESTS,
      );
    }

    const contextBits: string[] = [];
    if (input.projectName) contextBits.push(`Project name: ${input.projectName}`);
    if (input.ticker) contextBits.push(`Ticker: $${input.ticker.replace(/^\$/, '')}`);
    if (input.slug) contextBits.push(`Slug: ${input.slug}`);
    const contextLine = contextBits.length ? `\n\nContext (preserve these in the output):\n${contextBits.join('\n')}` : '';

    const userPrompt = `Draft tweet to paraphrase:\n"""\n${draft}\n"""${contextLine}\n\nReturn the rewritten tweet now.`;

    const projectId = await this.resolveProjectId(input.slug);

    let text: string;
    try {
      const result = await this.aiInvoker.invoke({
        section: 'share_paraphrase',
        messages: [
          { role: 'system', content: PARAPHRASE_SYSTEM_PROMPT },
          { role: 'user', content: userPrompt },
        ],
        temperature: 0.4,
        userId,
        projectId,
        // Preserve the conventional billing source for share paraphrase so the
        // adoption chart's existing bucketing continues to work.
        billingSource: 'platform_brain',
      });
      text = result.content;
    } catch (err) {
      // Re-raise ServiceUnavailableException (carries the admin-friendly message
      // from the invoker); wrap anything unexpected.
      if (err instanceof ServiceUnavailableException) throw err;
      this.logger.warn(
        `Paraphrase call failed: ${err instanceof Error ? err.message : String(err)}`,
      );
      throw new ServiceUnavailableException(
        'AI paraphrase call failed. Try again in a moment.',
      );
    }

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
