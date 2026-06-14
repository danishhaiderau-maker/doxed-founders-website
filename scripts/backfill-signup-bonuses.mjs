#!/usr/bin/env node
/**
 * Upgrade welcome DDollar from legacy 50 → WELCOME_DDOLLAR_GRANT (10,000).
 * Idempotent — safe to re-run.
 *
 * Usage: node scripts/backfill-signup-bonuses.mjs [--dry-run]
 */
import { readFileSync, existsSync } from 'node:fs';
import { join, dirname } from 'node:path';
import { fileURLToPath } from 'url';
import { spawnSync } from 'node:child_process';
import { getVaultDir } from './secrets-vault-path.mjs';
import {
  POINTS,
  STARTING_CASH_USD,
  WELCOME_DDOLLAR_GRANT,
  contributorLevelFromPoints,
} from '@dcf/utils';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const dryRun = process.argv.includes('--dry-run');
const neonPath = join(getVaultDir(), '.env.neon');
const LEGACY_WELCOME = 50;

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
      reputationAwards: {
        where: { actionKey: { in: ['REGISTER', 'WELCOME_UPGRADE'] } },
        select: { actionKey: true, amount: true },
      },
      paperPortfolio: { select: { id: true } },
    },
  });

  let fullGrants = 0;
  let upgrades = 0;
  let ledgerOnly = 0;
  let paperGrants = 0;

  for (const u of users) {
    const registerAward = u.reputationAwards.find((a) => a.actionKey === 'REGISTER');
    const hasUpgrade = u.reputationAwards.some((a) => a.actionKey === 'WELCOME_UPGRADE');
    const needsPaper = !u.paperPortfolio;
    const awardedWelcome = registerAward?.amount ?? 0;
    const needsFullWelcome =
      !registerAward && u.reputationPoints < WELCOME_DDOLLAR_GRANT;
    const needsUpgrade =
      !hasUpgrade &&
      awardedWelcome > 0 &&
      awardedWelcome < WELCOME_DDOLLAR_GRANT;

    if (!needsFullWelcome && !needsUpgrade && !needsPaper) continue;

    const action = needsFullWelcome
      ? `+${WELCOME_DDOLLAR_GRANT - u.reputationPoints} welcome`
      : needsUpgrade
        ? `+${WELCOME_DDOLLAR_GRANT - awardedWelcome} upgrade`
        : 'ok';

    console.log(
      `${dryRun ? '[dry-run] ' : ''}${u.email.slice(0, 42)} — ${action} paper:${needsPaper ? '+$10k' : 'ok'} (now ${u.reputationPoints})`,
    );

    if (dryRun) continue;

    if (needsFullWelcome) {
      const grant = WELCOME_DDOLLAR_GRANT - u.reputationPoints;
      try {
        await prisma.reputationAward.create({
          data: { userId: u.id, actionKey: 'REGISTER', amount: WELCOME_DDOLLAR_GRANT },
        });
      } catch (err) {
        if (err?.code !== 'P2002') throw err;
      }
      const updated = await prisma.user.update({
        where: { id: u.id },
        data: { reputationPoints: { increment: grant } },
        select: { reputationPoints: true },
      });
      await prisma.pointLedger.create({
        data: {
          userId: u.id,
          amount: grant,
          actionKey: 'REGISTER',
          label: 'Create account',
        },
      });
      await prisma.user.update({
        where: { id: u.id },
        data: { contributorLevel: contributorLevelFromPoints(updated.reputationPoints) },
      });
      fullGrants++;
    } else if (needsUpgrade) {
      const delta = WELCOME_DDOLLAR_GRANT - awardedWelcome;
      try {
        await prisma.reputationAward.create({
          data: { userId: u.id, actionKey: 'WELCOME_UPGRADE', amount: delta },
        });
        if (registerAward) {
          await prisma.reputationAward.update({
            where: { userId_actionKey: { userId: u.id, actionKey: 'REGISTER' } },
            data: { amount: WELCOME_DDOLLAR_GRANT },
          });
        }
        const updated = await prisma.user.update({
          where: { id: u.id },
          data: { reputationPoints: { increment: delta } },
          select: { reputationPoints: true },
        });
        await prisma.pointLedger.create({
          data: {
            userId: u.id,
            amount: delta,
            actionKey: 'WELCOME_UPGRADE',
            label: 'Welcome bonus upgrade',
          },
        });
        await prisma.user.update({
          where: { id: u.id },
          data: { contributorLevel: contributorLevelFromPoints(updated.reputationPoints) },
        });
        upgrades++;
      } catch (err) {
        if (err?.code !== 'P2002') throw err;
      }
    } else if (registerAward && registerAward.amount < WELCOME_DDOLLAR_GRANT && !hasUpgrade) {
      await prisma.reputationAward.update({
        where: { userId_actionKey: { userId: u.id, actionKey: 'REGISTER' } },
        data: { amount: WELCOME_DDOLLAR_GRANT },
      });
      ledgerOnly++;
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
      paperGrants++;
    }
  }

  console.log(
    `\nDone. Full welcome: ${fullGrants}, upgrades (+${WELCOME_DDOLLAR_GRANT - LEGACY_WELCOME}): ${upgrades}, ledger fixes: ${ledgerOnly}, paper: ${paperGrants}${dryRun ? ' (dry-run)' : ''}`,
  );
} finally {
  await prisma.$disconnect();
}
