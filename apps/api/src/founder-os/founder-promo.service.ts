import { BadRequestException, HttpException, HttpStatus, Injectable } from '@nestjs/common';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { PrismaService } from '../prisma/prisma.service';
import { BuilderScoreService } from './builder-score.service';

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

/** GLM (ZhipuAI / z.ai) OpenAI-compatible endpoint + default model for promo Brain calls. */
export { getGlmApiBaseUrl, getGlmDefaultModel, GLM_PROMO_BASE_URL, GLM_PROMO_DEFAULT_MODEL } from './glm-config';

@Injectable()
export class FounderPromoService {
  constructor(
    private readonly prisma: PrismaService,
    private readonly crypto: CredentialsCryptoService,
    private readonly builderScore: BuilderScoreService,
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

  /**
   * Platform-hosted key for promo users — never returns a key unless promo is eligible.
   *
   * Acquires a per-user Postgres advisory lock around the eligibility check so
   * parallel requests for the same user serialize instead of all reading the
   * same `tokensUsed < cap` snapshot before any of them logs usage. This shrinks
   * the promo-cap overshoot window dramatically when combined with the
   * per-user rate limiter (10/hr) now applied to every AI route.
   *
   * TODO(full-reservation): for a hard cap, insert a `platform_promo` row into
   * `aiTokenUsageLog` with estimated prompt tokens inside this same lock BEFORE
   * returning the key, then update it with real completion tokens after the LLM
   * call. That requires plumbing the reservation id through the invoker — left
   * for a follow-up; the rate limiter + advisory lock already bound the burst.
   */
  async resolvePromoApiKey(
    userId: string,
    provider: PromoCredentialProvider,
  ): Promise<string | null> {
    // Per-user advisory lock — hashtext gives a stable int32 per userId.
    await this.prisma.$executeRaw`SELECT pg_advisory_lock(hashtext(${userId}))`;
    try {
      const status = await this.getUserPromoStatus(userId);
      if (!status.eligible) return null;

      // Tier-based daily token cap + pool preservation. Happens AFTER the
      // xVerified gate + advisory lock, so every other gate still applies.
      await this.enforceTierCap(userId, status.tokensRemaining, status.tokenCap);

      const map = await this.loadDecryptedCredentials();
      return map[provider]?.trim() || null;
    } finally {
      await this.prisma.$executeRaw`SELECT pg_advisory_unlock(hashtext(${userId}))`;
    }
  }

  /**
   * Two-tier builder protection gate. Throws 429 if:
   *  - the user's daily platform-token usage exceeds their tier cap, OR
   *  - the global promo pool is below the preservation threshold and the
   *    user is PARASITE-tier (pool reserved for verified builders when low).
   *
   * PARASITE_DAILY_TOKEN_CAP default 25000, BUILDER_DAILY_TOKEN_CAP default
   * 500000, PROMO_POOL_PRESERVATION_PCT default 0.30.
   */
  private async enforceTierCap(
    userId: string,
    tokensRemaining: number,
    tokenCap: number,
  ): Promise<void> {
    const PARASITE_CAP = Number.parseInt(process.env.PARASITE_DAILY_TOKEN_CAP ?? '25000', 10);
    const BUILDER_CAP = Number.parseInt(process.env.BUILDER_DAILY_TOKEN_CAP ?? '500000', 10);
    const POOL_PRESERVATION_PCT = Number.parseFloat(
      process.env.PROMO_POOL_PRESERVATION_PCT ?? '0.30',
    );

    const [tier, dailyUsage] = await Promise.all([
      this.builderScore.getTier(userId),
      this.builderScore.dailyTokenUsage(userId),
    ]);

    const cap = tier === 'VERIFIED_BUILDER' ? BUILDER_CAP : PARASITE_CAP;
    if (dailyUsage >= cap) {
      const upgradeHint =
        tier === 'VERIFIED_BUILDER'
          ? 'Daily builder token cap reached. Connect your own API key to continue.'
          : 'Daily parasite-tier token cap reached. Connect GitHub + Cursor + push a commit to upgrade to Verified Builder.';
      throw new HttpException(
        {
          statusCode: HttpStatus.TOO_MANY_REQUESTS,
          message: upgradeHint,
          tier,
          dailyUsage,
          cap,
        },
        HttpStatus.TOO_MANY_REQUESTS,
      );
    }

    // Pool preservation: when remaining < threshold, starve parasite tier.
    const poolRemainingFraction = tokenCap > 0 ? tokensRemaining / tokenCap : 0;
    if (tier === 'PARASITE' && poolRemainingFraction < POOL_PRESERVATION_PCT) {
      throw new HttpException(
        {
          statusCode: HttpStatus.TOO_MANY_REQUESTS,
          message:
            'Promo pool reserved for verified builders. Connect GitHub + Cursor + push a commit to upgrade.',
          tier,
          poolRemainingFraction,
        },
        HttpStatus.TOO_MANY_REQUESTS,
      );
    }
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
      this.prisma.user.findUnique({
        where: { id: userId },
        select: { createdAt: true, xVerified: true, twitterHandle: true },
      }),
      this.prisma.aiTokenUsageLog.aggregate({
        where: { userId, billingSource: 'platform_promo' },
        _sum: { promptTokens: true, completionTokens: true },
      }),
    ]);

    // Free-token eligibility gate: only accounts with a verified X/Twitter
    // connection can draw from the 30M-token promo pool. Blocks signup-bonus
    // farming and burner-account abuse. Uses the existing `xVerified` flag
    // (set on X OAuth + blue-verified flow) — no schema migration needed.
    const twitterVerified = Boolean(user?.xVerified);
    const TWITTER_GATE_MESSAGE =
      'Free AI tokens require a verified Twitter account. Connect your X account in Settings → Connected Accounts to claim the promo.';

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
    const eligible = withinWindow && !exhausted && settings.credentialsConfigured && twitterVerified;

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

    if (!twitterVerified) {
      baseStatus.message = TWITTER_GATE_MESSAGE;
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

  /** Decrypted platform GLM (ZhipuAI) promo key, or null if not configured. */
  async getDecryptedPlatformGlmKey(): Promise<string | null> {
    const map = await this.loadDecryptedCredentials();
    return map.glm ?? null;
  }

  /** Decrypted platform DeepSeek promo key (distinct from platform brain fallback). */
  async getDecryptedPlatformPromoDeepseekKey(): Promise<string | null> {
    const map = await this.loadDecryptedCredentials();
    return map.deepseek ?? null;
  }

  /**
   * Returns the list of AI "brains" the calling user can pick from in the
   * workspace chat dropdown. Reflects what is actually wired up:
   *   - GLM / Gemini / DeepSeek: available when an admin has saved a platform
   *     promo key for that provider (any signed-up founder can use them while
   *     the promo window is open).
   *   - DeepSeek is also available when the legacy platform-brain DeepSeek key
   *     is configured (separate column on PlatformSettings).
   *   - OLLAMA: available when the user's own Founder Node has heartbeated in
   *     the last 3 minutes (user-scoped, requires userId).
   *   - RULE_BASED: always available as the free deterministic fallback.
   * The frontend merges this with a locally-stored BYOK option.
   */
  async getAvailableBrains(userId?: string): Promise<AvailableBrain[]> {
    const [settings, platformBrain] = await Promise.all([
      this.getPlatformPromoSettings(),
      this.getPlatformBrainStatus(),
    ]);

    const creds = settings.credentialsStatus;
    const brains: AvailableBrain[] = [
      {
        key: 'GLM',
        label: 'GLM 5.2',
        hint: 'Promo - fast',
        available: Boolean(creds.glm),
      },
      {
        key: 'DEEPSEEK',
        label: 'DeepSeek',
        hint: 'Platform brain',
        available: Boolean(creds.deepseek) || platformBrain.configured,
      },
      {
        key: 'GEMINI',
        label: 'Gemini',
        hint: 'Google',
        available: Boolean(creds.gemini),
      },
    ];

    if (userId) {
      const node = await this.prisma.founderNode.findFirst({
        where: { userId },
        orderBy: { lastSeenAt: 'desc' },
        select: { lastSeenAt: true },
      });
      const nodeOnline = Boolean(
        node?.lastSeenAt && Date.now() - node.lastSeenAt.getTime() < 180_000,
      );
      brains.push({
        key: 'OLLAMA',
        label: 'Ollama',
        hint: 'Local - Founder Node',
        available: nodeOnline,
      });
    }

    brains.push({
      key: 'RULE_BASED',
      label: 'Rule-based',
      hint: 'Free fallback',
      available: true,
    });

    return brains;
  }

}

export type AvailableBrain = {
  key: string;
  label: string;
  hint: string;
  available: boolean;
};
