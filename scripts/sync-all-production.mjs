/**
 * Full production sync: GitHub-ready scripts → Neon schema → Railway → Vercel → verify.
 * Secrets read from ../doxedcryptofounder-secrets/vault (never commit).
 */
import { execSync, spawnSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');

function run(cmd, opts = {}) {
  console.log(`\n> ${cmd}\n`);
  execSync(cmd, { cwd: root, stdio: 'inherit', shell: true, ...opts });
}

function runSoft(cmd) {
  const r = spawnSync(cmd, { cwd: root, shell: true, stdio: 'inherit' });
  return r.status === 0;
}

console.log('\n=== Full production sync ===\n');

console.log('--- 1/5 Neon schema ---');
run('npm run db:push:neon');

console.log('--- 2/5 Railway bot URL + API bridge ---');
run('npm run ensure:btc-bot-url');

console.log('--- 3/5 Railway services (API + btc bot) ---');
run('node scripts/sync-railway-services.mjs');

console.log('--- 4/5 Showcase bot credentials (skip if none saved) ---');
if (!runSoft('npm run push:showcase-bot')) {
  console.warn('Showcase credentials not in DB — save keys in Admin, then re-run push:showcase-bot');
}

console.log('--- 5/5 Vercel env + deploy + Railway API vars ---');
run('npm run sync:production');

console.log('\n=== Full sync complete ===');
