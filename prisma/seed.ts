import { PrismaClient } from '@prisma/client';

const prisma = new PrismaClient();

async function main() {
  const chains = [
    { slug: 'ETHEREUM' as const, name: 'Ethereum' },
    { slug: 'SOLANA' as const, name: 'Solana' },
    { slug: 'POLYGON' as const, name: 'Polygon' },
    { slug: 'ARBITRUM' as const, name: 'Arbitrum' },
    { slug: 'OPTIMISM' as const, name: 'Optimism' },
    { slug: 'BASE' as const, name: 'Base' },
    { slug: 'AVALANCHE' as const, name: 'Avalanche' },
    { slug: 'BNB_CHAIN' as const, name: 'BNB Chain' },
  ];

  for (const chain of chains) {
    await prisma.chain.upsert({
      where: { slug: chain.slug },
      update: { name: chain.name },
      create: chain,
    });
  }

  const categories = [
    { slug: 'defi', name: 'DeFi', description: 'Decentralized finance protocols' },
    {
      slug: 'infrastructure',
      name: 'Infrastructure',
      description: 'Blockchain infrastructure and tooling',
    },
    { slug: 'gaming', name: 'Gaming', description: 'Web3 gaming and metaverse' },
    { slug: 'payments', name: 'Payments', description: 'Payment and remittance solutions' },
    { slug: 'identity', name: 'Identity', description: 'Digital identity and credentials' },
  ];

  for (const category of categories) {
    await prisma.category.upsert({
      where: { slug: category.slug },
      update: { name: category.name, description: category.description },
      create: category,
    });
  }

  const counts = {
    chains: await prisma.chain.count(),
    categories: await prisma.category.count(),
    founders: await prisma.founder.count(),
    projects: await prisma.project.count(),
  };

  console.log('Seed complete (reference data + admin only):');
  console.log(JSON.stringify(counts, null, 2));

  const bcrypt = await import('bcrypt');
  const adminPassword = process.env.SEED_ADMIN_PASSWORD?.trim();
  if (!adminPassword) {
    if (process.env.DATABASE_URL?.includes('neon') || process.env.NODE_ENV === 'production') {
      throw new Error('Set SEED_ADMIN_PASSWORD before seeding production databases');
    }
    console.warn('SEED_ADMIN_PASSWORD not set — skipping admin user seed (dev only)');
  } else {
    const passwordHash = await bcrypt.hash(adminPassword, 12);
    await prisma.user.upsert({
      where: { email: 'admin@doxedcryptofounder.local' },
      update: {
        passwordHash,
        role: 'ADMIN',
        name: 'Platform Admin',
      },
      create: {
        email: 'admin@doxedcryptofounder.local',
        passwordHash,
        role: 'ADMIN',
        name: 'Platform Admin',
      },
    });
    console.log('Admin user ready: admin@doxedcryptofounder.local');
  }
}

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
