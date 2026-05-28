import crypto from 'node:crypto';
import fs from 'node:fs';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const neon = fs.readFileSync(path.join(root, '.env.neon'), 'utf8');
const match = neon.match(/DATABASE_URL="([^"]+)"/);
if (!match) process.exit(1);

const password = `Bitbro4Dcf!${crypto.randomBytes(14).toString('base64url')}#2026`;
fs.writeFileSync(
  path.join(root, '.env.admin-rotate'),
  `ADMIN_EMAIL=admin@doxedcryptofounder.local\nSEED_ADMIN_PASSWORD=${password}\nADMIN_PASSWORD=${password}\n`,
  { mode: 0o600 },
);

const secretsPath = path.join(root, '.env.x.secrets');
if (fs.existsSync(secretsPath)) {
  let secrets = fs.readFileSync(secretsPath, 'utf8');
  if (/^ADMIN_PASSWORD=/m.test(secrets)) {
    secrets = secrets.replace(/^ADMIN_PASSWORD=.*$/m, `ADMIN_PASSWORD=${password}`);
  } else {
    secrets = secrets.trimEnd() + `\nADMIN_PASSWORD=${password}\n`;
  }
  fs.writeFileSync(secretsPath, secrets);
}

const result = spawnSync('node', ['scripts/rotate-admin-password.mjs'], {
  cwd: root,
  env: { ...process.env, DATABASE_URL: match[1], SEED_ADMIN_PASSWORD: password },
  stdio: 'inherit',
});
process.exit(result.status ?? 1);
