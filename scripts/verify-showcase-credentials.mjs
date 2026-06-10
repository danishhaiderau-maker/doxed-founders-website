/**
 * Verify Admin Control showcase credentials in Neon (tails only — never full keys).
 * Usage: node scripts/verify-showcase-credentials.mjs [--expect-tails 4971,40bd,1c74]
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { createDecipheriv, scryptSync } from 'crypto';
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

function decrypt(payload, jwtSecret) {
  const key = scryptSync(jwtSecret, 'dcf-security-v1', 32);
  const buf = Buffer.from(payload, 'base64');
  const iv = buf.subarray(0, 12);
  const tag = buf.subarray(12, 28);
  const encrypted = buf.subarray(28);
  const decipher = createDecipheriv('aes-256-gcm', key, iv);
  decipher.setAuthTag(tag);
  return Buffer.concat([decipher.update(encrypted), decipher.final()]).toString('utf8');
}

function tail(s) {
  return s && s.length >= 4 ? s.slice(-4) : null;
}

const expectIdx = process.argv.indexOf('--expect-tails');
const expectTails = expectIdx >= 0 ? process.argv[expectIdx + 1]?.split(',') : null;

const vercel = readDotEnv(join(vault, '.env.vercel.check'));
const jwt = vercel.JWT_SECRET;
if (!process.env.DATABASE_URL || !jwt) {
  console.error('Missing DATABASE_URL or JWT_SECRET');
  process.exit(1);
}

const prisma = new PrismaClient();
const row = await prisma.platformSettings.findUnique({ where: { id: 'default' } });
await prisma.$disconnect();

const report = {
  exchangeProvider: row?.showcaseExchangeProvider ?? null,
  aiProvider: row?.showcaseAiProvider ?? null,
  exchangeSaved: Boolean(row?.showcaseExchangeCredentialEnc),
  aiSaved: Boolean(row?.showcaseAiCredentialEnc),
  credentialsUpdatedAt: row?.showcaseCredentialsUpdatedAt?.toISOString?.() ?? null,
  runtimePushedAt: row?.showcaseRuntimePushedAt?.toISOString?.() ?? null,
  botPublicUrl: row?.showcaseBotPublicUrl ?? null,
  tails: {},
  matchExpect: null,
};

if (row?.showcaseExchangeCredentialEnc) {
  const ex = JSON.parse(decrypt(row.showcaseExchangeCredentialEnc, jwt));
  report.tails.bitfinexApiKey = tail(ex.apiKey);
  report.tails.bitfinexApiSecret = tail(ex.apiSecret);
}
if (row?.showcaseAiCredentialEnc) {
  report.tails.deepseek = tail(decrypt(row.showcaseAiCredentialEnc, jwt));
}

if (expectTails?.length === 3) {
  const [ds, bxK, bxS] = expectTails;
  report.matchExpect = {
    deepseek: report.tails.deepseek === ds,
    bitfinexKey: report.tails.bitfinexApiKey === bxK,
    bitfinexSecret: report.tails.bitfinexApiSecret === bxS,
    allMatch:
      report.tails.deepseek === ds &&
      report.tails.bitfinexApiKey === bxK &&
      report.tails.bitfinexApiSecret === bxS,
  };
}

console.log(JSON.stringify(report, null, 2));

if (!report.exchangeSaved || !report.aiSaved) process.exit(2);
