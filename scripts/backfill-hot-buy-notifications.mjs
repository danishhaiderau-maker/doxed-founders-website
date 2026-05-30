#!/usr/bin/env node
/**
 * Backfill TRENDING_BUYS notifications with buyer metadata where missing.
 * Usage: node scripts/backfill-hot-buy-notifications.mjs
 */
import { PrismaClient, NotificationType } from '@prisma/client';

const prisma = new PrismaClient();

async function main() {
  const rows = await prisma.notification.findMany({
    where: { type: NotificationType.TRENDING_BUYS },
    take: 300,
    orderBy: { createdAt: 'desc' },
  });

  let updated = 0;
  for (const row of rows) {
    const meta = row.metadata;
    if (meta && typeof meta === 'object' && 'buyers' in meta && Array.isArray(meta.buyers)) {
      continue;
    }

    const link = row.link ?? '';
    const slugMatch = link.match(/\/project\/([^/?#]+)/);
    if (!slugMatch) continue;

    const project = await prisma.project.findFirst({
      where: { slug: slugMatch[1] },
      select: { id: true, slug: true, ticker: true },
    });
    if (!project) continue;

    const since = new Date(Date.now() - 30 * 86400000);
    const trades = await prisma.paperTrade.findMany({
      where: { projectId: project.id, side: 'BUY', createdAt: { gte: since } },
      orderBy: { createdAt: 'desc' },
      take: 40,
      include: {
        user: { select: { id: true, name: true, email: true, twitterHandle: true } },
      },
    });

    const seen = new Set();
    const buyers = [];
    for (const t of trades) {
      if (seen.has(t.userId)) continue;
      seen.add(t.userId);
      buyers.push({
        userId: t.userId,
        displayName: t.user.name?.trim() || t.user.email?.split('@')[0] || 'Trader',
        amountUsd: Number(t.totalUsd),
        twitterHandle: t.user.twitterHandle,
      });
      if (buyers.length >= 8) break;
    }

    if (buyers.length === 0) continue;

    await prisma.notification.update({
      where: { id: row.id },
      data: {
        metadata: {
          projectSlug: project.slug,
          projectTicker: project.ticker,
          buyers,
        },
      },
    });
    updated++;
  }

  console.log(`Backfilled ${updated} notification(s).`);
}

main()
  .catch((err) => {
    console.error(err);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
