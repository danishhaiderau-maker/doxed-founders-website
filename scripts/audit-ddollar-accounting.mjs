#!/usr/bin/env node
/**
 * Verify DDollar accounting: balances vs ledger, hire fees vs admin credits.
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

spawnSync('npx', ['prisma@6.8.2', 'generate', '--schema', 'prisma/schema.prisma'], {
  cwd: root,
  stdio: 'pipe',
  shell: true,
  env: process.env,
});

const { PrismaClient } = await import('@prisma/client');
const prisma = new PrismaClient();

try {
  const users = await prisma.user.findMany({
    where: { banned: false, email: { not: { contains: '@guest.local' } } },
    select: { id: true, email: true, reputationPoints: true, role: true },
  });

  const ledgerSum = await prisma.pointLedger.groupBy({
    by: ['userId'],
    _sum: { amount: true },
  });
  const ledgerByUser = new Map(ledgerSum.map((r) => [r.userId, r._sum.amount ?? 0]));

  const totalBalance = users.reduce((s, u) => s + u.reputationPoints, 0);
  const totalLedger = [...ledgerByUser.values()].reduce((s, v) => s + v, 0);

  const missingRegister = users.filter(
    (u) => u.reputationPoints < 10000 && u.role !== 'GUEST',
  );

  const hireSpends = await prisma.pointLedger.aggregate({
    _sum: { amount: true },
    where: { actionKey: { startsWith: 'AGENT_HIRE' } },
  });
  const platformFees = await prisma.pointLedger.aggregate({
    _sum: { amount: true },
    where: { actionKey: { startsWith: 'PLATFORM_FEE' } },
  });

  const mismatches = users.filter((u) => {
    const ledger = ledgerByUser.get(u.id);
    if (ledger == null) return u.reputationPoints !== 0;
    return ledger !== u.reputationPoints;
  });

  console.log('\n=== DDollar platform accounting ===\n');
  console.log(`Real users: ${users.length}`);
  console.log(`Total circulating (balances): ${totalBalance.toLocaleString()}`);
  console.log(`Total ledger net (all users): ${totalLedger.toLocaleString()}`);
  console.log(`Balance vs ledger mismatches: ${mismatches.length}`);
  if (mismatches.length) {
    for (const u of mismatches.slice(0, 5)) {
      console.log(`  ${u.email}: balance=${u.reputationPoints} ledger=${ledgerByUser.get(u.id) ?? 0}`);
    }
  }

  console.log(`\nAgent hire spends (user): ${Math.abs(hireSpends._sum.amount ?? 0).toLocaleString()}`);
  console.log(`Platform fees (admin): ${(platformFees._sum.amount ?? 0).toLocaleString()}`);
  const hireTotal = Math.abs(hireSpends._sum.amount ?? 0);
  const feeTotal = platformFees._sum.amount ?? 0;
  if (hireTotal !== feeTotal) {
    console.log(`⚠ Hire/fee mismatch: spends ${hireTotal} vs admin credits ${feeTotal}`);
  } else {
    console.log('✓ Hire fees match admin credits (or none yet)');
  }

  const below10k = users.filter((u) => u.reputationPoints < 10000);
  console.log(`\nAccounts below 10k DDollar: ${below10k.length}`);
  for (const u of below10k) {
    console.log(`  ${u.role} ${u.email}: ${u.reputationPoints}`);
  }
} finally {
  await prisma.$disconnect();
}
