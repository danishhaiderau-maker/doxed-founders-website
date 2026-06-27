/**
 * CI-friendly Neon schema push — uses DATABASE_URL from env (GitHub Secrets).
 * No vault dependency. Safe to run in GitHub Actions.
 *
 * Usage: DATABASE_URL=postgresql://... node scripts/ci-neon-schema-push.mjs
 */
import { spawnSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

const dbUrl = process.env.DATABASE_URL?.trim();
if (!dbUrl?.startsWith('postgres')) {
  console.error('DATABASE_URL must be a postgresql:// connection string (set in GitHub Secrets)');
  process.exit(1);
}

console.log('=== CI Neon schema push ===');
console.log(`Target: ${dbUrl.replace(/:[^:@]+@/, ':****@')}`);

const r = spawnSync(
  'npx',
  ['prisma@6.8.2', 'db', 'push', '--schema', 'prisma/schema.prisma', '--accept-data-loss', '--skip-generate'],
  { cwd: root, stdio: 'inherit', shell: true, env: process.env },
);

if (r.status !== 0) {
  console.error('Neon schema push FAILED');
  process.exit(r.status ?? 1);
}

console.log('Neon schema push OK');
