/**
 * Sync X OAuth 2.0 login vars to Vercel production from .env.x.secrets
 * Usage: node scripts/fix-x-oauth-vercel.mjs
 */
import { spawnSync } from 'node:child_process';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const secretsPath = path.join(root, '.env.x.secrets');

function readEnv(file) {
  const map = {};
  if (!fs.existsSync(file)) return map;
  for (const line of fs.readFileSync(file, 'utf8').split('\n')) {
    const t = line.trim();
    if (!t || t.startsWith('#')) continue;
    const i = t.indexOf('=');
    if (i < 1) continue;
    map[t.slice(0, i).trim()] = t.slice(i + 1).trim().replace(/^"|"$/g, '');
  }
  return map;
}

function setVercel(name, value) {
  if (!value) {
    console.warn(`  skip ${name} (empty)`);
    return;
  }
  const result = spawnSync('vercel', ['env', 'add', name, 'production', '--force'], {
    cwd: root,
    input: value,
    encoding: 'utf8',
    shell: true,
  });
  if (result.status !== 0) {
    console.error(`  failed ${name}:`, result.stderr || result.stdout);
  } else {
    console.log(`  set ${name}`);
  }
}

const env = readEnv(secretsPath);
if (!env.TWITTER_CLIENT_ID || !env.TWITTER_CLIENT_SECRET) {
  console.error('Missing TWITTER_CLIENT_ID / TWITTER_CLIENT_SECRET in .env.x.secrets');
  process.exit(1);
}

if (env.TWITTER_CLIENT_ID === env.TWITTER_API_KEY) {
  console.error('TWITTER_CLIENT_ID equals TWITTER_API_KEY — use OAuth 2.0 Client ID from User authentication settings');
  process.exit(1);
}

console.log('=== Fix X OAuth on Vercel ===\n');

const vars = {
  TWITTER_CLIENT_ID: env.TWITTER_CLIENT_ID,
  TWITTER_CLIENT_SECRET: env.TWITTER_CLIENT_SECRET,
  API_URL: env.API_URL || 'https://doxed-founders-website-production.up.railway.app',
  NEXTAUTH_URL: env.PUBLIC_SITE_URL || 'https://doxxedcrypto.digital',
  NEXTAUTH_SECRET: env.NEXTAUTH_SECRET || env.JWT_SECRET,
  CORS_ORIGINS: 'https://doxxedcrypto.digital,https://www.doxxedcrypto.digital,https://doxed-founders-website.vercel.app',
};

for (const [name, value] of Object.entries(vars)) {
  setVercel(name, value);
}

console.log('\nDone. Redeploy: vercel --prod');
console.log('X callback must be: https://doxxedcrypto.digital/api/auth/callback/twitter');
