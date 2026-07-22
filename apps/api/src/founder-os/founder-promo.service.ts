import { BadRequestException, HttpException, HttpStatus, Injectable } from '@nestjs/common';
import {
  estimateFounderManagedReservation,
  founderQuotaWindow,
  FOUNDER_MANAGED_RESERVATION_TTL_MINUTES,
} from '@dcf/utils';
import { CredentialsCryptoService } from '../credentials/credentials-crypto.service';
import { PrismaService } from '../prisma/prisma.service';
import { BuilderScoreService } from './builder-score.service';
import {
  chargeForManagedReservation,
  reconcileProviderUsage,
  type ProviderTokenUsage,
} from './founder-managed-quota';
import {
  FOUNDER_FREE_ALLOWANCE_WINDOW_DAYS,
  FOUNDER_FREE_MANAGED_TOKEN_CAP,
} from './founder-free.config';
import { FounderPlanEntitlementsService } from './founder-plan-entitlements.service';
import type { FounderPlanName } from './founder-plan-entitlements.service';

export type FounderPromoStatus = {
  plan: 'free' | 'builder' | 'team';
  priceCentsMonthly: number | null;
  teamId: string | null;
  teamName: string | null;
  teamRole: 'owner' | 'admin' | 'member' | null;
  coordination: boolean;
  remoteControl: boolean;
  rolesAndAudit: boolean;
  unit: 'weighted_tokens';
  weightsVersion: 'founder-wtu-v1';
  enabled: boolean;
  eligible: boolean;
  founderRegistered: boolean;
  promoStartedAt: string | null;
  expiresAt: string | null;
  daysRemaining: number | null;
  tokenCap: number;
  tokensUsed: number;
  reservedWeightedUnits: number;
  tokensRemaining: number;
  exhausted: boolean;
  message: string | null;
  providers: string[];
};

export type FounderManagedReservation = {
  id: string;
  requestId: string;
  reservedWeightedUnits: number;
  expiresAt: string;
};

/** Platform-managed providers. Production routing only enables health-verified models. */
export type PromoCredentialProvider = 'glm' | 'gemini' | 'deepseek';

export type PromoCredentialsMap = Partial<Record<PromoCredentialProvider, string>>;

export type PromoCredentialsStatus = Record<PromoCredentialProvider, boolean>;

const MANAGED_FOUNDER_PROVIDERS = ['DEEPSEEK', 'OLLAMA_LOCAL'] as const;

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
    private readonly planEntitlements: FounderPlanEntitlementsService,
  ) {}

  /** Resolve the plan before routing so Free requests cannot select managed Pro. */
  async managedPlanForUser(userId: string): Promise<FounderPlanName> {
    const entitlement = await this.planEntitlements.resolve(userId);
    return entitlement.plan;
  }

  async getPlatformPromoSettings() {
    const row = await this.prisma.platformSettings.findUnique({ where: { id: 'default' } });
    const credentialsStatus = this.credentialsStatusFromRow(row?.founderPromoAiCredentialsEnc);
    return {
      enabled: row?.founderPromoAiEnabled ?? false,
      tokenCap: row?.founderPromoTokenCap ?? FOUNDER_FREE_MANAGED_TOKEN_CAP,
      windowDays: row?.founderPromoWindowDays ?? FOUNDER_FREE_ALLOWANCE_WINDOW_DAYS,
      message:
        row?.founderPromoMessage?.trim() ||
        'Founder Free is available. Connect your own provider or local model at any time.',
      credentialsConfigured: credentialsStatus.deepseek,
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
   * Atomically reserve managed weighted units before any provider request.
   * The transaction-level advisory lock makes the user's remaining balance a
   * single-writer decision even when several IDE sessions submit together.
   */
  async reserveManagedUsage(input: {
    userId: string;
    requestId: string;
    provider: string;
    model: string;
    tier: string;
    estimatedInputTokens: number;
    maxOutputTokens?: number;
  }): Promise<FounderManagedReservation> {
    const reservedWeightedUnits = estimateFounderManagedReservation({
      inputTokens: input.estimatedInputTokens,
      maxOutputTokens: input.maxOutputTokens,
    });
    const estimatedOutputTokens = input.maxOutputTokens ?? 4_096;
    const now = new Date();
    const entitlement = await this.planEntitlements.resolve(input.userId, now);

    return this.prisma.$transaction(async (tx) => {
      await tx.$executeRaw`SELECT pg_advisory_xact_lock(hashtext(${entitlement.quotaOwnerKey}))`;

      const existing = await tx.aiManagedReservation.findUnique({
        where: { requestId: input.requestId },
      });
      if (existing) {
        if (existing.userId !== input.userId) {
          throw new HttpException('Reservation request id is already in use', HttpStatus.CONFLICT);
        }
        return {
          id: existing.id,
          requestId: existing.requestId,
          reservedWeightedUnits: existing.reservedWeightedUnits,
          expiresAt: existing.expiresAt.toISOString(),
        };
      }

      const [settings, founder, user] = await Promise.all([
        tx.platformSettings.findUnique({ where: { id: 'default' } }),
        tx.founder.findUnique({
          where: { userId: input.userId },
          select: { createdAt: true },
        }),
        tx.user.findUnique({
          where: { id: input.userId },
          select: { createdAt: true, xVerified: true },
        }),
      ]);
      const registeredAt = founder?.createdAt ?? user?.createdAt ?? null;
      const quotaAnchor = entitlement.currentPeriodStart
        ? new Date(entitlement.currentPeriodStart)
        : registeredAt;
      const enabled = settings?.founderPromoAiEnabled ?? false;
      const xGateFailed = entitlement.requiresXVerification && !user?.xVerified;
      if (!enabled || !quotaAnchor || xGateFailed) {
        throw new HttpException(
          {
            statusCode: HttpStatus.PAYMENT_REQUIRED,
            message: xGateFailed
              ? 'Founder Free requires a verified X account. Connect X or use a personal/local model.'
              : 'Founder managed AI is unavailable. Continue with a personal or local model.',
            code: 'FOUNDER_MANAGED_NOT_ELIGIBLE',
          },
          HttpStatus.PAYMENT_REQUIRED,
        );
      }

      const tokenCap = entitlement.weeklyWeightedUnitCap;
      const window = founderQuotaWindow(
        quotaAnchor,
        now,
        FOUNDER_FREE_ALLOWANCE_WINDOW_DAYS,
      );

      await tx.aiManagedReservation.updateMany({
        where: {
          quotaOwnerKey: entitlement.quotaOwnerKey,
          status: 'RESERVED',
          upstreamStartedAt: null,
          expiresAt: { lt: now },
        },
        data: { status: 'RELEASED', reconciledAt: now },
      });

      const reservations = await tx.aiManagedReservation.findMany({
          where: {
            quotaOwnerKey: entitlement.quotaOwnerKey,
            createdAt: { gte: window.startsAt, lt: window.resetsAt },
            status: { in: ['RESERVED', 'RECONCILED', 'UNCERTAIN'] },
          },
          select: {
            status: true,
            reservedWeightedUnits: true,
            actualWeightedUnits: true,
          },
        });
      const legacyUsage = entitlement.plan === 'team'
        ? { _sum: { promptTokens: null, completionTokens: null } }
        : await tx.aiTokenUsageLog.aggregate({
          where: {
            userId: input.userId,
            billingSource: { in: ['platform_promo', 'platform_brain'] },
            createdAt: { gte: window.startsAt, lt: window.resetsAt },
          },
          _sum: { promptTokens: true, completionTokens: true },
        });
      const reservationUsage = reservations.reduce(
        (sum, row) => sum + chargeForManagedReservation(row),
        0,
      );
      const legacyWeightedUsage =
        (legacyUsage._sum.promptTokens ?? 0) +
        (legacyUsage._sum.completionTokens ?? 0) * 3;
      const usedWeightedUnits = reservationUsage + legacyWeightedUsage;
      const remainingWeightedUnits = Math.max(0, tokenCap - usedWeightedUnits);
      if (reservedWeightedUnits > remainingWeightedUnits) {
        throw new HttpException(
          {
            statusCode: HttpStatus.TOO_MANY_REQUESTS,
            code: 'FOUNDER_MANAGED_QUOTA_EXCEEDED',
            message:
              'This request exceeds the remaining Founder Free quota. Reduce the output limit or switch to a personal/local model.',
            unit: 'weighted_tokens',
            weightsVersion: 'founder-wtu-v1',
            cap: tokenCap,
            used: usedWeightedUnits,
            remaining: remainingWeightedUnits,
            requested: reservedWeightedUnits,
            resetsAt: window.resetsAt.toISOString(),
          },
          HttpStatus.TOO_MANY_REQUESTS,
        );
      }

      const expiresAt = new Date(
        now.getTime() + FOUNDER_MANAGED_RESERVATION_TTL_MINUTES * 60_000,
      );
      const reservation = await tx.aiManagedReservation.create({
        data: {
          requestId: input.requestId,
          userId: input.userId,
          quotaOwnerKey: entitlement.quotaOwnerKey,
          provider: input.provider,
          model: input.model,
          tier: input.tier,
          reservedWeightedUnits,
          estimatedInputTokens: input.estimatedInputTokens,
          estimatedOutputTokens,
          expiresAt,
        },
      });
      return {
        id: reservation.id,
        requestId: reservation.requestId,
        reservedWeightedUnits: reservation.reservedWeightedUnits,
        expiresAt: reservation.expiresAt.toISOString(),
      };
    });
  }

  async markManagedReservationStarted(userId: string, requestId: string): Promise<void> {
    await this.prisma.aiManagedReservation.updateMany({
      where: { userId, requestId, status: 'RESERVED' },
      data: { upstreamStartedAt: new Date() },
    });
  }

  async releaseManagedReservation(userId: string, requestId: string): Promise<void> {
    await this.prisma.aiManagedReservation.updateMany({
      where: { userId, requestId, status: 'RESERVED' },
      data: { status: 'RELEASED', reconciledAt: new Date() },
    });
  }

  async markManagedReservationUncertain(userId: string, requestId: string): Promise<void> {
    await this.prisma.aiManagedReservation.updateMany({
      where: { userId, requestId, status: 'RESERVED' },
      data: { status: 'UNCERTAIN' },
    });
  }

  async reconcileManagedReservation(input: {
    userId: string;
    requestId: string;
    usage?: ProviderTokenUsage | null;
  }): Promise<number> {
    const reservation = await this.prisma.aiManagedReservation.findFirst({
      where: { userId: input.userId, requestId: input.requestId },
    });
    if (!reservation) {
      throw new Error(`Managed reservation ${input.requestId} was not found`);
    }
    if (reservation.status === 'RECONCILED') {
      return reservation.actualWeightedUnits ?? reservation.reservedWeightedUnits;
    }
    if (reservation.status === 'RELEASED') return 0;

    const usage = reconcileProviderUsage(input.usage, reservation.reservedWeightedUnits);
    await this.prisma.aiManagedReservation.update({
      where: { id: reservation.id },
      data: {
        status: 'RECONCILED',
        actualWeightedUnits: usage.weightedUnits,
        actualInputTokens: usage.inputTokens,
        actualCachedTokens: usage.cachedInputTokens,
        actualOutputTokens: usage.outputTokens,
        actualReasoningTokens: usage.reasoningTokens,
        reconciledAt: new Date(),
      },
    });
    return usage.weightedUnits;
  }

  /**
   * Legacy website copilot key lookup. Founder IDE and the Founder AI gateway
   * use reserveManagedUsage(), which writes a durable reservation before any
   * provider call. BuilderService keeps this serialized fallback only until
   * its older copilot paths are consolidated onto the gateway contract.
   */
  async resolvePromoApiKey(
    userId: string,
    provider: PromoCredentialProvider,
  ): Promise<string | null> {
    // Founder V1 has one managed cloud provider. Other stored credentials are
    // retained for admin migration only; GLM/Gemini remain personal BYOK.
    if (provider !== 'deepseek') return null;
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
          : 'Founder Free daily allowance reached. Connect GitHub and push a recent commit to increase your builder fair-use tier, or continue with personal or local AI.';
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
    if (provider !== 'deepseek') return false;
    const status = await this.getUserPromoStatus(userId);
    if (!status.eligible) return false;
    const map = await this.loadDecryptedCredentials();
    return Boolean(map[provider]?.trim());
  }

  promoEndedMessage(status: FounderPromoStatus): string {
    const label = status.plan === 'builder'
      ? 'Founder Builder'
      : status.plan === 'team'
        ? 'Founder Team'
        : 'Founder Free';
    if (status.enabled && status.founderRegistered && !status.exhausted) {
      return `${label} managed access is unavailable. Connect personal AI in Founder Settings to keep building.`;
    }
    if (!status.enabled || !status.founderRegistered) {
      return 'Connect personal AI in Founder Settings to use Founder AI.';
    }
    if (status.exhausted) {
      return `You have used your ${label} quota. Connect personal AI in Founder Settings to continue.`;
    }
    return `${label} managed access is unavailable. Connect personal AI in Founder Settings to keep building.`;
  }

  async getUserPromoStatus(userId: string): Promise<FounderPromoStatus> {
    const [settings, founder, user, entitlement] = await Promise.all([
      this.getPlatformPromoSettings(),
      this.prisma.founder.findUnique({ where: { userId }, select: { createdAt: true } }),
      this.prisma.user.findUnique({
        where: { id: userId },
        select: { createdAt: true, xVerified: true, twitterHandle: true },
      }),
      this.planEntitlements.resolve(userId),
    ]);

    // Free-token eligibility gate: only accounts with a verified X/Twitter
    // connection can draw from the managed allowance. Blocks signup-bonus
    // farming and burner-account abuse. Uses the existing `xVerified` flag
    // (set on X OAuth + blue-verified flow) — no schema migration needed.
    const twitterVerified = Boolean(user?.xVerified);
    const TWITTER_GATE_MESSAGE =
      'Founder Free requires a verified X account. Connect X in Founder Settings to activate your quota.';

    // Promo is available to ALL signed-up users — use founder.createdAt OR user.createdAt
    const registeredAt = founder?.createdAt ?? user?.createdAt ?? null;

    const tokenCap = entitlement.weeklyWeightedUnitCap;
    const now = new Date();
    const quotaAnchor = entitlement.currentPeriodStart
      ? new Date(entitlement.currentPeriodStart)
      : registeredAt;
    const window = quotaAnchor
      ? founderQuotaWindow(quotaAnchor, now, FOUNDER_FREE_ALLOWANCE_WINDOW_DAYS)
      : null;

    if (window) {
      await this.prisma.aiManagedReservation.updateMany({
        where: {
          quotaOwnerKey: entitlement.quotaOwnerKey,
          status: 'RESERVED',
          upstreamStartedAt: null,
          expiresAt: { lt: now },
        },
        data: { status: 'RELEASED', reconciledAt: now },
      });
    }

    const reservations = window
      ? await this.prisma.aiManagedReservation.findMany({
            where: {
              quotaOwnerKey: entitlement.quotaOwnerKey,
              createdAt: { gte: window.startsAt, lt: window.resetsAt },
              status: { in: ['RESERVED', 'RECONCILED', 'UNCERTAIN'] },
            },
            select: {
              status: true,
              reservedWeightedUnits: true,
              actualWeightedUnits: true,
            },
          })
      : [];
    const legacyUsage = window && entitlement.plan !== 'team'
      ? await this.prisma.aiTokenUsageLog.aggregate({
            where: {
              userId,
              billingSource: { in: ['platform_promo', 'platform_brain'] },
              createdAt: { gte: window.startsAt, lt: window.resetsAt },
            },
            _sum: { promptTokens: true, completionTokens: true },
          })
      : { _sum: { promptTokens: null, completionTokens: null } };

    const tokensUsed = Math.ceil(
      reservations.reduce((sum, row) => sum + chargeForManagedReservation(row), 0) +
        (legacyUsage._sum.promptTokens ?? 0) +
        (legacyUsage._sum.completionTokens ?? 0) * 3,
    );
    const reservedWeightedUnits = Math.ceil(
      reservations
        .filter((row) => row.status === 'RESERVED')
        .reduce((sum, row) => sum + row.reservedWeightedUnits, 0),
    );
    const tokensRemaining = Math.max(0, tokenCap - tokensUsed);
    const exhausted = tokensUsed >= tokenCap;

    const statusBase = {
      plan: entitlement.plan,
      priceCentsMonthly: entitlement.priceCentsMonthly,
      teamId: entitlement.teamId,
      teamName: entitlement.teamName,
      teamRole: entitlement.teamRole,
      coordination: entitlement.coordination,
      remoteControl: entitlement.remoteControl,
      rolesAndAudit: entitlement.rolesAndAudit,
      unit: 'weighted_tokens' as const,
      weightsVersion: 'founder-wtu-v1' as const,
      tokenCap,
      tokensUsed,
      reservedWeightedUnits,
      tokensRemaining,
      exhausted,
      providers: [...MANAGED_FOUNDER_PROVIDERS],
    };

    if (!settings.enabled) {
      return {
        ...statusBase,
        enabled: false,
        eligible: false,
        founderRegistered: Boolean(registeredAt),
        promoStartedAt: null,
        expiresAt: null,
        daysRemaining: null,
        message: null,
      };
    }

    if (!registeredAt || !window) {
      return {
        ...statusBase,
        enabled: true,
        eligible: false,
        founderRegistered: false,
        promoStartedAt: null,
        expiresAt: null,
        daysRemaining: null,
        message: settings.message,
      };
    }

    const daysRemaining = Math.max(
      0,
      Math.ceil((window.resetsAt.getTime() - now.getTime()) / (24 * 60 * 60 * 1000)),
    );
    const xEligible = !entitlement.requiresXVerification || twitterVerified;
    const eligible = !exhausted && settings.credentialsConfigured && xEligible;

    const baseStatus: FounderPromoStatus = {
      ...statusBase,
      enabled: true,
      eligible,
      founderRegistered: Boolean(registeredAt),
      promoStartedAt: window.startsAt.toISOString(),
      expiresAt: window.resetsAt.toISOString(),
      daysRemaining,
      message: eligible ? settings.message : null,
    };

    if (eligible) {
      baseStatus.message = settings.message;
      return baseStatus;
    }

    if (!xEligible) {
      baseStatus.message = TWITTER_GATE_MESSAGE;
      return baseStatus;
    }

    if (!settings.credentialsConfigured) {
      baseStatus.message =
        'Founder managed AI is enabled but DeepSeek capacity is not configured yet. Ask an admin to review AI & Usage.';
      return baseStatus;
    }

    baseStatus.message = this.promoEndedMessage(baseStatus);
    return baseStatus;
  }

  async getPromoUsageByProvider(userId: string) {
    const [status, entitlement] = await Promise.all([
      this.getUserPromoStatus(userId),
      this.planEntitlements.resolve(userId),
    ]);
    if (!status.promoStartedAt || !status.expiresAt) return [];
    const [reservations, logs] = await Promise.all([
      this.prisma.aiManagedReservation.findMany({
        where: {
          quotaOwnerKey: entitlement.quotaOwnerKey,
          createdAt: {
            gte: new Date(status.promoStartedAt),
            lt: new Date(status.expiresAt),
          },
          status: { in: ['RESERVED', 'RECONCILED', 'UNCERTAIN'] },
        },
        select: {
          provider: true,
          status: true,
          reservedWeightedUnits: true,
          actualWeightedUnits: true,
        },
      }),
      entitlement.plan === 'team'
        ? Promise.resolve([])
        : this.prisma.aiTokenUsageLog.groupBy({
        by: ['provider'],
        where: {
          userId,
          billingSource: { in: ['platform_promo', 'platform_brain'] },
          createdAt: {
            gte: new Date(status.promoStartedAt),
            lt: new Date(status.expiresAt),
          },
        },
        _sum: { promptTokens: true, completionTokens: true },
          }),
    ]);
    const byProvider = new Map<string, number>();
    for (const row of reservations) {
      byProvider.set(
        row.provider,
        (byProvider.get(row.provider) ?? 0) + chargeForManagedReservation(row),
      );
    }
    for (const row of logs) {
      byProvider.set(
        row.provider,
        (byProvider.get(row.provider) ?? 0) +
          (row._sum.promptTokens ?? 0) +
          (row._sum.completionTokens ?? 0) * 3,
      );
    }
    return [...byProvider.entries()].map(([provider, weightedUnits]) => ({
      provider,
      weightedUnits,
      unit: 'weighted_tokens' as const,
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

  /**
   * Legacy compatibility surface. Managed GLM is disabled in Founder V1;
   * founders can still connect GLM as a personal provider in Founder IDE.
   */
  async getDecryptedPlatformGlmKey(): Promise<string | null> {
    return null;
  }

  /** Decrypted platform DeepSeek promo key (distinct from platform brain fallback). */
  async getDecryptedPlatformPromoDeepseekKey(): Promise<string | null> {
    const map = await this.loadDecryptedCredentials();
    return map.deepseek ?? null;
  }

  /**
   * Returns the list of AI "brains" the calling user can pick from in the
   * workspace chat dropdown. Reflects what is actually wired up:
   *   - DeepSeek: available when the managed or legacy platform-brain key
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
        key: 'DEEPSEEK',
        label: 'DeepSeek',
        hint: 'Founder managed',
        available: Boolean(creds.deepseek) || platformBrain.configured,
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
