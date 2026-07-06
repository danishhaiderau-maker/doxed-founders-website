import { Injectable, Logger, UnauthorizedException } from '@nestjs/common';
import { ConfigService } from '@nestjs/config';
import { PrismaService } from '../prisma/prisma.service';
import { PlatformAdoptionService } from '../projects/platform-adoption.service';

const PLATFORM_ADMIN_EMAIL =
  process.env.PLATFORM_ADMIN_EMAIL ?? 'admin@doxedcryptofounder.local';

export type ShowcaseInferenceUsageEntry = {
  promptTokens: number;
  completionTokens: number;
  provider?: string;
  model?: string;
  source?: string;
  billingSource?: string;
};

let cachedAdminUserId: string | null | undefined;

@Injectable()
export class ShowcaseInferenceUsageService {
  private readonly logger = new Logger(ShowcaseInferenceUsageService.name);

  constructor(
    private readonly config: ConfigService,
    private readonly prisma: PrismaService,
    private readonly adoption: PlatformAdoptionService,
  ) {}

  assertAuthorized(secretHeader: string | undefined) {
    const expected = this.config.get<string>('BOT_CONTROL_SECRET')?.trim();
    if (!expected) {
      throw new UnauthorizedException('Showcase inference usage push not configured');
    }
    const provided = (secretHeader ?? '').trim();
    if (provided.length !== expected.length) {
      throw new UnauthorizedException('Invalid bot control secret');
    }
    let diff = 0;
    for (let i = 0; i < expected.length; i++) {
      diff |= provided.charCodeAt(i) ^ expected.charCodeAt(i);
    }
    if (diff !== 0) {
      throw new UnauthorizedException('Invalid bot control secret');
    }
  }

  /**
   * Persist batched token usage from the home showcase BTC bot (DeepSeek on
   * bot.doxxedcrypto.digital). Each entry becomes one `AiTokenUsageLog` row.
   */
  async recordBatch(entries: ShowcaseInferenceUsageEntry[]): Promise<{
    received: number;
    recorded: number;
  }> {
    const received = entries.length;
    if (received === 0) return { received: 0, recorded: 0 };

    const userId = await this.resolveAdminUserId();
    let recorded = 0;

    for (const entry of entries) {
      const promptTokens = Math.max(0, Math.floor(Number(entry?.promptTokens ?? 0)));
      const completionTokens = Math.max(0, Math.floor(Number(entry?.completionTokens ?? 0)));
      if (promptTokens <= 0 && completionTokens <= 0) continue;

      try {
        await this.adoption.recordAiUsage({
          userId,
          provider: entry.provider?.trim() || 'deepseek',
          source: entry.source?.trim() || 'showcase_bot',
          promptTokens,
          completionTokens,
          projectId: null,
          billingSource: entry.billingSource?.trim() || 'platform_showcase',
        });
        recorded += 1;
      } catch (err) {
        this.logger.warn(
          `recordAiUsage failed for showcase bot entry: ${err instanceof Error ? err.message : String(err)}`,
        );
      }
    }

    return { received, recorded };
  }

  private async resolveAdminUserId(): Promise<string | null> {
    if (cachedAdminUserId !== undefined) return cachedAdminUserId;
    try {
      const admin = await this.prisma.user.findUnique({
        where: { email: PLATFORM_ADMIN_EMAIL },
        select: { id: true },
      });
      cachedAdminUserId = admin?.id ?? null;
    } catch (err) {
      this.logger.warn(
        `Failed to resolve platform admin for showcase usage: ${err instanceof Error ? err.message : String(err)}`,
      );
      cachedAdminUserId = null;
    }
    return cachedAdminUserId;
  }
}
