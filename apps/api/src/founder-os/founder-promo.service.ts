import { Injectable } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

export type FounderPromoStatus = {
  enabled: boolean;
  eligible: boolean;
  founderRegistered: boolean;
  promoStartedAt: string | null;
  expiresAt: string | null;
  daysRemaining: number | null;
  tokenCap: number;
  tokensUsed: number;
  tokensRemaining: number;
  exhausted: boolean;
  message: string | null;
  providers: string[];
};

const PROMO_PROVIDERS = ['GEMINI', 'DEEPSEEK', 'CURSOR', 'OLLAMA_LOCAL', 'OPENAI', 'ANTHROPIC'] as const;

@Injectable()
export class FounderPromoService {
  constructor(private readonly prisma: PrismaService) {}

  async getPlatformPromoSettings() {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    return {
      enabled: row?.founderPromoAiEnabled ?? false,
      tokenCap: row?.founderPromoTokenCap ?? 10_000_000,
      windowDays: row?.founderPromoWindowDays ?? 30,
      message:
        row?.founderPromoMessage?.trim() ||
        'Join as a founder — get 1 month free access to Cursor, Gemini, DeepSeek & more on Founder OS.',
      credentialsConfigured: Boolean(row?.founderPromoAiCredentialsEnc),
    };
  }

  async updatePlatformPromoSettings(
    userId: string,
    input: {
      enabled?: boolean;
      tokenCap?: number;
      windowDays?: number;
      message?: string;
    },
  ) {
    const row = await this.prisma.platformSettings.upsert({
      where: { id: 'default' },
      create: { id: 'default' },
      update: {},
    });
    await this.prisma.platformSettings.update({
      where: { id: 'default' },
      data: {
        ...(input.enabled !== undefined ? { founderPromoAiEnabled: input.enabled } : {}),
        ...(input.tokenCap !== undefined ? { founderPromoTokenCap: input.tokenCap } : {}),
        ...(input.windowDays !== undefined ? { founderPromoWindowDays: input.windowDays } : {}),
        ...(input.message !== undefined ? { founderPromoMessage: input.message || null } : {}),
        updatedByUserId: userId,
      },
    });
    return this.getPlatformPromoSettings();
  }

  async getUserPromoStatus(userId: string): Promise<FounderPromoStatus> {
    const [settings, founder, usageAgg] = await Promise.all([
      this.getPlatformPromoSettings(),
      this.prisma.founder.findUnique({ where: { userId }, select: { createdAt: true } }),
      this.prisma.aiTokenUsageLog.aggregate({
        where: { userId, billingSource: 'platform_promo' },
        _sum: { promptTokens: true, completionTokens: true },
      }),
    ]);

    const tokenCap = settings.tokenCap;
    const tokensUsed =
      (usageAgg._sum.promptTokens ?? 0) + (usageAgg._sum.completionTokens ?? 0);
    const tokensRemaining = Math.max(0, tokenCap - tokensUsed);
    const exhausted = tokensUsed >= tokenCap;

    if (!settings.enabled) {
      return {
        enabled: false,
        eligible: false,
        founderRegistered: Boolean(founder),
        promoStartedAt: null,
        expiresAt: null,
        daysRemaining: null,
        tokenCap,
        tokensUsed,
        tokensRemaining,
        exhausted,
        message: null,
        providers: [...PROMO_PROVIDERS],
      };
    }

    if (!founder) {
      return {
        enabled: true,
        eligible: false,
        founderRegistered: false,
        promoStartedAt: null,
        expiresAt: null,
        daysRemaining: null,
        tokenCap,
        tokensUsed: 0,
        tokensRemaining: tokenCap,
        exhausted: false,
        message: settings.message,
        providers: [...PROMO_PROVIDERS],
      };
    }

    const startedAt = founder.createdAt;
    const expiresAt = new Date(startedAt);
    expiresAt.setUTCDate(expiresAt.getUTCDate() + settings.windowDays);
    const now = Date.now();
    const withinWindow = now <= expiresAt.getTime();
    const daysRemaining = withinWindow
      ? Math.max(0, Math.ceil((expiresAt.getTime() - now) / (24 * 60 * 60 * 1000)))
      : 0;
    const eligible = withinWindow && !exhausted;

    return {
      enabled: true,
      eligible,
      founderRegistered: true,
      promoStartedAt: startedAt.toISOString(),
      expiresAt: expiresAt.toISOString(),
      daysRemaining: withinWindow ? daysRemaining : 0,
      tokenCap,
      tokensUsed,
      tokensRemaining,
      exhausted,
      message: eligible
        ? settings.message
        : exhausted
          ? `You've used your free ${(tokenCap / 1_000_000).toFixed(0)}M token demo. Connect your own API keys in Connected Accounts to continue.`
          : `Your 1-month founder promo ended. Connect your own keys to keep using Founder Brain.`,
      providers: [...PROMO_PROVIDERS],
    };
  }

  async getPromoUsageByProvider(userId: string) {
    const logs = await this.prisma.aiTokenUsageLog.groupBy({
      by: ['provider'],
      where: { userId, billingSource: 'platform_promo' },
      _sum: { promptTokens: true, completionTokens: true },
    });
    return logs.map((l) => ({
      provider: l.provider,
      tokens: (l._sum.promptTokens ?? 0) + (l._sum.completionTokens ?? 0),
    }));
  }
}
