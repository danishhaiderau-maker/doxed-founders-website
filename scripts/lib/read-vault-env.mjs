import { existsSync, readFileSync } from 'fs';
import { join, dirname } from 'path';
import { fileURLToPath } from 'url';

const scriptsDir = dirname(fileURLToPath(import.meta.url));
export const repoRoot = join(scriptsDir, '..', '..');
export const vaultDir = join(dirname(repoRoot), 'doxedcryptofounder-secrets', 'vault');

export function readDotEnv(path) {
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

export function loadPhalaCvmEnv() {
  const phalaPath = join(vaultDir, '.env.phala');
  const vercelPath = join(vaultDir, '.env.vercel.check');
  const xPath = join(vaultDir, '.env.x.secrets');

  const phala = readDotEnv(phalaPath);
  const vercel = readDotEnv(vercelPath);
  const x = readDotEnv(xPath);

  const merged = {
    ...phala,
    JWT_SECRET: phala.JWT_SECRET || vercel.JWT_SECRET || '',
    RAILWAY_TOKEN: phala.RAILWAY_TOKEN || x.RAILWAY_TOKEN || process.env.RAILWAY_TOKEN || '',
  };

  return { merged, phalaPath, hasPhalaFile: existsSync(phalaPath) };
}
