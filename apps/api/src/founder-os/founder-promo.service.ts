import { BadRequestException, Injectable } from '@nestjs/common';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
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

/**
 * Promo LLM providers — cost-optimized for onboarding new founders.
 * GLM 5.2 (ZhipuAI) is the default: cheapest $/token with strong coding ability.
 * DeepSeek + Gemini kept as cheap fallbacks. Cursor/OpenAI/Anthropic removed
 * from promo to protect margins — founders can still BYOK those in Settings.
 */
export type PromoCredentialProvider = 'glm' | 'gemini' | 'deepseek';

export type PromoCredentialsMap = Partial<Record<PromoCredentialProvider, string>>;

export type PromoCredentialsStatus = Record<PromoCredentialProvider, boolean>;

const PROMO_PROVIDERS = ['GLM', 'DEEPSEEK', 'GEMINI', 'OLLAMA_LOCAL'] as const;

const PROMO_CREDENTIAL_KEYS: PromoCredentialProvider[] = ['glm', 'gemini', 'deepseek'];

const PROMO_PROVIDER_LABELS: Record<PromoCredentialProvider, string> = {
  glm: 'GLM 5.2 (ZhipuAI)',
  gemini: 'Google Gemini',
  deepseek: 'DeepSeek',
};

/** GLM (ZhipuAI) OpenAI-compatible endpoint + default model for promo Brain calls. */
export const GLM_PROMO_BASE_URL = 'https://open.bigmodel.cn/api/paas/v4';
export const GLM_PROMO_DEFAULT_MODEL = 'glm-5.2';

@Injectable()
export class FounderPromoService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
  ) {}

  async getPlatformPromoSettings() {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const credentialsStatus = this.credentialsStatusFromRow(row?.founderPromoAiCredentialsEnc);
    return {
      enabled: row?.founderPromoAiEnabled ?? false,
      tokenCap: row?.founderPromoTokenCap ?? 30_000_000,
      windowDays: row?.founderPromoWindowDays ?? 90,
      message:
        row?.founderPromoMessage?.trim() ||
        'Sign up — get 3 months free GLM 5.2, Gemini & DeepSeek on Founder OS. No credit card needed.',
      credentialsConfigured: Object.values(credentialsStatus).some(Boolean),
      credentialsStatus,
      credentialsUpdatedAt: row?.updatedAt?.toISOString() ?? null,
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
    await this.prisma.platformSettings.upsert({
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

  async savePromoCredentials(
    userId: string,
    input: Partial<Record<PromoCredentialProvider, string | null>>,
  ) {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const current = this.decryptCredentialsMap(row?.founderPromoAiCredentialsEnc);
    const next: PromoCredentialsMap = { ...current };

    for (const key of PROMO_CREDENTIAL_KEYS) {
      if (!(key in input)) continue;
      const raw = input[key];
      if (raw == null || raw === '') {
        delete next[key];
        continue;
      }
      const trimmed = String(raw).trim();
      if (trimmed.length < 8) {
        throw new BadRequestException(`${PROMO_PROVIDER_LABELS[key]} API key is too short`);
      }
      next[key] = trimmed;
    }

    const enc =
      Object.keys(next).length > 0 ? this.crypto.encrypt(JSON.stringify(next)) : null;

    await this.prisma.platformSettings.upsert({
      where: { id: 'default' },
      create: { id: 'default' },
      update: {},
    });
    await this.prisma.platformSettings.update({
      where: { id: 'default' },
      data: {
        founderPromoAiCredentialsEnc: enc,
        updatedByUserId: userId,
      },
    });
    return this.getPlatformPromoSettings();
  }

  /** Platform-hosted key for promo users — never returns a key unless promo is eligible. */
  async resolvePromoApiKey(
    userId: string,
    provider: PromoCredentialProvider,
  ): Promise<string | null> {
    const status = await this.getUserPromoStatus(userId);
    if (!status.eligible) return null;
    const map = await this.loadDecryptedCredentials();
    return map[provider]?.trim() || null;
  }

  /** Whether a provider can be used via promo (eligible + platform key saved). */
  async hasPromoProvider(userId: string, provider: PromoCredentialProvider): Promise<boolean> {
    const status = await this.getUserPromoStatus(userId);
    if (!status.eligible) return false;
    const map = await this.loadDecryptedCredentials();
    return Boolean(map[provider]?.trim());
  }

  promoEndedMessage(status: FounderPromoStatus): string {
    if (!status.enabled || !status.founderRegistered) {
      return 'Connect your own API keys in Settings → Builder (Step 3) to use Founder Brain.';
    }
    if (status.exhausted) {
      return `You've used your free ${(status.tokenCap / 1_000_000).toFixed(0)}M token promo. Connect your own API keys in Settings → Builder (Step 3) to continue.`;
    }
    return `Your 3-month AI promo has ended. Connect your own API keys in Settings → Builder (Step 3) to keep building.`;
  }

  async getUserPromoStatus(userId: string): Promise<FounderPromoStatus> {
    const [settings, founder, user, usageAgg] = await Promise.all([
      this.getPlatformPromoSettings(),
      this.prisma.founder.findUnique({ where: { userId }, select: { createdAt: true } }),
      this.prisma.user.findUnique({ where: { id: userId }, select: { createdAt: true } }),
      this.prisma.aiTokenUsageLog.aggregate({
        where: { userId, billingSource: 'platform_promo' },
        _sum: { promptTokens: true, completionTokens: true },
      }),
    ]);

    // Promo is available to ALL signed-up users — use founder.createdAt OR user.createdAt
    const registeredAt = founder?.createdAt ?? user?.createdAt ?? null;

    const tokenCap = settings.tokenCap;
    const tokensUsed =
      (usageAgg._sum.promptTokens ?? 0) + (usageAgg._sum.completionTokens ?? 0);
    const tokensRemaining = Math.max(0, tokenCap - tokensUsed);
    const exhausted = tokensUsed >= tokenCap;

    if (!settings.enabled) {
      return {
        enabled: false,
        eligible: false,
        founderRegistered: Boolean(registeredAt),
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

    if (!registeredAt) {
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

    const startedAt = registeredAt;
    const expiresAt = new Date(startedAt);
    expiresAt.setUTCDate(expiresAt.getUTCDate() + settings.windowDays);
    const now = Date.now();
    const withinWindow = now <= expiresAt.getTime();
    const daysRemaining = withinWindow
      ? Math.max(0, Math.ceil((expiresAt.getTime() - now) / (24 * 60 * 60 * 1000)))
      : 0;
    const eligible = withinWindow && !exhausted && settings.credentialsConfigured;

    const baseStatus: FounderPromoStatus = {
      enabled: true,
      eligible,
      founderRegistered: Boolean(registeredAt),
      promoStartedAt: startedAt.toISOString(),
      expiresAt: expiresAt.toISOString(),
      daysRemaining: withinWindow ? daysRemaining : 0,
      tokenCap,
      tokensUsed,
      tokensRemaining,
      exhausted,
      message: eligible ? settings.message : null,
      providers: [...PROMO_PROVIDERS],
    };

    if (eligible) {
      baseStatus.message = settings.message;
      return baseStatus;
    }

    if (!settings.credentialsConfigured) {
      baseStatus.message =
        'AI promo is enabled but platform API keys are not configured yet. Ask your admin to add keys in Connected Accounts.';
      return baseStatus;
    }

    baseStatus.message = this.promoEndedMessage(baseStatus);
    return baseStatus;
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

  private credentialsStatusFromRow(enc: string | null | undefined): PromoCredentialsStatus {
    const map = this.decryptCredentialsMap(enc);
    return {
      glm: Boolean(map.glm),
      gemini: Boolean(map.gemini),
      deepseek: Boolean(map.deepseek),
    };
  }

  private decryptCredentialsMap(enc: string | null | undefined): PromoCredentialsMap {
    if (!enc) return {};
    try {
      const decrypted = this.crypto.decrypt(enc);
      if (!decrypted) return {};
      const parsed = JSON.parse(decrypted) as PromoCredentialsMap;
      return parsed && typeof parsed === 'object' ? parsed : {};
    } catch {
      return {};
    }
  }

  private async loadDecryptedCredentials(): Promise<PromoCredentialsMap> {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    return this.decryptCredentialsMap(row?.founderPromoAiCredentialsEnc);
  }
  async getPlatformBrainStatus() {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    return { configured: Boolean(row?.platformBrainDeepseekKeyEnc), updatedAt: row?.updatedAt?.toISOString() ?? null };
  }
  async savePlatformBrainKey(userId: string, apiKey: string) {
    const trimmed = apiKey.trim();
    if (trimmed.length < 8) throw new BadRequestException('DeepSeek API key is too short');
    const enc = this.crypto.encrypt(trimmed);
    await this.prisma.platformSettings.upsert({ where: { id: 'default' }, create: { id: 'default', platformBrainDeepseekKeyEnc: enc }, update: { platformBrainDeepseekKeyEnc: enc, updatedByUserId: userId } });
    return this.getPlatformBrainStatus();
  }
  async removePlatformBrainKey(userId: string) {
    await this.prisma.platformSettings.update({ where: { id: 'default' }, data: { platformBrainDeepseekKeyEnc: null, updatedByUserId: userId } });
    return this.getPlatformBrainStatus();
  }
  async getDecryptedPlatformDeepseekKey(): Promise<string | null> {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    if (!row?.platformBrainDeepseekKeyEnc) return null;
    try { return this.crypto.decrypt(row.platformBrainDeepseekKeyEnc); } catch { return null; }
  }

}
