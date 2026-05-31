/**
 * Enable admin 2FA (TOTP + backup recovery codes) on production Neon.
 * Writes secrets to vault/.env.admin-security (never commit).
 *
 * Usage: npm run setup:admin-security
 */
import { createCipheriv, randomBytes, scryptSync } from 'crypto';
import { readFileSync, existsSync, writeFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import bcrypt from 'bcrypt';
import { generateSecret, generateURI } from 'otplib';
import { PrismaClient } from '@prisma/client';

const ADMIN_EMAIL = 'admin@doxedcryptofounder.local';
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vaultDir = join(root, '..', 'doxedcryptofounder-secrets', 'vault');

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    let val = trimmed.slice(idx + 1).trim();
    if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
      val = val.slice(1, -1);
    }
    map[trimmed.slice(0, idx).trim()] = val;
  }
  return map;
}

function loadEnv() {
  const neon = readDotEnv(join(vaultDir, '.env.neon'));
  const selfHost = readDotEnv(join(vaultDir, '.env.self-host'));
  const vercel = readDotEnv(join(vaultDir, '.env.vercel.check'));
  process.env.DATABASE_URL =
    process.env.DATABASE_URL || neon.DATABASE_URL || vercel.DATABASE_URL || selfHost.DATABASE_URL;
  process.env.JWT_SECRET =
    process.env.JWT_SECRET || vercel.JWT_SECRET || selfHost.JWT_SECRET;
  if (!process.env.DATABASE_URL) {
    console.error('Missing DATABASE_URL in vault');
    process.exit(1);
  }
  if (!process.env.JWT_SECRET || process.env.JWT_SECRET.length < 32) {
    console.error('Missing JWT_SECRET (32+ chars) in vault');
    process.exit(1);
  }
}

function encryptSecret(plain) {
  const key = scryptSync(process.env.JWT_SECRET, 'dcf-security-v1', 32);
  const iv = randomBytes(12);
  const cipher = createCipheriv('aes-256-gcm', key, iv);
  const encrypted = Buffer.concat([cipher.update(plain, 'utf8'), cipher.final()]);
  const tag = cipher.getAuthTag();
  return Buffer.concat([iv, tag, encrypted]).toString('base64');
}

function recoveryCode() {
  const part = () => randomBytes(2).toString('hex').toUpperCase();
  return `${part()}-${part()}-${part()}-${part()}`;
}

loadEnv();

const prisma = new PrismaClient();
const user = await prisma.user.findUnique({ where: { email: ADMIN_EMAIL } });
if (!user) {
  console.error('Admin user not found');
  process.exit(1);
}

const totpSecret = generateSecret();
const otpauthUrl = generateURI({ issuer: 'Founder OS', label: ADMIN_EMAIL, secret: totpSecret });

await prisma.userTotp.upsert({
  where: { userId: user.id },
  create: {
    userId: user.id,
    secretEncrypted: encryptSecret(totpSecret),
    enabled: true,
    enabledAt: new Date(),
  },
  update: {
    secretEncrypted: encryptSecret(totpSecret),
    enabled: true,
    enabledAt: new Date(),
  },
});

await prisma.recoveryCode.deleteMany({ where: { userId: user.id } });
const codes = Array.from({ length: 10 }, recoveryCode);
for (const code of codes) {
  await prisma.recoveryCode.create({
    data: {
      userId: user.id,
      codeHash: await bcrypt.hash(code.replace(/-/g, ''), 12),
    },
  });
}

const outPath = join(vaultDir, '.env.admin-security');
const body = [
  `# Admin 2FA — generated ${new Date().toISOString()}`,
  `ADMIN_EMAIL=${ADMIN_EMAIL}`,
  `ADMIN_TOTP_SECRET=${totpSecret}`,
  `ADMIN_TOTP_URI=${otpauthUrl}`,
  '',
  '# Backup recovery codes (one-time use each):',
  ...codes.map((c, i) => `ADMIN_RECOVERY_${i + 1}=${c}`),
  '',
].join('\n');
writeFileSync(outPath, body, { mode: 0o600 });

console.log(`Admin 2FA enabled for ${ADMIN_EMAIL}`);
console.log(`Saved to ${outPath}`);
console.log('\nAdd this secret to Google Authenticator / 1Password:');
console.log(totpSecret);
console.log('\nOr scan this URI in your authenticator app:');
console.log(otpauthUrl);
console.log('\nBackup recovery codes:');
for (const c of codes) console.log(`  ${c}`);
console.log('\nLogin flow: email + password → authenticator code OR one recovery code.');
console.log('Then add a passkey at /account?tab=security for faster sign-in.');

await prisma.$disconnect();
