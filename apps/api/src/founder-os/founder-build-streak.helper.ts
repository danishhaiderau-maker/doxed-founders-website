import {
  computeActiveBuildStreakFromDayKeys,
  computeNextBuildStreakDays,
  computePublicBuildDayNumber,
  founderCalendarDayKey,
} from '@dcf/utils';
import { PrismaService } from '../prisma/prisma.service';

type FounderStreakRow = {
  id: string;
  buildStreakDays: number;
  lastBuildPostAt: Date | null;
  publicBuildingSince: Date | null;
  createdAt: Date;
};

export function publicBuildDayNumberForFounder(founder: FounderStreakRow, now = new Date()): number {
  const anchor = founder.publicBuildingSince ?? founder.createdAt;
  return computePublicBuildDayNumber(anchor, now);
}

export async function repairFounderBuildStreakIfInflated(
  prisma: PrismaService,
  founder: FounderStreakRow,
): Promise<{ buildStreakDays: number; repaired: boolean }> {
  const publicDay = publicBuildDayNumberForFounder(founder);
  if (founder.buildStreakDays <= publicDay) {
    return { buildStreakDays: founder.buildStreakDays, repaired: false };
  }

  const posts = await prisma.founderBuildPost.findMany({
    where: { founderId: founder.id },
    select: { publishedAt: true, createdAt: true },
    orderBy: { publishedAt: 'asc' },
  });

  const dayKeys = posts.map((p) => founderCalendarDayKey(p.publishedAt ?? p.createdAt));
  const streak = computeActiveBuildStreakFromDayKeys(dayKeys);
  const nextStreak = Math.min(Math.max(streak, 1), publicDay);

  await prisma.founder.update({
    where: { id: founder.id },
    data: { buildStreakDays: nextStreak },
  });

  return { buildStreakDays: nextStreak, repaired: true };
}

export async function updateFounderBuildStreak(
  prisma: PrismaService,
  founderId: string,
): Promise<number> {
  const founder = await prisma.founder.findUnique({ where: { id: founderId } });
  if (!founder) return 0;

  await repairFounderBuildStreakIfInflated(prisma, founder);

  const refreshed = await prisma.founder.findUnique({ where: { id: founderId } });
  if (!refreshed) return 0;

  const now = new Date();
  const streak = computeNextBuildStreakDays(
    refreshed.lastBuildPostAt,
    refreshed.buildStreakDays,
    now,
  );
  const capped = Math.min(streak, publicBuildDayNumberForFounder(refreshed, now));

  await prisma.founder.update({
    where: { id: founderId },
    data: {
      buildStreakDays: capped,
      lastBuildPostAt: now,
      publicBuildingSince: refreshed.publicBuildingSince ?? now,
    },
  });

  return capped;
}
