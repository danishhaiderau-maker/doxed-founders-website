/**
 * Save showcase credentials to Neon (encrypted). Keys via env — never commit.
 * DEEPSEEK_API_KEY, BITFINEX_API_KEY, BITFINEX_API_SECRET required.
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { createCipheriv, randomBytes, scryptSync } from 'crypto';
import { PrismaClient } from '@prisma/client';
import { loadVaultEnv } from './load-vault-env.mjs';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault');
loadVaultEnv(root);

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    let v = t.slice(i + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    map[t.slice(0, i).trim()] = v;
  }
  return map;
}

function encrypt(plain, jwtSecret) {
  const key = scryptSync(jwtSecret, 'dcf-security-v1', 32);
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  const encrypted = Buffer.concat([cipher.update(plain, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, encrypted]).toString('base64');
}

const vercel = readDotEnv(join(vault, '.env.vercel.check'));
const jwt = vercel.JWT_SECRET;
const ds = process.env.DEEPSEEK_API_KEY?.trim();
const bxK = process.env.BITFINEX_API_KEY?.trim();
const bxS = process.env.BITFINEX_API_SECRET?.trim();

if (!process.env.DATABASE_URL || !jwt) {
  console.error('Missing DATABASE_URL or JWT_SECRET');
  process.exit(1);
}
if (!ds || !bxK || !bxS) {
  console.error('Set DEEPSEEK_API_KEY, BITFINEX_API_KEY, BITFINEX_API_SECRET');
  process.exit(1);
}

const exchangeEnc = encrypt(
  JSON.stringify({ apiKey: bxK, apiSecret: bxS, testnet: false }),
  jwt,
);
const aiEnc = encrypt(ds, jwt);

const prisma = new PrismaClient();
await prisma.platformSettings.upsert({
  where: { id: 'default' },
  create: {
    id: 'default',
    showcaseExchangeProvider: 'bitfinex',
    showcaseAiProvider: 'deepseek',
    showcaseExchangeCredentialEnc: exchangeEnc,
    showcaseAiCredentialEnc: aiEnc,
    showcaseCredentialsUpdatedAt: new Date(),
  },
  update: {
    showcaseExchangeProvider: 'bitfinex',
    showcaseAiProvider: 'deepseek',
    showcaseExchangeCredentialEnc: exchangeEnc,
    showcaseAiCredentialEnc: aiEnc,
    showcaseCredentialsUpdatedAt: new Date(),
  },
});
await prisma.$disconnect();

console.log(
  `Saved showcase credentials (tails: deepseek …${ds.slice(-4)}, bitfinex …${bxK.slice(-4)}/…${bxS.slice(-4)})`,
);
