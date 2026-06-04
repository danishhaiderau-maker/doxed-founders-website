import fs from 'fs';
import path from 'path';
import { getVaultDir } from './secrets-vault-path.mjs';

/** Load env from vault first, then repo .env (gitignored). Never logs values. */
export function loadVaultEnv(repoRoot) {
  const candidates = [
    path.join(getVaultDir(), '.env.neon'),
    path.join(getVaultDir(), '.env'),
    path.join(repoRoot, '.env'),
  ];
  for (const envPath of candidates) {
    if (!fs.existsSync(envPath)) continue;
    for (const line of fs.readFileSync(envPath, 'utf8').split('\n')) {
      const trimmed = line.trim();
      if (!trimmed || trimmed.startsWith('#')) continue;
      const idx = trimmed.indexOf('=');
      if (idx < 1) continue;
      const key = trimmed.slice(0, idx).trim();
      let val = trimmed.slice(idx + 1).trim();
      if ((val.startsWith('"') && val.endsWith('"')) || (val.startsWith("'") && val.endsWith("'"))) {
        val = val.slice(1, -1);
      }
      if (!process.env[key]) process.env[key] = val;
    }
  }
}

export function vaultEnvPath(name) {
  return path.join(getVaultDir(), name);
}

export function resolveWebEnvLocal(repoRoot) {
  const vault = path.join(getVaultDir(), 'apps-web.env.local');
  const local = path.join(repoRoot, 'apps', 'web', '.env.local');
  if (fs.existsSync(vault)) return vault;
  if (fs.existsSync(local)) return local;
  return null;
}
