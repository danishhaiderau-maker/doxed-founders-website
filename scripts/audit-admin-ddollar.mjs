#!/usr/bin/env node
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
  const admins = await prisma.user.findMany({
    where: { role: 'ADMIN' },
    select: {
      id: true,
      email: true,
      reputationPoints: true,
      createdAt: true,
      reputationAwards: { select: { actionKey: true, amount: true } },
      pointLedger: {
        orderBy: { createdAt: 'desc' },
        take: 20,
        select: { amount: true, actionKey: true, label: true, createdAt: true },
      },
    },
  });

  const totalCirc = await prisma.user.aggregate({
    _sum: { reputationPoints: true },
    where: { banned: false, email: { not: { contains: '@guest.local' } } },
  });

  const hireFees = await prisma.pointLedger.findMany({
    where: { actionKey: { startsWith: 'PLATFORM_FEE' } },
    orderBy: { createdAt: 'desc' },
    take: 15,
    select: { amount: true, actionKey: true, createdAt: true },
  });

  const agentHires = await prisma.pointLedger.findMany({
    where: { actionKey: { startsWith: 'AGENT_HIRE' } },
    orderBy: { createdAt: 'desc' },
    take: 15,
    select: { amount: true, actionKey: true, createdAt: true },
  });

  const circ = totalCirc._sum.reputationPoints ?? 0;
  console.log('\n=== ADMIN DDollar audit ===\n');
  for (const a of admins) {
    const share = circ > 0 ? ((a.reputationPoints / circ) * 100).toFixed(4) : '0';
    console.log(`Admin: ${a.email}`);
    console.log(`  Balance: ${a.reputationPoints} DDollar (${share}% of circulating ${circ})`);
    console.log(`  Awards: ${JSON.stringify(a.reputationAwards)}`);
    console.log('  Recent ledger:');
    for (const e of a.pointLedger.slice(0, 10)) {
      console.log(`    ${e.createdAt.toISOString().slice(0, 19)} ${e.amount >= 0 ? '+' : ''}${e.amount} ${e.actionKey}`);
    }
    console.log('');
  }

  console.log('--- Platform fees (admin income) ---');
  for (const f of hireFees) {
    console.log(`  ${f.createdAt.toISOString().slice(0, 19)} +${f.amount} ${f.actionKey}`);
  }
  if (!hireFees.length) console.log('  (none yet)');

  console.log('\n--- Agent hire spends (user deductions) ---');
  for (const h of agentHires) {
    console.log(`  ${h.createdAt.toISOString().slice(0, 19)} ${h.amount} ${h.actionKey}`);
  }
  if (!agentHires.length) console.log('  (none yet)');

  const usersWith10k = await prisma.user.count({
    where: { reputationPoints: { gte: 10000 }, role: 'USER' },
  });
  const usersTotal = await prisma.user.count({
    where: { role: 'USER', banned: false, email: { not: { contains: '@guest.local' } } },
  });
  const adminNoRegister = admins.filter((a) => !a.reputationAwards.some((x) => x.actionKey === 'REGISTER'));
  console.log(`\nUsers with >=10k: ${usersWith10k} / ${usersTotal}`);
  console.log(`Admins missing REGISTER award: ${adminNoRegister.map((a) => a.email).join(', ') || 'none'}`);
} finally {
  await prisma.$disconnect();
}
