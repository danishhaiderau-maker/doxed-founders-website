#!/usr/bin/env node
/**
 * Audit recent user DDollar (reputationPoints) and paper cash balances.
 */
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'url';
import { spawnSync } from 'node:child_process';
import { getVaultDir } from './secrets-vault-path.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const neonPath = join(getVaultDir(), '.env.neon');

if (!existsSync(neonPath)) {
  console.error('Missing vault/.env.neon');
  process.exit(1);
}

for (const line of readFileSync(neonPath, 'utf8').split('\n')) {
  const t = line.trim();
  if (!t || t.startsWith('#')) continue;
  const i = t.indexOf('=');
  if (i < 1) continue;
  let v = t.slice(i + 1).trim();
  if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) v = v.slice(1, -1);
  process.env[t.slice(0, i).trim()] = v;
}

const gen = spawnSync(
  'npx',
  ['prisma@6.8.2', 'generate', '--schema', 'prisma/schema.prisma'],
  { cwd: root, stdio: 'pipe', shell: true, env: process.env },
);
if (gen.status !== 0) {
  console.error('prisma generate failed:', gen.stderr?.toString() || gen.stdout?.toString());
  process.exit(1);
}

const { PrismaClient } = await import('@prisma/client');
const prisma = new PrismaClient();
const days = Number(process.argv[2] ?? 90);
const since = new Date(Date.now() - days * 24 * 60 * 60 * 1000);

try {
  const users = await prisma.user.findMany({
    where: {
      createdAt: { gte: since },
      email: { not: { contains: '@guest.local' } },
      role: 'USER',
    },
    orderBy: { createdAt: 'desc' },
    take: 40,
    select: {
      email: true,
      reputationPoints: true,
      createdAt: true,
      passwordHash: true,
      oauthAccounts: { select: { provider: true } },
      paperPortfolio: { select: { cashBalance: true } },
      pointLedger: {
        where: { OR: [{ actionKey: 'REGISTER' }, { actionKey: { startsWith: 'AGENT_HIRE' } }] },
        select: { amount: true, actionKey: true },
      },
    },
  });

  console.log(`\n=== DDollar / paper audit (last ${days} days, showing ${users.length}) ===\n`);
  console.log('joined     | DDollar | paper $  | auth     | email / flags');
  console.log('-'.repeat(90));

  for (const u of users) {
    const cash = u.paperPortfolio ? Number(u.paperPortfolio.cashBalance) : null;
    const auth = u.oauthAccounts.map((o) => o.provider).join(',') || (u.passwordHash ? 'email' : 'none');
    const hasRegister = u.pointLedger.some((p) => p.actionKey === 'REGISTER');
    const hireSpend = u.pointLedger
      .filter((p) => p.actionKey.startsWith('AGENT_HIRE'))
      .reduce((s, p) => s + Math.abs(p.amount), 0);
    const flags = [];
    if (!hasRegister && u.reputationPoints < 50) flags.push('NO_REGISTER');
    if (!u.paperPortfolio) flags.push('NO_PAPER');
    if (cash != null && cash < 10000) flags.push('paper<' + cash.toFixed(0));
    if (hireSpend) flags.push('hired:' + hireSpend);

    console.log(
      `${u.createdAt.toISOString().slice(0, 10)} | ${String(u.reputationPoints).padStart(7)} | ${cash != null ? ('$' + cash.toFixed(0)).padStart(8) : '    none'} | ${auth.padEnd(8)} | ${u.email.slice(0, 35)}${flags.length ? ' [' + flags.join(';') + ']' : ''}`,
    );
  }

  const [zeroNoReg, noPaper, below10k, total] = await Promise.all([
    prisma.user.count({
      where: {
        createdAt: { gte: since },
        role: 'USER',
        email: { not: { contains: '@guest.local' } },
        reputationPoints: 0,
        pointLedger: { none: { actionKey: 'REGISTER' } },
      },
    }),
    prisma.user.count({
      where: {
        createdAt: { gte: since },
        role: 'USER',
        email: { not: { contains: '@guest.local' } },
        paperPortfolio: null,
      },
    }),
    prisma.user.count({
      where: {
        createdAt: { gte: since },
        role: 'USER',
        email: { not: { contains: '@guest.local' } },
        paperPortfolio: { cashBalance: { lt: 10000, gt: 0 } },
      },
    }),
    prisma.user.count({
      where: {
        createdAt: { gte: since },
        role: 'USER',
        email: { not: { contains: '@guest.local' } },
      },
    }),
  ]);

  console.log('\n--- Summary ---');
  console.log(`Total real users (last ${days}d): ${total}`);
  console.log(`0 DDollar + no REGISTER ledger: ${zeroNoReg}`);
  console.log(`Missing paper portfolio: ${noPaper}`);
  console.log(`Paper cash below $10k (traded/spent): ${below10k}`);
} finally {
  await prisma.$disconnect();
}
