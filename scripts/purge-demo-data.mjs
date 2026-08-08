/**
 * Remove demo-harness leftovers from Neon only.
 *
 * Safety:
 * - Targets ONLY slug prefix `demo-` and emails ending `@doxxed.demo`
 * - Never touches real founder/user projects
 * - Requires --confirm (or --dry-run)
 *
 * Usage:
 *   node scripts/purge-demo-data.mjs --dry-run
 *   node scripts/purge-demo-data.mjs --confirm
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';

const DEMO_EMAIL_DOMAIN = '@doxxed.demo';
const DEMO_SLUG_PREFIX = 'demo-';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vaultCandidates = [
  join(root, '..', 'doxedcryptofounder-secrets', 'vault', '.env.neon'),
  join(root, 'vault', '.env.neon'),
  join(root, '.env.neon'),
];
const vaultPath = vaultCandidates.find((p) => existsSync(p));
if (!vaultPath) {
  console.error('Missing vault/.env.neon');
  process.exit(1);
}
const neon = readFileSync(vaultPath, 'utf8');
const dbUrl = neon.match(/^DATABASE_URL=(.+)$/m)?.[1]?.trim().replace(/^"|"$/g, '');
if (!dbUrl?.startsWith('postgres')) {
  console.error('DATABASE_URL missing/invalid');
  process.exit(1);
}

const dryRun = process.argv.includes('--dry-run');
const confirm = process.argv.includes('--confirm');
if (!dryRun && !confirm) {
  console.error('Pass --dry-run or --confirm');
  process.exit(1);
}

const prisma = new PrismaClient({ datasources: { db: { url: dbUrl } } });

async function main() {
  const demoProjects = await prisma.project.findMany({
    where: { slug: { startsWith: DEMO_SLUG_PREFIX } },
    select: { id: true, slug: true, name: true, ticker: true, approved: true },
    orderBy: { slug: 'asc' },
  });
  const demoFounders = await prisma.founder.findMany({
    where: { slug: { startsWith: DEMO_SLUG_PREFIX } },
    select: { id: true, slug: true, name: true },
    orderBy: { slug: 'asc' },
  });
  const demoUsers = await prisma.user.findMany({
    where: { email: { endsWith: DEMO_EMAIL_DOMAIN } },
    select: { id: true, email: true },
    orderBy: { email: 'asc' },
  });
  const realProjects = await prisma.project.count({
    where: { NOT: { slug: { startsWith: DEMO_SLUG_PREFIX } } },
  });

  const plan = {
    dryRun,
    demoProjects: demoProjects.length,
    demoFounders: demoFounders.length,
    demoUsers: demoUsers.length,
    realProjectsUntouched: realProjects,
    sampleProjectSlugs: demoProjects.slice(0, 12).map((p) => p.slug),
    sampleFounderSlugs: demoFounders.slice(0, 8).map((f) => f.slug),
  };
  console.log(JSON.stringify(plan, null, 2));

  if (dryRun) {
    console.log('Dry run only — no deletes.');
    return;
  }

  if (demoProjects.length === 0 && demoFounders.length === 0 && demoUsers.length === 0) {
    console.log('Nothing to purge.');
    return;
  }

  const demoProjectIds = demoProjects.map((p) => p.id);
  const demoFounderIds = demoFounders.map((f) => f.id);
  const demoUserIds = demoUsers.map((u) => u.id);

  await prisma.$transaction(async (tx) => {
    if (demoProjectIds.length > 0) {
      await tx.project.deleteMany({ where: { id: { in: demoProjectIds } } });
    }
    if (demoFounderIds.length > 0) {
      await tx.founder.deleteMany({ where: { id: { in: demoFounderIds } } });
    }
    if (demoUserIds.length > 0) {
      await tx.user.deleteMany({ where: { id: { in: demoUserIds } } });
    }
  });

  const after = {
    demoProjects: await prisma.project.count({
      where: { slug: { startsWith: DEMO_SLUG_PREFIX } },
    }),
    demoFounders: await prisma.founder.count({
      where: { slug: { startsWith: DEMO_SLUG_PREFIX } },
    }),
    demoUsers: await prisma.user.count({
      where: { email: { endsWith: DEMO_EMAIL_DOMAIN } },
    }),
    realProjects: await prisma.project.count({
      where: { NOT: { slug: { startsWith: DEMO_SLUG_PREFIX } } },
    }),
  };
  console.log(JSON.stringify({ deleted: plan, after }, null, 2));
}

main()
  .catch((err) => {
    console.error(err);
    process.exit(1);
  })
  .finally(() => prisma.$disconnect());
