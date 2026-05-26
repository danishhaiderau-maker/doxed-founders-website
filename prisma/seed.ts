import { PrismaClient, Prisma } from '@prisma/client';

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

  const chainMap = Object.fromEntries(
    (await prisma.chain.findMany()).map((c) => [c.slug, c.id]),
  );
  const categoryMap = Object.fromEntries(
    (await prisma.category.findMany()).map((c) => [c.slug, c.id]),
  );

  const founders = [
    {
      slug: 'sarah-chen',
      name: 'Sarah Chen',
      bio: 'Former Stripe engineer. Building transparent on-chain infrastructure since 2019. MIT CS, publicly doxxed since day one.',
      linkedInUrl: 'https://linkedin.com/in/sarahchen',
      twitterUrl: 'https://x.com/sarahchen',
      githubUrl: 'https://github.com/sarahchen',
      verifications: ['LINKEDIN', 'GITHUB', 'TEAM_DOXXED'] as const,
    },
    {
      slug: 'marcus-webb',
      name: 'Marcus Webb',
      bio: 'DeFi protocol architect with 12 years in traditional finance. Led engineering at two public fintech companies before going on-chain.',
      linkedInUrl: 'https://linkedin.com/in/marcuswebb',
      twitterUrl: 'https://x.com/marcuswebb',
      githubUrl: 'https://github.com/marcuswebb',
      verifications: ['LINKEDIN', 'AUDIT', 'TEAM_DOXXED'] as const,
    },
    {
      slug: 'elena-vasquez',
      name: 'Elena Vasquez',
      bio: 'Payments and identity specialist. Built remittance rails across LATAM. All team members publicly identified with quarterly transparency reports.',
      linkedInUrl: 'https://linkedin.com/in/elenavasquez',
      twitterUrl: 'https://x.com/elenavasquez',
      githubUrl: 'https://github.com/elenavasquez',
      verifications: ['LINKEDIN', 'KYC', 'IDENTITY'] as const,
    },
  ];

  const founderMap: Record<string, string> = {};

  for (const founder of founders) {
    const { verifications, ...data } = founder;
    const record = await prisma.founder.upsert({
      where: { slug: data.slug },
      update: data,
      create: data,
    });
    founderMap[data.slug] = record.id;

    for (const type of verifications) {
      await prisma.founderVerification.upsert({
        where: { founderId_type: { founderId: record.id, type } },
        update: { verified: true, verifiedAt: new Date() },
        create: {
          founderId: record.id,
          type,
          verified: true,
          verifiedAt: new Date(),
        },
      });
    }
  }

  const projects = [
    {
      slug: 'chainlens',
      name: 'ChainLens',
      ticker: 'CLENS',
      summary: 'Real-time blockchain analytics for institutions with public audit trails.',
      description:
        'ChainLens provides institutional-grade on-chain analytics with fully documented APIs, public founder accountability, and SOC2-aligned infrastructure monitoring.',
      websiteUrl: 'https://chainlens.example.com',
      docsUrl: 'https://docs.chainlens.example.com',
      contractAddress: '0x1234567890abcdef1234567890abcdef12345678',
      chainSlug: 'ETHEREUM' as const,
      categorySlug: 'infrastructure',
      founderSlug: 'sarah-chen',
      featured: true,
      approved: true,
      metrics: {
        priceUsd: 2.45,
        marketCap: 48_500_000,
        fdv: 98_000_000,
        volume24h: 1_240_000,
        liquidity: 890_000,
        holders: 12400,
        priceChange24h: 3.2,
      },
      socials: {
        twitterUrl: 'https://x.com/chainlens',
        githubUrl: 'https://github.com/chainlens',
        discordUrl: 'https://discord.gg/chainlens',
      },
    },
    {
      slug: 'vaultprotocol',
      name: 'VaultProtocol',
      ticker: 'VAULT',
      summary: 'Non-custodial yield infrastructure with transparent risk dashboards.',
      description:
        'VaultProtocol offers curated yield strategies with on-chain proof-of-reserves, public team identities, and third-party audit publication.',
      websiteUrl: 'https://vaultprotocol.example.com',
      docsUrl: 'https://docs.vaultprotocol.example.com',
      whitepaperUrl: 'https://vaultprotocol.example.com/whitepaper.pdf',
      contractAddress: '0xabcdef1234567890abcdef1234567890abcdef12',
      chainSlug: 'ARBITRUM' as const,
      categorySlug: 'defi',
      founderSlug: 'marcus-webb',
      featured: true,
      approved: true,
      metrics: {
        priceUsd: 0.87,
        marketCap: 22_100_000,
        fdv: 45_000_000,
        volume24h: 620_000,
        liquidity: 410_000,
        holders: 8900,
        priceChange24h: -1.4,
      },
      socials: {
        twitterUrl: 'https://x.com/vaultprotocol',
        githubUrl: 'https://github.com/vaultprotocol',
      },
    },
    {
      slug: 'payrail',
      name: 'PayRail',
      ticker: 'RAIL',
      summary: 'Cross-border stablecoin payments with KYC-compliant merchant rails.',
      description:
        'PayRail connects businesses to stablecoin settlement with transparent fee structures, public compliance documentation, and identified leadership.',
      websiteUrl: 'https://payrail.example.com',
      docsUrl: 'https://docs.payrail.example.com',
      contractAddress: '0x9876543210fedcba9876543210fedcba98765432',
      chainSlug: 'BASE' as const,
      categorySlug: 'payments',
      founderSlug: 'elena-vasquez',
      featured: true,
      approved: true,
      metrics: {
        priceUsd: 1.12,
        marketCap: 31_800_000,
        fdv: 56_000_000,
        volume24h: 980_000,
        liquidity: 720_000,
        holders: 5600,
        priceChange24h: 0.8,
      },
      socials: {
        twitterUrl: 'https://x.com/payrail',
        telegramUrl: 'https://t.me/payrail',
      },
    },
    {
      slug: 'identitypass',
      name: 'IdentityPass',
      ticker: 'IDP',
      summary: 'Portable on-chain credentials with privacy-preserving verification.',
      description:
        'IdentityPass enables reusable digital credentials for Web3 applications. Founding team is fully public with quarterly transparency reports.',
      websiteUrl: 'https://identitypass.example.com',
      docsUrl: 'https://docs.identitypass.example.com',
      contractAddress: '0x5555555555555555555555555555555555555555',
      chainSlug: 'POLYGON' as const,
      categorySlug: 'identity',
      founderSlug: 'sarah-chen',
      featured: false,
      approved: true,
      metrics: {
        priceUsd: 0.34,
        marketCap: 8_900_000,
        fdv: 17_000_000,
        volume24h: 210_000,
        liquidity: 180_000,
        holders: 3200,
        priceChange24h: 5.6,
      },
      socials: {
        twitterUrl: 'https://x.com/identitypass',
        githubUrl: 'https://github.com/identitypass',
      },
    },
    {
      slug: 'gameforge',
      name: 'GameForge',
      ticker: 'FORGE',
      summary: 'Studio-grade Web3 game infrastructure with public roadmap delivery.',
      description:
        'GameForge ships SDKs and live ops tooling for Web3 games. Active GitHub, public founders, and documented token utility — no anonymous team.',
      websiteUrl: 'https://gameforge.example.com',
      docsUrl: 'https://docs.gameforge.example.com',
      contractAddress: 'So11111111111111111111111111111111111111112',
      chainSlug: 'SOLANA' as const,
      categorySlug: 'gaming',
      founderSlug: 'marcus-webb',
      featured: false,
      approved: true,
      metrics: {
        priceUsd: 0.19,
        marketCap: 12_400_000,
        fdv: 28_000_000,
        volume24h: 440_000,
        liquidity: 290_000,
        holders: 9800,
        priceChange24h: -2.1,
      },
      socials: {
        twitterUrl: 'https://x.com/gameforge',
        discordUrl: 'https://discord.gg/gameforge',
        githubUrl: 'https://github.com/gameforge',
      },
    },
  ];

  let featuredOrder = 0;

  for (const project of projects) {
    const { chainSlug, categorySlug, founderSlug, metrics, socials, ...data } =
      project;

    const record = await prisma.project.upsert({
      where: { slug: data.slug },
      update: {
        name: data.name,
        ticker: data.ticker,
        summary: data.summary,
        description: data.description,
        websiteUrl: data.websiteUrl,
        docsUrl: data.docsUrl,
        whitepaperUrl: data.whitepaperUrl,
        contractAddress: data.contractAddress,
        chainId: chainMap[chainSlug],
        categoryId: categoryMap[categorySlug],
        founderId: founderMap[founderSlug],
        featured: data.featured,
        approved: data.approved,
      },
      create: {
        ...data,
        chainId: chainMap[chainSlug],
        categoryId: categoryMap[categorySlug],
        founderId: founderMap[founderSlug],
      },
    });

    await prisma.projectMetrics.upsert({
      where: { projectId: record.id },
      update: {
        priceUsd: new Prisma.Decimal(metrics.priceUsd),
        marketCap: new Prisma.Decimal(metrics.marketCap),
        fdv: new Prisma.Decimal(metrics.fdv),
        volume24h: new Prisma.Decimal(metrics.volume24h),
        liquidity: new Prisma.Decimal(metrics.liquidity),
        holders: metrics.holders,
        priceChange24h: new Prisma.Decimal(metrics.priceChange24h),
      },
      create: {
        projectId: record.id,
        priceUsd: new Prisma.Decimal(metrics.priceUsd),
        marketCap: new Prisma.Decimal(metrics.marketCap),
        fdv: new Prisma.Decimal(metrics.fdv),
        volume24h: new Prisma.Decimal(metrics.volume24h),
        liquidity: new Prisma.Decimal(metrics.liquidity),
        holders: metrics.holders,
        priceChange24h: new Prisma.Decimal(metrics.priceChange24h),
      },
    });

    await prisma.projectSocials.upsert({
      where: { projectId: record.id },
      update: socials,
      create: { projectId: record.id, ...socials },
    });

    await prisma.trendingScore.upsert({
      where: { projectId: record.id },
      update: {
        score: new Prisma.Decimal(
          metrics.volume24h / 100_000 + metrics.holders / 1000,
        ),
      },
      create: {
        projectId: record.id,
        score: new Prisma.Decimal(
          metrics.volume24h / 100_000 + metrics.holders / 1000,
        ),
      },
    });

    if (data.featured) {
      await prisma.featuredProject.upsert({
        where: { projectId: record.id },
        update: { order: featuredOrder },
        create: {
          projectId: record.id,
          order: featuredOrder,
          note: 'Curated by DoxedCryptoFounder team',
        },
      });
      featuredOrder += 1;
    }
  }

  const counts = {
    chains: await prisma.chain.count(),
    categories: await prisma.category.count(),
    founders: await prisma.founder.count(),
    projects: await prisma.project.count(),
    verifications: await prisma.founderVerification.count(),
    featured: await prisma.featuredProject.count(),
  };

  console.log('Phase 2 seed complete:');
  console.log(JSON.stringify(counts, null, 2));

  const bcrypt = await import('bcrypt');
  const adminPassword = process.env.SEED_ADMIN_PASSWORD ?? 'Admin123!';
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

main()
  .catch((e) => {
    console.error(e);
    process.exit(1);
  })
  .finally(async () => {
    await prisma.$disconnect();
  });
