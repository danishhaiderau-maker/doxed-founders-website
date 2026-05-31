/**
 * Resync Railway JWT_SECRET with vault, then re-encrypt admin TOTP + recovery codes.
 * Fixes "Authenticator unavailable" on login when Railway JWT differs from vault.
 */
import { spawnSync } from 'child_process';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const root = join(dirname(fileURLToPath(import.meta.url)), '..');
const isWin = process.platform === 'win32';

function run(cmd, args) {
  const result = spawnSync(cmd, args, { cwd: root, stdio: 'inherit', shell: isWin });
  if (result.status !== 0) process.exit(result.status ?? 1);
}

console.log('=== Fix admin 2FA (JWT sync + TOTP re-setup) ===\n');

if (isWin) {
  run('powershell', ['-ExecutionPolicy', 'Bypass', '-File', 'scripts/fix-railway-production.ps1']);
} else {
  console.warn('On non-Windows: set JWT_SECRET on Railway to match vault, then continue.');
}

run('node', ['scripts/setup-admin-security.mjs']);
console.log('\nDone. Use new TOTP from vault/.env.admin-security (or existing recovery codes).');
