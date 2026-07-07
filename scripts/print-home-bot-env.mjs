#!/usr/bin/env node
/**
 * Write home-bot.env to vault with credentials + relay vars for the home PC.
 * Does NOT push to Railway bot (deprecated).
 */
import { readFileSync, existsSync, writeFileSync } from 'fs';
import { join } from 'path';
import { createDecipheriv, randomBytes, scryptSync } from 'crypto';
import { PrismaClient } from '@prisma/client';
import { exchangeCredentialsToEnvVars } from '@dcf/utils';
import { loadVaultEnv } from './load-vault-env.mjs';
import { fileURLToPath } from 'url';
import path from 'path';
import {
  DEFAULT_HOME_BOT_LOCAL_PORT,
  DEFAULT_HOME_BOT_PUBLIC_URL,
  RELAY_WEBHOOK_URL,
  readDotEnv,
  resolveHomeBotPublicUrl,
} from './home-bot-config.mjs';
import { getVaultDir } from './secrets-vault-path.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
loadVaultEnv(root);

function decryptSecret(payload, jwtSecret) {
  const key = scryptSync(jwtSecret, 'dcf-security-v1', 32);
  const buf = Buffer.from(payload, 'base64');
  const iv = buf.subarray(0, 12);
  const tag = buf.subarray(12, 28);
  const encrypted = buf.subarray(28);
  const decipher = createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString('utf8');
}

const vercel = readDotEnv(join(getVaultDir(), '.env.vercel.check'));
const jwtSecret = vercel.JWT_SECRET?.trim();
if (!process.env.DATABASE_URL || !jwtSecret) {
  console.error('Missing DATABASE_URL or JWT_SECRET in vault');
  process.exit(1);
}

const publicUrl = resolveHomeBotPublicUrl(process.argv[2]);
const localPort = process.env.HOME_BOT_LOCAL_PORT?.trim() || String(DEFAULT_HOME_BOT_LOCAL_PORT);

const prisma = new PrismaClient();
const row = await prisma.platformSettings.findUnique({ where: { id: 'default' } });
await prisma.$disconnect();

if (!row?.showcaseExchangeCredentialEnc) {
  console.error('No showcase credentials in Admin Control — save Bitfinex + DeepSeek keys first.');
  process.exit(1);
}

const lines = [
  '# Home BTC bot — copy to research PC (10.0.0.102) or load on same machine',
  `# Generated ${new Date().toISOString()}`,
  '',
  `PORT=${localPort}`,
  `DASHBOARD_PORT=${localPort}`,
  `HOME_BOT_PUBLIC_URL=${publicUrl}`,
  `DASHBOARD_PUBLIC_URL=${publicUrl}`,
  `SHOWCASE_RELAY_WEBHOOK_URL=${RELAY_WEBHOOK_URL}`,
  'CREDENTIALS_FROM=admin_control',
  'SHOWCASE_AGENT=1',
  '',
];

if (vercel.BOT_CONTROL_SECRET?.trim()) {
  lines.push(`BOT_CONTROL_SECRET=${vercel.BOT_CONTROL_SECRET.trim()}`);
}

// Preserve existing phone/remote operator token (or mint one). Never commit this file.
{
  const existingEnvPath = join(getVaultDir(), 'home-bot.env');
  const existing = existsSync(existingEnvPath) ? readDotEnv(existingEnvPath) : {};
  let adminTok = (existing.BOT_ADMIN_TOKEN || process.env.BOT_ADMIN_TOKEN || '').trim();
  if (!adminTok) {
    adminTok = randomBytes(32).toString('base64url');
  }
  lines.push('# Phone/remote operator: https://bot.doxxedcrypto.digital/?admin_token=<token>');
  lines.push(`BOT_ADMIN_TOKEN=${adminTok}`);
}

const ex = JSON.parse(decryptSecret(row.showcaseExchangeCredentialEnc, jwtSecret));
const provider = row.showcaseExchangeProvider ?? 'bitfinex';
const exVars = exchangeCredentialsToEnvVars(provider, {
  apiKey: ex.apiKey,
  apiSecret: ex.apiSecret,
  passphrase: ex.passphrase,
  testnet: ex.testnet,
});
for (const [k, v] of Object.entries(exVars)) {
  if (v) lines.push(`${k}=${v}`);
}

if (row.showcaseAiCredentialEnc) {
  try {
    lines.push(`DEEPSEEK_API_KEY=${decryptSecret(row.showcaseAiCredentialEnc, jwtSecret)}`);
  } catch (err) {
    console.warn('Warning: could not decrypt showcase AI credentials; skipping DEEPSEEK_API_KEY:', err?.message ?? err);
  }
}
const outPath = join(getVaultDir(), 'home-bot.env');
writeFileSync(outPath, `${lines.join('\n')}\n`, 'utf8');

console.log(`\nWrote ${outPath}`);
console.log(`
On home PC (port ${localPort}):

  Option A — research repo (bybit_bot.py) with relay env loaded
  Option B — monorepo after sync:
    cd services/btc-conservative-agent
    python bot.py

  Load env (PowerShell):
    Get-Content "${outPath}" | ForEach-Object {
      if ($_ -match '^([^#=]+)=(.*)$') { Set-Item -Path "env:$($matches[1])" -Value $matches[2] }
    }

  Or: npm run setup:home-bot-tunnel  then  npm run wire:home-bot -- ${publicUrl}
`);
