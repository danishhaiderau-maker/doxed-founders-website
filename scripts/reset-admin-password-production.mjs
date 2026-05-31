/**
 * Rotate admin password in Neon + update vault/.env.x.secrets
 * Usage: node scripts/reset-admin-password-production.mjs
 */
import { randomBytes } from 'crypto';
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';
import { spawnSync } from 'child_process';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vaultDir = join(root, '..', 'doxedcryptofounder-secrets', 'vault');
const xSecretsPath = join(vaultDir, '.env.x.secrets');

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    map[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim().replace(/^"|"$/g, '');
  }
  return map;
}

function writeDotEnvKey(path, key, value) {
  const lines = existsSync(path) ? readFileSync(path, 'utf8').split('\n') : [];
  let found = false;
  const out = lines.map((line) => {
    if (line.startsWith(`${key}=`)) {
      found = true;
      return `${key}=${value}`;
    }
    return line;
  });
  if (!found) out.push(`${key}=${value}`);
  writeFileSync(path, out.filter((l, i, a) => i < a.length - 1 || l.trim()).join('\n') + '\n', {
    mode: 0o600,
  });
}

const neon = readDotEnv(join(vaultDir, '.env.neon'));
const vercel = readDotEnv(join(vaultDir, '.env.vercel.check'));
const dbUrl = process.env.DATABASE_URL || neon.DATABASE_URL || vercel.DATABASE_URL;
if (!dbUrl) {
  console.error('Missing DATABASE_URL in vault');
  process.exit(1);
}

const password = `DcfAdmin!${randomBytes(4).toString('hex')}A1`;

const result = spawnSync(
  'node',
  ['scripts/rotate-admin-password.mjs'],
  {
    cwd: root,
    stdio: 'inherit',
    env: { ...process.env, DATABASE_URL: dbUrl, SEED_ADMIN_PASSWORD: password },
  },
);
if (result.status !== 0) process.exit(result.status ?? 1);

writeDotEnvKey(xSecretsPath, 'ADMIN_PASSWORD', password);
writeDotEnvKey(xSecretsPath, 'ADMIN_EMAIL', 'admin@doxedcryptofounder.local');

console.log('\n=== Admin credentials (save securely) ===');
console.log('Email:    admin@doxedcryptofounder.local');
console.log(`Password: ${password}`);
console.log('\nNext: npm run fix:admin-2fa  (sync TOTP with Railway JWT)');
