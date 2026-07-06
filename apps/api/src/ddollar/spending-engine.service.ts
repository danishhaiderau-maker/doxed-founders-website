import {
  BadRequestException,
  HttpException,
  HttpStatus,
  Injectable,
  Logger,
  ServiceUnavailableException,
} from '@nestjs/common';
import { contributorLevelFromPoints, pointActionLabel } from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';
import { BuilderScoreService } from '../founder-os/builder-score.service';
import {
  DDOLLAR_ACTION_KEYS,
  MARKETPLACE_TREASURY_FEE_BPS,
} from './ddollar.constants';

const DB_DOWN_RE = /connection|timeout|unreachable|refused|terminated/i;

function isDbDownError(err: unknown): boolean {
  if (err == null) return false;
  const code = (err as { code?: string }).code;
  if (code === 'P1001' || code === 'P1008' || code === 'P1017') return true;
  const name = (err as { name?: string }).name;
  if (name === 'PrismaClientInitializationError') return true;
  const msg = err instanceof Error ? err.message : String(err);
  return DB_DOWN_RE.test(msg);
}

export type SpendOptions = {
  aiSpend?: boolean;
  marketplaceListingKey?: string;
  marketplaceLabel?: string;
};

@Injectable()
export class SpendingEngine {
  private readonly logger = new Logger(SpendingEngine.name);

  constructor(
    private readonly prisma: PrismaService,
    private readonly builderScore: BuilderScoreService,
  ) {}

  async spend(
    userId: string,
    amount: number,
    actionKey?: string,
    options: SpendOptions = {},
  ): Promise<void> {
    if (amount <= 0) return;

    if (options.aiSpend) {
      await this.enforceTierCap(userId);
    }

    const lifetimeBefore = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { lifetimeContributionEarned: true },
    });

    let result;
    try {
      result = await this.prisma.user.updateMany({
        where: { id: userId, reputationPoints: { gte: amount } },
        data: { reputationPoints: { decrement: amount } },
      });
    } catch (err) {
      if (isDbDownError(err)) {
        this.logger.error(
          `SpendingEngine.spend DB-down — fail CLOSED user=${userId}: ${err instanceof Error ? err.message : String(err)}`,
        );
        throw new ServiceUnavailableException('Balance check unavailable — please try again');
      }
      throw err;
    }

    if (result.count === 0) {
      throw new BadRequestException(
        `Need ${amount.toLocaleString()} DDollar — earn more by scouting, trading, and validating listings.`,
      );
    }

    const lifetimeAfter = await this.prisma.user.findUnique({
      where: { id: userId },
      select: { lifetimeContributionEarned: true, reputationPoints: true },
    });

    if (
      lifetimeBefore &&
      lifetimeAfter &&
      lifetimeAfter.lifetimeContributionEarned !== lifetimeBefore.lifetimeContributionEarned
    ) {
      this.logger.error(
        `Lifetime contribution changed on spend for user=${userId} — invariant violated`,
      );
    }

    if (lifetimeAfter) {
      const level = contributorLevelFromPoints(lifetimeAfter.reputationPoints);
      await this.prisma.user.update({
        where: { id: userId },
        data: { contributorLevel: level },
      });
    }

    const resolvedKey = actionKey?.split(':')[0] ?? actionKey ?? DDOLLAR_ACTION_KEYS.AI_SPEND;
    if (actionKey) {
      await this.prisma.pointLedger.create({
        data: {
          userId,
          amount: -amount,
          actionKey: resolvedKey,
          label: pointActionLabel(actionKey),
        },
      });
    }

    if (options.marketplaceListingKey) {
      await this.recordMarketplaceSpend(userId, amount, options.marketplaceListingKey, options.marketplaceLabel);
    }
  }

  async recordMarketplaceSpend(
    userId: string,
    amount: number,
    listingKey: string,
    label = 'Marketplace purchase',
  ): Promise<void> {
    await this.prisma.marketplaceLedgerEntry.create({
      data: {
        userId,
        listingKey,
        amountDdollar: -amount,
        label,
        metadata: { demo: listingKey.startsWith('demo-') },
      },
    });

    const treasuryAmount = Math.max(1, Math.floor((amount * MARKETPLACE_TREASURY_FEE_BPS) / 10000));
    await this.prisma.founderTreasuryLedgerEntry.create({
      data: {
        userId,
        amountDdollar: treasuryAmount,
        actionKey: DDOLLAR_ACTION_KEYS.TREASURY_FEE,
        label: `Treasury fee — ${label}`,
        metadata: { listingKey, grossSpend: amount },
      },
    });
  }

  private async enforceTierCap(userId: string): Promise<void> {
    const PARASITE_CAP = Number.parseInt(process.env.PARASITE_DAILY_TOKEN_CAP ?? '25000', 10);
    const BUILDER_CAP = Number.parseInt(process.env.BUILDER_DAILY_TOKEN_CAP ?? '500000', 10);

    const [tier, dailyUsage] = await Promise.all([
      this.builderScore.getTier(userId),
      this.builderScore.dailyTokenUsage(userId),
    ]);
    const cap = tier === 'VERIFIED_BUILDER' ? BUILDER_CAP : PARASITE_CAP;
    if (dailyUsage >= cap) {
      const message =
        tier === 'VERIFIED_BUILDER'
          ? 'Daily builder token cap reached. Connect your own API key to continue.'
          : 'Daily parasite-tier token cap reached. Connect GitHub + Cursor + push a commit to upgrade to Verified Builder.';
      throw new HttpException(
        {
          statusCode: HttpStatus.TOO_MANY_REQUESTS,
          message,
          tier,
          dailyUsage,
          cap,
        },
        HttpStatus.TOO_MANY_REQUESTS,
      );
    }
  }
}
