import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { loadVaultEnv } from './load-vault-env.mjs';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

loadVaultEnv(root);

function getSchema() {
  if (process.env.PRISMA_SCHEMA) {
    return process.env.PRISMA_SCHEMA;
  }

  const dbUrl = process.env.DATABASE_URL ?? '';
  if (dbUrl.includes('neon.tech') || dbUrl.includes('supabase.co')) {
    return 'prisma/schema.prisma';
  }
  if (process.env.DEV_DB === 'sqlite' || dbUrl.startsWith('file:')) {
    return 'prisma/schema.sqlite.prisma';
  }
  return 'prisma/schema.prisma';
}

const schema = getSchema();
const args = process.argv.slice(2);
const result = spawnSync('npx', ['prisma', ...args, '--schema', schema], {
  cwd: root,
  stdio: 'inherit',
  shell: true,
});

process.exit(result.status ?? 1);
