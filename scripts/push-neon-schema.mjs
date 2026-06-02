/**
 * Push prisma/schema.prisma to Neon using vault/.env.neon
 */
import fs from 'fs';
import path from 'path';
import { spawnSync } from 'child_process';
import { fileURLToPath } from 'url';
import { getVaultDir } from './secrets-vault-path.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const neonPath = path.join(getVaultDir(), '.env.neon');

if (!fs.existsSync(neonPath)) {
  console.error('Missing vault/.env.neon');
  process.exit(1);
}

for (const line of fs.readFileSync(neonPath, 'utf8').split('\n')) {
  const trimmed = line.trim();
  if (!trimmed || trimmed.startsWith('#')) continue;
  const idx = trimmed.indexOf('=');
  if (idx < 1) continue;
  const key = trimmed.slice(0, idx).trim();
  let val = trimmed.slice(idx + 1).trim();
  if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
    val = val.slice(1, -1);
  }
  process.env[key] = val;
}

if (!process.env.DATABASE_URL?.startsWith('postgres')) {
  console.error('DATABASE_URL in .env.neon must be postgresql://');
  process.exit(1);
}

const r = spawnSync('npx', ['prisma@6.8.2', 'db', 'push', '--schema', 'prisma/schema.prisma'], {
  cwd: root,
  stdio: 'inherit',
  shell: true,
  env: process.env,
});

process.exit(r.status ?? 1);
