/**
 * One-shot: obtain admin JWT (password + recovery if 2FA) and run daily X jobs.
 */
import { readFileSync, existsSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

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
    map[trimmed.slice(0, idx).trim()] = trimmed
      .slice(idx + 1)
      .trim()
      .replace(/^"|"$/g, '');
  }
  return map;
}

const xSecrets = readDotEnv(
  existsSync(join(root, '.env.x.secrets'))
    ? join(root, '.env.x.secrets')
    : join(vaultDir, '.env.x.secrets'),
);
const adminSecurity = readDotEnv(join(vaultDir, '.env.admin-security'));
const apiUrl = (xSecrets.API_URL ?? 'https://doxed-founders-website-production.up.railway.app').replace(
  /\/$/,
  '',
);
const email = xSecrets.ADMIN_EMAIL || 'admin@doxedcryptofounder.local';
const password = xSecrets.ADMIN_PASSWORD?.trim();
const recoveryCodes = Object.keys(adminSecurity)
  .filter((k) => k.startsWith('ADMIN_RECOVERY_'))
  .sort()
  .map((k) => adminSecurity[k]?.trim())
  .filter(Boolean);

async function passwordLogin() {
  const res = await fetch(`${apiUrl}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ email, password }),
  });
  if (!res.ok) throw new Error(`Login failed: ${res.status}`);
  return res.json();
}

async function obtainJwt() {
  let data = await passwordLogin();
  if (data.accessToken) return data.accessToken;
  if (!data.requires2fa || !data.pendingToken) {
    throw new Error('Login did not return JWT or 2FA challenge');
  }
  for (const recoveryCode of recoveryCodes) {
    const verifyRes = await fetch(`${apiUrl}/api/auth/verify-2fa`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ pendingToken: data.pendingToken, recoveryCode }),
    });
    if (verifyRes.ok) {
      const verified = await verifyRes.json();
      if (verified.accessToken) return verified.accessToken;
    }
    data = await passwordLogin();
    if (!data.requires2fa || !data.pendingToken) break;
  }
  throw new Error('Could not complete 2FA with recovery codes');
}

async function post(path, jwt) {
  const res = await fetch(`${apiUrl}/api${path}`, {
    method: 'POST',
    headers: { Authorization: `Bearer ${jwt}` },
  });
  const body = await res.text();
  console.log(path, res.status, body);
  return res.ok;
}

const jwt = await obtainJwt();
console.log('Admin JWT obtained\n');

const founderOk = await post('/founder-updates/sync-x', jwt);
const socialOk = await post('/x-social/daily-run', jwt);

const feedRes = await fetch(`${apiUrl}/api/feed/unified?limit=5`);
console.log('\nFeed sample:', feedRes.status, (await feedRes.text()).slice(0, 500));

process.exit(founderOk && socialOk ? 0 : 1);
