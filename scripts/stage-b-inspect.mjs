/**
 * Inspect pending publishes (Neon) — no secrets printed.
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { PrismaClient } from '@prisma/client';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault');

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    map[t.slice(0, i).trim()] = t.slice(i + 1).trim().replace(/^"|"$/g, '');
  }
  return map;
}

const neon = readDotEnv(join(vault, '.env.neon'));
const db = neon.DATABASE_URL;
if (!db) {
  console.error('Missing DATABASE_URL');
  process.exit(1);
}

process.env.DATABASE_URL = db;
const prisma = new PrismaClient();

const pending = await prisma.suggestedBuildUpdate.findMany({
  where: { status: 'PENDING' },
  orderBy: { createdAt: 'asc' },
  select: {
    id: true,
    headline: true,
    founderId: true,
    createdAt: true,
    founder: {
      select: {
        userId: true,
        githubRepoFullName: true,
        user: { select: { email: true, platformHandle: true } },
      },
    },
  },
});

const founders = await prisma.founder.findMany({
  select: {
    id: true,
    userId: true,
    user: { select: { email: true, platformHandle: true, name: true } },
  },
});

console.log('Founders:', founders.length);
for (const f of founders) {
  const label = f.user?.email ?? f.user?.platformHandle ?? f.user?.name ?? `userId=${f.userId}`;
  console.log(`  ${label} (founder ${f.id.slice(0, 8)}…)`);
}
console.log(`\nPending updates: ${pending.length}`);
for (const p of pending.slice(0, 10)) {
  const owner = p.founder?.user?.email ?? p.founder?.userId ?? p.founderId;
  const repo = p.founder?.githubRepoFullName ?? '';
  console.log(`  ${p.id.slice(0, 8)}… ${p.headline.slice(0, 50)} | ${owner} ${repo}`);
}

const adminEmail = 'admin@doxedcryptofounder.local';
const admin = await prisma.user.findFirst({
  where: { email: adminEmail },
  select: { id: true, email: true, founder: { select: { id: true } } },
});
console.log('\nAdmin user:', admin?.email ?? 'not found', admin?.founder?.id ?? 'no founder profile');

await prisma.$disconnect();
