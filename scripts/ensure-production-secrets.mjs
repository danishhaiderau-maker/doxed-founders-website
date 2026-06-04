/**
 * Ensure vault has strong production secrets (JWT, sync keys, bot control).
 * Generates missing values and writes vault/.env.vercel.check updates.
 */
import { readFileSync, writeFileSync, existsSync } from 'fs';
import { randomBytes } from 'node:crypto';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const vault = join(root, '..', 'doxedcryptofounder-secrets', 'vault');
const vercelPath = join(vault, '.env.vercel.check');

function readDotEnv(path) {
  const map = {};
  if (!existsSync(path)) return map;
  for (const line of readFileSync(path, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    const k = trimmed.slice(0, idx).trim();
    let v = trimmed.slice(idx + 1).trim();
    if ((v.startsWith('"') && v.endsWith('"')) || (v.startsWith("'") && v.endsWith("'"))) {
      v = v.slice(1, -1);
    }
    if (v) map[k] = v;
  }
  return map;
}

function writeDotEnv(path, map) {
  const lines = Object.entries(map).map(([k, v]) => `${k}=${v}`);
  writeFileSync(path, `${lines.join('\n')}\n`, 'utf8');
}

function strongSecret(bytes = 48) {
  return randomBytes(bytes).toString('base64url');
}

const KNOWN_WEAK = new Set([
  'dev-secret-change-in-production',
  'dcf-jwt-fallback-do-not-use-in-production',
]);

const env = readDotEnv(vercelPath);
let changed = false;

if (!env.JWT_SECRET || env.JWT_SECRET.length < 32 || KNOWN_WEAK.has(env.JWT_SECRET)) {
  env.JWT_SECRET = strongSecret();
  changed = true;
  console.log('Generated new JWT_SECRET');
}

if (!env.NEXTAUTH_SECRET || env.NEXTAUTH_SECRET.length < 32 || KNOWN_WEAK.has(env.NEXTAUTH_SECRET)) {
  env.NEXTAUTH_SECRET = env.JWT_SECRET;
  changed = true;
  console.log('Synced NEXTAUTH_SECRET to JWT_SECRET');
}

for (const key of ['METRICS_SYNC_SECRET', 'BOT_CONTROL_SECRET', 'GITHUB_WEBHOOK_SECRET']) {
  if (!env[key]?.trim()) {
    env[key] = strongSecret(32);
    changed = true;
    console.log(`Generated ${key}`);
  }
}

if (changed) {
  writeDotEnv(vercelPath, env);
  console.log(`Updated ${vercelPath}`);
} else {
  console.log('Production secrets already present in vault/.env.vercel.check');
}

console.log('\nNote: Rotating JWT_SECRET invalidates encrypted showcase credentials — re-save keys in Admin after sync.');
