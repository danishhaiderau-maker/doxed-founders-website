import { POINTS, STARTING_CASH_USD } from '@dcf/utils';
import { Prisma } from '@prisma/client';
import { PrismaService } from '../prisma/prisma.service';
import { PointsService } from '../points/points.service';

/** Idempotent signup grants — safe on register, OAuth create, and OAuth link. */
export async function bootstrapUserEconomy(
  prisma: PrismaService,
  points: PointsService,
  userId: string,
) {
  // Free DDollar signup bonus is gated on a verified Twitter account (matches
  // the free-token eligibility gate in FounderPromoService). `awardOnce` is
  // idempotent, and `auth.service` re-invokes `bootstrapUserEconomy` on every
  // X-link / X-verify path, so the deferred REGISTER bonus lands automatically
  // the moment `xVerified` flips to true.
  const user = await prisma.user.findUnique({
    where: { id: userId },
    select: { xVerified: true },
  });
  if (user?.xVerified) {
    await points.awardOnce(userId, 'REGISTER', POINTS.REGISTER);
  }

  const existing = await prisma.paperPortfolio.findUnique({ where: { userId } });
  if (existing) return existing;

  const portfolio = await prisma.paperPortfolio.create({
    data: {
      userId,
      cashBalance: STARTING_CASH_USD,
      totalValue: STARTING_CASH_USD,
    },
  });

  await prisma.virtualEconomyEvent.create({
    data: {
      userId,
      type: 'INITIAL_GRANT',
      amountUsd: new Prisma.Decimal(STARTING_CASH_USD),
      note: 'Signup paper trading grant',
    },
  });

  return portfolio;
}
