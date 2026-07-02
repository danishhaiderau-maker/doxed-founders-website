import { Global, Injectable, Logger, Module } from '@nestjs/common';
import { PrismaService } from '../prisma/prisma.service';

/**
 * Two-tier builder protection.
 *
 * Tier is derived from a composite score:
 *   +30  xVerified = true
 *   +25  GitHub connected (GitHubConnection row exists)
 *   +25  Cursor connected (IntegrationCredential provider='cursor' with verifiedAt)
 *   +1   per GITHUB_COMMIT / GITHUB_PR_MERGED FounderEvent in last 14d (cap +20)
 *   +10  account age > 7 days
 *   -50  abuse flag (rate-limited 10+ times in 24h, OR balance < 0,
 *        OR >100 AiTokenUsageLog rows in a single 1h window)
 *
 * Score >= BUILDER_SCORE_THRESHOLD (default 50) → VERIFIED_BUILDER, else PARASITE.
 *
 * The score is cached on User.builderScore / User.builderTier / scoreRefreshedAt
 * (added in `prisma/schema.prisma`). Because the columns may not exist yet on
 * the prod DB until `prisma migrate deploy` runs, all reads/writes of those
 * columns go through raw SQL wrapped in try/catch and default to PARASITE on
 * any error. This lets the code ship before the migration lands.
 */

export type BuilderTier = 'PARASITE' | 'VERIFIED_BUILDER';

const SCORE_THRESHOLD = Number.parseInt(process.env.BUILDER_SCORE_THRESHOLD ?? '50', 10);
const REFRESH_TTL_MS = Number.parseInt(process.env.BUILDER_SCORE_REFRESH_TTL_MS ?? '3600000', 10);
const COMMIT_LOOKBACK_DAYS = 14;
const COMMIT_SCORE_CAP = 20;
const ABUSE_RATE_LIMIT_HITS = 10;
const ABUSE_HOURLY_AI_CALLS = 100;

@Injectable()
export class BuilderScoreService {
  private readonly logger = new Logger(BuilderScoreService.name);

  constructor(private readonly prisma: PrismaService) {}

  /** Compute the composite builder score for a user (no caching). */
  async computeScore(userId: string): Promise<number> {
    const fourteenDaysAgo = new Date(Date.now() - COMMIT_LOOKBACK_DAYS * 24 * 60 * 60 * 1000);
    const sevenDaysAgo = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000);
    const dayStart = new Date(Date.now() - 24 * 60 * 60 * 1000);
    const hourStart = new Date(Date.now() - 60 * 60 * 1000);

    const [user, github, cursor, commitEvents, rateLimitHits, aiCallsLastHour] = await Promise.all([
      this.prisma.user.findUnique({
        where: { id: userId },
        select: {
          xVerified: true,
          reputationPoints: true,
          createdAt: true,
        },
      }),
      this.prisma.gitHubConnection.findFirst({ where: { userId }, select: { id: true } }),
      this.prisma.integrationCredential.findFirst({
        where: { userId, provider: 'cursor', verifiedAt: { not: null } },
        select: { id: true },
      }),
      this.prisma.founderEvent.count({
        where: {
          userId,
          type: { in: ['GITHUB_COMMIT', 'GITHUB_PR_MERGED'] },
          createdAt: { gte: fourteenDaysAgo },
        },
      }),
      this.prisma.rateLimit.count({
        where: { userId, windowStart: { gte: dayStart } },
      }),
      this.prisma.aiTokenUsageLog.count({
        where: { userId, createdAt: { gte: hourStart } },
      }),
    ]);

    if (!user) return 0;

    let score = 0;
    if (user.xVerified) score += 30;
    if (github) score += 25;
    if (cursor) score += 25;
    score += Math.min(commitEvents, COMMIT_SCORE_CAP);
    if (user.createdAt && user.createdAt < sevenDaysAgo) score += 10;

    // Abuse flag: any one of these triggers a -50 penalty.
    const abuseRateLimit = rateLimitHits >= ABUSE_RATE_LIMIT_HITS;
    const abuseNegativeBalance = user.reputationPoints < 0;
    const abuseHourlyCalls = aiCallsLastHour >= ABUSE_HOURLY_AI_CALLS;
    if (abuseRateLimit || abuseNegativeBalance || abuseHourlyCalls) {
      score -= 50;
    }

    return score;
  }

  /** Returns the user's tier — score >= threshold → VERIFIED_BUILDER. */
  async getTier(userId: string): Promise<BuilderTier> {
    // Fast path: read the cached tier from the User row. Falls back to PARASITE
    // if the columns don't exist yet (pre-migration) or the cache is stale.
    const cached = await this.readCachedTier(userId);
    if (cached.fresh) return cached.tier;

    // Stale or missing — recompute + persist.
    await this.refreshUserScore(userId);
    const refreshed = await this.readCachedTier(userId);
    return refreshed.tier;
  }

  /** Recompute the score + write it to User.builderScore / builderTier / scoreRefreshedAt. */
  async refreshUserScore(userId: string): Promise<void> {
    const score = await this.computeScore(userId);
    const tier: BuilderTier = score >= SCORE_THRESHOLD ? 'VERIFIED_BUILDER' : 'PARASITE';
    await this.writeCachedTier(userId, score, tier);
  }

  /**
   * Read the cached tier + freshness flag via raw SQL so the service works
   * even before the `prisma migrate deploy` that adds the columns. On any
   * error (column missing, DB down) → returns PARASITE + fresh=false so the
   * caller falls back to a recompute attempt (which also degrades safely).
   */
  private async readCachedTier(userId: string): Promise<{ tier: BuilderTier; fresh: boolean }> {
    try {
      const rows = (await this.prisma.$queryRaw`
        SELECT "builderTier", "scoreRefreshedAt" FROM "User" WHERE id = ${userId} LIMIT 1
      `) as Array<{ builderTier: string | null; scoreRefreshedAt: Date | null }>;
      const row = rows[0];
      if (!row) return { tier: 'PARASITE', fresh: false };
      const tier: BuilderTier = row.builderTier === 'VERIFIED_BUILDER' ? 'VERIFIED_BUILDER' : 'PARASITE';
      const refreshedAt = row.scoreRefreshedAt ? new Date(row.scoreRefreshedAt) : null;
      const fresh = refreshedAt != null && Date.now() - refreshedAt.getTime() < REFRESH_TTL_MS;
      return { tier, fresh };
    } catch (err) {
      this.logger.debug(
        `readCachedTier failed (columns missing pre-migration?) — defaulting to PARASITE: ${err instanceof Error ? err.message : String(err)}`,
      );
      return { tier: 'PARASITE', fresh: false };
    }
  }

  private async writeCachedTier(userId: string, score: number, tier: BuilderTier): Promise<void> {
    try {
      await this.prisma.$executeRaw`
        UPDATE "User"
        SET "builderScore" = ${score}, "builderTier" = ${tier}::\"BuilderTier\", "scoreRefreshedAt" = NOW()
        WHERE id = ${userId}
      `;
    } catch (err) {
      // Non-fatal: the in-memory tier still works for this request. Next
      // request will recompute. Logged at debug so a noisy pre-migration deploy
      // doesn't spam the logs.
      this.logger.debug(
        `writeCachedTier failed (columns missing pre-migration?) — score=${score} tier=${tier} not persisted: ${err instanceof Error ? err.message : String(err)}`,
      );
    }
  }

  /**
   * Sum of a user's platform-promo + platform-brain tokens in the last 24h.
   * Used by the tier-cap gate in FounderPromoService and PointsService.spend.
   */
  async dailyTokenUsage(userId: string): Promise<number> {
    const since = new Date(Date.now() - 24 * 60 * 60 * 1000);
    const agg = await this.prisma.aiTokenUsageLog.aggregate({
      where: {
        userId,
        createdAt: { gte: since },
        billingSource: { in: ['platform_promo', 'platform_brain'] },
      },
      _sum: { promptTokens: true, completionTokens: true },
    });
    return (agg._sum.promptTokens ?? 0) + (agg._sum.completionTokens ?? 0);
  }

  /** Threshold exposed for admin display + tests. */
  get threshold(): number {
    return SCORE_THRESHOLD;
  }

  /**
   * Admin overview of the builder vs parasite split — used by the Admin
   * Control panel. All tier-column reads go through raw SQL with try/catch
   * so this still returns a payload (with zeros) before the migration lands.
   */
  async getTierBreakdown(): Promise<{
    poolRemaining: number;
    poolCap: number;
    poolRemainingFraction: number;
    spendTodayByTier: { tier: BuilderTier; tokens: number }[];
    accountCountsByTier: { tier: BuilderTier; count: number }[];
    topParasitesBy24h: {
      userId: string;
      email: string;
      twitterHandle: string | null;
      tokens: number;
      calls: number;
      builderScore: number;
    }[];
    env: {
      PARASITE_DAILY_TOKEN_CAP: number;
      BUILDER_DAILY_TOKEN_CAP: number;
      PROMO_POOL_PRESERVATION_PCT: number;
      BUILDER_SCORE_THRESHOLD: number;
      BUILDER_SCORE_REFRESH_TTL_MS: number;
    };
  }> {
    const since = new Date(Date.now() - 24 * 60 * 60 * 1000);

    // Pool stats (re-uses the promo aggregation logic without needing the
    // promo service — sum of all platform_promo tokens vs the configured cap).
    const settings = await this.prisma.platformSettings.findFirst();
    const poolCap = settings?.founderPromoTokenCap ?? 30_000_000;
    const poolAgg = await this.prisma.aiTokenUsageLog.aggregate({
      where: { billingSource: 'platform_promo' },
      _sum: { promptTokens: true, completionTokens: true },
    });
    const poolUsed = (poolAgg._sum.promptTokens ?? 0) + (poolAgg._sum.completionTokens ?? 0);
    const poolRemaining = Math.max(0, poolCap - poolUsed);

    // Spend today by tier + account counts by tier + top parasites — all
    // require the builderTier column. Wrapped in try/catch so a pre-migration
    // DB returns zeros instead of 500ing the admin panel.
    let spendTodayByTier: { tier: BuilderTier; tokens: number }[] = [
      { tier: 'PARASITE', tokens: 0 },
      { tier: 'VERIFIED_BUILDER', tokens: 0 },
    ];
    let accountCountsByTier: { tier: BuilderTier; count: number }[] = [
      { tier: 'PARASITE', count: 0 },
      { tier: 'VERIFIED_BUILDER', count: 0 },
    ];
    let topParasitesBy24h: {
      userId: string;
      email: string;
      twitterHandle: string | null;
      tokens: number;
      calls: number;
      builderScore: number;
    }[] = [];

    try {
      const byTier = (await this.prisma.$queryRaw`
        SELECT u."builderTier" AS tier,
               COALESCE(SUM(t."promptTokens" + t."completionTokens"), 0) AS tokens,
               COUNT(*) AS calls
        FROM "AiTokenUsageLog" t
        JOIN "User" u ON u.id = t."userId"
        WHERE t."billingSource" IN ('platform_promo', 'platform_brain')
          AND t."createdAt" >= ${since}
        GROUP BY u."builderTier"
      `) as Array<{ tier: string; tokens: bigint; calls: bigint }>;
      spendTodayByTier = [
        { tier: 'PARASITE', tokens: Number(byTier.find((r) => r.tier !== 'VERIFIED_BUILDER')?.tokens ?? 0) },
        { tier: 'VERIFIED_BUILDER', tokens: Number(byTier.find((r) => r.tier === 'VERIFIED_BUILDER')?.tokens ?? 0) },
      ];

      const counts = (await this.prisma.$queryRaw`
        SELECT "builderTier" AS tier, COUNT(*) AS count
        FROM "User"
        GROUP BY "builderTier"
      `) as Array<{ tier: string; count: bigint }>;
      accountCountsByTier = [
        { tier: 'PARASITE', count: Number(counts.find((r) => r.tier !== 'VERIFIED_BUILDER')?.count ?? 0) },
        { tier: 'VERIFIED_BUILDER', count: Number(counts.find((r) => r.tier === 'VERIFIED_BUILDER')?.count ?? 0) },
      ];

      topParasitesBy24h = ((await this.prisma.$queryRaw`
        SELECT u.id AS "userId", u.email, u."twitterHandle",
               COALESCE(SUM(t."promptTokens" + t."completionTokens"), 0) AS tokens,
               COUNT(*) AS calls,
               COALESCE(u."builderScore", 0) AS "builderScore"
        FROM "AiTokenUsageLog" t
        JOIN "User" u ON u.id = t."userId"
        WHERE t."billingSource" IN ('platform_promo', 'platform_brain')
          AND t."createdAt" >= ${since}
          AND (u."builderTier" IS NULL OR u."builderTier" = 'PARASITE')
        GROUP BY u.id, u.email, u."twitterHandle", u."builderScore"
        ORDER BY tokens DESC
        LIMIT 10
      `) as Array<{
        userId: string;
        email: string;
        twitterHandle: string | null;
        tokens: bigint;
        calls: bigint;
        builderScore: number;
      }>).map((r) => ({
        userId: r.userId,
        email: r.email,
        twitterHandle: r.twitterHandle,
        tokens: Number(r.tokens),
        calls: Number(r.calls),
        builderScore: Number(r.builderScore),
      }));
    } catch (err) {
      this.logger.debug(
        `getTierBreakdown tier queries failed (columns missing pre-migration?) — returning zeros: ${err instanceof Error ? err.message : String(err)}`,
      );
    }

    return {
      poolRemaining,
      poolCap,
      poolRemainingFraction: poolCap > 0 ? poolRemaining / poolCap : 0,
      spendTodayByTier,
      accountCountsByTier,
      topParasitesBy24h,
      env: {
        PARASITE_DAILY_TOKEN_CAP: Number.parseInt(process.env.PARASITE_DAILY_TOKEN_CAP ?? '25000', 10),
        BUILDER_DAILY_TOKEN_CAP: Number.parseInt(process.env.BUILDER_DAILY_TOKEN_CAP ?? '500000', 10),
        PROMO_POOL_PRESERVATION_PCT: Number.parseFloat(process.env.PROMO_POOL_PRESERVATION_PCT ?? '0.30'),
        BUILDER_SCORE_THRESHOLD: SCORE_THRESHOLD,
        BUILDER_SCORE_REFRESH_TTL_MS: REFRESH_TTL_MS,
      },
    };
  }

  /**
   * Admin action: flag a user as abusive (forces their cached tier to PARASITE
   * regardless of score). Implemented as a direct tier write so the flag
   * sticks even when the score formula would otherwise put them over the
   * threshold. Idempotent.
   */
  async flagParasite(userId: string): Promise<void> {
    try {
      await this.prisma.$executeRaw`
        UPDATE "User"
        SET "builderTier" = 'PARASITE'::\"BuilderTier\",
            "builderScore" = 0,
            "scoreRefreshedAt" = NOW()
        WHERE id = ${userId}
      `;
    } catch (err) {
      this.logger.warn(
        `flagParasite failed (columns missing pre-migration?) for ${userId}: ${err instanceof Error ? err.message : String(err)}`,
      );
      throw err;
    }
  }
}

/**
 * Global module so every controller/service that needs tier info can inject
 * `BuilderScoreService` without re-importing the whole FounderOsModule.
 */
@Global()
@Module({
  providers: [BuilderScoreService],
  exports: [BuilderScoreService],
})
export class BuilderScoreModule {}
