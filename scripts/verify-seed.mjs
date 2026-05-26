import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

async function main() {
  const counts = {
    chains: await prisma.chain.count(),
    categories: await prisma.category.count(),
    founders: await prisma.founder.count(),
    projects: await prisma.project.count(),
    verifications: await prisma.founderVerification.count(),
    featured: await prisma.featuredProject.count(),
  };

  const projects = await prisma.project.findMany({
    select: { slug: true, name: true, ticker: true, approved: true, featured: true },
    orderBy: { name: 'asc' },
  });

  const founders = await prisma.founder.findMany({
    select: { slug: true, name: true },
    orderBy: { name: 'asc' },
  });

  console.log('\n=== Phase 2 Seed Verification ===\n');
  console.log('Counts:', counts);

  const ok =
    counts.chains >= 8 &&
    counts.categories >= 5 &&
    counts.founders >= 3 &&
    counts.projects >= 5;

  console.log('\nProjects:');
  for (const p of projects) {
    console.log(`  - ${p.name} (${p.ticker}) [${p.slug}] featured=${p.featured}`);
  }

  console.log('\nFounders:');
  for (const f of founders) {
    console.log(`  - ${f.name} [${f.slug}]`);
  }

  console.log(ok ? '\nPASS: Phase 2 seed data is complete.\n' : '\nFAIL: Seed data incomplete.\n');
  process.exit(ok ? 0 : 1);
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
