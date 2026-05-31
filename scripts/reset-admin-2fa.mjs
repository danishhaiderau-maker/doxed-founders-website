/**
 * Emergency admin 2FA reset (production Neon).
 * Usage: node scripts/reset-admin-2fa.mjs [--generate-recovery]
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { randomBytes } from 'crypto';
import bcrypt from 'bcrypt';
import { PrismaClient } from '@prisma/client';

const ADMIN_EMAIL = 'admin@doxedcryptofounder.local';
const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const neonPath = join(root, '..', 'doxedcryptofounder-secrets', 'vault', '.env.neon');

function loadDatabaseUrl() {
  if (process.env.DATABASE_URL?.trim()) return process.env.DATABASE_URL.trim();
  if (!existsSync(neonPath)) {
    console.error('Set DATABASE_URL or add vault/.env.neon');
    process.exit(1);
  }
  for (const line of readFileSync(neonPath, 'utf8').split('\n')) {
    const m = line.match(/^DATABASE_URL="([^"]+)"/);
    if (m) return m[1];
  }
  process.exit(1);
}

function recoveryCode() {
  return randomBytes(5).toString('hex').toUpperCase().match(/.{1,4}/g).join('-');
}

const generateRecovery = process.argv.includes('--generate-recovery');
const prisma = new PrismaClient({ datasources: { db: { url: loadDatabaseUrl() } } });

const user = await prisma.user.findUnique({ where: { email: ADMIN_EMAIL } });
if (!user) {
  console.error('Admin user not found');
  process.exit(1);
}

const before = {
  passkeys: await prisma.webAuthnCredential.count({ where: { userId: user.id } }),
  totp: await prisma.userTotp.findUnique({ where: { userId: user.id } }),
  recovery: await prisma.recoveryCode.count({ where: { userId: user.id, usedAt: null } }),
};

await prisma.webAuthnCredential.deleteMany({ where: { userId: user.id } });
await prisma.userTotp.deleteMany({ where: { userId: user.id } });
await prisma.authPendingChallenge.deleteMany({ where: { userId: user.id } });

let codes = [];
if (generateRecovery) {
  await prisma.recoveryCode.deleteMany({ where: { userId: user.id } });
  codes = Array.from({ length: 8 }, recoveryCode);
  for (const code of codes) {
    const codeHash = await bcrypt.hash(code.replace(/-/g, ''), 10);
    await prisma.recoveryCode.create({ data: { userId: user.id, codeHash } });
  }
}

console.log(`Admin 2FA cleared for ${ADMIN_EMAIL}`);
console.log(`Before: passkeys=${before.passkeys}, totp=${before.totp?.enabled ? 'on' : 'off'}, recovery=${before.recovery}`);
console.log('You can now sign in with email + password only (no 2FA step).');
if (codes.length) {
  console.log('\nNew recovery codes (save securely — shown once):');
  for (const c of codes) console.log(`  ${c}`);
}

await prisma.$disconnect();
