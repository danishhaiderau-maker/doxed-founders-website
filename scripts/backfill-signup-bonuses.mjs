#!/usr/bin/env node
/**
 * Backfill missing REGISTER DDollar + $10k paper grant for users who signed up
 * via OAuth link or older code paths. Idempotent — safe to re-run.
 *
 * Usage: node scripts/backfill-signup-bonuses.mjs [--dry-run]
 */
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'url';
import { spawnSync } from 'node:child_process';
import { getVaultDir } from './secrets-vault-path.mjs';
import { POINTS, STARTING_CASH_USD } from '@dcf/utils';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dryRun = process.argv.includes('--dry-run');
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
  console.error('prisma generate failed');
  process.exit(1);
}

const { PrismaClient, Prisma } = await import('@prisma/client');
const prisma = new PrismaClient();

try {
  const users = await prisma.user.findMany({
    where: {
      role: 'USER',
      banned: false,
      email: { not: { contains: '@guest.local' } },
    },
    select: {
      id: true,
      email: true,
      reputationPoints: true,
      createdAt: true,
      paperPortfolio: { select: { id: true, cashBalance: true } },
      reputationAwards: { where: { actionKey: 'REGISTER' }, select: { id: true } },
    },
  });

  let grantedDdollar = 0;
  let grantedPaper = 0;
  let ledgerOnly = 0;

  for (const u of users) {
    const missingAward = u.reputationAwards.length === 0;
    const shortBalance = u.reputationPoints < POINTS.REGISTER;
    const needsPaper = !u.paperPortfolio;

    if (!missingAward && !needsPaper && !shortBalance) continue;

    const registerAction = shortBalance
      ? `+${POINTS.REGISTER - u.reputationPoints} DDollar`
      : missingAward
        ? 'ledger-only'
        : 'ok';

    console.log(
      `${dryRun ? '[dry-run] ' : ''}${u.email.slice(0, 45)} — register:${registerAction} paper:${needsPaper ? '+$' + STARTING_CASH_USD : 'ok'} (balance ${u.reputationPoints} DDollar)`,
    );

    if (dryRun) continue;

    if (missingAward && shortBalance) {
      const grant = POINTS.REGISTER - u.reputationPoints;
      try {
        await prisma.reputationAward.create({
          data: { userId: u.id, actionKey: 'REGISTER', amount: POINTS.REGISTER },
        });
        await prisma.user.update({
          where: { id: u.id },
          data: { reputationPoints: { increment: grant } },
        });
        await prisma.pointLedger.create({
          data: {
            userId: u.id,
            amount: grant,
            actionKey: 'REGISTER',
            label: 'Create account',
          },
        });
        grantedDdollar++;
      } catch (err) {
        if (err?.code !== 'P2002') throw err;
      }
    } else if (missingAward) {
      try {
        await prisma.reputationAward.create({
          data: { userId: u.id, actionKey: 'REGISTER', amount: POINTS.REGISTER },
        });
        await prisma.pointLedger.create({
          data: {
            userId: u.id,
            amount: POINTS.REGISTER,
            actionKey: 'REGISTER',
            label: 'Create account',
          },
        });
        ledgerOnly++;
      } catch (err) {
        if (err?.code !== 'P2002') throw err;
      }
    }

    if (needsPaper) {
      await prisma.paperPortfolio.create({
        data: {
          userId: u.id,
          cashBalance: STARTING_CASH_USD,
          totalValue: STARTING_CASH_USD,
        },
      });
      await prisma.virtualEconomyEvent.create({
        data: {
          userId: u.id,
          type: 'INITIAL_GRANT',
          amountUsd: new Prisma.Decimal(STARTING_CASH_USD),
          note: 'Signup paper trading grant (backfill)',
        },
      });
      grantedPaper++;
    }
  }

  console.log(`\nDone. DDollar credits: ${grantedDdollar}, ledger-only fixes: ${ledgerOnly}, paper portfolios: ${grantedPaper}${dryRun ? ' (dry-run)' : ''}`);
} finally {
  await prisma.$disconnect();
}
