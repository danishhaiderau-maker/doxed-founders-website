#!/usr/bin/env node
/**
 * Print GitHub Actions secrets for release-signed APK (same key every build).
 * Run after: npm run pack:android (creates vault/.env.android + keystore once)
 */
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const root = path.join(path.dirname(fileURLToPath(import.meta.url)), '..');
const vault = path.join(path.dirname(root), 'doxedcryptofounder-secrets', 'vault');
const envPath = path.join(vault, '.env.android');

function readDotEnv(filePath) {
  const map = {};
  if (!fs.existsSync(filePath)) return map;
  for (const line of fs.readFileSync(filePath, 'utf8').split('\n')) {
    const trimmed = line.trim();
    if (!trimmed || trimmed.startsWith('#')) continue;
    const idx = trimmed.indexOf('=');
    if (idx < 1) continue;
    map[trimmed.slice(0, idx).trim()] = trimmed.slice(idx + 1).trim();
  }
  return map;
}

const env = readDotEnv(envPath);
const keystorePath = env.ANDROID_KEYSTORE_PATH || path.join(vault, 'android-release.keystore');
if (!fs.existsSync(keystorePath)) {
  console.error('No keystore found. Run: npm run pack:android');
  process.exit(1);
}

const b64 = fs.readFileSync(keystorePath).toString('base64');
console.log('\nAdd these GitHub repo secrets (Settings → Secrets → Actions):\n');
console.log('ANDROID_RELEASE_KEYSTORE_B64 = <base64 below>');
console.log(`ANDROID_KEYSTORE_PASSWORD = ${env.ANDROID_KEYSTORE_PASSWORD ?? '(from .env.android)'}`);
console.log(`ANDROID_KEY_ALIAS = ${env.ANDROID_KEY_ALIAS ?? 'doxxedcrypto'}`);
console.log('\n--- base64 (first 80 chars) ---');
console.log(b64.slice(0, 80) + '…');
console.log(`\nFull length: ${b64.length} chars. Pipe to clipboard or save securely.`);
