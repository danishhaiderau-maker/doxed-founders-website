/**
 * Configure production USDC top-ups:
 * 1. Ensure PlatformTreasury + TopUpPayment schema on Neon
 * 2. Set Solana treasury address (DB + optional env fallback)
 * 3. Print Railway env vars to apply
 *
 * Usage:
 *   node scripts/bootstrap-crypto-topups.mjs
 *   node scripts/bootstrap-crypto-topups.mjs --treasury YOUR_SOLANA_WALLET
 *   SOLANA_TREASURY_ADDRESS=... node scripts/bootstrap-crypto-topups.mjs
 */
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { getVaultDir } from './secrets-vault-path.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function loadEnvFile(filePath) {
  if (!fs.existsSync(filePath)) return;
  for (const line of fs.readFileSync(filePath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    const key = trimmed.slice(0, idx).trim();
    let val = trimmed.slice(idx + 1).trim();
    if (
      (val.startsWith('"') && val.endsWith('"')) ||
      (val.startsWith("'") && val.endsWith("'"))
    ) {
      val = val.slice(1, -1);
    }
    if (!process.env[key]) process.env[key] = val;
  }
}

function argValue(flag) {
  const i = process.argv.indexOf(flag);
  return i >= 0 ? process.argv[i + 1]?.trim() : undefined;
}

loadEnvFile(path.join(getVaultDir(), '.env.neon'));
loadEnvFile(path.join(getVaultDir(), '.env'));
loadEnvFile(path.join(getVaultDir(), '.env.x.secrets'));
loadEnvFile(path.join(root, '.env'));

const treasuryArg =
  argValue('--treasury') ||
  process.env.SOLANA_TREASURY_ADDRESS?.trim() ||
  process.env.PLATFORM_SOLANA_TREASURY?.trim();

const rpcUrl =
  process.env.HELIUS_RPC_URL?.trim() ||
  process.env.SOLANA_RPC_URL?.trim() ||
  'https://api.mainnet-beta.solana.com';

if (!process.env.DATABASE_URL?.startsWith('postgres')) {
  console.error('Missing DATABASE_URL. Add vault/.env.neon or run npm run setup:neon');
  process.exit(1);
}

console.log('\n=== Bootstrap crypto top-ups (production) ===\n');

console.log('[1/3] Prisma db push (Neon schema)...');
const push = spawnSync(
  'npx',
  ['prisma', 'db', 'push', '--schema', 'prisma/schema.prisma', '--skip-generate'],
  { cwd: root, stdio: 'inherit', shell: true, env: process.env },
);
if (push.status !== 0) {
  console.error('db push failed');
  process.exit(push.status ?? 1);
}

console.log('\n[2/3] Reading platform treasury + admin wallets...');
const { PrismaClient } = await import('@prisma/client');
const prisma = new PrismaClient();

try {
  let treasury = await prisma.platformTreasury.findUnique({ where: { id: 'default' } });
  const adminWallets = await prisma.walletConnection.findMany({
    where: { chain: 'SOLANA', user: { role: 'ADMIN' } },
    select: { address: true, user: { select: { email: true } } },
  });

  const treasuryToSet = treasuryArg || treasury?.solanaTreasuryAddress?.trim();
  const fallbackAdminWallet = adminWallets[0]?.address?.trim();

  if (!treasuryToSet && fallbackAdminWallet) {
    console.log(
      `No treasury configured. Using first admin Solana wallet (${adminWallets[0].user.email}).`,
    );
    treasury = await prisma.platformTreasury.upsert({
      where: { id: 'default' },
      create: { id: 'default', solanaTreasuryAddress: fallbackAdminWallet },
      update: { solanaTreasuryAddress: fallbackAdminWallet },
    });
  } else if (treasuryToSet) {
    treasury = await prisma.platformTreasury.upsert({
      where: { id: 'default' },
      create: { id: 'default', solanaTreasuryAddress: treasuryToSet },
      update: { solanaTreasuryAddress: treasuryToSet },
    });
  }

  const finalTreasury = treasury?.solanaTreasuryAddress?.trim() ?? null;

  console.log('\n[3/3] Production checklist\n');
  console.log('Neon PlatformTreasury:', finalTreasury ?? '(NOT SET — pass --treasury or link admin Solana wallet)');
  console.log('Admin Solana wallets:', adminWallets.length ? adminWallets.map((w) => w.address).join(', ') : '(none)');

  const railwayPaste = [
    process.env.HELIUS_RPC_URL?.trim()
      ? 'HELIUS_RPC_URL=(set in vault)'
      : `SOLANA_RPC_URL=${rpcUrl}`,
    finalTreasury ? 'PLATFORM_SOLANA_TREASURY=(treasury address saved to Neon)' : null,
  ]
    .filter(Boolean)
    .join('\n');

  const pastePath = path.join(getVaultDir(), 'railway-crypto-paste.env');
  const pasteSecrets = [
    process.env.HELIUS_RPC_URL?.trim()
      ? `HELIUS_RPC_URL=${process.env.HELIUS_RPC_URL.trim()}`
      : `SOLANA_RPC_URL=${rpcUrl}`,
    finalTreasury ? `PLATFORM_SOLANA_TREASURY=${finalTreasury}` : null,
  ]
    .filter(Boolean)
    .join('\n');
  fs.writeFileSync(pastePath, pasteSecrets, 'utf8');
  console.log('\nWrote Railway vars to vault/railway-crypto-paste.env (secrets not printed)');
  console.log('Paste into Railway → API service → Variables → Raw Editor, then redeploy.\n');
  console.log(railwayPaste);
  console.log('\nAfter Railway redeploy, verify:');
  console.log('  curl https://doxxedcrypto.digital/api/paper-trading/reset-info');
  console.log('  → cryptoEnabled: true, treasuryAddress set\n');

  if (!finalTreasury) {
    console.error(
      'Treasury still empty. Set --treasury YOUR_WALLET or connect Solana in Account → Security as admin, then re-run.',
    );
    process.exit(1);
  }
} finally {
  await prisma.$disconnect();
}
