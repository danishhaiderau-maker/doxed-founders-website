/**
 * Removes seeded sample projects/founders and related data.
 * Keeps chains, categories, and user accounts (including admin).
 */
import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

async function main() {
  const before = {
    projects: await prisma.project.count(),
    founders: await prisma.founder.count(),
  };

  await prisma.$transaction([
    prisma.feedComment.deleteMany(),
    prisma.feedPost.deleteMany(),
    prisma.leaderboardEntry.deleteMany(),
    prisma.paperTrade.deleteMany(),
    prisma.paperPosition.deleteMany(),
    prisma.paperPortfolio.deleteMany(),
    prisma.watchlist.deleteMany(),
    prisma.analyticsEvent.deleteMany({ where: { projectId: { not: null } } }),
    prisma.featuredProject.deleteMany(),
    prisma.trendingScore.deleteMany(),
    prisma.projectDocument.deleteMany(),
    prisma.auditReport.deleteMany(),
    prisma.projectSocials.deleteMany(),
    prisma.projectMetrics.deleteMany(),
    prisma.project.deleteMany(),
    prisma.founderVerification.deleteMany(),
    prisma.founder.deleteMany(),
    prisma.listingApplication.deleteMany(),
  ]);

  const after = {
    projects: await prisma.project.count(),
    founders: await prisma.founder.count(),
  };

  console.log('Sample data cleared.');
  console.log(JSON.stringify({ before, after }, null, 2));
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
